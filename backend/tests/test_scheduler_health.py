from __future__ import annotations

import asyncio
import fcntl
import json
import multiprocessing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.responses import JSONResponse

import app.main as main_module
from app.services import telegram_push_service


def _hold_synthetic_flock(path: str, ready: Any, release: Any) -> None:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        ready.set()
        release.wait(timeout=10)


@pytest.fixture(autouse=True)
def restore_scheduler_runtime() -> Any:
    original = telegram_push_service.scheduler_runtime_status()
    yield
    for name, state in original.items():
        state.pop("deliveryDegraded", None)
        current = telegram_push_service._scheduler_runtime[name]
        current.clear()
        current.update(state)


@pytest.mark.parametrize(
    ("scheduler_name", "runner_name", "due_name", "send_name", "save_name"),
    [
        (
            "dailyPush",
            "_run_due_pushes",
            "_due_records",
            "_send_push_record",
            "_save_push_failure",
        ),
        (
            "backgroundStory",
            "_run_due_background_stories",
            "_due_story_records",
            "_send_daily_full_story_part",
            "_save_story_failure",
        ),
    ],
)
def test_scheduler_records_per_record_failures_and_keeps_them_across_empty_runs(
    monkeypatch: pytest.MonkeyPatch,
    scheduler_name: str,
    runner_name: str,
    due_name: str,
    send_name: str,
    save_name: str,
) -> None:
    settings = SimpleNamespace(
        telegram_daily_push_enabled=True,
        background_story_enabled=True,
    )
    records = [
        {"telegramId": 101, "petId": "pet-101"},
        {"telegramId": 202, "petId": "pet-202"},
    ]
    due_calls = 0
    saved_failures: list[int] = []

    def due_records(_now: Any) -> list[dict[str, Any]]:
        nonlocal due_calls
        due_calls += 1
        return records if due_calls == 1 else []

    def fail_record(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise telegram_push_service.TelegramPushError(
            "TELEGRAM_SEND_FAILED",
            "synthetic delivery failure",
        )

    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(telegram_push_service, due_name, due_records)
    monkeypatch.setattr(telegram_push_service, send_name, fail_record)
    monkeypatch.setattr(
        telegram_push_service,
        save_name,
        lambda record, _exc: saved_failures.append(record["telegramId"]),
    )
    monkeypatch.setattr(telegram_push_service, "_fresh_record", lambda record: record)
    state = telegram_push_service._scheduler_runtime[scheduler_name]
    state.update(
        running=False,
        consecutiveFailures=0,
        lastRunAt=None,
        lastAttempted=0,
        lastSucceeded=0,
        lastFailed=0,
        lastError=None,
        degradedUntil=None,
    )
    runner = getattr(telegram_push_service, runner_name)

    async def scenario() -> None:
        task = asyncio.create_task(
            telegram_push_service._scheduler_loop(scheduler_name, runner, 0.01)
        )
        while due_calls < 2:
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert saved_failures == [101, 202]
    assert state["running"] is False
    assert state["consecutiveFailures"] == 0
    assert state["lastRunAt"] is not None
    assert state["lastAttempted"] == 2
    assert state["lastSucceeded"] == 0
    assert state["lastFailed"] == 2
    assert state["lastError"] == "TELEGRAM_SEND_FAILED"
    assert (
        telegram_push_service.scheduler_runtime_status()[scheduler_name]["deliveryDegraded"] is True
    )


def test_successful_attempted_batch_clears_previous_delivery_failure() -> None:
    outcomes = [
        telegram_push_service._SchedulerBatchResult(
            results=[{"telegramId": 101}],
            attempted=2,
            failed=1,
            health_failed=1,
            last_error="TELEGRAM_SEND_FAILED",
        ),
        telegram_push_service._SchedulerBatchResult(
            results=[],
            attempted=0,
            failed=0,
            health_failed=0,
            last_error=None,
        ),
        telegram_push_service._SchedulerBatchResult(
            results=[{"telegramId": 101}, {"telegramId": 202}],
            attempted=2,
            failed=0,
            health_failed=0,
            last_error=None,
        ),
    ]
    calls = 0
    release_success = Event()
    state = telegram_push_service._scheduler_runtime["dailyPush"]
    state.update(
        running=False,
        consecutiveFailures=0,
        lastRunAt=None,
        lastAttempted=0,
        lastSucceeded=0,
        lastFailed=0,
        lastError=None,
        degradedUntil=None,
    )

    def operation() -> telegram_push_service._SchedulerBatchResult:
        nonlocal calls
        outcome_index = min(calls, len(outcomes) - 1)
        outcome = outcomes[outcome_index]
        calls += 1
        if outcome_index == 2:
            release_success.wait(timeout=1)
        return outcome

    async def wait_for_state(predicate: Any) -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    async def scenario() -> None:
        task = asyncio.create_task(
            telegram_push_service._scheduler_loop("dailyPush", operation, 0.01)
        )
        await wait_for_state(lambda: calls >= 3)
        assert state["lastAttempted"] == 2
        assert state["lastSucceeded"] == 1
        assert state["lastFailed"] == 1
        assert state["lastError"] == "TELEGRAM_SEND_FAILED"
        release_success.set()
        await wait_for_state(lambda: state["lastFailed"] == 0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert state["running"] is False
    assert state["lastAttempted"] == 2
    assert state["lastSucceeded"] == 2
    assert state["lastFailed"] == 0
    assert state["lastError"] is None
    assert state["degradedUntil"] is None


def test_media_cleanup_scheduler_starts_without_story_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False
    settings = SimpleNamespace(
        background_story_enabled=False,
        generated_media_cleanup_enabled=True,
        bot_token=None,
        webapp_url=None,
    )

    async def run_once() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        telegram_push_service,
        "_generated_media_cleanup_loop",
        run_once,
    )

    async def scenario() -> None:
        assert telegram_push_service.start_background_story_scheduler() is None
        task = telegram_push_service.start_generated_media_cleanup_scheduler()
        assert task is not None
        await task

    asyncio.run(scenario())

    assert called is True


def test_media_cleanup_scheduler_surfaces_sweep_failure_for_supervision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fail_sweep(*, records, now: datetime) -> None:
        captured.update(records=records, now=now)
        raise RuntimeError("synthetic cleanup failure")

    monkeypatch.setattr(
        telegram_push_service,
        "_run_background_story_media_cleanup",
        fail_sweep,
    )
    monkeypatch.setattr(
        telegram_push_service,
        "cleanup_stale_generated_processing_temp_directories",
        lambda **_kwargs: SimpleNamespace(removed=(), failed=(), unsafe=0),
    )

    with pytest.raises(RuntimeError, match="synthetic cleanup failure"):
        telegram_push_service._run_generated_media_cleanup()

    assert captured["records"] is None
    assert telegram_push_service.GENERATED_MEDIA_CLEANUP_LOOP_INTERVAL_SECONDS == 6 * 60 * 60


def test_scheduler_cancellation_drains_started_thread_iteration() -> None:
    started = Event()
    release = Event()

    def operation() -> telegram_push_service._SchedulerBatchResult:
        started.set()
        release.wait(timeout=2)
        return telegram_push_service._SchedulerBatchResult(
            results=[],
            attempted=0,
            failed=0,
            health_failed=0,
            last_error=None,
        )

    async def scenario() -> None:
        task = asyncio.create_task(
            telegram_push_service._scheduler_loop("dailyPush", operation, 60)
        )
        while not started.is_set():
            await asyncio.sleep(0.001)

        task.cancel()
        await asyncio.sleep(0.01)
        assert task.done() is False

        release.set()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)

    asyncio.run(scenario())


