from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.bot import TelegramAPIError
from app.config import Settings
from app.schemas import LocalPetPushSnapshotRequest, LocalProactiveResponse
from app.services import telegram_push_service
from app.services.telegram_auth_service import TelegramUserContext
from app.services.telegram_push_store import (
    JsonTelegramPushStore,
    SQLiteTelegramPushStore,
    TelegramPushStoreCapacityError,
)

TEST_TELEGRAM_ID = 62943754


def test_push_store_wiring_selects_backend_from_suffix_or_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        telegram_push_service,
        "get_settings",
        lambda: SimpleNamespace(
            telegram_push_store_path=str(tmp_path / "push.sqlite3"),
            telegram_push_legacy_json_path=str(tmp_path / "legacy.json"),
            telegram_push_legacy_json_required=False,
        ),
    )

    assert isinstance(telegram_push_service._push_store(), SQLiteTelegramPushStore)

    monkeypatch.setattr(
        telegram_push_service,
        "get_settings",
        lambda: SimpleNamespace(
            telegram_push_store_path=str(tmp_path / "compat.state"),
            telegram_push_store_backend="json",
        ),
    )

    assert isinstance(telegram_push_service._push_store(), JsonTelegramPushStore)


def _reserved(fake):
    @contextmanager
    def reservation(*args, **kwargs):
        yield fake(*args, **kwargs)

    return reservation


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


def _seed_due_daily_full_story(
    telegram_id: int = TEST_TELEGRAM_ID,
    *,
    pet_id: str = "pet-1",
) -> None:
    snapshot = _snapshot_payload().model_copy(update={"petId": pet_id})
    telegram_push_service.register_push_snapshot(_user_with_id(telegram_id), snapshot)
    telegram_push_service.mark_chat_started(chat_id=telegram_id)
    record = telegram_push_service._read_store()["records"][str(telegram_id)]
    story = {
        "overallTitle": "Синтетическая история дня",
        "generatedAt": "2026-07-12T06:00:00Z",
        "localDate": "2026-07-12",
        "parts": [
            {
                "partNumber": number,
                "title": f"Часть {number}",
                "storyText": "Синтетическое продолжение общей истории.",
                "valence": "positive",
                "statImpacts": [],
            }
            for number in range(1, 5)
        ],
    }
    record["dailyFullStory"] = story
    record["lastFullStory"] = deepcopy(story)
    telegram_push_service._save_record(record)


def test_snapshot_schema_rejects_oversized_persisted_fields() -> None:
    oversized_url = _snapshot_payload().model_dump()
    oversized_url["pet"]["assetImages"] = {"adult": {"idle": "https://example.test/" + "x" * 1000}}
    with pytest.raises(ValidationError):
        LocalPetPushSnapshotRequest.model_validate(oversized_url)

    oversized_bible = _snapshot_payload().model_dump()
    oversized_bible["pet"]["characterBible"] = {"blob": "x" * 262_144}
    with pytest.raises(ValidationError, match="persisted size limit"):
        LocalPetPushSnapshotRequest.model_validate(oversized_bible)


def test_character_bible_schema_rejects_adversarial_depth_and_node_count() -> None:
    deeply_nested: dict[str, object] = {"leaf": "value"}
    for _ in range(25):
        deeply_nested = {"next": deeply_nested}
    deep_payload = _snapshot_payload().model_dump(mode="json")
    deep_payload["pet"]["characterBible"] = deeply_nested

    with pytest.raises(ValidationError, match="nested too deeply"):
        LocalPetPushSnapshotRequest.model_validate(deep_payload)

    wide_payload = _snapshot_payload().model_dump(mode="json")
    wide_payload["pet"]["characterBible"] = {
        "facts": {f"key-{index}": index for index in range(10_001)}
    }
    with pytest.raises(ValidationError, match="too many values"):
        LocalPetPushSnapshotRequest.model_validate(wide_payload)


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


def test_modern_snapshot_replaces_client_bible_but_preserves_server_overlay(
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
                        "identity": {"name": "Старое имя", "obsolete": True},
                        "obsoleteBranch": {"blob": "old"},
                        "extensions": {
                            "lite_overlay": {
                                "facts": [{"sphere": "world", "text": "Серверный факт"}]
                            }
                        },
                    }
                }
            )
        }
    )
    telegram_push_service.register_push_snapshot(_user(), first)
    modern = _snapshot_payload().model_copy(
        update={
            "pet": _snapshot_payload().pet.model_copy(
                update={
                    "characterBible": {
                        "identity": {"name": "Новое имя"},
                        "extensions": {
                            "lite_overlay": {
                                "facts": [{"sphere": "character", "text": "Локальный факт"}]
                            }
                        },
                    }
                }
            )
        }
    )

    telegram_push_service.register_push_snapshot(_user(), modern)

    bible = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]["pet"][
        "characterBible"
    ]
    assert bible["identity"] == {"name": "Новое имя"}
    assert "obsoleteBranch" not in bible
    assert {fact["text"] for fact in bible["extensions"]["lite_overlay"]["facts"]} == {
        "Локальный факт",
        "Серверный факт",
    }


def test_legacy_snapshot_cannot_accumulate_arbitrary_extension_keys(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    initial = _snapshot_payload().model_copy(
        update={
            "pet": _snapshot_payload().pet.model_copy(
                update={"characterBible": {"identity": {"name": "Громм"}}}
            )
        }
    )
    telegram_push_service.register_push_snapshot(_user(), initial)

    for index in range(5):
        legacy = _snapshot_payload().model_copy(
            update={
                "pet": _snapshot_payload().pet.model_copy(
                    update={
                        "characterBible": {
                            "extensions": {
                                f"attacker-{index}": {"blob": "x" * 1_000},
                                "lite_overlay": {"facts": [{"text": f"fact-{index}"}]},
                            }
                        }
                    }
                )
            }
        )
        telegram_push_service.register_push_snapshot(_user(), legacy)

    bible = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]["pet"][
        "characterBible"
    ]
    extensions = bible["extensions"]
    assert all(f"attacker-{index}" not in extensions for index in range(5))
    assert len(extensions["lite_overlay"]["facts"]) == 5


def test_snapshot_normalizes_lite_overlay_to_bounded_allowlist(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    payload = _snapshot_payload().model_copy(
        update={
            "pet": _snapshot_payload().pet.model_copy(
                update={
                    "characterBible": {
                        "identity": {"name": "Громм"},
                        "extensions": {
                            "lite_overlay": {
                                "facts": [
                                    {
                                        "sphere": "world",
                                        "text": f"fact-{index}-" + "x" * 1_000,
                                        "unknown": "x" * 1_000,
                                    }
                                    for index in range(100)
                                ],
                                "spheres": {
                                    "evil": {"facts": [{"text": "never persist"}]},
                                    "world": {
                                        "facts": [
                                            {"sphere": "world", "text": f"world-{index}"}
                                            for index in range(50)
                                        ]
                                    },
                                },
                                "worldSeed": {
                                    "source": "s" * 500,
                                    "createdAt": "2026-07-15T00:00:00Z",
                                    "blob": "x" * 10_000,
                                },
                                "unknownRoot": "x" * 10_000,
                            }
                        },
                    }
                }
            )
        }
    )

    telegram_push_service.register_push_snapshot(_user(), payload)

    overlay = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]["pet"][
        "characterBible"
    ]["extensions"]["lite_overlay"]
    assert set(overlay) <= {"facts", "spheres", "worldSeed"}
    assert len(overlay["facts"]) == 80
    assert all(
        len(fact["text"]) <= 500 and set(fact) <= {"sphere", "text"} for fact in overlay["facts"]
    )
    assert set(overlay["spheres"]) == {"world"}
    assert len(overlay["spheres"]["world"]["facts"]) == 40
    assert overlay["worldSeed"] == {
        "source": "s" * 80,
        "createdAt": "2026-07-15T00:00:00Z",
    }


