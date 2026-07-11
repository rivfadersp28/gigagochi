from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.bot import TelegramAPIError
from app.schemas import LocalPetPushSnapshotRequest, LocalProactiveResponse
from app.services import telegram_push_service
from app.services.telegram_auth_service import TelegramUserContext

TEST_TELEGRAM_ID = 62943754


def _user_with_id(telegram_id: int, username: str = "serge") -> TelegramUserContext:
    return TelegramUserContext(
        telegram_id=telegram_id,
        username=username,
        first_name="Serge",
        language_code="ru",
        auth_date=datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
    )


def _user() -> TelegramUserContext:
    return _user_with_id(TEST_TELEGRAM_ID)


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
    assert result["telegramId"] == TEST_TELEGRAM_ID
    assert captured["chat_id"] == TEST_TELEGRAM_ID
    assert captured["text"] == "Привет, Громм!"
    assert captured["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"] == (
        "https://example.com/app"
    )
    assert telegram_push_service.push_status()["latest"]["lastDebugPushAt"] is not None


def test_chat_start_marks_snapshot_reachable(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        telegram_push_service,
        "_now",
        lambda: datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
    )

    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    assert telegram_push_service.push_status()["latest"]["chatReachable"] is False

    telegram_push_service.mark_chat_started(
        chat_id=TEST_TELEGRAM_ID,
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
    telegram_push_service.mark_chat_started(chat_id=TEST_TELEGRAM_ID)
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

    assert result["sentCount"] == 2
    assert result["failedCount"] == 0
    assert result["skippedCount"] == 1
    assert result["targetCount"] == 2
    assert set(captured_chat_ids) == {TEST_TELEGRAM_ID, 380566596}


def test_manual_push_allows_any_registered_reachable_user(monkeypatch, tmp_path) -> None:
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

    result = telegram_push_service.send_manual_push(telegram_id=380566596)

    assert result["sent"] is True
    assert captured_chat_ids == [380566596]


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
            },
        },
    }

    pet = telegram_push_service._current_pet_record(record, now)

    assert pet["stage"] == "adult"
    assert pet["stats"] == {
        "hunger": 0,
        "happiness": 0,
        "energy": 0,
    }


def test_record_dies_only_after_more_than_24_hours_at_zero() -> None:
    zero_since = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    record = {
        "deathTrackingEnabled": True,
        "lastStatsTickAt": zero_since.isoformat().replace("+00:00", "Z"),
        "lastStatTickAt": {
            "hunger": zero_since.isoformat().replace("+00:00", "Z"),
            "happiness": zero_since.isoformat().replace("+00:00", "Z"),
            "energy": zero_since.isoformat().replace("+00:00", "Z"),
        },
        "zeroStatSinceAt": {"hunger": zero_since.isoformat().replace("+00:00", "Z")},
        "pet": {
            "stats": {"hunger": 0, "happiness": 80, "energy": 80},
        },
    }

    threshold = zero_since + timedelta(hours=24)
    assert telegram_push_service._record_death_at(record, threshold) is None
    assert telegram_push_service._record_death_at(
        record,
        threshold + timedelta(microseconds=1),
    ) == threshold