def test_scheduler_leadership_flock_is_exclusive_across_processes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "push" / "state.json"
    monkeypatch.setattr(
        telegram_push_service,
        "get_settings",
        lambda: SimpleNamespace(telegram_push_store_path=str(store_path)),
    )
    lock_path = telegram_push_service._scheduler_lock_path("dailyPush")
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_synthetic_flock,
        args=(str(lock_path), ready, release),
    )
    process.start()
    try:
        assert ready.wait(timeout=5)
        assert telegram_push_service._try_acquire_scheduler_leadership("dailyPush") is None
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    assert process.exitcode == 0
    leadership = telegram_push_service._try_acquire_scheduler_leadership("dailyPush")
    assert leadership is not None
    telegram_push_service._release_scheduler_leadership(leadership)


def test_scheduler_standby_retries_and_takes_over_released_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        telegram_push_service,
        "get_settings",
        lambda: SimpleNamespace(telegram_push_store_path=str(tmp_path / "push" / "state.json")),
    )
    blocker = telegram_push_service._try_acquire_scheduler_leadership("dailyPush")
    assert blocker is not None
    operation_started = Event()

    def operation() -> telegram_push_service._SchedulerBatchResult:
        operation_started.set()
        return telegram_push_service._SchedulerBatchResult(
            results=[],
            attempted=0,
            failed=0,
            health_failed=0,
            last_error=None,
        )

    async def wait_until(predicate: Any) -> None:
        async with asyncio.timeout(2):
            while not predicate():
                await asyncio.sleep(0.001)

    async def scenario() -> None:
        task = asyncio.create_task(
            telegram_push_service._scheduler_leadership_loop(
                "dailyPush",
                operation,
                60,
                retry_interval=0.01,
            )
        )
        await wait_until(
            lambda: (
                telegram_push_service.scheduler_runtime_status()["dailyPush"]["role"] == "standby"
            )
        )
        assert operation_started.is_set() is False

        telegram_push_service._release_scheduler_leadership(blocker)
        await wait_until(operation_started.is_set)
        runtime = telegram_push_service.scheduler_runtime_status()["dailyPush"]
        assert runtime["role"] == "leader"
        assert runtime["leaderSince"] is not None
        assert runtime["lastLeadershipAttemptAt"] is not None

        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)

    asyncio.run(scenario())

    runtime = telegram_push_service.scheduler_runtime_status()["dailyPush"]
    assert runtime["running"] is False
    assert runtime["role"] == "stopped"
    assert runtime["leaderSince"] is None
    leadership = telegram_push_service._try_acquire_scheduler_leadership("dailyPush")
    assert leadership is not None
    telegram_push_service._release_scheduler_leadership(leadership)