def test_late_snapshot_cannot_roll_back_same_pet_context(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    newer = LocalPetPushSnapshotRequest.model_validate(
        {
            **_snapshot_payload().model_dump(mode="json"),
            "updatedAt": "2026-07-07T12:01:00Z",
            "history": [
                {
                    "role": "user",
                    "text": "новая реплика",
                    "createdAt": "2026-07-07T12:00:59Z",
                }
            ],
            "recentAmbientReplies": ["новый ambient"],
            "memoryContext": {"summary": "новая память"},
            "timezone": "Asia/Tokyo",
        },
    )
    older = LocalPetPushSnapshotRequest.model_validate(
        {
            **_snapshot_payload().model_dump(mode="json"),
            "updatedAt": "2026-07-07T12:00:00Z",
            "history": [
                {
                    "role": "user",
                    "text": "старая реплика",
                    "createdAt": "2026-07-07T11:59:59Z",
                }
            ],
            "recentAmbientReplies": ["старый ambient"],
            "memoryContext": {"summary": "старая память"},
            "timezone": "Europe/Moscow",
        },
    )

    telegram_push_service.register_push_snapshot(_user(), newer)
    telegram_push_service.register_push_snapshot(_user(), older)

    record = json.loads((tmp_path / "push.json").read_text(encoding="utf-8"))["records"][
        str(TEST_TELEGRAM_ID)
    ]
    assert record["updatedAt"] == "2026-07-07T12:01:00Z"
    assert record["history"][0]["text"] == "новая реплика"
    assert record["recentAmbientReplies"] == ["новый ambient"]
    assert record["memoryContext"]["summary"] == "новая память"
    assert record["timezone"] == "Asia/Tokyo"


def test_writer_revision_orders_same_pet_without_trusting_device_clock(
    monkeypatch,
    tmp_path,
) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    writer_id = "writer-session-00000001"
    future_clock = LocalPetPushSnapshotRequest.model_validate(
        {
            **_snapshot_payload().model_dump(mode="json"),
            "snapshotWriterId": writer_id,
            "snapshotRevision": 1,
            "updatedAt": "2099-07-07T12:00:00Z",
            "history": [{"role": "user", "text": "старое", "createdAt": None}],
        }
    )
    newer_revision = LocalPetPushSnapshotRequest.model_validate(
        {
            **_snapshot_payload().model_dump(mode="json"),
            "snapshotWriterId": writer_id,
            "snapshotRevision": 2,
            "updatedAt": "2026-07-07T12:00:00Z",
            "history": [{"role": "user", "text": "новое", "createdAt": None}],
        }
    )

    telegram_push_service.register_push_snapshot(_user(), future_clock)
    telegram_push_service.register_push_snapshot(_user(), newer_revision)

    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert record["snapshotRevision"] == 2
    assert record["history"][0]["text"] == "новое"
    assert record["updatedAt"] == "2026-07-07T12:00:00Z"


def test_late_old_pet_snapshot_cannot_replace_new_pet_from_same_writer(
    monkeypatch,
    tmp_path,
) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    writer_id = "writer-session-00000002"
    old_pet = LocalPetPushSnapshotRequest.model_validate(
        {
            **_snapshot_payload().model_dump(mode="json"),
            "snapshotWriterId": writer_id,
            "snapshotRevision": 1,
            "petId": "pet-old",
        }
    )
    new_pet = LocalPetPushSnapshotRequest.model_validate(
        {
            **_snapshot_payload().model_dump(mode="json"),
            "snapshotWriterId": writer_id,
            "snapshotRevision": 2,
            "petId": "pet-new",
            "createdAt": "2026-07-08T12:00:00Z",
        }
    )

    telegram_push_service.register_push_snapshot(_user(), new_pet)
    telegram_push_service.register_push_snapshot(_user(), old_pet)

    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert record["petId"] == "pet-new"
    assert record["snapshotRevision"] == 2


def test_late_old_pet_snapshot_cannot_replace_new_pet_from_another_writer(
    monkeypatch,
    tmp_path,
) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    old_pet = LocalPetPushSnapshotRequest.model_validate(
        {
            **_snapshot_payload().model_dump(mode="json"),
            "snapshotWriterId": "writer-session-old-0001",
            "snapshotRevision": 50,
            "petId": "pet-old",
            "createdAt": "2026-07-07T12:00:00Z",
        }
    )
    new_pet = LocalPetPushSnapshotRequest.model_validate(
        {
            **_snapshot_payload().model_dump(mode="json"),
            "snapshotWriterId": "writer-session-new-0001",
            "snapshotRevision": 1,
            "petId": "pet-new",
            "createdAt": "2026-07-08T12:00:00Z",
        }
    )

    telegram_push_service.register_push_snapshot(_user(), new_pet)
    telegram_push_service.register_push_snapshot(_user(), old_pet)

    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert record["petId"] == "pet-new"
    assert record["snapshotWriterId"] == "writer-session-new-0001"
    assert record["snapshotRevision"] == 1


def test_epoch_snapshot_order_is_total_across_tab_writers(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    revision_floor = telegram_push_service.SNAPSHOT_EPOCH_REVISION_FLOOR
    newer = LocalPetPushSnapshotRequest.model_validate(
        {
            **_snapshot_payload().model_dump(mode="json"),
            "snapshotWriterId": "writer-tab-newer-0001",
            "snapshotRevision": revision_floor + 2,
            "updatedAt": "2026-07-15T12:00:00Z",
            "history": [{"role": "user", "text": "новое", "createdAt": None}],
        }
    )
    late_older = LocalPetPushSnapshotRequest.model_validate(
        {
            **_snapshot_payload().model_dump(mode="json"),
            "snapshotWriterId": "writer-tab-older-0001",
            "snapshotRevision": revision_floor + 1,
            "updatedAt": "2026-07-15T12:05:00Z",
            "history": [{"role": "user", "text": "старое", "createdAt": None}],
        }
    )

    telegram_push_service.register_push_snapshot(_user(), newer)
    telegram_push_service.register_push_snapshot(_user(), late_older)

    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert record["snapshotWriterId"] == "writer-tab-newer-0001"
    assert record["snapshotRevision"] == revision_floor + 2
    assert record["history"][0]["text"] == "новое"


def test_legacy_counter_cannot_overwrite_epoch_snapshot(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    revision_floor = telegram_push_service.SNAPSHOT_EPOCH_REVISION_FLOOR
    epoch_snapshot = LocalPetPushSnapshotRequest.model_validate(
        {
            **_snapshot_payload().model_dump(mode="json"),
            "snapshotWriterId": "writer-tab-epoch-0001",
            "snapshotRevision": revision_floor,
            "history": [{"role": "user", "text": "epoch", "createdAt": None}],
        }
    )
    legacy_counter = LocalPetPushSnapshotRequest.model_validate(
        {
            **_snapshot_payload().model_dump(mode="json"),
            "snapshotWriterId": "writer-tab-legacy-001",
            "snapshotRevision": 99_999,
            "updatedAt": "2099-07-15T12:00:00Z",
            "history": [{"role": "user", "text": "legacy", "createdAt": None}],
        }
    )

    telegram_push_service.register_push_snapshot(_user(), epoch_snapshot)
    telegram_push_service.register_push_snapshot(_user(), legacy_counter)

    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert record["snapshotWriterId"] == "writer-tab-epoch-0001"
    assert record["history"][0]["text"] == "epoch"


def test_snapshot_schema_requires_writer_and_revision_together() -> None:
    raw = _snapshot_payload().model_dump(mode="json")
    with pytest.raises(ValidationError, match="provided together"):
        LocalPetPushSnapshotRequest.model_validate(
            {**raw, "snapshotWriterId": "writer-session-00000003"}
        )
    with pytest.raises(ValidationError, match="provided together"):
        LocalPetPushSnapshotRequest.model_validate({**raw, "snapshotRevision": 1})


def test_snapshot_schema_normalizes_pet_id() -> None:
    raw = _snapshot_payload().model_dump(mode="json")

    payload = LocalPetPushSnapshotRequest.model_validate({**raw, "petId": "  pet-1  "})

    assert payload.petId == "pet-1"


def test_pet_reset_deletes_server_data_and_resets_only_matching_local_pet(
    monkeypatch,
    tmp_path,
) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    cleanup_records: list[list[dict[str, object]]] = []
    monkeypatch.setattr(
        telegram_push_service,
        "_cleanup_background_story_media_for_records",
        lambda records, **_kwargs: cleanup_records.append(records),
    )

    reset_record = telegram_push_service.request_pet_reset(TEST_TELEGRAM_ID)

    assert reset_record["petResetRequest"]["petId"] == "pet-1"
    assert cleanup_records == [[{"telegramId": TEST_TELEGRAM_ID, "petId": "pet-1"}]]
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


def test_unregister_snapshot_fences_late_writes_without_blocking_a_new_pet(
    monkeypatch,
    tmp_path,
) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    cleanup_records: list[list[dict[str, object]]] = []
    monkeypatch.setattr(
        telegram_push_service,
        "_cleanup_background_story_media_for_records",
        lambda records, **_kwargs: cleanup_records.append(records),
    )

    assert telegram_push_service.unregister_push_snapshot(TEST_TELEGRAM_ID, "pet-1") is True
    assert cleanup_records == [[{"telegramId": TEST_TELEGRAM_ID, "petId": "pet-1"}]]
    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert "pet" not in record
    assert record["petResetTombstones"][-1]["petId"] == "pet-1"

    late_response = telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    assert late_response.resetPet is True
    assert "pet" not in telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]

    new_pet_payload = _snapshot_payload().model_copy(update={"petId": "pet-2"})
    new_pet_response = telegram_push_service.register_push_snapshot(_user(), new_pet_payload)
    assert new_pet_response.resetPet is False
    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert record["petId"] == "pet-2"
    assert record["petResetTombstones"][-1]["petId"] == "pet-1"

    assert telegram_push_service.unregister_push_snapshot(TEST_TELEGRAM_ID, "pet-1") is True
    assert telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]["petId"] == (
        "pet-2"
    )


def test_unregister_snapshot_matches_legacy_whitespace_pet_id(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    telegram_push_service._save_record(
        {
            "telegramId": TEST_TELEGRAM_ID,
            "petId": "  pet-1  ",
            "pet": {"description": "legacy"},
        }
    )

    assert telegram_push_service.unregister_push_snapshot(TEST_TELEGRAM_ID, " pet-1 ") is True

    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert "pet" not in record
    assert record["petResetTombstones"][-1]["petId"] == "pet-1"


def test_unregister_old_snapshot_preserves_already_registered_new_pet(
    monkeypatch,
    tmp_path,
) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    new_pet_payload = _snapshot_payload().model_copy(update={"petId": "pet-2"})
    telegram_push_service.register_push_snapshot(_user(), new_pet_payload)

    assert telegram_push_service.unregister_push_snapshot(TEST_TELEGRAM_ID, "pet-1") is True

    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert record["petId"] == "pet-2"
    assert record["petResetTombstones"][-1]["petId"] == "pet-1"


def test_unregister_missing_snapshot_fails_atomically_at_record_capacity(
    monkeypatch,
    tmp_path,
) -> None:
    settings = SimpleNamespace(
        telegram_push_store_path=str(tmp_path / "push.json"),
        telegram_push_store_max_records=1,
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    telegram_push_service._save_record({"telegramId": 99, "petId": "other-pet", "pet": {}})

    with pytest.raises(TelegramPushStoreCapacityError):
        telegram_push_service.unregister_push_snapshot(TEST_TELEGRAM_ID, "pet-1")

    records = telegram_push_service._read_store()["records"]
    assert set(records) == {"99"}


def test_unregister_snapshot_keeps_durable_tombstones(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)

    for index in range(11):
        assert (
            telegram_push_service.unregister_push_snapshot(
                TEST_TELEGRAM_ID,
                f"pet-{index}",
            )
            is True
        )

    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    tombstones = record["petResetTombstones"]
    assert [item["petId"] for item in tombstones] == [f"pet-{index}" for index in range(11)]

    late_payload = _snapshot_payload().model_copy(update={"petId": "pet-0"})
    late_response = telegram_push_service.register_push_snapshot(_user(), late_payload)

    assert late_response.resetPet is True
    assert "pet" not in telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]


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
        "reserve_background_story_image_bytes",
        _reserved(fake_story_image_bytes),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(lambda image_bytes: b"story-mp4" if image_bytes == b"story-png" else b""),
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
        "reserve_background_story_image_bytes",
        _reserved(fail_story_image),
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


def test_full_story_applies_impacts_to_latest_record(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    now = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: now)
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())

    class Part:
        stat_impacts = ({"stat": "hunger", "amount": -7},)

        @staticmethod
        def model_dump():
            return {
                "partNumber": 1,
                "title": "Поздняя часть",
                "storyText": "История завершилась после обновления.",
                "statImpacts": list(Part.stat_impacts),
            }

    def fake_generate_full_story(**_kwargs):
        def concurrent_snapshot(current):
            next_record = deepcopy(current)
            next_record["pet"]["stats"]["hunger"] = 40
            return next_record

        telegram_push_service._update_record(TEST_TELEGRAM_ID, concurrent_snapshot)
        return SimpleNamespace(
            overall_title="Свежая история",
            arc_plan={},
            story_direction={},
            parts=(Part(),),
            prompt_debug=[],
        )

    monkeypatch.setattr(
        telegram_push_service,
        "generate_full_story",
        fake_generate_full_story,
    )

    result = telegram_push_service.generate_full_story_for_telegram_user(
        telegram_id=TEST_TELEGRAM_ID,
    )

    assert result["statsPatch"]["stats"]["hunger"] == 33
    saved = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert saved["pet"]["stats"]["hunger"] == 33


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

    class Part:
        stat_impacts: tuple[dict, ...] = ()

        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def model_dump(self):
            return self.payload.copy()

    monkeypatch.setattr(
        telegram_push_service,
        "generate_full_story",
        lambda **_kwargs: SimpleNamespace(
            overall_title=story["overallTitle"],
            arc_plan={},
            story_direction={},
            parts=tuple(Part(part) for part in story["parts"]),
            prompt_debug=[],
        ),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_full_story_part_image_bytes",
        _reserved(
            lambda **kwargs: (
                kwargs["direction_output"].update({"poseFamily": "resting_or_recovering"})
                or f"png-{kwargs['part']['partNumber']}".encode()
            )
        ),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(lambda image_bytes: b"mp4-" + image_bytes),
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


def test_manual_full_story_media_failure_does_not_apply_stats(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json"))
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    now = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: now)
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())

    class Part:
        stat_impacts = ({"stat": "energy", "amount": -25},)

        @staticmethod
        def model_dump():
            return {
                "partNumber": 1,
                "title": "Опасная часть",
                "storyText": "Путь оказался трудным.",
                "statImpacts": list(Part.stat_impacts),
            }

    monkeypatch.setattr(
        telegram_push_service,
        "generate_full_story",
        lambda **_kwargs: SimpleNamespace(
            overall_title="Несохранённая история",
            arc_plan={},
            story_direction={},
            parts=(Part(),),
            prompt_debug=[],
        ),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_full_story_part_image_bytes",
        _reserved(lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("image unavailable"))),
    )

    with pytest.raises(telegram_push_service.TelegramPushError) as error:
        telegram_push_service.send_full_story_for_telegram_user(
            SimpleNamespace(),
            telegram_id=TEST_TELEGRAM_ID,
            keyboard={"inline_keyboard": []},
        )

    assert error.value.code == "FULL_STORY_MEDIA_FAILED"
    saved = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert saved["pet"]["stats"] == {"hunger": 80, "happiness": 70, "energy": 60}
    assert "lastFullStory" not in saved


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