def test_background_story_is_saved_and_preserved_on_next_snapshot(
    monkeypatch,
    tmp_path,
) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        telegram_push_service,
        "_now",
        lambda: datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
    )
    lite_overlay_patch = {
        "facts": [
            {
                "sphere": "world",
                "kind": "world_fact",
                "text": "У каменного порога Громма теперь видны меловые следы тени.",
                "pathHint": "lite_overlay.spheres.world",
                "source": "background_story_aftermath",
                "createdAt": "2026-07-08T07:40:00Z",
            }
        ],
        "spheres": {
            "world": {
                "facts": [
                    {
                        "sphere": "world",
                        "kind": "world_fact",
                        "text": ("У каменного порога Громма теперь видны меловые следы тени."),
                        "pathHint": "lite_overlay.spheres.world",
                        "source": "background_story_aftermath",
                        "createdAt": "2026-07-08T07:40:00Z",
                    }
                ]
            }
        },
    }

    monkeypatch.setattr(
        telegram_push_service,
        "generate_background_story",
        lambda **kwargs: SimpleNamespace(
            title="Нападение меловой тени",
            summary=("Меловая тень попыталась стереть следы Громма."),
            story_text=("На Громма напала меловая тень у каменного порога."),
            event_type="attack",
            valence="negative",
            tags=("тень",),
            rag_text=("На Громма напала меловая тень у каменного порога."),
            story_library_patch=None,
            lite_overlay_patch=lite_overlay_patch,
            recent_story_event={
                "summary": "На Громма напала меловая тень у каменного порога.",
                "compactText": "Меловая тень напала на Громма у каменного порога.",
                "eventType": "attack",
                "valence": "negative",
                "participants": ["Громм", "меловая тень"],
                "actions": ["нападение"],
                "objects": [],
                "location": "каменный порог",
                "outcome": "Громм устоял.",
                "canonicalFacts": ["меловая тень напала на Громма"],
                "statusChanges": [],
                "createdAt": "2026-07-08T07:40:00Z",
                "source": "background_story",
            },
            stat_impacts=(
                {
                    "stat": "energy",
                    "amount": -15,
                    "reason": "Громм получил урон от меловой тени.",
                },
                {
                    "stat": "happiness",
                    "amount": -20,
                    "reason": "Громм расстроился после нападения.",
                },
            ),
            stat_impact=None,
            prompt_debug=[],
        ),
    )
    image_calls: list[dict[str, object]] = []

    def fake_story_image_bytes(**kwargs):
        image_calls.append(kwargs)
        return b"story-png"

    monkeypatch.setattr(
        telegram_push_service,
        "generate_background_story_image_bytes",
        fake_story_image_bytes,
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_persist_background_story_image",
        lambda *_args, **_kwargs: "/static/generated/pet-1/background-story.png?v=1",
    )

    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    result = telegram_push_service.generate_story_for_telegram_user(
        telegram_id=TEST_TELEGRAM_ID,
        include_debug=False,
    )

    assert result["storyLibraryPatch"] is None
    assert result["liteOverlayPatch"] is not None
    assert result["storyImage"] == {"bytes": b"story-png", "mimeType": "image/png"}
    assert result["storyImageError"] is None
    assert result["statsPatch"]["stats"] == {"energy": 45, "happiness": 50}
    assert result["story"]["statsDelta"] == {"hunger": 0, "happiness": -20, "energy": -15}
    assert set(result["statsPatch"]["lastStatTickAt"]) == {"energy", "happiness"}
    assert result["story"]["statImpacts"] == [
        {
            "stat": "energy",
            "amount": -15,
            "reason": "Громм получил урон от меловой тени.",
        },
        {
            "stat": "happiness",
            "amount": -20,
            "reason": "Громм расстроился после нападения.",
        },
    ]
    assert image_calls[0]["pet"].name == "Громм"
    assert image_calls[0]["story"].title == "Нападение меловой тени"
    store = json.loads((tmp_path / "push.json").read_text(encoding="utf-8"))
    events = store["records"][str(TEST_TELEGRAM_ID)]["recentStoryEvents"]
    assert events[0]["summary"] == "На Громма напала меловая тень у каменного порога."
    assert events[0]["storyText"] == "На Громма напала меловая тень у каменного порога."
    assert events[0]["imageUrl"] == "/static/generated/pet-1/background-story.png?v=1"
    assert events[0]["canonicalFacts"] == ["меловая тень напала на Громма"]
    assert events[0]["statImpacts"][1]["stat"] == "happiness"
    assert store["records"][str(TEST_TELEGRAM_ID)]["lastStory"]["statsDelta"] == {
        "hunger": 0,
        "happiness": -20,
        "energy": -15,
    }
    assert (
        store["records"][str(TEST_TELEGRAM_ID)]["lastStory"]["imageUrl"]
        == "/static/generated/pet-1/background-story.png?v=1"
    )
    assert store["records"][str(TEST_TELEGRAM_ID)]["lastStoryImageStatus"] == "generated"
    assert store["records"][str(TEST_TELEGRAM_ID)]["lastStoryImageError"] is None
    assert store["records"][str(TEST_TELEGRAM_ID)]["lastStoryImageErrorAt"] is None
    assert result["recentStoryEvent"]["eventType"] == "attack"

    response = telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())

    assert response.storyLibraryPatch is None
    assert response.liteOverlayPatch is not None
    assert response.liteOverlayPatch["facts"][0]["source"] == "background_story_aftermath"
    assert response.recentStoryEventsPatch is not None
    assert response.recentStoryEventsPatch["events"][0]["eventType"] == "attack"
    assert (
        response.recentStoryEventsPatch["events"][0]["imageUrl"]
        == "/static/generated/pet-1/background-story.png?v=1"
    )
    store = json.loads((tmp_path / "push.json").read_text(encoding="utf-8"))
    assert (
        store["records"][str(TEST_TELEGRAM_ID)]["recentStoryEvents"][0]["summary"]
        == "На Громма напала меловая тень у каменного порога."
    )


