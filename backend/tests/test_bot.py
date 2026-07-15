from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import httpx
import pytest

from app import bot
from app.routers import tma
from app.schemas import LocalPetPushSnapshotRequest
from app.services import telegram_client, telegram_push_service
from app.services.bot_command_runtime import (
    BotCommandLeaseKeeper,
    BoundedBotCommandDispatcher,
    SQLiteBotCommandInbox,
    normalize_bot_command_update,
)
from app.services.story_delivery_format import (
    TELEGRAM_PHOTO_CAPTION_LIMIT,
    format_full_story_message,
    format_story_caption,
)
from app.services.telegram_auth_service import TelegramUserContext

TEST_TELEGRAM_ID = 62943754
STORY_IMPACT_TEXT = "Влияние на параметры:\nздоровье: минус 25"


def _reserved(fake):
    @contextmanager
    def reservation(*args, **kwargs):
        yield fake(*args, **kwargs)

    return reservation


def _story_update() -> dict:
    return {
        "message": {
            "chat": {"id": TEST_TELEGRAM_ID},
            "from": {"first_name": "Serge"},
            "text": "/story",
        }
    }


def _push_update() -> dict:
    return {
        "message": {
            "chat": {"id": TEST_TELEGRAM_ID},
            "from": {"first_name": "Serge"},
            "text": "/push",
        }
    }


def _full_story_update() -> dict:
    return {
        "message": {
            "chat": {"id": TEST_TELEGRAM_ID},
            "from": {"first_name": "Serge"},
            "text": "/full_story",
        }
    }


def _durable_story_update(update_id: int = 42) -> dict:
    update = _story_update()
    update["update_id"] = update_id
    update["message"]["from"]["id"] = TEST_TELEGRAM_ID
    return update


def _durable_command_update(update_id: int, command: str, *, chat_id: int) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": chat_id, "first_name": "Test"},
            "text": command,
        },
    }


def _bot_settings(**overrides) -> SimpleNamespace:
    values = {
        "bot_token": "bot-token",
        "webapp_url": "https://example.com/app",
        "enable_in_memory_rate_limit": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _register_durable_story_snapshot(monkeypatch, tmp_path: Path) -> datetime:
    generated_at = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(
        telegram_push_service,
        "get_settings",
        lambda: SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json")),
    )
    monkeypatch.setattr(telegram_push_service, "_now", lambda: generated_at)
    monkeypatch.setattr(
        telegram_push_service,
        "generated_dir_for",
        lambda pet_id: tmp_path / "generated" / str(pet_id),
    )
    snapshot = LocalPetPushSnapshotRequest.model_validate(
        {
            "petId": "pet-1",
            "createdAt": "2026-07-06T12:00:00Z",
            "updatedAt": "2026-07-07T12:00:00Z",
            "lastStatsTickAt": "2026-07-07T12:00:00Z",
            "timezone": "Europe/Moscow",
            "pet": {
                "name": "Громм",
                "description": "земляной великан",
                "stage": "adult",
                "mood": "idle",
                "stats": {"hunger": 80, "happiness": 70, "energy": 60},
            },
            "memoryContext": {},
        }
    )
    telegram_push_service.register_push_snapshot(
        TelegramUserContext(
            telegram_id=TEST_TELEGRAM_ID,
            username="test",
            first_name="Test",
            language_code="ru",
            auth_date=generated_at,
        ),
        snapshot,
    )
    return generated_at


def test_polled_command_is_persisted_before_offset_advances(monkeypatch, tmp_path: Path) -> None:
    inbox = SQLiteBotCommandInbox(tmp_path / "bot-command-inbox.sqlite3")
    offset_path = tmp_path / "bot-update-offset.json"
    real_save_offset = bot.save_bot_update_offset
    observed_statuses: list[str | None] = []

    def assert_inbox_first(path: Path, offset: int) -> int:
        observed_statuses.append(inbox.status(42))
        return real_save_offset(path, offset)

    monkeypatch.setattr(bot, "save_bot_update_offset", assert_inbox_first)

    offset, inbox_full = bot._persist_polled_updates(
        inbox,
        offset_path,
        None,
        [_durable_story_update()],
    )

    assert observed_statuses == ["pending"]
    assert offset == 43
    assert inbox_full is False
    assert bot.load_bot_update_offset(offset_path) == 43


def test_polled_updates_are_sorted_before_monotonic_offset_advances(
    monkeypatch,
    tmp_path: Path,
) -> None:
    inbox = SQLiteBotCommandInbox(tmp_path / "bot-command-inbox.sqlite3")
    offset_path = tmp_path / "bot-update-offset.json"
    real_save_offset = bot.save_bot_update_offset
    requested_offsets: list[int] = []

    def record_offset(path: Path, offset: int) -> int:
        requested_offsets.append(offset)
        return real_save_offset(path, offset)

    monkeypatch.setattr(bot, "save_bot_update_offset", record_offset)

    offset, inbox_full = bot._persist_polled_updates(
        inbox,
        offset_path,
        None,
        [
            _durable_story_update(3),
            _durable_story_update(1),
            _durable_story_update(2),
        ],
    )

    assert requested_offsets == [2, 3, 4]
    assert offset == 4
    assert inbox_full is False
    assert [inbox.status(update_id) for update_id in (1, 2, 3)] == [
        "pending",
        "pending",
        "pending",
    ]


def test_per_chat_burst_is_tombstoned_and_offset_continues(tmp_path: Path) -> None:
    inbox = SQLiteBotCommandInbox(
        tmp_path / "bot-command-inbox.sqlite3",
        max_pending=10,
        max_pending_per_chat=1,
    )
    offset_path = tmp_path / "bot-update-offset.json"

    offset, inbox_full = bot._persist_polled_updates(
        inbox,
        offset_path,
        None,
        [
            _durable_command_update(1, "/story", chat_id=1001),
            _durable_command_update(2, "/story", chat_id=1001),
            _durable_command_update(3, "/story", chat_id=2002),
        ],
    )

    assert offset == 4
    assert inbox_full is False
    assert [inbox.status(update_id) for update_id in (1, 2, 3)] == [
        "pending",
        "completed",
        "pending",
    ]


def test_noncommands_do_not_accumulate_in_durable_inbox(tmp_path: Path) -> None:
    inbox = SQLiteBotCommandInbox(tmp_path / "bot-command-inbox.sqlite3")
    offset_path = tmp_path / "bot-update-offset.json"
    updates = [
        {
            "update_id": 1,
            "message": {"chat": {"id": TEST_TELEGRAM_ID}, "text": "hello"},
        },
        {
            "update_id": 2,
            "message": {"chat": {}, "text": "/story"},
        },
    ]

    offset, inbox_full = bot._persist_polled_updates(
        inbox,
        offset_path,
        None,
        updates,
    )

    assert offset == 3
    assert inbox_full is False
    assert inbox.list_ready(limit=10) == []


def test_full_durable_inbox_does_not_acknowledge_unstored_command(tmp_path: Path) -> None:
    inbox = SQLiteBotCommandInbox(
        tmp_path / "bot-command-inbox.sqlite3",
        max_pending=1,
    )
    offset_path = tmp_path / "bot-update-offset.json"

    offset, inbox_full = bot._persist_polled_updates(
        inbox,
        offset_path,
        None,
        [_durable_story_update(1), _durable_story_update(2)],
    )

    assert offset == 2
    assert inbox_full is True
    assert bot.load_bot_update_offset(offset_path) == 2
    assert [item.update_id for item in inbox.list_ready(limit=10)] == [1]


def test_durable_command_completes_only_after_worker_callback_and_shutdown_drain(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bot, "get_settings", lambda: _bot_settings())
    started = Event()
    release = Event()

    def worker(_chat_id: int, _keyboard: dict) -> None:
        started.set()
        release.wait(timeout=2)

    monkeypatch.setattr(bot, "_story_worker", worker)
    inbox = SQLiteBotCommandInbox(tmp_path / "bot-command-inbox.sqlite3")
    command = normalize_bot_command_update(_durable_story_update())
    assert command is not None
    inbox.enqueue(command)
    dispatcher = BoundedBotCommandDispatcher(max_workers=1, max_queued_commands=0)
    keeper = BotCommandLeaseKeeper(
        inbox,
        owner="process",
        lease_seconds=30,
        heartbeat_interval_seconds=0.01,
    )
    keeper.start()
    try:
        with httpx.Client() as client:
            assert (
                bot._dispatch_pending_commands(
                    client,
                    inbox,
                    dispatcher,
                    keeper,
                    owner="process",
                    lease_seconds=30,
                    ready_limit=10,
                )
                == 1
            )
        assert started.wait(timeout=1)
        assert inbox.status(42) == "processing"

        shutdown_finished = Event()

        def shutdown() -> None:
            dispatcher.shutdown()
            shutdown_finished.set()

        shutdown_thread = Thread(target=shutdown)
        shutdown_thread.start()
        time.sleep(0.02)
        assert shutdown_finished.is_set() is False
        assert inbox.status(42) == "processing"

        release.set()
        shutdown_thread.join(timeout=2)
        assert shutdown_finished.is_set() is True
        assert inbox.status(42) == "completed"
    finally:
        release.set()
        keeper.stop()


def test_run_bot_recovers_durable_lifecycle_and_drains_on_sigterm(
    monkeypatch,
    tmp_path: Path,
) -> None:
    inbox_path = tmp_path / "bot-command-inbox.sqlite3"
    offset_path = tmp_path / "bot-update-offset.json"
    settings = _bot_settings(
        bot_update_offset_path=str(offset_path),
        bot_command_inbox_path=str(inbox_path),
        bot_command_inbox_max_pending=10,
        bot_command_inbox_max_pending_per_chat=8,
        bot_command_inbox_max_completed=10,
        bot_command_inbox_completed_retention_seconds=3_600,
        bot_command_inbox_lease_seconds=30,
        bot_story_workers=1,
        bot_command_max_queued=0,
    )
    monkeypatch.setattr(bot, "get_settings", lambda: settings)
    monkeypatch.setattr(bot, "BOT_HEARTBEAT_PATH", tmp_path / "heartbeat")
    worker_calls: list[int] = []
    monkeypatch.setattr(
        bot,
        "_story_worker",
        lambda chat_id, _keyboard: worker_calls.append(chat_id),
    )
    handlers: dict[int, object] = {}

    def fake_signal(signum: int, handler: object) -> object:
        previous = handlers.get(signum, bot.signal.SIG_DFL)
        handlers[signum] = handler
        return previous

    monkeypatch.setattr(bot.signal, "signal", fake_signal)

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self) -> None:
            self.poll_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, *_args, **_kwargs) -> FakeResponse:
            self.poll_count += 1
            if self.poll_count == 1:
                return FakeResponse({"result": [_durable_story_update()]})
            handler = handlers[bot.signal.SIGTERM]
            assert callable(handler)
            handler(bot.signal.SIGTERM, None)
            return FakeResponse({"result": []})

    monkeypatch.setattr(bot.httpx, "Client", FakeClient)

    bot.run_bot()

    inbox = SQLiteBotCommandInbox(inbox_path)
    assert worker_calls == [TEST_TELEGRAM_ID]
    assert inbox.status(42) == "completed"
    assert bot.load_bot_update_offset(offset_path) == 43