def test_bot_generation_receipts_are_bounded_and_replace_same_request() -> None:
    record: dict[str, object] = {}
    for index in range(20):
        record["botGenerationReceipts"] = telegram_push_service._append_bot_generation_receipt(
            record,
            {
                "requestKey": f"telegram-update:{index}",
                "kind": "story",
                "story": {"title": f"История {index}"},
            },
        )

    receipts = telegram_push_service._record_bot_generation_receipts(record)
    assert len(receipts) == telegram_push_service.MAX_BOT_GENERATION_RECEIPTS
    assert receipts[0]["requestKey"] == "telegram-update:4"

    record["botGenerationReceipts"] = telegram_push_service._append_bot_generation_receipt(
        record,
        {
            "requestKey": "telegram-update:19",
            "kind": "story",
            "story": {"title": "Обновлённая история"},
        },
    )
    receipts = telegram_push_service._record_bot_generation_receipts(record)
    assert len(receipts) == telegram_push_service.MAX_BOT_GENERATION_RECEIPTS
    assert receipts[-1]["story"]["title"] == "Обновлённая история"


def test_same_pet_snapshot_preserves_bot_generation_receipts(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        telegram_push_service,
        "get_settings",
        lambda: SimpleNamespace(telegram_push_store_path=str(tmp_path / "push.json")),
    )
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())

    def add_receipt(current):
        next_record = deepcopy(current)
        next_record["botGenerationReceipts"] = [
            {
                "requestKey": "telegram-update:42",
                "kind": "story",
                "story": {"title": "Уже применённая история"},
            }
        ]
        return next_record

    telegram_push_service._update_record(TEST_TELEGRAM_ID, add_receipt)
    newer_snapshot = _snapshot_payload().model_copy(update={"updatedAt": "2026-07-07T12:01:00Z"})
    telegram_push_service.register_push_snapshot(_user(), newer_snapshot)

    saved = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert saved["botGenerationReceipts"][0]["requestKey"] == "telegram-update:42"