def test_background_story_image_error_is_saved(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        telegram_push_service,
        "_now",
        lambda: datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "generate_background_story",
        lambda **_kwargs: SimpleNamespace(
            title="Шёпот под мельницей",
            summary="Громм услышал шёпот.",
            story_text="Под мельницей раздался шёпот.",
            event_type="mystery",
            valence="neutral",
            tags=("мельница",),
            rag_text="Под мельницей раздался шёпот.",
            story_library_patch=None,
            lite_overlay_patch=None,
            recent_story_event=None,
            stat_impacts=(),
            stat_impact=None,
            prompt_debug=[],
        ),
    )

    def fail_story_image(**_kwargs):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(
        telegram_push_service,
        "generate_background_story_image_bytes",
        fail_story_image,
    )

    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    result = telegram_push_service.generate_story_for_telegram_user(
        telegram_id=TEST_TELEGRAM_ID,
        include_debug=False,
    )

    assert result["storyImage"] is None
    assert result["storyImageError"] == "ConnectTimeout"
    latest = telegram_push_service.push_status()["latest"]
    assert latest["lastStoryImageStatus"] == "failed"
    assert latest["lastStoryImageError"] == "ConnectTimeout"
    assert latest["lastStoryImageErrorAt"] == "2026-07-07T12:00:00Z"


def test_background_story_can_restore_stats() -> None:
    now = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    record = {
        "updatedAt": now.isoformat().replace("+00:00", "Z"),
        "lastStatsTickAt": now.isoformat().replace("+00:00", "Z"),
        "lastStatTickAt": {
            key: now.isoformat().replace("+00:00", "Z") for key in ("hunger", "happiness", "energy")
        },
        "pet": {
            "description": "земляной великан",
            "stage": "adult",
            "mood": "idle",
            "stats": {"hunger": 80, "happiness": 70, "energy": 50},
        },
    }

    pet, stats_patch, _ticks, stats_delta = telegram_push_service._apply_story_stat_impact(
        record,
        [
            {"stat": "energy", "amount": 20, "reason": "Громм отдохнул."},
            {"stat": "happiness", "amount": 10, "reason": "Громм обрадовался."},
        ],
        now=now,
    )

    assert pet["stats"]["energy"] == 70
    assert pet["stats"]["happiness"] == 80
    assert stats_patch["stats"] == {"energy": 70, "happiness": 80}
    assert stats_delta == {"hunger": 0, "happiness": 10, "energy": 20}