def test_global_full_inbox_does_not_refresh_bot_heartbeat(monkeypatch, tmp_path: Path) -> None:
    inbox_path = tmp_path / "bot-command-inbox.sqlite3"
    offset_path = tmp_path / "bot-update-offset.json"
    settings = _bot_settings(
        bot_update_offset_path=str(offset_path),
        bot_command_inbox_path=str(inbox_path),
        bot_command_inbox_max_pending=1,
        bot_command_inbox_max_pending_per_chat=8,
        bot_command_inbox_max_completed=10,
        bot_command_inbox_completed_retention_seconds=3_600,
        bot_command_inbox_lease_seconds=30,
        bot_story_workers=1,
        bot_command_max_queued=0,
    )
    monkeypatch.setattr(bot, "get_settings", lambda: settings)
    blocked_inbox = SQLiteBotCommandInbox(inbox_path, max_pending=1)
    blocked_command = normalize_bot_command_update(_durable_story_update(1))
    assert blocked_command is not None
    blocked_inbox.enqueue(blocked_command)
    assert blocked_inbox.claim(1, owner="stalled-process", lease_seconds=3_600) is True

    class HeartbeatSpy:
        touches = 0

        def touch(self) -> None:
            self.touches += 1

    heartbeat = HeartbeatSpy()
    monkeypatch.setattr(bot, "BOT_HEARTBEAT_PATH", heartbeat)

    class FastEvent:
        def __init__(self) -> None:
            self._set = False

        def set(self) -> None:
            self._set = True

        def is_set(self) -> bool:
            return self._set

        def wait(self, _timeout: float | None = None) -> bool:
            return self._set

    monkeypatch.setattr(bot, "Event", FastEvent)
    handlers: dict[int, object] = {}

    def fake_signal(signum: int, handler: object) -> object:
        previous = handlers.get(signum, bot.signal.SIG_DFL)
        handlers[signum] = handler
        return previous

    monkeypatch.setattr(bot.signal, "signal", fake_signal)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"result": [_durable_command_update(2, "/story", chat_id=2002)]}

    class FakeClient:
        def __init__(self) -> None:
            self.poll_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, *_args, **_kwargs) -> FakeResponse:
            self.poll_count += 1
            if self.poll_count == 2:
                handler = handlers[bot.signal.SIGTERM]
                assert callable(handler)
                handler(bot.signal.SIGTERM, None)
            return FakeResponse()

    monkeypatch.setattr(bot.httpx, "Client", FakeClient)

    bot.run_bot()

    assert heartbeat.touches == 1
    assert blocked_inbox.status(2) is None


def test_dispatch_stops_claiming_new_commands_after_shutdown_request(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bot, "get_settings", lambda: _bot_settings())
    inbox = SQLiteBotCommandInbox(tmp_path / "bot-command-inbox.sqlite3")
    for update_id, chat_id in ((1, 1001), (2, 2002)):
        command = normalize_bot_command_update(
            _durable_command_update(update_id, "/help", chat_id=chat_id)
        )
        assert command is not None
        inbox.enqueue(command)

    stop_requested = Event()

    def send_once(*_args, **_kwargs) -> None:
        stop_requested.set()

    monkeypatch.setattr(bot, "send_message", send_once)
    dispatcher = BoundedBotCommandDispatcher(max_workers=1, max_queued_commands=0)
    keeper = BotCommandLeaseKeeper(inbox, owner="process", lease_seconds=30)
    try:
        assert (
            bot._dispatch_pending_commands(
                object(),
                inbox,
                dispatcher,
                keeper,
                owner="process",
                lease_seconds=30,
                ready_limit=10,
                should_stop=stop_requested.is_set,
            )
            == 1
        )
    finally:
        dispatcher.shutdown()

    assert inbox.status(1) == "completed"
    assert inbox.status(2) == "pending"


