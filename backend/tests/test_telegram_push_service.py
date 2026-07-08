from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.bot import TelegramAPIError
from app.schemas import LocalPetPushSnapshotRequest, LocalProactiveResponse
from app.services import telegram_push_service
from app.services.telegram_auth_service import TelegramUserContext

DEBUG_TARGET_TELEGRAM_ID = telegram_push_service.DEBUG_PUSH_TARGET_TELEGRAM_ID


def _user_with_id(telegram_id: int, username: str = "serge") -> TelegramUserContext:
    return TelegramUserContext(
        telegram_id=telegram_id,
        username=username,
        first_name="Serge",
        language_code="ru",
        auth_date=datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
    )


def _user() -> TelegramUserContext:
    return _user_with_id(DEBUG_TARGET_TELEGRAM_ID)


def _snapshot_payload() -> LocalPetPushSnapshotRequest:
    return LocalPetPushSnapshotRequest(
        petId="pet-1",
        createdAt="2026-07-06T12:00:00Z",
        updatedAt="2026-07-07T12:00:00Z",
        lastStatsTickAt="2026-07-07T12:00:00Z",
        timezone="Europe/Moscow",
        pet={
            "name": "Громм",
            "description": "гигантский земляной великан",
            "stage": "adult",
            "mood": "idle",
            "stats": {
                "hunger": 80,
                "happiness": 70,
                "energy": 60,
                "cleanliness": 90,
            },
        },
        memoryContext={
            "relevantMemories": [
                {
                    "id": "m1",
                    "kind": "preference",
                    "text": "Пользователь любит короткие сообщения.",
                }
            ]
        },
    )


def test_manual_push_uses_registered_telegram_chat(monkeypatch, tmp_path) -> None:
    captured = {}
    settings = SimpleNamespace(
        bot_token="bot-token",
        webapp_url="https://example.com/app",
        telegram_push_store_path=str(tmp_path / "push.json"),
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        telegram_push_service,
        "generate_push_pet_message",
        lambda payload: LocalProactiveResponse(reply=f"Привет, {payload.pet.name}!"),
    )

    def fake_send_message(client, chat_id, text, reply_markup):
        captured["chat_id"] = chat_id
        captured["text"] = text
        captured["reply_markup"] = reply_markup

    monkeypatch.setattr(telegram_push_service, "send_message", fake_send_message)

    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    result = telegram_push_service.send_manual_push(reason="debug", include_debug=False)

    assert result["sent"] is True
    assert result["manual"] is True
    assert result["telegramId"] == DEBUG_TARGET_TELEGRAM_ID
    assert captured["chat_id"] == DEBUG_TARGET_TELEGRAM_ID
    assert captured["text"] == "Привет, Громм!"
    assert captured["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == (
        "https://example.com/app"
    )
    assert telegram_push_service.push_status()["latest"]["lastDebugPushAt"] is not None


def test_chat_start_marks_snapshot_reachable(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)

    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    assert telegram_push_service.push_status()["latest"]["chatReachable"] is False

    telegram_push_service.mark_chat_started(
        chat_id=DEBUG_TARGET_TELEGRAM_ID,
        username="serge-updated",
        first_name="Serge",
        language_code="ru",
    )

    latest = telegram_push_service.push_status()["latest"]
    assert latest["chatReachable"] is True
    assert latest["username"] == "serge-updated"
    assert latest["chatStartedAt"] is not None
    assert latest["lastChatSeenAt"] is not None

    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    assert telegram_push_service.push_status()["latest"]["chatReachable"] is True


def test_chat_start_without_snapshot_is_not_push_target(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)

    telegram_push_service.mark_chat_started(chat_id=42)

    assert telegram_push_service.push_status()["count"] == 0
    with pytest.raises(telegram_push_service.TelegramPushError) as exc_info:
        telegram_push_service.send_manual_push(telegram_id=42)
    assert exc_info.value.code == "PUSH_SNAPSHOT_NOT_FOUND"


