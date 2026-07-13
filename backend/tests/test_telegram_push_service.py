from __future__ import annotations

import asyncio
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


def test_snapshot_preserves_rich_character_bible_when_legacy_client_sends_only_extensions(
    monkeypatch,
    tmp_path,
) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    first = _snapshot_payload().model_copy(
        update={
            "pet": _snapshot_payload().pet.model_copy(
                update={
                    "characterBible": {
                        "identity": {"name": "Громм", "species": "земляной великан"},
                        "inner_state": {"core_want": "строить надёжные мосты"},
                        "extensions": {"lite_overlay": {"facts": []}},
                    }
                }
            )
        }
    )
    telegram_push_service.register_push_snapshot(_user(), first)
    legacy = _snapshot_payload().model_copy(
        update={
            "pet": _snapshot_payload().pet.model_copy(
                update={
                    "characterBible": {
                        "extensions": {"lite_overlay": {"facts": [{"text": "Громм починил мост."}]}}
                    }
                }
            )
        }
    )

    telegram_push_service.register_push_snapshot(_user(), legacy)

    store = json.loads((tmp_path / "push.json").read_text(encoding="utf-8"))
    bible = store["records"][str(TEST_TELEGRAM_ID)]["pet"]["characterBible"]
    assert bible["identity"]["species"] == "земляной великан"
    assert bible["inner_state"]["core_want"] == "строить надёжные мосты"
    assert bible["extensions"]["lite_overlay"]["facts"] == [{"text": "Громм починил мост."}]


