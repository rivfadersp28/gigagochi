from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace

from app.services import ops_alert_service


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "ops_alerts_enabled": True,
        "bot_token": "fake-token",
        "ops_alert_dedup_seconds": 300,
        "ops_alert_telegram_ids": set(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _reset_state(monkeypatch) -> None:
    monkeypatch.setattr(ops_alert_service, "_last_sent", {})
    monkeypatch.setattr(ops_alert_service, "_pending_slots", ops_alert_service.BoundedSemaphore(8))


def test_notify_ops_deduplicates_without_retaining_raw_key(monkeypatch) -> None:
    _reset_state(monkeypatch)
    submitted: list[tuple[object, str]] = []

    class Executor:
        def submit(self, callback, text):
            submitted.append((callback, text))
            return Future()

    monkeypatch.setattr(ops_alert_service, "get_settings", _settings)
    monkeypatch.setattr(ops_alert_service, "_executor", Executor())
    monkeypatch.setattr(ops_alert_service.time, "monotonic", lambda: 1_000.0)

    ops_alert_service.notify_ops("secret/dynamic/raw-key", "first")
    ops_alert_service.notify_ops("secret/dynamic/raw-key", "second")

    assert [item[1] for item in submitted] == ["first"]
    assert "secret/dynamic/raw-key" not in ops_alert_service._last_sent
    assert len(next(iter(ops_alert_service._last_sent))) == 64


def test_notify_ops_drops_work_when_bounded_queue_is_full(monkeypatch, caplog) -> None:
    _reset_state(monkeypatch)
    submitted: list[str] = []

    class Executor:
        def submit(self, _callback, text):
            submitted.append(text)
            return Future()

    monkeypatch.setattr(ops_alert_service, "get_settings", _settings)
    monkeypatch.setattr(ops_alert_service, "_executor", Executor())
    monkeypatch.setattr(ops_alert_service.time, "monotonic", lambda: 2_000.0)

    for index in range(12):
        ops_alert_service.notify_ops(f"key-{index}", f"message-{index}")

    assert len(submitted) == 8
    assert len(ops_alert_service._last_sent) == 8
    assert "ops_alert_queue_full" in caplog.text


def test_notify_ops_bounds_deduplication_keys(monkeypatch) -> None:
    _reset_state(monkeypatch)

    class Executor:
        def submit(self, callback, text):
            callback(text)
            return Future()

    monkeypatch.setattr(ops_alert_service, "get_settings", _settings)
    monkeypatch.setattr(ops_alert_service, "_executor", Executor())
    monkeypatch.setattr(ops_alert_service, "_send_alert", lambda _text: None)

    for index in range(ops_alert_service._MAX_DEDUP_KEYS + 20):
        monkeypatch.setattr(
            ops_alert_service.time,
            "monotonic",
            lambda current=index: 10_000.0 + current / 1_000,
        )
        ops_alert_service.notify_ops(f"dynamic-key-{index}", "message")

    assert len(ops_alert_service._last_sent) == ops_alert_service._MAX_DEDUP_KEYS


def test_notify_ops_releases_slot_if_executor_is_shutting_down(monkeypatch, caplog) -> None:
    _reset_state(monkeypatch)

    class Executor:
        def submit(self, _callback, _text):
            raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(ops_alert_service, "get_settings", _settings)
    monkeypatch.setattr(ops_alert_service, "_executor", Executor())
    monkeypatch.setattr(ops_alert_service.time, "monotonic", lambda: 3_000.0)

    ops_alert_service.notify_ops("shutdown", "message")

    assert ops_alert_service._pending_slots.acquire(blocking=False)
    assert ops_alert_service._last_sent == {}
    assert "ops_alert_executor_unavailable" in caplog.text


def test_completed_alert_releases_bounded_queue_slot(monkeypatch) -> None:
    _reset_state(monkeypatch)
    monkeypatch.setattr(ops_alert_service, "_send_alert", lambda _text: None)
    assert ops_alert_service._pending_slots.acquire(blocking=False)

    ops_alert_service._send_alert_and_release("message")

    acquired = 0
    while ops_alert_service._pending_slots.acquire(blocking=False):
        acquired += 1
    assert acquired == 8
