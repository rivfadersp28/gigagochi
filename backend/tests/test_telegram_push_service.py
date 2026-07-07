from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.schemas import LocalPetPushSnapshotRequest, LocalProactiveResponse
from app.services import telegram_push_service
from app.services.telegram_auth_service import TelegramUserContext


def _user() -> TelegramUserContext:
    return TelegramUserContext(
        telegram_id=42,
        username="serge",
        first_name="Serge",
        language_code="ru",
        auth_date=datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
    )


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
    assert result["telegramId"] == 42
    assert captured["chat_id"] == 42
    assert captured["text"] == "Привет, Громм!"
    assert captured["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == (
        "https://example.com/app"
    )
    assert telegram_push_service.push_status()["latest"]["lastDebugPushAt"] is not None


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