def test_pet_reset_deletes_server_data_and_resets_only_matching_local_pet(
    monkeypatch,
    tmp_path,
) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())

    reset_record = telegram_push_service.request_pet_reset(TEST_TELEGRAM_ID)

    assert reset_record["petResetRequest"]["petId"] == "pet-1"
    assert "pet" not in reset_record
    assert "history" not in reset_record
    assert "memoryContext" not in reset_record

    old_pet_response = telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    assert old_pet_response.resetPet is True

    new_pet_payload = _snapshot_payload().model_copy(update={"petId": "pet-2"})
    new_pet_response = telegram_push_service.register_push_snapshot(_user(), new_pet_payload)
    assert new_pet_response.resetPet is False

    store = json.loads((tmp_path / "push.json").read_text(encoding="utf-8"))
    assert store["records"][str(TEST_TELEGRAM_ID)]["petId"] == "pet-2"
    assert "petResetRequest" not in store["records"][str(TEST_TELEGRAM_ID)]


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
    assert (
        telegram_push_service._record_death_at(
            record,
            threshold + timedelta(microseconds=1),
        )
        == threshold
    )


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
        kwargs["direction_output"].update(
            {
                "poseFamily": "defending_or_evading",
                "heroPose": "Громм пригнулся и прикрыл голову лапой.",
                "camera": "Низкий боковой план.",
            }
        )
        return b"story-png"

    monkeypatch.setattr(
        telegram_push_service,
        "generate_background_story_image_bytes",
        fake_story_image_bytes,
    )
    monkeypatch.setattr(
        telegram_push_service,
        "generate_background_story_video_bytes",
        lambda image_bytes: b"story-mp4" if image_bytes == b"story-png" else b"",
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_persist_background_story_image",
        lambda *_args, **_kwargs: "/static/generated/pet-1/background-story.png?v=1",
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_persist_background_story_video",
        lambda *_args, **_kwargs: "/static/generated/pet-1/background-story.mp4?v=1",
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
    assert result["storyVideo"] == {"bytes": b"story-mp4", "mimeType": "video/mp4"}
    assert result["storyVideoError"] is None
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
    assert events[0]["videoUrl"] == "/static/generated/pet-1/background-story.mp4?v=1"
    assert events[0]["imagePoseFamily"] == "defending_or_evading"
    assert events[0]["imageHeroPose"] == "Громм пригнулся и прикрыл голову лапой."
    assert events[0]["imageCamera"] == "Низкий боковой план."
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
    assert (
        store["records"][str(TEST_TELEGRAM_ID)]["lastStory"]["imagePoseFamily"]
        == "defending_or_evading"
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
    assert result["storyVideo"] is None
    assert result["storyVideoError"] == "ConnectTimeout"
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


def test_full_story_applies_each_parts_stat_impacts_sequentially(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    now = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: now)

    class Part:
        def __init__(self, number: int, impacts: list[dict]) -> None:
            self.number = number
            self.stat_impacts = tuple(impacts)

        def model_dump(self):
            return {
                "partNumber": self.number,
                "title": f"Часть {self.number}",
                "storyText": f"Событие {self.number}.",
                "statImpacts": list(self.stat_impacts),
            }

    monkeypatch.setattr(
        telegram_push_service,
        "generate_full_story",
        lambda **_kwargs: SimpleNamespace(
            overall_title="Лекарство до снегопада",
            arc_plan={"goal": "Доставить лекарства."},
            story_direction={
                "plotMode": "rescue_or_help",
                "incidentClass": "rescue_or_aid",
                "settingClass": "remote_landscape",
                "resolutionMode": "cooperation",
            },
            parts=(
                Part(1, [{"stat": "energy", "amount": -8}]),
                Part(2, [{"stat": "hunger", "amount": -7}]),
                Part(3, [{"stat": "happiness", "amount": 8}]),
                Part(4, [{"stat": "hunger", "amount": 15}]),
            ),
            prompt_debug=[],
        ),
    )
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())

    result = telegram_push_service.generate_full_story_for_telegram_user(
        telegram_id=TEST_TELEGRAM_ID,
    )

    assert result["statsPatch"]["stats"] == {
        "hunger": 88,
        "happiness": 78,
        "energy": 52,
    }
    assert result["story"]["parts"][0]["statsDelta"]["energy"] == -8
    assert result["story"]["parts"][3]["statsDelta"]["hunger"] == 15
    store = json.loads((tmp_path / "push.json").read_text(encoding="utf-8"))
    saved = store["records"][str(TEST_TELEGRAM_ID)]
    assert saved["lastFullStory"]["overallTitle"] == "Лекарство до снегопада"
    assert saved["lastFullStory"]["storyDirection"]["plotMode"] == "rescue_or_help"
    assert saved["fullStoryHistory"] == [
        {
            "overallTitle": "Лекарство до снегопада",
            "goal": "Доставить лекарства.",
            "plotMode": "rescue_or_help",
            "incidentClass": "rescue_or_aid",
            "settingClass": "remote_landscape",
            "resolutionMode": "cooperation",
            "generatedAt": "2026-07-07T12:00:00Z",
        }
    ]
    assert saved["pet"]["stats"] == {"hunger": 88, "happiness": 78, "energy": 52}


def test_manual_full_story_sends_each_part_as_video(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    now = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: now)
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    story = {
        "overallTitle": "Четыре тихих часа",
        "generatedAt": "2026-07-07T12:00:00Z",
        "parts": [
            {
                "partNumber": number,
                "title": f"Часть {number}",
                "storyText": f"Тихое событие {number}.",
                "statImpacts": [],
            }
            for number in range(1, 5)
        ],
    }

    def fake_generate_full_story_for_telegram_user(**_kwargs):
        telegram_push_service._update_record(
            TEST_TELEGRAM_ID,
            lambda record: {**(record or {}), "lastFullStory": story},
        )
        return {"generated": True, "story": story}

    monkeypatch.setattr(
        telegram_push_service,
        "generate_full_story_for_telegram_user",
        fake_generate_full_story_for_telegram_user,
    )
    monkeypatch.setattr(
        telegram_push_service,
        "generate_full_story_part_image_bytes",
        lambda **kwargs: (
            kwargs["direction_output"].update({"poseFamily": "resting_or_recovering"})
            or f"png-{kwargs['part']['partNumber']}".encode()
        ),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "generate_background_story_video_bytes",
        lambda image_bytes: b"mp4-" + image_bytes,
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_persist_background_story_image",
        lambda _record, _bytes, *, generated_at: f"/{generated_at.microsecond}.png",
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_persist_background_story_video",
        lambda _record, _bytes, *, generated_at: f"/{generated_at.microsecond}.mp4",
    )
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(
        telegram_push_service,
        "send_video",
        lambda _client, chat_id, video, caption, _keyboard: sent.append(
            {"chatId": chat_id, "video": video, "caption": caption}
        ),
    )

    result = telegram_push_service.send_full_story_for_telegram_user(
        SimpleNamespace(),
        telegram_id=TEST_TELEGRAM_ID,
        keyboard={"inline_keyboard": []},
    )

    assert len(sent) == 4
    assert [item["video"] for item in sent] == [
        b"mp4-png-1",
        b"mp4-png-2",
        b"mp4-png-3",
        b"mp4-png-4",
    ]
    assert all("Четыре тихих часа" in str(item["caption"]) for item in sent)
    assert all(part.get("videoUrl") for part in result["story"]["parts"])


def test_full_story_history_includes_legacy_last_story_without_duplicates() -> None:
    record = {
        "fullStoryHistory": [
            {
                "overallTitle": "Старый спор",
                "goal": "Договориться о воде.",
                "plotMode": "social_event",
                "generatedAt": "2026-07-06T12:00:00Z",
            }
        ],
        "lastFullStory": {
            "overallTitle": "Старый спор",
            "arcPlan": {"goal": "Договориться о воде."},
            "storyDirection": {"plotMode": "social_event"},
            "generatedAt": "2026-07-06T12:00:00Z",
        },
    }

    assert telegram_push_service._record_full_story_history(record) == [
        {
            "overallTitle": "Старый спор",
            "goal": "Договориться о воде.",
            "plotMode": "social_event",
            "generatedAt": "2026-07-06T12:00:00Z",
        }
    ]


def test_automatic_full_story_sends_four_parts_with_images_in_local_slots(
    monkeypatch,
    tmp_path,
) -> None:
    current_now = [datetime(2026, 7, 12, 6, 0, tzinfo=UTC)]
    settings = SimpleNamespace(
        telegram_push_store_path=str(tmp_path / "push.json"),
        telegram_daily_push_default_timezone="Europe/Moscow",
        background_story_enabled=True,
        background_story_hours=[9, 13, 17, 21],
        background_story_window_minutes=120,
        bot_token="bot-token",
        webapp_url="https://example.com/app",
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: current_now[0])

    class Part:
        def __init__(self, number: int) -> None:
            self.number = number
            self.stat_impacts = (
                {"stat": "happiness", "amount": 1, "reason": "История продолжается."},
            )

        def model_dump(self):
            return {
                "partNumber": self.number,
                "title": f"Часть {self.number}",
                "summary": f"Кратко о части {self.number}.",
                "storyText": f"Событие части {self.number} происходит последовательно.",
                "valence": "positive",
                "statImpacts": list(self.stat_impacts),
            }

    generated_contexts: list[dict] = []

    def fake_generate_full_story(**kwargs):
        generated_contexts.append(kwargs["day_context"])
        return SimpleNamespace(
            overall_title="Один длинный день",
            arc_plan={"goal": "Закончить общее дело."},
            story_direction={"plotMode": "social_event"},
            parts=tuple(Part(number) for number in range(1, 5)),
            prompt_debug=[],
        )

    image_parts: list[dict] = []
    sent_videos: list[dict] = []
    monkeypatch.setattr(
        telegram_push_service,
        "generate_full_story",
        fake_generate_full_story,
    )

    def fake_generate_image(**kwargs):
        image_parts.append(kwargs["part"].copy())
        kwargs["direction_output"].update({"poseFamily": "locomotion"})
        return f"png-{kwargs['part']['partNumber']}".encode()

    monkeypatch.setattr(
        telegram_push_service,
        "generate_full_story_part_image_bytes",
        fake_generate_image,
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_persist_background_story_image",
        lambda _record, _bytes, *, generated_at: f"/story-{generated_at.hour}.png",
    )
    monkeypatch.setattr(
        telegram_push_service,
        "generate_background_story_video_bytes",
        lambda image_bytes: b"mp4-" + image_bytes,
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_persist_background_story_video",
        lambda _record, _bytes, *, generated_at: f"/story-{generated_at.hour}.mp4",
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_video",
        lambda _client, chat_id, video, caption, _keyboard: sent_videos.append(
            {"chatId": chat_id, "video": video, "caption": caption}
        ),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_message",
        lambda *_args, **_kwargs: pytest.fail("image fallback was not expected"),
    )

    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    telegram_push_service.mark_chat_started(chat_id=TEST_TELEGRAM_ID)

    for utc_hour, part_number in zip((6, 10, 14, 18), range(1, 5), strict=True):
        current_now[0] = datetime(2026, 7, 12, utc_hour, 0, tzinfo=UTC)
        result = telegram_push_service.send_due_background_stories()
        assert result[0]["partNumber"] == part_number
        assert telegram_push_service.send_due_background_stories() == []
        if part_number == 1:
            telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
            refreshed = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
            assert refreshed["dailyFullStory"]["parts"][0]["deliveredAt"]

    assert len(generated_contexts) == 1
    assert [item["scheduledLocalTime"] for item in generated_contexts[0]["parts"]] == [
        "09:00",
        "13:00",
        "17:00",
        "21:00",
    ]
    assert [item["dayPeriod"] for item in generated_contexts[0]["parts"]] == [
        "утро",
        "день",
        "вечер",
        "ночь",
    ]
    assert [item["scheduledLocalTime"] for item in image_parts] == [
        "09:00",
        "13:00",
        "17:00",
        "21:00",
    ]
    assert len(sent_videos) == 4
    assert all("Один длинный день" in item["caption"] for item in sent_videos)
    stored = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert all(part.get("deliveredAt") for part in stored["dailyFullStory"]["parts"])
    assert all(part.get("statsAppliedAt") for part in stored["dailyFullStory"]["parts"])
    assert len(stored["fullStoryHistory"]) == 1


def test_automatic_full_story_does_not_start_from_second_daily_slot(
    monkeypatch,
    tmp_path,
) -> None:
    now = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)
    settings = SimpleNamespace(
        telegram_push_store_path=str(tmp_path / "push.json"),
        telegram_daily_push_default_timezone="Europe/Moscow",
        background_story_hours=[9, 13, 17, 21],
        background_story_window_minutes=120,
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    telegram_push_service.mark_chat_started(chat_id=TEST_TELEGRAM_ID)

    assert telegram_push_service._due_story_records(now) == []


def test_automatic_full_story_does_not_send_text_when_image_fails(
    monkeypatch,
    tmp_path,
) -> None:
    now = datetime(2026, 7, 12, 6, 0, tzinfo=UTC)
    settings = SimpleNamespace(
        telegram_push_store_path=str(tmp_path / "push.json"),
        telegram_daily_push_default_timezone="Europe/Moscow",
        background_story_enabled=True,
        background_story_hours=[9, 13, 17, 21],
        background_story_window_minutes=120,
        bot_token="bot-token",
        webapp_url="https://example.com/app",
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: now)
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    telegram_push_service.mark_chat_started(chat_id=TEST_TELEGRAM_ID)
    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    story = {
        "overallTitle": "История с обязательной картинкой",
        "generatedAt": "2026-07-12T06:00:00Z",
        "localDate": "2026-07-12",
        "parts": [
            {
                "partNumber": number,
                "title": f"Часть {number}",
                "storyText": "Продолжение общей истории.",
                "valence": "positive",
                "statImpacts": [{"stat": "happiness", "amount": 2, "reason": "Хороший поворот."}],
            }
            for number in range(1, 5)
        ],
    }
    record["dailyFullStory"] = story
    record["lastFullStory"] = story
    telegram_push_service._save_record(record)
    monkeypatch.setattr(
        telegram_push_service,
        "generate_full_story_part_image_bytes",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("image unavailable")),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_video",
        lambda *_args, **_kwargs: pytest.fail("video must not be sent"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_message",
        lambda *_args, **_kwargs: pytest.fail("text fallback is forbidden"),
    )

    assert telegram_push_service.send_due_background_stories() == []

    stored = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert stored["lastStoryErrorCode"] == "DAILY_FULL_STORY_MEDIA_FAILED"
    assert "statsAppliedAt" not in stored["dailyFullStory"]["parts"][0]
    assert "deliveredAt" not in stored["dailyFullStory"]["parts"][0]
    assert telegram_push_service._due_story_records(datetime(2026, 7, 12, 10, 0, tzinfo=UTC)) == []


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
                    "incidentClass": "other_agent_action",
                    "causalOrigin": "other_agent",
                    "eventScale": "shared_situation",
                    "settingClass": "castle_or_tower",
                    "oppositionClass": "supernatural",
                    "resolutionMode": "investigation",
                    "resolutionFamily": "evidence_based_investigation",
                    "createdAt": "2026-07-11T12:00:00Z",
                }
            ]
        }
    )

    assert history[0]["plotMode"] == "mystery"
    assert history[0]["incidentClass"] == "other_agent_action"
    assert history[0]["causalOrigin"] == "other_agent"
    assert history[0]["eventScale"] == "shared_situation"
    assert history[0]["settingClass"] == "castle_or_tower"
    assert history[0]["oppositionClass"] == "supernatural"
    assert history[0]["resolutionMode"] == "investigation"
    assert history[0]["resolutionFamily"] == "evidence_based_investigation"


def test_scheduler_loop_survives_iteration_failure() -> None:
    calls = 0
    state = telegram_push_service._scheduler_runtime["dailyPush"]
    state.update(running=False, consecutiveFailures=0, lastError=None)

    def operation() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary store failure")

    async def scenario() -> None:
        task = asyncio.create_task(
            telegram_push_service._scheduler_loop("dailyPush", operation, 0.01)
        )
        while calls < 2:
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())

    assert calls >= 2
    assert state["running"] is False
    assert state["consecutiveFailures"] == 0
    assert state["lastError"] is None