def test_background_story_paid_media_budget_config_is_fail_closed() -> None:
    field = Settings.model_fields["scheduled_background_story_paid_media_daily_cap"]
    assert field.default == 0
    with pytest.raises(ValidationError):
        Settings(_env_file=None, scheduled_background_story_paid_media_daily_cap=-1)


def test_background_story_paid_media_budget_uses_durable_global_counter(
    monkeypatch,
    tmp_path,
) -> None:
    rate_limit_path = tmp_path / "rate-limits.sqlite3"
    settings = SimpleNamespace(
        scheduled_background_story_paid_media_daily_cap=0,
        rate_limit_store_path=str(rate_limit_path),
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)

    with pytest.raises(telegram_push_service._BackgroundStoryPaidMediaBudgetError) as disabled:
        telegram_push_service._consume_background_story_paid_media_budget(stage="image")

    assert disabled.value.status == "disabled"
    assert disabled.value.code == telegram_push_service.BACKGROUND_STORY_PAID_MEDIA_BUDGET_DISABLED
    assert not rate_limit_path.exists()

    settings.scheduled_background_story_paid_media_daily_cap = 2
    telegram_push_service._consume_background_story_paid_media_budget(stage="image")
    telegram_push_service._consume_background_story_paid_media_budget(stage="video")
    with pytest.raises(telegram_push_service._BackgroundStoryPaidMediaBudgetError) as exhausted:
        telegram_push_service._consume_background_story_paid_media_budget(stage="image")

    assert exhausted.value.status == "exhausted"
    assert exhausted.value.code == (
        telegram_push_service.BACKGROUND_STORY_PAID_MEDIA_BUDGET_EXHAUSTED
    )
    assert exhausted.value.retry_after_seconds >= 1
    with sqlite3.connect(rate_limit_path) as connection:
        counter = connection.execute(
            """
            SELECT event_count, request_keys_json
            FROM rate_limit_counters
            WHERE bucket = ? AND user_id = ?
            """,
            (
                telegram_push_service.BACKGROUND_STORY_PAID_MEDIA_BUDGET_BUCKET,
                telegram_push_service.BACKGROUND_STORY_PAID_MEDIA_BUDGET_USER_ID,
            ),
        ).fetchone()
    assert counter == (2, "[]")