def test_two_dispatchers_cannot_process_same_chat_concurrently(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bot, "get_settings", lambda: _bot_settings())
    inbox = SQLiteBotCommandInbox(tmp_path / "bot-command-inbox.sqlite3")
    for update_id in (1, 2):
        command = normalize_bot_command_update(_durable_story_update(update_id))
        assert command is not None
        assert inbox.enqueue(command) is True

    started = Event()
    release = Event()
    calls: list[int] = []

    def worker(chat_id: int, _keyboard: dict) -> None:
        calls.append(chat_id)
        started.set()
        release.wait(timeout=2)

    monkeypatch.setattr(bot, "_story_worker", worker)
    first_dispatcher = BoundedBotCommandDispatcher(max_workers=1, max_queued_commands=0)
    second_dispatcher = BoundedBotCommandDispatcher(max_workers=1, max_queued_commands=0)
    first_keeper = BotCommandLeaseKeeper(
        inbox,
        owner="first-process",
        lease_seconds=30,
        heartbeat_interval_seconds=0.01,
    )
    second_keeper = BotCommandLeaseKeeper(
        inbox,
        owner="second-process",
        lease_seconds=30,
        heartbeat_interval_seconds=0.01,
    )
    first_keeper.start()
    second_keeper.start()
    try:
        assert (
            bot._dispatch_pending_commands(
                object(),
                inbox,
                first_dispatcher,
                first_keeper,
                owner="first-process",
                lease_seconds=30,
                ready_limit=10,
            )
            == 1
        )
        assert started.wait(timeout=1)
        assert (
            bot._dispatch_pending_commands(
                object(),
                inbox,
                second_dispatcher,
                second_keeper,
                owner="second-process",
                lease_seconds=30,
                ready_limit=10,
            )
            == 0
        )
        assert calls == [TEST_TELEGRAM_ID]
        assert inbox.status(1) == "processing"
        assert inbox.status(2) == "pending"
    finally:
        release.set()
        first_dispatcher.shutdown()
        second_dispatcher.shutdown()
        first_keeper.stop()
        second_keeper.stop()


def test_fatal_worker_exception_leaves_lease_for_recovery(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bot, "get_settings", lambda: _bot_settings())
    now = [1_000.0]
    inbox = SQLiteBotCommandInbox(
        tmp_path / "bot-command-inbox.sqlite3",
        clock=lambda: now[0],
    )
    command = normalize_bot_command_update(_durable_story_update())
    assert command is not None
    inbox.enqueue(command)
    assert inbox.claim(command.update_id, owner="process", lease_seconds=30) is True
    keeper = BotCommandLeaseKeeper(inbox, owner="process", lease_seconds=30)
    keeper.add(command.update_id)

    def abort(_chat_id: int, _keyboard: dict) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        bot._finish_durable_worker(
            abort,
            command.chat_id,
            {},
            command=command,
            inbox=inbox,
            owner="process",
            lease_keeper=keeper,
        )

    assert inbox.status(command.update_id) == "processing"
    now[0] += 31
    assert [item.update_id for item in inbox.list_ready(limit=10)] == [command.update_id]


def test_taken_over_command_is_fenced_before_worker_callback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bot, "get_settings", lambda: _bot_settings())
    now = [1_000.0]
    path = tmp_path / "bot-command-inbox.sqlite3"
    inbox = SQLiteBotCommandInbox(path, clock=lambda: now[0])
    command = normalize_bot_command_update(_durable_story_update())
    assert command is not None
    inbox.enqueue(command)
    assert inbox.claim(command.update_id, owner="first", lease_seconds=3) is True
    keeper = BotCommandLeaseKeeper(inbox, owner="first", lease_seconds=30)
    keeper.add(command.update_id)

    now[0] += 4
    takeover = SQLiteBotCommandInbox(path, clock=lambda: now[0])
    assert takeover.claim(command.update_id, owner="takeover", lease_seconds=30) is True
    worker_calls: list[int] = []

    bot._finish_durable_worker(
        lambda chat_id, _keyboard: worker_calls.append(chat_id),
        command.chat_id,
        {},
        command=command,
        inbox=inbox,
        owner="first",
        lease_keeper=keeper,
    )

    assert worker_calls == []
    assert keeper.snapshot() == set()
    assert takeover.status(command.update_id) == "processing"
    assert takeover.load_prepared(command.update_id, owner="takeover") is None


def test_takeover_during_story_text_fences_next_paid_stage_and_delivery(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bot, "get_settings", lambda: _bot_settings())
    now = [1_000.0]
    path = tmp_path / "bot-command-inbox.sqlite3"
    inbox = SQLiteBotCommandInbox(path, clock=lambda: now[0])
    command = normalize_bot_command_update(_durable_story_update(43))
    assert command is not None
    inbox.enqueue(command)
    keeper = BotCommandLeaseKeeper(inbox, owner="first", lease_seconds=3)
    dispatcher = BoundedBotCommandDispatcher(max_workers=1, max_queued_commands=0)
    text_started = Event()
    release_text = Event()
    paid_stage_started = Event()
    deliveries: list[str] = []

    def generate_story(**kwargs):
        text_started.set()
        assert release_text.wait(timeout=2)
        claim_check = kwargs.get("assert_active")
        assert callable(claim_check)
        claim_check()
        paid_stage_started.set()
        return {"petId": "pet-1", "story": {"title": "synthetic"}}

    monkeypatch.setattr(
        telegram_push_service,
        "generate_story_for_telegram_user",
        generate_story,
    )
    monkeypatch.setattr(bot, "send_message", lambda *_args, **_kwargs: deliveries.append("text"))
    monkeypatch.setattr(bot, "send_video", lambda *_args, **_kwargs: deliveries.append("video"))

    try:
        assert (
            bot._dispatch_pending_commands(
                object(),
                inbox,
                dispatcher,
                keeper,
                owner="first",
                lease_seconds=3,
                ready_limit=1,
            )
            == 1
        )
        assert text_started.wait(timeout=1)
        now[0] += 4
        takeover = SQLiteBotCommandInbox(path, clock=lambda: now[0])
        assert takeover.claim(command.update_id, owner="takeover", lease_seconds=30) is True
        release_text.set()
    finally:
        release_text.set()
        dispatcher.shutdown()

    assert not paid_stage_started.is_set()
    assert deliveries == []
    assert keeper.snapshot() == set()
    assert takeover.status(command.update_id) == "processing"


def test_story_crash_replay_reuses_checkpoint_without_second_generation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    now = [1_000.0]
    inbox = SQLiteBotCommandInbox(
        tmp_path / "bot-command-inbox.sqlite3",
        clock=lambda: now[0],
    )
    command = normalize_bot_command_update(_durable_story_update())
    assert command is not None
    inbox.enqueue(command)
    assert inbox.claim(command.update_id, owner="first", lease_seconds=30) is True

    generation_calls: list[int] = []

    def generate_story(**_kwargs):
        generation_calls.append(1)
        return {
            "petId": "pet-1",
            "story": {
                "title": "Один результат",
                "storyText": "Эта история не должна генерироваться повторно.",
                "videoUrl": "/static/generated/pet-1/background-story-once.mp4?v=1",
            },
            "storyVideo": {"bytes": b"first-delivery-video"},
        }

    monkeypatch.setattr(
        telegram_push_service,
        "generate_story_for_telegram_user",
        generate_story,
    )
    monkeypatch.setattr(
        telegram_push_service,
        "load_persisted_story_video_bytes",
        lambda **_kwargs: b"checkpoint-video",
    )
    delivered: list[bytes] = []

    def crash_once(_client, _chat_id, video, _caption, _keyboard):
        delivered.append(video)
        if len(delivered) == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(bot, "send_video", crash_once)

    with pytest.raises(KeyboardInterrupt):
        bot._story_worker(
            command.chat_id,
            {},
            durable_inbox=inbox,
            durable_update_id=command.update_id,
            durable_owner="first",
        )

    assert generation_calls == [1]
    assert inbox.load_prepared(command.update_id, owner="first")["kind"] == "story"
    now[0] += 31
    assert inbox.claim(command.update_id, owner="second", lease_seconds=30) is True

    bot._story_worker(
        command.chat_id,
        {},
        durable_inbox=inbox,
        durable_update_id=command.update_id,
        durable_owner="second",
    )

    assert generation_calls == [1]
    assert delivered == [b"first-delivery-video", b"checkpoint-video"]