def test_manual_push_to_reachable_skips_unstarted_chats(monkeypatch, tmp_path) -> None:
    captured_chat_ids: list[int] = []
    settings = SimpleNamespace(
        bot_token="bot-token",
        webapp_url="https://example.com/app",
        telegram_push_store_path=str(tmp_path / "push.json"),
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        telegram_push_service,
        "generate_push_pet_message",
        lambda payload: LocalProactiveResponse(reply=f"Привет, {payload.pet.name}!"),
    )

    def fake_send_message(client, chat_id, text, reply_markup):
        captured_chat_ids.append(chat_id)

    monkeypatch.setattr(telegram_push_service, "send_message", fake_send_message)

    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    telegram_push_service.mark_chat_started(chat_id=DEBUG_TARGET_TELEGRAM_ID)
    telegram_push_service.register_push_snapshot(
        _user_with_id(99, username="unstarted"),
        _snapshot_payload(),
    )
    telegram_push_service.register_push_snapshot(
        _user_with_id(380566596, username="dendimitrov"),
        _snapshot_payload(),
    )
    telegram_push_service.mark_chat_started(chat_id=380566596)

    result = telegram_push_service.send_manual_push_to_reachable()

    assert result["sentCount"] == 1
    assert result["failedCount"] == 0
    assert result["skippedCount"] == 2
    assert result["targetCount"] == 1
    assert captured_chat_ids == [DEBUG_TARGET_TELEGRAM_ID]


def test_manual_push_rejects_non_target_user(monkeypatch, tmp_path) -> None:
    captured_chat_ids: list[int] = []
    settings = SimpleNamespace(
        bot_token="bot-token",
        webapp_url="https://example.com/app",
        telegram_push_store_path=str(tmp_path / "push.json"),
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        telegram_push_service,
        "generate_push_pet_message",
        lambda payload: LocalProactiveResponse(reply=f"Привет, {payload.pet.name}!"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_message",
        lambda client, chat_id, text, reply_markup: captured_chat_ids.append(chat_id),
    )

    telegram_push_service.register_push_snapshot(
        _user_with_id(380566596, username="dendimitrov"),
        _snapshot_payload(),
    )
    telegram_push_service.mark_chat_started(chat_id=380566596)

    with pytest.raises(telegram_push_service.TelegramPushError) as exc_info:
        telegram_push_service.send_manual_push(telegram_id=380566596)

    assert exc_info.value.code == "PUSH_TARGET_RESTRICTED"
    assert str(DEBUG_TARGET_TELEGRAM_ID) in exc_info.value.message
    assert captured_chat_ids == []


def test_current_pet_record_decays_stats_and_recomputes_stage() -> None:
    now = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    record = {
        "createdAt": (now - timedelta(days=8)).isoformat().replace("+00:00", "Z"),
        "lastStatsTickAt": (now - timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        "pet": {
            "name": "Громм",
            "description": "гигантский земляной великан",
            "stage": "baby",
            "mood": "idle",
            "stats": {
                "hunger": 100,
                "happiness": 80,
                "energy": 50,
                "cleanliness": 90,
            },
        },
    }

    pet = telegram_push_service._current_pet_record(record, now)

    assert pet["stage"] == "adult"
    assert pet["stats"] == {
        "hunger": 0,
        "happiness": 0,
        "energy": 0,
        "cleanliness": 90,
    }


def test_telegram_send_error_is_sanitized(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        bot_token="secret-token",
        webapp_url="https://example.com/app",
        telegram_push_store_path=str(tmp_path / "push.json"),
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        telegram_push_service,
        "generate_push_pet_message",
        lambda payload: LocalProactiveResponse(reply="Привет!"),
    )

    def fake_send_message(client, chat_id, text, reply_markup):
        request = httpx.Request(
            "POST",
            "https://api.telegram.org/botsecret-token/sendMessage",
        )
        response = httpx.Response(
            400,
            request=request,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: chat not found",
            },
        )
        raise TelegramAPIError("sendMessage", response)

    monkeypatch.setattr(telegram_push_service, "send_message", fake_send_message)

    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())

    with pytest.raises(telegram_push_service.TelegramPushError) as exc_info:
        telegram_push_service.send_manual_push(telegram_id=DEBUG_TARGET_TELEGRAM_ID)

    assert exc_info.value.code == "TELEGRAM_CHAT_NOT_FOUND"
    assert "/start" in exc_info.value.message
    assert "secret-token" not in exc_info.value.message
    assert "api.telegram.org" not in exc_info.value.message
    latest = telegram_push_service.push_status()["latest"]
    assert latest["lastPushErrorCode"] == "TELEGRAM_CHAT_NOT_FOUND"
    assert latest["lastPushAttemptAt"] is not None
    assert latest["chatReachable"] is False