def test_due_background_story_order_is_stable_per_utc_budget_day_and_rotates(
    monkeypatch,
) -> None:
    records = {
        str(telegram_id): {
            "telegramId": telegram_id,
            "petId": f"pet-{telegram_id}",
            "pet": {},
            "chatReachable": True,
        }
        for telegram_id in range(1_000, 1_064)
    }
    monkeypatch.setattr(
        telegram_push_service,
        "_read_store",
        lambda: {"version": 1, "records": records},
    )
    monkeypatch.setattr(telegram_push_service, "_record_is_dead", lambda *_args: False)
    monkeypatch.setattr(
        telegram_push_service,
        "_background_story_slot",
        lambda _record, now: (0, now, "UTC"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_daily_full_story_part",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_daily_full_story_attempt_due",
        lambda *_args, **_kwargs: True,
    )

    def ordered_ids(now: datetime) -> list[int]:
        return [record["telegramId"] for record in telegram_push_service._due_story_records(now)]

    morning = datetime(2026, 7, 12, 0, 1, tzinfo=UTC)
    evening = datetime(2026, 7, 12, 23, 59, tzinfo=UTC)
    next_window = datetime(2026, 7, 13, 0, 1, tzinfo=UTC)
    morning_order = ordered_ids(morning)

    assert morning_order == ordered_ids(evening)
    assert morning_order != ordered_ids(next_window)
    assert sorted(morning_order) == list(range(1_000, 1_064))


def test_scheduled_background_story_order_breaks_hash_ties_by_telegram_id(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        telegram_push_service.hashlib,
        "sha256",
        lambda _value: SimpleNamespace(digest=lambda: b"same-digest"),
    )
    now = datetime(2026, 7, 12, tzinfo=UTC)
    records = [{"telegramId": telegram_id} for telegram_id in (30, 10, 20)]

    ordered = sorted(
        records,
        key=lambda record: telegram_push_service._scheduled_background_story_order_key(
            record,
            now=now,
        ),
    )

    assert [record["telegramId"] for record in ordered] == [10, 20, 30]


def test_disabled_paid_media_delivers_text_without_degrading_or_retrying(
    monkeypatch,
    tmp_path,
) -> None:
    now = datetime(2026, 7, 12, 6, 0, tzinfo=UTC)
    rate_limit_path = tmp_path / "rate-limits.sqlite3"
    settings = SimpleNamespace(
        telegram_push_store_path=str(tmp_path / "push.json"),
        telegram_daily_push_default_timezone="Europe/Moscow",
        background_story_enabled=True,
        background_story_hours=[9, 13, 17, 21],
        background_story_window_minutes=120,
        scheduled_background_story_paid_media_daily_cap=0,
        rate_limit_store_path=str(rate_limit_path),
        bot_token="bot-token",
        webapp_url="https://example.com/app",
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: now)
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_full_story_part_image_bytes",
        lambda **_kwargs: pytest.fail("disabled image budget must fail before reserve"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        lambda *_args, **_kwargs: pytest.fail("video reserve must not be attempted"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_photo",
        lambda *_args, **_kwargs: pytest.fail("there is no image to deliver"),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_video",
        lambda *_args, **_kwargs: pytest.fail("there is no video to deliver"),
    )
    sent_text: list[str] = []
    monkeypatch.setattr(
        telegram_push_service,
        "send_message",
        lambda _client, _chat_id, text, _keyboard: sent_text.append(text),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_cleanup_background_story_media_for_records",
        lambda *_args, **_kwargs: None,
    )
    _seed_due_daily_full_story()

    batch = telegram_push_service._run_due_background_stories()

    assert batch.attempted == 1
    assert batch.failed == 0
    assert batch.health_failed == 0
    assert len(batch.results) == 1
    assert batch.results[0]["storyMediaStatus"] == "budget_disabled"
    assert batch.results[0]["storyMediaErrorCode"] == (
        telegram_push_service.BACKGROUND_STORY_PAID_MEDIA_BUDGET_DISABLED
    )
    assert sent_text and "Синтетическая история дня" in sent_text[0]
    assert not rate_limit_path.exists()

    stored = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    part = stored["dailyFullStory"]["parts"][0]
    assert part["deliveredAt"]
    assert part["mediaStatus"] == "budget_disabled"
    assert part["mediaBudgetStage"] == "image"
    assert stored["lastStoryStatus"] == "delivered_media_budget_disabled"
    assert stored["lastStoryErrorCode"] == (
        telegram_push_service.BACKGROUND_STORY_PAID_MEDIA_BUDGET_DISABLED
    )

    second_batch = telegram_push_service._run_due_background_stories()
    assert second_batch.attempted == 0
    assert second_batch.health_failed == 0


def test_global_exhausted_video_budget_delivers_one_photo_then_text(
    monkeypatch,
    tmp_path,
) -> None:
    now = datetime(2026, 7, 12, 6, 0, tzinfo=UTC)
    rate_limit_path = tmp_path / "rate-limits.sqlite3"
    settings = SimpleNamespace(
        telegram_push_store_path=str(tmp_path / "push.json"),
        telegram_daily_push_default_timezone="Europe/Moscow",
        background_story_enabled=True,
        background_story_hours=[9, 13, 17, 21],
        background_story_window_minutes=120,
        scheduled_background_story_paid_media_daily_cap=1,
        rate_limit_store_path=str(rate_limit_path),
        bot_token="bot-token",
        webapp_url="https://example.com/app",
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: now)
    image_calls: list[int] = []

    def image_provider(**kwargs):
        image_calls.append(kwargs["part"]["partNumber"])
        return b"paid-image"

    monkeypatch.setattr(
        telegram_push_service,
        "reserve_full_story_part_image_bytes",
        _reserved(image_provider),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_persist_background_story_image",
        lambda *_args, **_kwargs: "/static/generated/synthetic-story.png",
    )
    video_calls: list[bytes] = []
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(lambda image_bytes: video_calls.append(image_bytes) or b"unexpected-video"),
    )
    sent_photos: list[tuple[int, bytes]] = []
    monkeypatch.setattr(
        telegram_push_service,
        "send_photo",
        lambda _client, chat_id, photo, _caption, _keyboard: sent_photos.append((chat_id, photo)),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_video",
        lambda *_args, **_kwargs: pytest.fail("video must not be sent"),
    )
    sent_text: list[int] = []
    monkeypatch.setattr(
        telegram_push_service,
        "send_message",
        lambda _client, chat_id, _text, _keyboard: sent_text.append(chat_id),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_cleanup_background_story_media_for_records",
        lambda *_args, **_kwargs: None,
    )
    _seed_due_daily_full_story()
    second_telegram_id = TEST_TELEGRAM_ID + 1
    _seed_due_daily_full_story(second_telegram_id, pet_id="pet-2")

    batch = telegram_push_service._run_due_background_stories()

    assert batch.attempted == 2
    assert batch.failed == 0
    assert batch.health_failed == 0
    assert image_calls == [1]
    assert video_calls == []
    assert sent_photos == [(TEST_TELEGRAM_ID, b"paid-image")]
    assert sent_text == [second_telegram_id]
    stored = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    part = stored["dailyFullStory"]["parts"][0]
    assert part["deliveredAt"]
    assert part["imageUrl"] == "/static/generated/synthetic-story.png"
    assert part["videoUrl"] is None
    assert part["mediaStatus"] == "budget_exhausted"
    assert part["mediaBudgetStage"] == "video"
    assert stored["lastStoryStatus"] == "delivered_media_budget_exhausted"
    assert stored["lastStoryErrorCode"] == (
        telegram_push_service.BACKGROUND_STORY_PAID_MEDIA_BUDGET_EXHAUSTED
    )
    second_stored = telegram_push_service._read_store()["records"][str(second_telegram_id)]
    second_part = second_stored["dailyFullStory"]["parts"][0]
    assert second_part["deliveredAt"]
    assert second_part["imageUrl"] is None
    assert second_part["videoUrl"] is None
    assert second_part["mediaStatus"] == "budget_exhausted"
    assert second_part["mediaBudgetStage"] == "image"
    with sqlite3.connect(rate_limit_path) as connection:
        counter = connection.execute(
            """
            SELECT event_count
            FROM rate_limit_counters
            WHERE bucket = ? AND user_id = ?
            """,
            (
                telegram_push_service.BACKGROUND_STORY_PAID_MEDIA_BUDGET_BUCKET,
                telegram_push_service.BACKGROUND_STORY_PAID_MEDIA_BUDGET_USER_ID,
            ),
        ).fetchone()
    assert counter == (1,)
    assert telegram_push_service._run_due_background_stories().attempted == 0


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
        scheduled_background_story_paid_media_daily_cap=100,
        rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
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
        "reserve_full_story_part_image_bytes",
        _reserved(fake_generate_image),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "_persist_background_story_image",
        lambda _record, _bytes, *, generated_at: f"/story-{generated_at.hour}.png",
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(lambda image_bytes: b"mp4-" + image_bytes),
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
        scheduled_background_story_paid_media_daily_cap=100,
        rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
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
        "reserve_full_story_part_image_bytes",
        _reserved(lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("image unavailable"))),
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