@pytest.mark.parametrize("crash_stage", ["text", "image", "video"])
def test_story_staged_crash_replay_calls_each_provider_once(
    monkeypatch,
    tmp_path: Path,
    crash_stage: str,
) -> None:
    _register_durable_story_snapshot(monkeypatch, tmp_path)
    text_calls: list[int] = []
    image_calls: list[int] = []
    video_calls: list[bytes] = []

    def generate_text(**_kwargs):
        text_calls.append(1)
        return SimpleNamespace(
            title="Одна история",
            summary="Громм прошёл через каменный дождь.",
            story_text="Камни гремели, но Громм дошёл до укрытия.",
            event_type="journey",
            valence="negative",
            tags=("камни",),
            rag_text="Громм пережил каменный дождь.",
            story_library_patch=None,
            lite_overlay_patch=None,
            recent_story_event=None,
            stat_impacts=({"stat": "energy", "amount": -5, "reason": "Трудный путь."},),
            stat_impact=None,
            stat_validation=None,
            prompt_debug=[],
        )

    def generate_image(**kwargs):
        image_calls.append(1)
        kwargs["direction_output"].update({"poseFamily": "walking_or_exploring"})
        return b"story-image"

    def generate_video(image_bytes: bytes) -> bytes:
        video_calls.append(image_bytes)
        return b"story-video"

    monkeypatch.setattr(telegram_push_service, "generate_background_story", generate_text)
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_image_bytes",
        _reserved(generate_image),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(generate_video),
    )
    delivered: list[bytes] = []
    monkeypatch.setattr(
        bot,
        "send_video",
        lambda _client, _chat_id, video, _caption, _keyboard: delivered.append(video),
    )

    now = [1_000.0]
    inbox = SQLiteBotCommandInbox(
        tmp_path / "bot-command-inbox.sqlite3",
        clock=lambda: now[0],
    )
    command = normalize_bot_command_update(_durable_story_update(101))
    assert command is not None
    inbox.enqueue(command)
    assert inbox.claim(command.update_id, owner="first", lease_seconds=30) is True
    real_checkpoint = inbox.checkpoint_prepared
    crashed: list[str] = []

    def crash_after_checkpoint(update_id, *, owner, payload):
        checkpointed = real_checkpoint(update_id, owner=owner, payload=payload)
        progress = payload.get("progress")
        if not crashed and isinstance(progress, dict) and crash_stage in progress:
            crashed.append(crash_stage)
            raise KeyboardInterrupt
        return checkpointed

    monkeypatch.setattr(inbox, "checkpoint_prepared", crash_after_checkpoint)

    with pytest.raises(KeyboardInterrupt):
        bot._story_worker(
            command.chat_id,
            {},
            durable_inbox=inbox,
            durable_update_id=command.update_id,
            durable_owner="first",
        )

    now[0] += 31
    assert inbox.claim(command.update_id, owner="second", lease_seconds=30) is True
    bot._story_worker(
        command.chat_id,
        {},
        durable_inbox=inbox,
        durable_update_id=command.update_id,
        durable_owner="second",
    )

    assert crashed == [crash_stage]
    assert text_calls == [1]
    assert image_calls == [1]
    assert video_calls == [b"story-image"]
    assert delivered == [b"story-video"]
    saved = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert saved["pet"]["stats"]["energy"] == 55