def test_recent_story_events_fallback_uses_last_story_for_anti_repeat() -> None:
    events = telegram_push_service._record_recent_story_events(
        {
            "lastStory": {
                "title": "Падение у миски",
                "summary": "Громм уже споткнулся у миски.",
                "storyText": "Громм задел миску лапой и упал на мокрый пол.",
                "imageUrl": "/static/generated/pet-1/story.png?v=1",
                "generatedAt": "2026-07-07T12:00:00Z",
                "eventType": "accident",
                "tags": ["случайность"],
            }
        }
    )

    assert events[0]["title"] == "Падение у миски"
    assert events[0]["summary"] == "Громм уже споткнулся у миски."
    assert events[0]["compactText"] == "Громм уже споткнулся у миски."
    assert events[0]["storyText"] == "Громм задел миску лапой и упал на мокрый пол."
    assert events[0]["imageUrl"] == "/static/generated/pet-1/story.png?v=1"
    assert events[0]["generatedAt"] == "2026-07-07T12:00:00Z"
    assert events[0]["eventType"] == "accident"
    assert events[0]["tags"] == ["случайность"]
    assert events[0]["source"] == "last_story_fallback"


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
        telegram_push_service.send_manual_push(telegram_id=TEST_TELEGRAM_ID)

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
        telegram_daily_push_hours=[9, 15, 21],
        telegram_daily_push_window_minutes=120,
        telegram_daily_push_default_timezone="Europe/Moscow",
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
    telegram_push_service.mark_chat_started(chat_id=TEST_TELEGRAM_ID)
    store = telegram_push_service._read_store()
    record = store["records"][str(TEST_TELEGRAM_ID)]
    record["registeredAt"] = (now - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
    record["chatStartedAt"] = (now - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
    telegram_push_service._save_record(record)

    assert telegram_push_service.send_due_pushes() == []

    latest = telegram_push_service.push_status()["latest"]
    assert latest["lastPushErrorCode"] == "TELEGRAM_CHAT_NOT_FOUND"
    assert latest["lastPushAttemptAt"] == now.isoformat().replace("+00:00", "Z")
    assert telegram_push_service._due_records(now) == []


def test_due_push_uses_three_local_daily_windows(monkeypatch, tmp_path) -> None:
    now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    settings = SimpleNamespace(
        telegram_push_store_path=str(tmp_path / "push.json"),
        telegram_daily_push_hours=[9, 15, 21],
        telegram_daily_push_window_minutes=120,
        telegram_daily_push_default_timezone="Europe/Moscow",
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)

    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    telegram_push_service.mark_chat_started(chat_id=TEST_TELEGRAM_ID)
    store = telegram_push_service._read_store()
    record = store["records"][str(TEST_TELEGRAM_ID)]
    record["registeredAt"] = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    record["chatStartedAt"] = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    telegram_push_service._save_record(record)

    due = telegram_push_service._due_records(now)
    assert len(due) == 1
    assert due[0]["telegramId"] == TEST_TELEGRAM_ID

    record["lastPushAt"] = now.isoformat().replace("+00:00", "Z")
    telegram_push_service._save_record(record)
    assert telegram_push_service._due_records(now) == []

    evening = datetime(2026, 7, 8, 18, 0, tzinfo=UTC)
    assert len(telegram_push_service._due_records(evening)) == 1

    after_evening_window = datetime(2026, 7, 8, 20, 0, tzinfo=UTC)
    assert telegram_push_service._due_records(after_evening_window) == []


def test_daily_push_reason_uses_actual_low_pet_stat(monkeypatch) -> None:
    now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    settings = SimpleNamespace(
        telegram_daily_push_hours=[9, 15, 21],
        telegram_daily_push_default_timezone="Europe/Moscow",
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    record = {
        "timezone": "Europe/Moscow",
        "lastStatsTickAt": now.isoformat().replace("+00:00", "Z"),
        "pet": {
            "stats": {"hunger": 12, "happiness": 80, "energy": 70},
        },
    }

    reason = telegram_push_service._push_reason_for_record(record, now)

    assert "хочешь кушать" in reason


def test_latest_fresh_story_event_uses_timestamp_not_list_order() -> None:
    now = datetime(2026, 7, 10, 20, 0, tzinfo=UTC)
    record = {
        "recentStoryEvents": [
            {
                "summary": "Это последняя история.",
                "createdAt": "2026-07-10T18:00:00Z",
            },
            {
                "summary": "Это более старая история, записанная последней в массиве.",
                "createdAt": "2026-07-10T12:00:00Z",
            },
        ]
    }

    event = telegram_push_service._latest_fresh_story_event(record, now)

    assert event is not None
    assert event["summary"] == "Это последняя история."


def test_latest_fresh_story_event_rejects_stale_or_missing_story() -> None:
    now = datetime(2026, 7, 10, 20, 0, tzinfo=UTC)
    stale_record = {
        "recentStoryEvents": [
            {
                "summary": "Эта история уже устарела.",
                "createdAt": "2026-07-10T07:59:59Z",
            }
        ]
    }

    assert telegram_push_service._latest_fresh_story_event(stale_record, now) is None
    assert telegram_push_service._latest_fresh_story_event({}, now) is None


def test_push_reason_uses_only_latest_fresh_story_or_another_topic(monkeypatch) -> None:
    settings = SimpleNamespace(
        telegram_daily_push_hours=[9, 15, 21],
        telegram_daily_push_default_timezone="UTC",
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    now = next(
        datetime(2026, 7, day, 21, 0, tzinfo=UTC)
        for day in range(1, 5)
        if (datetime(2026, 7, day).date().toordinal() + 2) % 3 == 0
    )
    record = {
        "timezone": "UTC",
        "pet": {"stats": {"hunger": 80, "happiness": 80, "energy": 80}},
        "recentStoryEvents": [
            {
                "summary": "Последняя история про новый мост.",
                "createdAt": (now - timedelta(hours=2)).isoformat(),
            },
            {
                "summary": "Старая история про башню.",
                "createdAt": (now - timedelta(hours=8)).isoformat(),
            },
        ],
    }

    story_reason = telegram_push_service._push_reason_for_record(record, now)
    stale_reason = telegram_push_service._push_reason_for_record(
        {
            **record,
            "recentStoryEvents": [
                {
                    "summary": "Давно забытая история.",
                    "createdAt": (now - timedelta(days=2)).isoformat(),
                }
            ],
        },
        now,
    )

    assert "Последняя история про новый мост" in story_reason
    assert "Старая история про башню" not in story_reason
    assert "Недавно со мной произошло" in story_reason
    assert "скучаешь" in stale_reason


def test_story_novelty_history_keeps_compact_long_term_entries() -> None:
    record = {
        "recentStoryEvents": [
            {
                "id": "event-1",
                "title": "Медный ключ",
                "summary": "Очень длинная история, которая не нужна novelty archive.",
                "storyText": "Полный текст истории.",
                "tags": ["ключ", "башня"],
                "createdAt": "2026-01-01T12:00:00Z",
            }
        ]
    }

    history = telegram_push_service._record_story_novelty_history(record)

    assert history == [
        {
            "id": "event-1",
            "title": "Медный ключ",
            "tags": ["ключ", "башня"],
            "createdAt": "2026-01-01T12:00:00Z",
        }
    ]


def test_story_novelty_detects_reused_title_and_tags() -> None:
    story = SimpleNamespace(title="Медный ключ", tags=("ключ", "башня"))
    history = [{"title": "Медный ключ", "tags": ["ключ", "руины"]}]

    assert telegram_push_service._story_is_lexical_duplicate(story, history) is True


def test_story_novelty_preserves_structural_signature() -> None:
    history = telegram_push_service._record_story_novelty_history(
        {
            "storyNoveltyHistory": [
                {
                    "title": "Гость в башне",
                    "tags": ["привидение"],
                    "plotMode": "mystery",
                    "settingClass": "castle_or_tower",
                    "oppositionClass": "supernatural",
                    "resolutionMode": "investigation",
                    "createdAt": "2026-07-11T12:00:00Z",
                }
            ]
        }
    )

    assert history[0]["plotMode"] == "mystery"
    assert history[0]["settingClass"] == "castle_or_tower"
    assert history[0]["oppositionClass"] == "supernatural"
    assert history[0]["resolutionMode"] == "investigation"