@pytest.mark.parametrize("persisted_video", [False, True])
def test_automatic_full_story_recovers_media_saved_before_checkpoint(
    monkeypatch,
    tmp_path,
    persisted_video,
) -> None:
    now = datetime(2026, 7, 12, 6, 0, tzinfo=UTC)
    settings = SimpleNamespace(
        telegram_push_store_path=str(tmp_path / "push.json"),
        telegram_daily_push_default_timezone="Europe/Moscow",
        background_story_enabled=True,
        background_story_hours=[9, 13, 17, 21],
        background_story_window_minutes=120,
        scheduled_background_story_paid_media_daily_cap=0 if persisted_video else 100,
        rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
        bot_token="bot-token",
        webapp_url="https://example.com/app",
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: now)
    monkeypatch.setattr(
        telegram_push_service,
        "generated_dir_for",
        lambda pet_id: tmp_path / "generated" / str(pet_id),
    )
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    telegram_push_service.mark_chat_started(chat_id=TEST_TELEGRAM_ID)
    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    story = {
        "overallTitle": "История после сбоя checkpoint",
        "generatedAt": "2026-07-12T06:00:00Z",
        "localDate": "2026-07-12",
        "parts": [
            {
                "partNumber": number,
                "title": f"Часть {number}",
                "storyText": "Продолжение общей истории.",
                "valence": "positive",
                "statImpacts": [{"stat": "happiness", "amount": 2}],
            }
            for number in range(1, 5)
        ],
    }
    record["dailyFullStory"] = story
    record["lastFullStory"] = story
    telegram_push_service._save_record(record)
    media_time = datetime(2026, 7, 12, 6, 0, tzinfo=UTC)
    telegram_push_service._persist_background_story_image(
        record,
        b"image-saved-before-checkpoint",
        generated_at=media_time,
    )
    if persisted_video:
        telegram_push_service._persist_background_story_video(
            record,
            b"video-saved-before-checkpoint",
            generated_at=media_time,
        )

    image_calls: list[int] = []
    video_calls: list[bytes] = []

    def unexpected_image(**kwargs):
        image_calls.append(kwargs["part"]["partNumber"])
        return b"duplicate-paid-image"

    def synthetic_video(image_bytes: bytes):
        video_calls.append(image_bytes)
        return b"new-video"

    monkeypatch.setattr(
        telegram_push_service,
        "reserve_full_story_part_image_bytes",
        _reserved(unexpected_image),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(synthetic_video),
    )
    sent: list[bytes] = []
    monkeypatch.setattr(
        telegram_push_service,
        "send_video",
        lambda _client, _chat_id, video, _caption, _keyboard: sent.append(video),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_message",
        lambda *_args, **_kwargs: pytest.fail("video delivery was expected"),
    )

    delivered = telegram_push_service.send_due_background_stories()

    assert delivered[0]["partNumber"] == 1
    assert image_calls == []
    assert video_calls == ([] if persisted_video else [b"image-saved-before-checkpoint"])
    assert sent == [b"video-saved-before-checkpoint" if persisted_video else b"new-video"]
    stored = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    stored_part = stored["dailyFullStory"]["parts"][0]
    assert stored_part["imageUrl"]
    assert stored_part["videoUrl"]
    if persisted_video:
        assert not Path(settings.rate_limit_store_path).exists()


def test_background_story_media_namespace_is_owner_bound_and_lossless(
    monkeypatch,
    tmp_path,
) -> None:
    generated_root = tmp_path / "generated"
    monkeypatch.setattr(
        telegram_push_service,
        "generated_dir_for",
        lambda owner_name: generated_root / str(owner_name),
    )

    first_telegram_id = 62943754
    second_telegram_id = 62943755
    first_record = {"telegramId": first_telegram_id, "petId": "shared/pet"}
    first_owner, first_path = telegram_push_service._background_story_output(first_record)
    second_owner, second_path = telegram_push_service._background_story_output(
        {"telegramId": second_telegram_id, "petId": "shared/pet"}
    )
    colliding_legacy_owner, colliding_legacy_path = telegram_push_service._background_story_output(
        {"telegramId": first_telegram_id, "petId": "shared-pet"}
    )
    _target, first_url = telegram_push_service._background_story_media_target(
        first_record,
        generated_at=datetime(2026, 7, 12, 6, 0, tzinfo=UTC),
        suffix=".png",
    )

    assert first_owner.startswith("story-")
    assert second_owner.startswith("story-")
    assert str(first_telegram_id) not in first_owner
    assert str(first_telegram_id) not in first_url
    assert first_path != second_path
    assert first_owner != second_owner
    assert first_path != colliding_legacy_path
    assert first_owner != colliding_legacy_owner


def test_background_story_crash_recovery_cannot_reuse_another_owners_media(
    monkeypatch,
    tmp_path,
) -> None:
    generated_root = tmp_path / "generated"
    monkeypatch.setattr(
        telegram_push_service,
        "generated_dir_for",
        lambda owner_name: generated_root / str(owner_name),
    )
    generated_at = datetime(2026, 7, 12, 6, 0, tzinfo=UTC)
    first = {"telegramId": 111, "petId": "same-pet"}
    second = {"telegramId": 222, "petId": "same-pet"}

    first_url = telegram_push_service._persist_background_story_image(
        first,
        b"first-owner-image",
        generated_at=generated_at,
    )

    assert telegram_push_service._existing_background_story_media(
        first,
        generated_at=generated_at,
        suffix=".png",
    ) == (first_url, b"first-owner-image")
    assert (
        telegram_push_service._existing_background_story_media(
            second,
            generated_at=generated_at,
            suffix=".png",
        )
        is None
    )