def test_story_replay_recovers_atomic_image_saved_before_stage_checkpoint(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _register_durable_story_snapshot(monkeypatch, tmp_path)
    image_calls: list[int] = []
    monkeypatch.setattr(
        telegram_push_service,
        "generate_background_story",
        lambda **_kwargs: SimpleNamespace(
            title="Сохранённый кадр",
            summary="Громм увидел свет.",
            story_text="Громм увидел свет между камнями.",
            event_type="discovery",
            valence="neutral",
            tags=("свет",),
            rag_text="Громм увидел свет.",
            story_library_patch=None,
            lite_overlay_patch=None,
            recent_story_event=None,
            stat_impacts=(),
            stat_impact=None,
            stat_validation=None,
            prompt_debug=[],
        ),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_image_bytes",
        _reserved(lambda **_kwargs: image_calls.append(1) or b"atomic-image"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(lambda image: b"video-" + image),
    )
    monkeypatch.setattr(bot, "send_video", lambda *_args, **_kwargs: None)

    now = [1_000.0]
    inbox = SQLiteBotCommandInbox(
        tmp_path / "bot-command-inbox.sqlite3",
        clock=lambda: now[0],
    )
    command = normalize_bot_command_update(_durable_story_update(102))
    assert command is not None
    inbox.enqueue(command)
    assert inbox.claim(command.update_id, owner="first", lease_seconds=30) is True
    real_checkpoint = inbox.checkpoint_prepared
    crashed = [False]

    def crash_before_image_checkpoint(update_id, *, owner, payload):
        progress = payload.get("progress")
        if not crashed[0] and isinstance(progress, dict) and "image" in progress:
            crashed[0] = True
            raise KeyboardInterrupt
        return real_checkpoint(update_id, owner=owner, payload=payload)

    monkeypatch.setattr(inbox, "checkpoint_prepared", crash_before_image_checkpoint)
    with pytest.raises(KeyboardInterrupt):
        bot._story_worker(
            command.chat_id,
            {},
            durable_inbox=inbox,
            durable_update_id=command.update_id,
            durable_owner="first",
        )

    assert image_calls == [1]
    now[0] += 31
    assert inbox.claim(command.update_id, owner="second", lease_seconds=30) is True
    bot._story_worker(
        command.chat_id,
        {},
        durable_inbox=inbox,
        durable_update_id=command.update_id,
        durable_owner="second",
    )

    assert image_calls == [1]


def test_story_receipt_prevents_duplicate_stats_after_intervening_overwrite(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _register_durable_story_snapshot(monkeypatch, tmp_path)
    text_calls: list[int] = []
    monkeypatch.setattr(
        telegram_push_service,
        "generate_background_story",
        lambda **_kwargs: (
            text_calls.append(1)
            or SimpleNamespace(
                title="Первая история",
                summary="Громм устал.",
                story_text="Громм долго шёл по камням.",
                event_type="journey",
                valence="negative",
                tags=("дорога",),
                rag_text="Громм долго шёл.",
                story_library_patch=None,
                lite_overlay_patch=None,
                recent_story_event=None,
                stat_impacts=({"stat": "energy", "amount": -5, "reason": "Долгий путь."},),
                stat_impact=None,
                stat_validation=None,
                prompt_debug=[],
            )
        ),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_image_bytes",
        _reserved(lambda **_kwargs: b"receipt-image"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(lambda image: b"video-" + image),
    )
    monkeypatch.setattr(bot, "send_video", lambda *_args, **_kwargs: None)

    now = [1_000.0]
    inbox = SQLiteBotCommandInbox(
        tmp_path / "bot-command-inbox.sqlite3",
        clock=lambda: now[0],
    )
    command = normalize_bot_command_update(_durable_story_update(104))
    assert command is not None
    inbox.enqueue(command)
    assert inbox.claim(command.update_id, owner="first", lease_seconds=30) is True
    real_checkpoint = inbox.checkpoint_prepared
    crashed = [False]

    def crash_before_commit_checkpoint(update_id, *, owner, payload):
        progress = payload.get("progress")
        if not crashed[0] and isinstance(progress, dict) and "storyCommitted" in progress:
            crashed[0] = True
            raise KeyboardInterrupt
        return real_checkpoint(update_id, owner=owner, payload=payload)

    monkeypatch.setattr(inbox, "checkpoint_prepared", crash_before_commit_checkpoint)
    with pytest.raises(KeyboardInterrupt):
        bot._story_worker(
            command.chat_id,
            {},
            durable_inbox=inbox,
            durable_update_id=command.update_id,
            durable_owner="first",
        )

    def overwrite_last_story(current):
        next_record = dict(current)
        next_record["lastStoryAt"] = "2026-07-07T12:01:00Z"
        next_record["lastStory"] = {
            "title": "Более новая история",
            "storyText": "Другая история уже заняла lastStory.",
            "generatedAt": "2026-07-07T12:01:00Z",
            "requestKey": "scheduler:other",
        }
        return next_record

    telegram_push_service._update_record(TEST_TELEGRAM_ID, overwrite_last_story)
    now[0] += 31
    assert inbox.claim(command.update_id, owner="second", lease_seconds=30) is True
    bot._story_worker(
        command.chat_id,
        {},
        durable_inbox=inbox,
        durable_update_id=command.update_id,
        durable_owner="second",
    )

    saved = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert text_calls == [1]
    assert saved["pet"]["stats"]["energy"] == 55
    assert saved["lastStory"]["title"] == "Более новая история"
    assert any(
        receipt.get("requestKey") == "telegram-update:104"
        for receipt in saved["botGenerationReceipts"]
    )


def test_full_story_crash_replay_calls_each_provider_only_once(
    monkeypatch,
    tmp_path: Path,
) -> None:
    service_settings = SimpleNamespace(
        telegram_push_store_path=str(tmp_path / "push.json"),
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: service_settings)
    generated_at = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: generated_at)
    monkeypatch.setattr(
        telegram_push_service,
        "generated_dir_for",
        lambda pet_id: tmp_path / "generated" / str(pet_id),
    )
    snapshot = LocalPetPushSnapshotRequest.model_validate(
        {
            "petId": "pet-1",
            "createdAt": "2026-07-06T12:00:00Z",
            "updatedAt": "2026-07-07T12:00:00Z",
            "lastStatsTickAt": "2026-07-07T12:00:00Z",
            "timezone": "Europe/Moscow",
            "pet": {
                "name": "Громм",
                "description": "земляной великан",
                "stage": "adult",
                "mood": "idle",
                "stats": {"hunger": 80, "happiness": 70, "energy": 60},
            },
            "memoryContext": {},
        }
    )
    telegram_push_service.register_push_snapshot(
        TelegramUserContext(
            telegram_id=TEST_TELEGRAM_ID,
            username="test",
            first_name="Test",
            language_code="ru",
            auth_date=generated_at,
        ),
        snapshot,
    )

    class Part:
        stat_impacts: tuple[dict, ...] = ()

        def __init__(self, number: int) -> None:
            self.number = number

        def model_dump(self) -> dict:
            return {
                "partNumber": self.number,
                "title": f"Часть {self.number}",
                "storyText": f"Событие {self.number}.",
                "statImpacts": [],
            }

    text_provider_calls: list[int] = []
    image_provider_calls: list[int] = []
    video_provider_calls: list[bytes] = []

    def generate_full_story(**_kwargs):
        text_provider_calls.append(1)
        return SimpleNamespace(
            overall_title="Одна большая история",
            arc_plan={},
            story_direction={},
            parts=tuple(Part(number) for number in range(1, 5)),
            prompt_debug=[],
        )

    def generate_image(**kwargs):
        number = kwargs["part"]["partNumber"]
        image_provider_calls.append(number)
        return f"image-{number}".encode()

    def generate_video(image_bytes: bytes) -> bytes:
        video_provider_calls.append(image_bytes)
        return b"video-" + image_bytes

    monkeypatch.setattr(telegram_push_service, "generate_full_story", generate_full_story)
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_full_story_part_image_bytes",
        _reserved(generate_image),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(generate_video),
    )

    now = [1_000.0]
    inbox = SQLiteBotCommandInbox(
        tmp_path / "bot-command-inbox.sqlite3",
        clock=lambda: now[0],
    )
    command = normalize_bot_command_update(
        _durable_command_update(99, "/full_story", chat_id=TEST_TELEGRAM_ID)
    )
    assert command is not None
    inbox.enqueue(command)
    assert inbox.claim(command.update_id, owner="first", lease_seconds=30) is True
    delivered: list[bytes] = []

    def crash_once(_client, _chat_id, video, _caption, _keyboard):
        delivered.append(video)
        if len(delivered) == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(telegram_push_service, "send_video", crash_once)

    with pytest.raises(KeyboardInterrupt):
        bot._full_story_worker(
            command.chat_id,
            {},
            durable_inbox=inbox,
            durable_update_id=command.update_id,
            durable_owner="first",
        )

    now[0] += 31
    assert inbox.claim(command.update_id, owner="second", lease_seconds=30) is True
    bot._full_story_worker(
        command.chat_id,
        {},
        durable_inbox=inbox,
        durable_update_id=command.update_id,
        durable_owner="second",
    )

    assert text_provider_calls == [1]
    assert image_provider_calls == [1, 2, 3, 4]
    assert video_provider_calls == [
        b"image-1",
        b"image-2",
        b"image-3",
        b"image-4",
    ]
    assert delivered == [
        b"video-image-1",
        b"video-image-1",
        b"video-image-2",
        b"video-image-3",
        b"video-image-4",
    ]


@pytest.mark.parametrize(
    "crash_stage",
    ["text", "part:2:image", "part:3:video"],
)
def test_full_story_staged_crash_replay_calls_each_provider_once(
    monkeypatch,
    tmp_path: Path,
    crash_stage: str,
) -> None:
    _register_durable_story_snapshot(monkeypatch, tmp_path)

    class Part:
        stat_impacts = ({"stat": "energy", "amount": -1, "reason": "Долгая дорога."},)

        def __init__(self, number: int) -> None:
            self.number = number

        def model_dump(self) -> dict:
            return {
                "partNumber": self.number,
                "title": f"Часть {self.number}",
                "summary": f"Кратко {self.number}.",
                "storyText": f"Событие {self.number}.",
                "valence": "negative",
                "statImpacts": list(self.stat_impacts),
            }

    text_calls: list[int] = []
    image_calls: list[int] = []
    video_calls: list[bytes] = []

    def generate_text(**_kwargs):
        text_calls.append(1)
        return SimpleNamespace(
            overall_title="Возобновляемая история",
            arc_plan={},
            story_direction={},
            parts=tuple(Part(number) for number in range(1, 5)),
            prompt_debug=[],
        )

    def generate_image(**kwargs):
        number = kwargs["part"]["partNumber"]
        image_calls.append(number)
        return f"image-{number}".encode()

    def generate_video(image: bytes) -> bytes:
        video_calls.append(image)
        return b"video-" + image

    monkeypatch.setattr(telegram_push_service, "generate_full_story", generate_text)
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_full_story_part_image_bytes",
        _reserved(generate_image),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(generate_video),
    )
    delivered: list[bytes] = []
    monkeypatch.setattr(
        telegram_push_service,
        "send_video",
        lambda _client, _chat_id, video, _caption, _keyboard: delivered.append(video),
    )

    now = [1_000.0]
    inbox = SQLiteBotCommandInbox(
        tmp_path / "bot-command-inbox.sqlite3",
        clock=lambda: now[0],
    )
    command = normalize_bot_command_update(
        _durable_command_update(103, "/full_story", chat_id=TEST_TELEGRAM_ID)
    )
    assert command is not None
    inbox.enqueue(command)
    assert inbox.claim(command.update_id, owner="first", lease_seconds=30) is True
    real_checkpoint = inbox.checkpoint_prepared
    crashed: list[str] = []

    def crash_after_checkpoint(update_id, *, owner, payload):
        checkpointed = real_checkpoint(update_id, owner=owner, payload=payload)
        progress = payload.get("progress")
        if not crashed and isinstance(progress, dict) and crash_stage in progress:
            crashed.append(crash_stage)
            raise KeyboardInterrupt
        return checkpointed

    monkeypatch.setattr(inbox, "checkpoint_prepared", crash_after_checkpoint)
    with pytest.raises(KeyboardInterrupt):
        bot._full_story_worker(
            command.chat_id,
            {},
            durable_inbox=inbox,
            durable_update_id=command.update_id,
            durable_owner="first",
        )

    now[0] += 31
    assert inbox.claim(command.update_id, owner="second", lease_seconds=30) is True
    bot._full_story_worker(
        command.chat_id,
        {},
        durable_inbox=inbox,
        durable_update_id=command.update_id,
        durable_owner="second",
    )

    assert crashed == [crash_stage]
    assert text_calls == [1]
    assert image_calls == [1, 2, 3, 4]
    assert video_calls == [b"image-1", b"image-2", b"image-3", b"image-4"]
    assert delivered == [
        b"video-image-1",
        b"video-image-2",
        b"video-image-3",
        b"video-image-4",
    ]
    saved = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert saved["pet"]["stats"]["energy"] == 56


def test_full_story_receipt_prevents_duplicate_stats_after_last_story_overwrite(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _register_durable_story_snapshot(monkeypatch, tmp_path)

    class Part:
        stat_impacts = ({"stat": "energy", "amount": -1, "reason": "Долгая дорога."},)

        def __init__(self, number: int) -> None:
            self.number = number

        def model_dump(self) -> dict:
            return {
                "partNumber": self.number,
                "title": f"Часть {self.number}",
                "summary": f"Кратко {self.number}.",
                "storyText": f"Событие {self.number}.",
                "valence": "negative",
                "statImpacts": list(self.stat_impacts),
            }

    text_calls: list[int] = []
    image_calls: list[int] = []
    video_calls: list[bytes] = []
    monkeypatch.setattr(
        telegram_push_service,
        "generate_full_story",
        lambda **_kwargs: (
            text_calls.append(1)
            or SimpleNamespace(
                overall_title="Первая большая история",
                arc_plan={},
                story_direction={},
                parts=tuple(Part(number) for number in range(1, 5)),
                prompt_debug=[],
            )
        ),
    )

    def generate_image(**kwargs):
        number = kwargs["part"]["partNumber"]
        image_calls.append(number)
        return f"image-{number}".encode()

    monkeypatch.setattr(
        telegram_push_service,
        "reserve_full_story_part_image_bytes",
        _reserved(generate_image),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(lambda image: video_calls.append(image) or b"video-" + image),
    )
    monkeypatch.setattr(telegram_push_service, "send_video", lambda *_args, **_kwargs: None)

    now = [1_000.0]
    inbox = SQLiteBotCommandInbox(
        tmp_path / "bot-command-inbox.sqlite3",
        clock=lambda: now[0],
    )
    command = normalize_bot_command_update(
        _durable_command_update(105, "/full_story", chat_id=TEST_TELEGRAM_ID)
    )
    assert command is not None
    inbox.enqueue(command)
    assert inbox.claim(command.update_id, owner="first", lease_seconds=30) is True
    real_checkpoint = inbox.checkpoint_prepared
    crashed = [False]

    def crash_before_final_checkpoint(update_id, *, owner, payload):
        progress = payload.get("progress")
        if not crashed[0] and isinstance(progress, dict) and "preparedResult" in progress:
            crashed[0] = True
            raise KeyboardInterrupt
        return real_checkpoint(update_id, owner=owner, payload=payload)

    monkeypatch.setattr(inbox, "checkpoint_prepared", crash_before_final_checkpoint)
    with pytest.raises(KeyboardInterrupt):
        bot._full_story_worker(
            command.chat_id,
            {},
            durable_inbox=inbox,
            durable_update_id=command.update_id,
            durable_owner="first",
        )

    def overwrite_last_full_story(current):
        next_record = dict(current)
        next_record["lastFullStoryAt"] = "2026-07-07T12:01:00Z"
        next_record["lastFullStory"] = {
            "overallTitle": "Более новая большая история",
            "parts": [],
            "generatedAt": "2026-07-07T12:01:00Z",
            "requestKey": "scheduler:other",
        }
        return next_record

    telegram_push_service._update_record(TEST_TELEGRAM_ID, overwrite_last_full_story)
    now[0] += 31
    assert inbox.claim(command.update_id, owner="second", lease_seconds=30) is True
    bot._full_story_worker(
        command.chat_id,
        {},
        durable_inbox=inbox,
        durable_update_id=command.update_id,
        durable_owner="second",
    )

    saved = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert text_calls == [1]
    assert image_calls == [1, 2, 3, 4]
    assert video_calls == [b"image-1", b"image-2", b"image-3", b"image-4"]
    assert saved["pet"]["stats"]["energy"] == 56
    assert saved["lastFullStory"]["overallTitle"] == "Более новая большая история"
    assert any(
        receipt.get("requestKey") == "telegram-update:105"
        for receipt in saved["botGenerationReceipts"]
    )


def test_http_client_preflight_failure_releases_command_for_safe_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(bot, "get_settings", lambda: _bot_settings())
    monkeypatch.setattr(
        bot.httpx,
        "Client",
        lambda: (_ for _ in ()).throw(OSError("no file descriptors")),
    )
    inbox = SQLiteBotCommandInbox(tmp_path / "bot-command-inbox.sqlite3")
    command = normalize_bot_command_update(_durable_story_update())
    assert command is not None
    inbox.enqueue(command)
    assert inbox.claim(command.update_id, owner="process", lease_seconds=30) is True
    keeper = BotCommandLeaseKeeper(inbox, owner="process", lease_seconds=30)
    keeper.add(command.update_id)

    bot._finish_durable_worker(
        bot._story_worker,
        command.chat_id,
        {},
        command=command,
        inbox=inbox,
        owner="process",
        lease_keeper=keeper,
    )

    assert inbox.status(command.update_id) == "pending"
    assert [item.update_id for item in inbox.list_ready(limit=10)] == [command.update_id]


def test_push_command_generates_for_requesting_user(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_manual_push",
        lambda **kwargs: calls.append(kwargs) or {"sent": True},
    )

    bot.handle_update(httpx.Client(), _push_update())

    assert calls == [{"telegram_id": TEST_TELEGRAM_ID, "include_debug": False}]


def test_push_command_can_be_submitted_without_blocking_polling(monkeypatch) -> None:
    submitted: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )

    bot.handle_update(
        httpx.Client(),
        _push_update(),
        submit_push=lambda chat_id, keyboard: submitted.append((chat_id, keyboard)),
    )

    assert submitted[0][0] == TEST_TELEGRAM_ID
    assert submitted[0][1]["inline_keyboard"][0][0]["web_app"]["url"] == ("https://example.com/app")


def test_story_command_sends_generated_video(monkeypatch) -> None:
    sent: dict[str, object] = {}
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "generate_story_for_telegram_user",
        lambda **kwargs: {
            "story": {
                "title": "След под кроной",
                "storyText": "Олег нашел теплый знак под древним дубом.",
                "statImpact": {
                    "applies": True,
                    "isNegativeOutcome": True,
                    "stat": "energy",
                    "amount": 25,
                    "reason": "Олег поцарапал лапу.",
                },
                "statsDelta": {"energy": -25, "hunger": 0, "happiness": 0},
            },
            "storyVideo": {"bytes": b"mp4", "mimeType": "video/mp4"},
        },
    )

    def fake_send_video(client, chat_id, video, caption, reply_markup):
        sent["method"] = "video"
        sent["chat_id"] = chat_id
        sent["video"] = video
        sent["caption"] = caption
        sent["reply_markup"] = reply_markup

    monkeypatch.setattr(bot, "send_video", fake_send_video)
    monkeypatch.setattr(bot, "send_message", lambda *args, **kwargs: sent.setdefault("text", True))

    bot.handle_update(httpx.Client(), _story_update())

    assert sent["method"] == "video"
    assert sent["chat_id"] == TEST_TELEGRAM_ID
    assert sent["video"] == b"mp4"
    assert sent["caption"] == (
        f"След под кроной\n\nОлег нашел теплый знак под древним дубом.\n\n{STORY_IMPACT_TEXT}"
    )
    assert "text" not in sent