def test_failed_daily_attempt_delays_next_due_push(monkeypatch, tmp_path) -> None:
    now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    settings = SimpleNamespace(
        bot_token="secret-token",
        webapp_url="https://example.com/app",
        telegram_push_store_path=str(tmp_path / "push.json"),
        telegram_daily_push_enabled=True,
        telegram_daily_push_min_interval_hours=24,
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: now)
    monkeypatch.setattr(
        telegram_push_service,
        "generate_push_pet_message",
        lambda payload: LocalProactiveResponse(reply="Привет!"),
    )

    def fake_send_message(client, chat_id, text, reply_markup):
        request = httpx.Request(
            "POST",
            "https://api.telegram.org/botsecret-token/sendMessage",
        )
        response = httpx.Response(
            400,
            request=request,
            json={
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: chat not found",
            },
        )
        raise TelegramAPIError("sendMessage", response)

    monkeypatch.setattr(telegram_push_service, "send_message", fake_send_message)

    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    telegram_push_service.mark_chat_started(chat_id=DEBUG_TARGET_TELEGRAM_ID)
    store = telegram_push_service._read_store()
    record = store["records"][str(DEBUG_TARGET_TELEGRAM_ID)]
    record["registeredAt"] = (now - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
    telegram_push_service._save_record(record)

    assert telegram_push_service.send_due_pushes() == []

    latest = telegram_push_service.push_status()["latest"]
    assert latest["lastPushErrorCode"] == "TELEGRAM_CHAT_NOT_FOUND"
    assert latest["lastPushAttemptAt"] == now.isoformat().replace("+00:00", "Z")
    assert telegram_push_service._due_records(now) == []


def test_due_push_can_use_seconds_interval(monkeypatch, tmp_path) -> None:
    now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    settings = SimpleNamespace(
        telegram_push_store_path=str(tmp_path / "push.json"),
        telegram_daily_push_min_interval_hours=24,
        telegram_daily_push_min_interval_seconds=120,
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)

    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    telegram_push_service.mark_chat_started(chat_id=DEBUG_TARGET_TELEGRAM_ID)
    telegram_push_service.register_push_snapshot(
        _user_with_id(380566596, username="dendimitrov"),
        _snapshot_payload(),
    )
    telegram_push_service.mark_chat_started(chat_id=380566596)
    store = telegram_push_service._read_store()
    record = store["records"][str(DEBUG_TARGET_TELEGRAM_ID)]
    record["registeredAt"] = (now - timedelta(seconds=119)).isoformat().replace(
        "+00:00",
        "Z",
    )
    telegram_push_service._save_record(record)
    non_target = store["records"]["380566596"]
    non_target["registeredAt"] = (now - timedelta(seconds=120)).isoformat().replace(
        "+00:00",
        "Z",
    )
    telegram_push_service._save_record(non_target)

    assert telegram_push_service._due_records(now) == []

    record["registeredAt"] = (now - timedelta(seconds=120)).isoformat().replace(
        "+00:00",
        "Z",
    )
    telegram_push_service._save_record(record)

    assert len(telegram_push_service._due_records(now)) == 1