def test_scheduler_shutdown_keeps_leadership_until_started_iteration_drains(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        telegram_push_service,
        "get_settings",
        lambda: SimpleNamespace(telegram_push_store_path=str(tmp_path / "push" / "state.json")),
    )
    started = Event()
    release = Event()

    def operation() -> telegram_push_service._SchedulerBatchResult:
        started.set()
        release.wait(timeout=2)
        return telegram_push_service._SchedulerBatchResult(
            results=[],
            attempted=0,
            failed=0,
            health_failed=0,
            last_error=None,
        )

    async def scenario() -> None:
        task = asyncio.create_task(
            telegram_push_service._scheduler_leadership_loop(
                "dailyPush",
                operation,
                60,
                retry_interval=0.01,
            )
        )
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(0.001)

        task.cancel()
        await asyncio.sleep(0.01)
        assert task.done() is False
        assert telegram_push_service._try_acquire_scheduler_leadership("dailyPush") is None

        release.set()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)

    asyncio.run(scenario())

    leadership = telegram_push_service._try_acquire_scheduler_leadership("dailyPush")
    assert leadership is not None
    telegram_push_service._release_scheduler_leadership(leadership)


def test_scheduler_standby_cancels_without_waiting_for_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        telegram_push_service,
        "get_settings",
        lambda: SimpleNamespace(telegram_push_store_path=str(tmp_path / "push" / "state.json")),
    )
    blocker = telegram_push_service._try_acquire_scheduler_leadership("backgroundStory")
    assert blocker is not None
    operation_started = Event()

    async def scenario() -> None:
        task = asyncio.create_task(
            telegram_push_service._scheduler_leadership_loop(
                "backgroundStory",
                operation_started.set,
                60,
                retry_interval=60,
            )
        )
        async with asyncio.timeout(2):
            while (
                telegram_push_service.scheduler_runtime_status()["backgroundStory"]["role"]
                != "standby"
            ):
                await asyncio.sleep(0.001)
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)

    try:
        asyncio.run(scenario())
    finally:
        telegram_push_service._release_scheduler_leadership(blocker)

    assert operation_started.is_set() is False
    runtime = telegram_push_service.scheduler_runtime_status()["backgroundStory"]
    assert runtime["running"] is False
    assert runtime["role"] == "stopped"