def test_send_photo_uses_detected_jpeg_mime(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        telegram_client,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )

    class FakeClient:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return SimpleNamespace(is_success=True)

    telegram_client.send_photo(
        FakeClient(),
        123,
        b"\xff\xd8\xff\xe0jpeg-bytes",
        "caption",
        {"inline_keyboard": []},
    )

    assert captured["files"]["photo"] == (
        "story.jpg",
        b"\xff\xd8\xff\xe0jpeg-bytes",
        "image/jpeg",
    )


def test_send_video_uses_mp4_and_streaming(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        telegram_client,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )

    class FakeClient:
        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return SimpleNamespace(is_success=True)

    telegram_client.send_video(
        FakeClient(),
        123,
        b"mp4-bytes",
        "caption",
        {"inline_keyboard": []},
    )

    assert captured["url"].endswith("/sendVideo")
    assert captured["files"]["video"] == (
        "story.mp4",
        b"mp4-bytes",
        "video/mp4",
    )
    assert captured["data"]["supports_streaming"] == "true"


def test_story_command_can_be_submitted_without_blocking_polling(monkeypatch) -> None:
    submitted: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )

    bot.handle_update(
        httpx.Client(),
        _story_update(),
        submit_story=lambda chat_id, keyboard: submitted.append((chat_id, keyboard)),
    )

    assert submitted[0][0] == TEST_TELEGRAM_ID
    assert submitted[0][1]["inline_keyboard"][0][0]["web_app"]["url"] == ("https://example.com/app")