def test_background_story_scheduler_recovery_isolates_same_pet_id_owners(
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
        scheduled_background_story_paid_media_daily_cap=100,
        rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
        generated_media_cleanup_enabled=False,
        bot_token="bot-token",
        webapp_url="https://example.com/app",
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: now)
    monkeypatch.setattr(
        telegram_push_service,
        "generated_dir_for",
        lambda owner_name: tmp_path / "generated" / str(owner_name),
    )
    payload = _snapshot_payload().model_copy(update={"petId": "same-pet"})
    story = {
        "overallTitle": "Общий слот, разные владельцы",
        "generatedAt": "2026-07-12T06:00:00Z",
        "localDate": "2026-07-12",
        "parts": [
            {
                "partNumber": number,
                "title": f"Часть {number}",
                "storyText": "Синтетическая история.",
                "valence": "positive",
                "statImpacts": [],
            }
            for number in range(1, 5)
        ],
    }
    records: dict[int, dict[str, object]] = {}
    for telegram_id in (111, 222):
        telegram_push_service.register_push_snapshot(_user_with_id(telegram_id), payload)
        telegram_push_service.mark_chat_started(chat_id=telegram_id)
        record = telegram_push_service._read_store()["records"][str(telegram_id)]
        record["dailyFullStory"] = deepcopy(story)
        record["lastFullStory"] = deepcopy(story)
        telegram_push_service._save_record(record)
        records[telegram_id] = record

    telegram_push_service._persist_background_story_image(
        records[111],
        b"owner-111-image",
        generated_at=now,
    )
    image_calls: list[int] = []

    def generate_fresh_image(**_kwargs):
        image_calls.append(1)
        return b"fresh-owner-222-image"

    monkeypatch.setattr(
        telegram_push_service,
        "reserve_full_story_part_image_bytes",
        _reserved(generate_fresh_image),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(lambda image_bytes: b"video:" + image_bytes),
    )
    sent: dict[int, bytes] = {}
    monkeypatch.setattr(
        telegram_push_service,
        "send_video",
        lambda _client, chat_id, video, _caption, _keyboard: sent.__setitem__(chat_id, video),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_message",
        lambda *_args, **_kwargs: pytest.fail("video delivery was expected"),
    )

    delivered = telegram_push_service.send_due_background_stories()

    assert {item["telegramId"] for item in delivered} == {111, 222}
    assert image_calls == [1]
    assert sent == {
        111: b"video:owner-111-image",
        222: b"video:fresh-owner-222-image",
    }


def test_persisted_background_story_reader_supports_legacy_urls_without_discovery(
    monkeypatch,
    tmp_path,
) -> None:
    generated_root = tmp_path / "generated"
    monkeypatch.setattr(
        telegram_push_service,
        "generated_dir_for",
        lambda owner_name: generated_root / str(owner_name),
    )
    record = {"telegramId": 111, "petId": "legacy/pet"}
    generated_at = datetime(2026, 7, 12, 6, 0, tzinfo=UTC)
    legacy_owner, legacy_dir = telegram_push_service._legacy_background_story_output(record)
    filename = "background-story-20260712T060000000000Z.mp4"
    legacy_path = legacy_dir / filename
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"legacy-video")
    legacy_url = f"/static/generated/{legacy_owner}/{filename}?v=1"

    assert (
        telegram_push_service._persisted_background_story_media_bytes(
            record,
            legacy_url,
            suffix=".mp4",
        )
        == b"legacy-video"
    )
    assert (
        telegram_push_service._existing_background_story_media(
            record,
            generated_at=generated_at,
            suffix=".mp4",
        )
        is None
    )


def test_automatic_full_story_retry_reuses_prepared_media(monkeypatch, tmp_path) -> None:
    current_now = [datetime(2026, 7, 12, 6, 0, tzinfo=UTC)]
    settings = SimpleNamespace(
        telegram_push_store_path=str(tmp_path / "push.json"),
        telegram_daily_push_default_timezone="Europe/Moscow",
        background_story_enabled=True,
        background_story_hours=[9, 13, 17, 21],
        background_story_window_minutes=120,
        scheduled_background_story_paid_media_daily_cap=100,
        rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
        bot_token="bot-token",
        webapp_url="https://example.com/app",
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: current_now[0])
    monkeypatch.setattr(
        telegram_push_service,
        "generated_dir_for",
        lambda pet_id: tmp_path / "generated" / str(pet_id),
    )
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    telegram_push_service.mark_chat_started(chat_id=TEST_TELEGRAM_ID)
    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    story = {
        "overallTitle": "История с повторной доставкой",
        "generatedAt": "2026-07-12T06:00:00Z",
        "localDate": "2026-07-12",
        "parts": [
            {
                "partNumber": number,
                "title": f"Часть {number}",
                "storyText": "Продолжение общей истории.",
                "valence": "positive",
                "statImpacts": [{"stat": "happiness", "amount": 2}],
            }
            for number in range(1, 5)
        ],
    }
    record["dailyFullStory"] = story
    record["lastFullStory"] = story
    telegram_push_service._save_record(record)
    image_calls: list[int] = []
    video_calls: list[bytes] = []

    def fake_image(**kwargs):
        image_calls.append(kwargs["part"]["partNumber"])
        return b"prepared-image"

    def fake_video(image_bytes: bytes):
        video_calls.append(image_bytes)
        return b"prepared-video"

    monkeypatch.setattr(
        telegram_push_service,
        "reserve_full_story_part_image_bytes",
        _reserved(fake_image),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(fake_video),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "send_video",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.ConnectError("telegram unavailable")),
    )

    assert telegram_push_service.send_due_background_stories() == []
    failed = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    failed_part = failed["dailyFullStory"]["parts"][0]
    assert failed_part["videoUrl"]
    assert failed_part["statsAppliedAt"]
    assert "deliveredAt" not in failed_part

    sent: list[bytes] = []
    monkeypatch.setattr(
        telegram_push_service,
        "send_video",
        lambda _client, _chat_id, video, _caption, _keyboard: sent.append(video),
    )
    current_now[0] += timedelta(minutes=15)

    retried = telegram_push_service.send_due_background_stories()

    assert retried[0]["partNumber"] == 1
    assert image_calls == [1]
    assert video_calls == [b"prepared-image"]
    assert sent == [b"prepared-video"]
    delivered = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    assert delivered["dailyFullStory"]["parts"][0]["deliveredAt"]