def test_delivery_degradation_expires_without_erasing_batch_telemetry(monkeypatch) -> None:
    observed_at = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    state = telegram_push_service._scheduler_runtime["dailyPush"]
    state.update(
        running=True,
        consecutiveFailures=0,
        lastRunAt=observed_at.isoformat(),
        lastAttempted=1,
        lastSucceeded=0,
        lastFailed=1,
        lastError="TELEGRAM_SEND_FAILED",
        degradedUntil=(observed_at + timedelta(minutes=10)).isoformat(),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_now",
        lambda: observed_at + timedelta(minutes=11),
    )

    runtime = telegram_push_service.scheduler_runtime_status()["dailyPush"]

    assert runtime["deliveryDegraded"] is False
    assert runtime["lastFailed"] == 1
    assert runtime["lastError"] == "TELEGRAM_SEND_FAILED"


def test_unreachable_user_is_telemetry_failure_but_not_health_failure() -> None:
    unreachable = telegram_push_service.TelegramPushError(
        "TELEGRAM_CHAT_NOT_FOUND",
        "synthetic unreachable chat",
    )

    assert telegram_push_service._scheduler_delivery_affects_health(unreachable) is False
    assert (
        telegram_push_service._scheduler_delivery_affects_health(
            telegram_push_service.TelegramPushError(
                "TELEGRAM_SEND_FAILED",
                "synthetic provider outage",
            )
        )
        is True
    )


class _RunningTask:
    @staticmethod
    def done() -> bool:
        return False


def _runtime(
    *, attempted: int, succeeded: int, failed: int, degraded: bool
) -> dict[str, dict[str, Any]]:
    return {
        "dailyPush": {
            "running": True,
            "consecutiveFailures": 0,
            "lastRunAt": "2026-07-15T12:00:00Z",
            "lastAttempted": attempted,
            "lastSucceeded": succeeded,
            "lastFailed": failed,
            "lastError": "TELEGRAM_SEND_FAILED" if failed else None,
            "deliveryDegraded": degraded,
        },
        "backgroundStory": {
            "running": False,
            "consecutiveFailures": 0,
            "lastRunAt": None,
            "lastAttempted": 0,
            "lastSucceeded": 0,
            "lastFailed": 0,
            "lastError": None,
            "deliveryDegraded": False,
        },
    }


def _health(monkeypatch: pytest.MonkeyPatch, runtime: dict[str, dict[str, Any]]) -> Any:
    monkeypatch.setattr(main_module, "scheduler_runtime_status", lambda: runtime)
    monkeypatch.setattr(main_module.tma, "generation_job_runtime_status", lambda: {"stuck": 0})
    monkeypatch.setattr(main_module, "llm_runtime_status", lambda: {"status": "ok"})
    monkeypatch.setattr(main_module, "media_runtime_status", lambda: {"status": "ok"})
    monkeypatch.setattr(
        main_module,
        "storage_runtime_status",
        lambda: {"status": "ok", "failedPaths": []},
    )
    monkeypatch.setattr(main_module, "notify_ops", lambda *_args, **_kwargs: None)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                scheduler_tasks={
                    "dailyPush": _RunningTask(),
                    "backgroundStory": None,
                }
            )
        )
    )
    return asyncio.run(main_module.health(request))


@pytest.mark.parametrize(
    ("attempted", "succeeded", "failed"),
    [
        (3, 2, 1),
        (3, 0, 3),
    ],
)
def test_health_degrades_for_any_failure_in_latest_attempted_batch(
    monkeypatch: pytest.MonkeyPatch,
    attempted: int,
    succeeded: int,
    failed: int,
) -> None:
    response = _health(
        monkeypatch,
        _runtime(attempted=attempted, succeeded=succeeded, failed=failed, degraded=True),
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload == {"status": "degraded"}


def test_health_recovers_after_next_attempted_batch_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _health(
        monkeypatch,
        _runtime(attempted=2, succeeded=2, failed=0, degraded=False),
    )

    assert response == {"status": "ok"}