def test_story_command_reports_bounded_queue_rejection(monkeypatch) -> None:
    sent: dict[str, object] = {}
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda _client, chat_id, text, _keyboard: sent.update(chat_id=chat_id, text=text),
    )

    bot.handle_update(
        httpx.Client(),
        _story_update(),
        submit_story=lambda _chat_id, _keyboard: False,
    )

    assert sent == {"chat_id": TEST_TELEGRAM_ID, "text": bot.BOT_COMMAND_BUSY_MESSAGE}


def test_api_generation_quota_blocks_bot_before_story_callback(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        bot_token="bot-token",
        webapp_url="https://example.com/app",
        enable_in_memory_rate_limit=True,
        rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
        generation_rate_limit_per_day=1,
    )
    monkeypatch.setattr(tma, "get_settings", lambda: settings)
    monkeypatch.setattr(bot, "get_settings", lambda: settings)
    sent_messages: list[str] = []
    generation_callbacks: list[int] = []
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda _client, _chat_id, text, _keyboard: sent_messages.append(text),
    )
    user = TelegramUserContext(
        telegram_id=TEST_TELEGRAM_ID,
        auth_date=datetime.now(UTC),
    )
    tma.check_rate_limit("generation", user)

    bot.handle_update(
        httpx.Client(),
        _story_update(),
        submit_story=lambda chat_id, _keyboard: generation_callbacks.append(chat_id),
    )

    assert generation_callbacks == []
    assert len(sent_messages) == 1
    assert sent_messages[0].startswith("Лимит генераций исчерпан.")


def test_full_story_consumes_one_generation_action(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        bot_token="bot-token",
        webapp_url="https://example.com/app",
        enable_in_memory_rate_limit=True,
        rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
        generation_rate_limit_per_day=2,
    )
    monkeypatch.setattr(bot, "get_settings", lambda: settings)
    submitted: list[str] = []
    sent_messages: list[str] = []
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda _client, _chat_id, text, _keyboard: sent_messages.append(text),
    )

    bot.handle_update(
        httpx.Client(),
        _story_update(),
        submit_story=lambda _chat_id, _keyboard: submitted.append("story"),
    )
    bot.handle_update(
        httpx.Client(),
        _full_story_update(),
        submit_full_story=lambda _chat_id, _keyboard: submitted.append("full_story"),
    )
    bot.handle_update(
        httpx.Client(),
        _story_update(),
        submit_story=lambda _chat_id, _keyboard: submitted.append("unexpected"),
    )

    assert submitted == ["story", "full_story"]
    assert len(sent_messages) == 1
    assert sent_messages[0].startswith("Лимит генераций исчерпан.")


def test_push_command_does_not_consume_generation_quota(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        bot_token="bot-token",
        webapp_url="https://example.com/app",
        enable_in_memory_rate_limit=True,
        rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
        generation_rate_limit_per_day=1,
    )
    monkeypatch.setattr(bot, "get_settings", lambda: settings)
    submitted: list[str] = []

    bot.handle_update(
        httpx.Client(),
        _push_update(),
        submit_push=lambda _chat_id, _keyboard: submitted.append("push"),
    )
    bot.handle_update(
        httpx.Client(),
        _story_update(),
        submit_story=lambda _chat_id, _keyboard: submitted.append("story"),
    )

    assert submitted == ["push", "story"]


def test_bot_commits_quota_before_generation_callback(monkeypatch, tmp_path) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    settings = SimpleNamespace(
        bot_token="bot-token",
        webapp_url="https://example.com/app",
        enable_in_memory_rate_limit=True,
        rate_limit_store_path=str(store_path),
        generation_rate_limit_per_day=1,
    )
    monkeypatch.setattr(bot, "get_settings", lambda: settings)
    event_counts_seen_by_callback: list[int] = []

    def generation_callback(_chat_id, _keyboard):
        with sqlite3.connect(store_path) as connection:
            count = connection.execute(
                """
                SELECT COUNT(*) FROM rate_limit_events
                WHERE bucket = 'generation' AND user_id = ?
                """,
                (TEST_TELEGRAM_ID,),
            ).fetchone()[0]
        event_counts_seen_by_callback.append(count)

    bot.handle_update(
        httpx.Client(),
        _story_update(),
        submit_story=generation_callback,
    )

    assert event_counts_seen_by_callback == [1]