def test_automatic_full_story_video_retry_reuses_checkpointed_paid_image(
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
        scheduled_background_story_paid_media_daily_cap=100,
        rate_limit_store_path=str(tmp_path / "rate-limits.sqlite3"),
        bot_token="bot-token",
        webapp_url="https://example.com/app",
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(telegram_push_service, "_now", lambda: current_now[0])
    monkeypatch.setattr(
        telegram_push_service,
        "generated_dir_for",
        lambda pet_id: tmp_path / "generated" / str(pet_id),
    )
    telegram_push_service.register_push_snapshot(_user(), _snapshot_payload())
    telegram_push_service.mark_chat_started(chat_id=TEST_TELEGRAM_ID)
    record = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    story = {
        "overallTitle": "История с ошибкой видео",
        "generatedAt": "2026-07-12T06:00:00Z",
        "localDate": "2026-07-12",
        "parts": [
            {
                "partNumber": number,
                "title": f"Часть {number}",
                "storyText": "Продолжение общей истории.",
                "valence": "positive",
                "statImpacts": [{"stat": "happiness", "amount": 2}],
            }
            for number in range(1, 5)
        ],
    }
    record["dailyFullStory"] = story
    record["lastFullStory"] = story
    telegram_push_service._save_record(record)
    image_calls: list[int] = []
    video_calls: list[bytes] = []

    def fake_image(**kwargs):
        image_calls.append(kwargs["part"]["partNumber"])
        return b"paid-image-once"

    def fake_video(image_bytes: bytes):
        video_calls.append(image_bytes)
        if len(video_calls) == 1:
            raise RuntimeError("video provider unavailable")
        return b"prepared-video"

    monkeypatch.setattr(
        telegram_push_service,
        "reserve_full_story_part_image_bytes",
        _reserved(fake_image),
    )
    monkeypatch.setattr(
        telegram_push_service,
        "reserve_background_story_video_bytes",
        _reserved(fake_video),
    )
    sent: list[bytes] = []
    monkeypatch.setattr(
        telegram_push_service,
        "send_video",
        lambda _client, _chat_id, video, _caption, _keyboard: sent.append(video),
    )

    assert telegram_push_service.send_due_background_stories() == []
    failed = telegram_push_service._read_store()["records"][str(TEST_TELEGRAM_ID)]
    failed_part = failed["dailyFullStory"]["parts"][0]
    assert failed_part["imageUrl"]
    assert "videoUrl" not in failed_part
    assert "statsAppliedAt" not in failed_part

    current_now[0] += timedelta(minutes=15)
    retried = telegram_push_service.send_due_background_stories()

    assert retried[0]["partNumber"] == 1
    assert image_calls == [1]
    assert video_calls == [b"paid-image-once", b"paid-image-once"]
    assert sent == [b"prepared-video"]


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


def test_telegram_blocked_user_is_marked_unreachable() -> None:
    request = httpx.Request("POST", "https://api.telegram.org/botredacted/sendMessage")
    response = httpx.Response(
        403,
        request=request,
        json={
            "ok": False,
            "error_code": 403,
            "description": "Forbidden: bot was blocked by the user",
        },
    )

    error = telegram_push_service._telegram_push_error(TelegramAPIError("sendMessage", response))

    assert error.code == "TELEGRAM_CHAT_NOT_FOUND"
    assert "/start" in error.message


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


def test_background_story_gc_preserves_push_and_durable_inbox_references(
    monkeypatch,
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    generated_root = tmp_path / "static" / "generated"
    owner_dir = generated_root / "pet-1"
    owner_dir.mkdir(parents=True)
    push_media = owner_dir / "background-story-20260710T120000000000Z.png"
    inbox_media = owner_dir / "background-story-20260710T120001000000Z.mp4"
    orphan_media = owner_dir / "background-story-20260710T120002000000Z.png"
    for path in (push_media, inbox_media, orphan_media):
        path.write_bytes(b"synthetic")
        timestamp = (now - timedelta(days=10)).timestamp()
        os.utime(path, (timestamp, timestamp))

    push_path = tmp_path / "push.json"
    push_path.write_text(
        json.dumps(
            {
                "version": 1,
                "records": {
                    str(TEST_TELEGRAM_ID): {
                        "telegramId": TEST_TELEGRAM_ID,
                        "petId": "pet-1",
                        "lastStory": {"imageUrl": f"/static/generated/pet-1/{push_media.name}?v=1"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    inbox_path = tmp_path / "bot-inbox.sqlite3"
    with sqlite3.connect(inbox_path) as connection:
        connection.execute("CREATE TABLE bot_command_inbox (prepared_json TEXT)")
        connection.execute(
            "INSERT INTO bot_command_inbox VALUES (?)",
            (
                json.dumps(
                    {
                        "progress": {
                            "video": {"url": f"/static/generated/pet-1/{inbox_media.name}?v=2"}
                        }
                    }
                ),
            ),
        )
    settings = SimpleNamespace(
        telegram_push_store_path=str(push_path),
        bot_command_inbox_path=str(inbox_path),
        storage_health_generated_assets_path=str(generated_root),
        generated_media_cleanup_enabled=True,
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        telegram_push_service,
        "generated_dir_for",
        lambda pet_id: generated_root / str(pet_id),
    )

    telegram_push_service._cleanup_background_story_media_for_records(
        [{"telegramId": TEST_TELEGRAM_ID, "petId": "pet-1"}],
        now=now,
    )

    assert push_media.exists()
    assert inbox_media.exists()
    assert not orphan_media.exists()


def test_background_story_global_gc_never_enters_private_storage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    generated_root = tmp_path / "static" / "generated"
    orphan = generated_root / "deleted-pet" / "background-story-20260710T120000000000Z.mp4"
    grace_protected = (
        generated_root / "recently-deleted-pet" / "background-story-20260708T120000000000Z.png"
    )
    reservation = (
        generated_root
        / ".private"
        / "media-storage-reservations"
        / "background-story-20260710T120000000000Z.mp4"
    )
    for path in (orphan, reservation):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic")
        timestamp = (now - timedelta(days=10)).timestamp()
        os.utime(path, (timestamp, timestamp))
    grace_protected.parent.mkdir(parents=True, exist_ok=True)
    grace_protected.write_bytes(b"synthetic")
    grace_timestamp = (now - timedelta(days=7, hours=23)).timestamp()
    os.utime(grace_protected, (grace_timestamp, grace_timestamp))
    push_path = tmp_path / "push.json"
    push_path.write_text('{"version":1,"records":{}}', encoding="utf-8")
    settings = SimpleNamespace(
        telegram_push_store_path=str(push_path),
        bot_command_inbox_path=str(tmp_path / "missing-inbox.sqlite3"),
        storage_health_generated_assets_path=str(generated_root),
        generated_media_cleanup_enabled=True,
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        telegram_push_service,
        "generated_dir_for",
        lambda pet_id: generated_root / str(pet_id),
    )
    monkeypatch.setattr(telegram_push_service, "_now", lambda: now)

    telegram_push_service._run_generated_media_cleanup()

    assert not orphan.exists()
    assert grace_protected.exists()
    assert reservation.exists()


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


def test_scheduler_start_does_not_create_duplicate_task(monkeypatch) -> None:
    settings = SimpleNamespace(
        telegram_daily_push_enabled=True,
        bot_token="bot-token",
        webapp_url="https://example.com/app",
    )
    monkeypatch.setattr(telegram_push_service, "get_settings", lambda: settings)
    started = 0

    async def fake_loop() -> None:
        nonlocal started
        started += 1
        await asyncio.Event().wait()

    monkeypatch.setattr(telegram_push_service, "_daily_push_loop", fake_loop)

    async def scenario() -> None:
        first = telegram_push_service.start_daily_push_scheduler()
        second = telegram_push_service.start_daily_push_scheduler()
        assert first is not None
        assert second is None
        await asyncio.sleep(0)
        assert started == 1
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert "dailyPush" not in telegram_push_service._scheduler_tasks