def test_rejected_bot_queue_submission_refunds_quota(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        bot_token="bot-token",
        webapp_url="https://example.com/app",
        enable_in_memory_rate_limit=True,
        rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
        generation_rate_limit_per_day=1,
    )
    monkeypatch.setattr(bot, "get_settings", lambda: settings)
    monkeypatch.setattr(bot, "send_message", lambda *_args, **_kwargs: None)
    accepted: list[int] = []

    bot.handle_update(
        httpx.Client(),
        _story_update(),
        submit_story=lambda _chat_id, _keyboard: False,
    )
    bot.handle_update(
        httpx.Client(),
        _story_update(),
        submit_story=lambda chat_id, _keyboard: accepted.append(chat_id),
    )

    assert accepted == [TEST_TELEGRAM_ID]


def test_replayed_telegram_update_consumes_exactly_one_generation_action(
    monkeypatch,
    tmp_path,
) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    settings = _bot_settings(
        enable_in_memory_rate_limit=True,
        rate_limit_store_path=str(store_path),
        generation_rate_limit_per_day=1,
    )
    monkeypatch.setattr(bot, "get_settings", lambda: settings)
    submitted: list[int] = []
    update = _durable_story_update(77)

    bot.handle_update(
        httpx.Client(),
        update,
        submit_story=lambda chat_id, _keyboard: submitted.append(chat_id),
    )
    bot.handle_update(
        httpx.Client(),
        update,
        submit_story=lambda chat_id, _keyboard: submitted.append(chat_id),
    )

    with sqlite3.connect(store_path) as connection:
        rows = connection.execute(
            """
            SELECT request_key FROM rate_limit_events
            WHERE bucket = 'generation' AND user_id = ?
            """,
            (TEST_TELEGRAM_ID,),
        ).fetchall()
    assert submitted == [TEST_TELEGRAM_ID, TEST_TELEGRAM_ID]
    assert rows == [("telegram-update:77",)]


def test_queue_rejection_on_replay_does_not_refund_original_quota_event(
    monkeypatch,
    tmp_path,
) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    settings = _bot_settings(
        enable_in_memory_rate_limit=True,
        rate_limit_store_path=str(store_path),
        generation_rate_limit_per_day=1,
    )
    monkeypatch.setattr(bot, "get_settings", lambda: settings)
    monkeypatch.setattr(bot, "send_message", lambda *_args, **_kwargs: None)
    accepted: list[int] = []
    replayed_update = _durable_story_update(77)

    bot.handle_update(
        httpx.Client(),
        replayed_update,
        submit_story=lambda chat_id, _keyboard: accepted.append(chat_id),
    )
    bot.handle_update(
        httpx.Client(),
        replayed_update,
        submit_story=lambda _chat_id, _keyboard: False,
    )
    bot.handle_update(
        httpx.Client(),
        _durable_story_update(78),
        submit_story=lambda chat_id, _keyboard: accepted.append(chat_id),
    )

    with sqlite3.connect(store_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM rate_limit_events WHERE bucket = 'generation'"
        ).fetchone()[0]
    assert accepted == [TEST_TELEGRAM_ID]
    assert count == 1


def test_bot_error_log_redacts_token(monkeypatch, caplog) -> None:
    token = "123456:SUPERSECRET"
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token=token, webapp_url="https://example.com/app"),
    )
    request = httpx.Request("GET", f"https://api.telegram.org/bot{token}/getUpdates")
    response = httpx.Response(500, request=request)
    error = httpx.HTTPStatusError(
        f"request failed for {request.url}",
        request=request,
        response=response,
    )

    with caplog.at_level(logging.ERROR):
        bot._log_bot_error("poll failed", error)

    assert token not in caplog.text
    assert "<redacted>" in caplog.text


def test_full_story_command_can_be_submitted_without_blocking_polling(monkeypatch) -> None:
    submitted: list[tuple[int, dict]] = []
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )

    bot.handle_update(
        httpx.Client(),
        _full_story_update(),
        submit_full_story=lambda chat_id, keyboard: submitted.append((chat_id, keyboard)),
    )

    assert submitted[0][0] == TEST_TELEGRAM_ID


def test_full_story_command_generates_for_requesting_user(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_full_story_for_telegram_user",
        lambda client, **kwargs: calls.append(kwargs) or {"generated": True},
    )

    bot.handle_update(httpx.Client(), _full_story_update())

    assert calls[0]["telegram_id"] == TEST_TELEGRAM_ID


def test_story_command_falls_back_to_message_without_video(monkeypatch) -> None:
    sent: dict[str, object] = {}
    monkeypatch.setattr(
        bot,
        "get_settings",
        lambda: SimpleNamespace(bot_token="bot-token", webapp_url="https://example.com/app"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "generate_story_for_telegram_user",
        lambda **kwargs: {
            "story": {
                "title": "След под кроной",
                "storyText": "Олег нашел теплый знак под древним дубом.",
                "statImpact": {
                    "applies": False,
                    "isNegativeOutcome": False,
                    "stat": "none",
                    "amount": 0,
                    "reason": "Последствий нет.",
                },
                "statsDelta": {"energy": 0, "hunger": 0, "happiness": 0},
            },
            "storyVideo": None,
        },
    )
    monkeypatch.setattr(bot, "send_video", lambda *args, **kwargs: sent.setdefault("video", True))

    def fake_send_message(client, chat_id, text, reply_markup):
        sent["method"] = "message"
        sent["chat_id"] = chat_id
        sent["text"] = text
        sent["reply_markup"] = reply_markup

    monkeypatch.setattr(bot, "send_message", fake_send_message)

    bot.handle_update(httpx.Client(), _story_update())

    assert sent["method"] == "message"
    assert sent["chat_id"] == TEST_TELEGRAM_ID
    assert sent["text"] == (
        "След под кроной\n\n"
        "Олег нашел теплый знак под древним дубом.\n\n"
        "Влияние на параметры:\n"
        "без изменений"
    )
    assert "video" not in sent


def test_story_caption_preserves_stat_debug_tail() -> None:
    caption = format_story_caption(
        {
            "title": "Длинная история",
            "storyText": "Очень длинный текст. " * 200,
            "statImpact": {
                "applies": True,
                "isNegativeOutcome": True,
                "stat": "hunger",
                "amount": 25,
                "reason": "Питомец потерял еду.",
            },
            "statsDelta": {"energy": 0, "hunger": -25, "happiness": 0},
        }
    )

    assert len(caption) <= TELEGRAM_PHOTO_CAPTION_LIMIT
    assert caption.endswith("Влияние на параметры:\nголод: минус 25")


def test_full_story_message_formats_all_four_parts() -> None:
    message = format_full_story_message(
        {
            "overallTitle": "Большой путь",
            "parts": [
                {
                    "title": f"Этап {index}",
                    "storyText": f"Событие {index}.",
                    "statsDelta": {"energy": -index, "hunger": 0, "happiness": index},
                }
                for index in range(1, 5)
            ],
        }
    )

    assert "Большой путь" in message
    assert "Часть 4. Этап 4" in message
    assert "здоровье: минус 4" in message
    assert "настроение: плюс 4" in message


def test_story_caption_shows_recovery_as_plus() -> None:
    caption = format_story_caption(
        {
            "title": "Теплый привал",
            "storyText": "Питомец отдохнул и восстановил силы.",
            "statsDelta": {"energy": 18, "hunger": 0, "happiness": 7},
        }
    )

    assert caption.endswith("Влияние на параметры:\nздоровье: плюс 18\nнастроение: плюс 7")
