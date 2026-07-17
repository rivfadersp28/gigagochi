from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.config import get_settings
from app.schemas import (
    DebugSavedPetBundle,
    LocalChatHistoryItem,
    LocalPetChatContext,
    LocalPetMemoryContext,
    LocalPetPushSnapshotRequest,
    LocalPetPushSnapshotResponse,
    LocalPetStatsPatch,
    LocalPushRequest,
)
from app.services.background_story_paid_media_budget import (
    BACKGROUND_STORY_PAID_MEDIA_BUDGET_BUCKET,
    BackgroundStoryPaidMediaBudgetError,
    consume_background_story_paid_media_budget,
)
from app.services.background_story_service import (
    BackgroundStoryResult,
    generate_background_story,
    reserve_background_story_image_bytes,
    reserve_background_story_video_bytes,
)
from app.services.full_story_service import (
    FullStoryPart,
    FullStoryResult,
    generate_full_story,
    reserve_full_story_part_image_bytes,
)
from app.services.generated_media_cleanup import (
    cleanup_stale_generated_processing_temp_directories,
    cleanup_unreferenced_background_story_media,
    generated_media_cleanup_is_enabled,
)
from app.services.image_service import generated_dir_for
from app.services.interactive_travel_service import (
    scheduled_interactive_episode_correct_choice,
    scheduled_interactive_episode_result,
)
from app.services.lite_overlay import merge_lite_overlay_patch, normalize_lite_overlay_patch
from app.services.pet_reply_engine.lite_generator import generate_push_pet_message
from app.services.scheduled_short_story_service import (
    generate_scheduled_short_story_episode,
    run_scheduled_short_story_provider_job,
    scheduled_short_story_provider_error_is_retryable,
)
from app.services.story_delivery_format import (
    format_full_story_part_message,
)
from app.services.telegram_auth_service import TelegramUserContext
from app.services.telegram_client import (
    TelegramAPIError,
    mini_app_keyboard,
    redact_telegram_token,
    send_message,
    send_photo,
    send_video,
)
from app.services.telegram_push_store import (
    DEFAULT_PUSH_RECORD_MAX_BYTES,
    DEFAULT_PUSH_STORE_MAX_BYTES,
    DEFAULT_PUSH_STORE_MAX_RECORDS,
    DEFAULT_PUSH_UNREACHABLE_RETENTION_SECONDS,
    TelegramPushStore,
    create_telegram_push_store,
)

STORE_VERSION = 1
STAT_KEYS = ("hunger", "happiness", "energy")
STAT_FULL_DECAY_HOURS = 24
STAT_DECAY_PER_HOUR = 100 / STAT_FULL_DECAY_HOURS
PET_DEATH_AFTER_ZERO = timedelta(hours=24)
DAILY_PUSH_REASON = "Ежедневный короткий пуш владельцу от питомца."
MANUAL_PUSH_REASON = "Ручной debug-триггер из админки."
MAX_RECENT_STORY_EVENTS = 10
PUSH_STORY_MAX_AGE = timedelta(hours=12)
MAX_STORY_NOVELTY_HISTORY = 400
MAX_FULL_STORY_HISTORY = 8
MAX_BOT_GENERATION_RECEIPTS = 16
MAX_AUTOMATIC_INTERACTIVE_STORIES = 12
SCHEDULED_SHORT_STORY_PROVIDER_MAX_ATTEMPTS = 3
SCHEDULED_SHORT_STORY_PROVIDER_RETRY_DELAYS_SECONDS = (15.0, 45.0)
MAX_PERSISTED_ERROR_CHARS = 2_000
SNAPSHOT_EPOCH_REVISION_FLOOR = 1_000_000_000_000
STORY_STAT_MAX_ITEMS = 2
STORY_STAT_MAX_SINGLE_DAMAGE = 25
STORY_STAT_MAX_TOTAL_DAMAGE = 35
DEBUG_SAVED_PET_SLOT_KEY = "debugSavedPetSlot"
_DEBUG_SAVED_SERVER_RECORD_KEYS = (
    "telegramId",
    "chatId",
    "username",
    "firstName",
    "languageCode",
    "petId",
    "pet",
    "history",
    "recentAmbientReplies",
    "memoryContext",
    "createdAt",
    "updatedAt",
    "lastStatsTickAt",
    "lastStatTickAt",
    "zeroStatSinceAt",
    "diedAt",
    "deathTrackingEnabled",
    "timezone",
    "registeredAt",
    "snapshotWriterId",
    "snapshotRevision",
    "recentStoryEvents",
)

logger = logging.getLogger(__name__)

DEFAULT_DAILY_PUSH_HOURS = (9, 15, 21)
DEFAULT_DAILY_PUSH_WINDOW_MINUTES = 120
DEFAULT_PUSH_TIMEZONE = "Europe/Moscow"
DEFAULT_BACKGROUND_STORY_HOURS = (9, 13, 17, 21)
DEFAULT_BACKGROUND_STORY_WINDOW_MINUTES = 120
DAILY_FULL_STORY_RETRY_DELAY = timedelta(minutes=15)
DAILY_FULL_STORY_MAX_ATTEMPTS = 2
_BackgroundStoryPaidMediaBudgetError = BackgroundStoryPaidMediaBudgetError
BACKGROUND_STORY_MEDIA_GC_MIN_AGE = timedelta(days=8)
GENERATED_MEDIA_CLEANUP_LOOP_INTERVAL_SECONDS = 6 * 60 * 60
SCHEDULER_LEADERSHIP_RETRY_SECONDS = 5.0
SCHEDULER_LOCK_NAMES = {
    "dailyPush": "daily-push",
    "backgroundStory": "background-story",
    "scheduledShortStory": "scheduled-short-story",
    "generatedMediaCleanup": "generated-media-cleanup",
}
_background_story_media_gc_lock = Lock()


class TelegramPushError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _safe_error_message(value: object) -> str:
    settings = get_settings()
    redacted = redact_telegram_token(value, getattr(settings, "bot_token", None))
    compact = " ".join(redacted.split())
    return compact[:MAX_PERSISTED_ERROR_CHARS]


@dataclass(frozen=True, slots=True)
class _SchedulerBatchResult:
    results: list[dict[str, Any]]
    attempted: int
    failed: int
    health_failed: int
    last_error: str | None

    @property
    def succeeded(self) -> int:
        return len(self.results)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _latest_time(*values: Any) -> datetime | None:
    parsed_values = [_parse_iso(value) for value in values]
    valid_values = [value for value in parsed_values if value is not None]
    return max(valid_values) if valid_values else None


def _daily_push_hours(settings: Any) -> tuple[int, ...]:
    raw_hours = getattr(settings, "telegram_daily_push_hours", DEFAULT_DAILY_PUSH_HOURS)
    if not isinstance(raw_hours, (list, tuple, set)):
        raw_hours = DEFAULT_DAILY_PUSH_HOURS
    hours: set[int] = set()
    for raw_hour in raw_hours:
        try:
            hour = int(raw_hour)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 23:
            hours.add(hour)
    return tuple(sorted(hours))[:3] or DEFAULT_DAILY_PUSH_HOURS


def _background_story_hours(settings: Any) -> tuple[int, ...]:
    raw_hours = getattr(settings, "background_story_hours", DEFAULT_BACKGROUND_STORY_HOURS)
    if not isinstance(raw_hours, (list, tuple, set)):
        return DEFAULT_BACKGROUND_STORY_HOURS
    hours: list[int] = []
    for raw_hour in raw_hours:
        try:
            hour = int(raw_hour)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 23 and hour not in hours:
            hours.append(hour)
    return tuple(sorted(hours)) if len(hours) == 4 else DEFAULT_BACKGROUND_STORY_HOURS


def _day_period(hour: int) -> str:
    if hour < 12:
        return "утро"
    if hour < 17:
        return "день"
    if hour < 21:
        return "вечер"
    return "ночь"


def _background_story_slot(
    record: dict[str, Any],
    now: datetime,
) -> tuple[int, datetime, str] | None:
    settings = get_settings()
    timezone = _push_timezone(record, settings)
    local_now = now.astimezone(timezone)
    window_minutes = max(
        5,
        min(
            180,
            int(
                getattr(
                    settings,
                    "background_story_window_minutes",
                    DEFAULT_BACKGROUND_STORY_WINDOW_MINUTES,
                )
            ),
        ),
    )
    for index, hour in reversed(list(enumerate(_background_story_hours(settings)))):
        local_slot = local_now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if local_slot <= local_now < local_slot + timedelta(minutes=window_minutes):
            return index, local_slot, getattr(timezone, "key", str(timezone))
    return None


def _push_timezone(record: dict[str, Any], settings: Any) -> ZoneInfo:
    fallback_name = str(
        getattr(settings, "telegram_daily_push_default_timezone", DEFAULT_PUSH_TIMEZONE)
    )
    timezone_name = record.get("timezone")
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        timezone_name = fallback_name
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        try:
            return ZoneInfo(fallback_name)
        except ZoneInfoNotFoundError:
            return ZoneInfo("UTC")


def _scheduled_push_slot(record: dict[str, Any], now: datetime) -> datetime | None:
    settings = get_settings()
    timezone = _push_timezone(record, settings)
    local_now = now.astimezone(timezone)
    window_minutes = max(
        5,
        min(
            180,
            int(
                getattr(
                    settings,
                    "telegram_daily_push_window_minutes",
                    DEFAULT_DAILY_PUSH_WINDOW_MINUTES,
                )
            ),
        ),
    )
    eligible_after = _latest_time(
        record.get("registeredAt"),
        record.get("chatStartedAt"),
        record.get("lastPushAt"),
        record.get("lastPushAttemptAt"),
    )
    for hour in reversed(_daily_push_hours(settings)):
        local_slot = local_now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if not (local_slot <= local_now < local_slot + timedelta(minutes=window_minutes)):
            continue
        slot = local_slot.astimezone(UTC)
        return slot if eligible_after is None or slot > eligible_after else None
    return None


def _store_path() -> Path:
    path = Path(get_settings().telegram_push_store_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _push_store() -> TelegramPushStore:
    settings = get_settings()
    legacy_json_path_value = getattr(settings, "telegram_push_legacy_json_path", None)
    legacy_json_path = None
    if isinstance(legacy_json_path_value, str) and legacy_json_path_value.strip():
        legacy_json_path = Path(legacy_json_path_value).expanduser()
        if not legacy_json_path.is_absolute():
            legacy_json_path = Path.cwd() / legacy_json_path
    return create_telegram_push_store(
        _store_path(),
        version=STORE_VERSION,
        backend=getattr(settings, "telegram_push_store_backend", "auto"),
        record_max_bytes=getattr(
            settings,
            "telegram_push_record_max_bytes",
            DEFAULT_PUSH_RECORD_MAX_BYTES,
        ),
        store_max_bytes=getattr(
            settings,
            "telegram_push_store_max_bytes",
            DEFAULT_PUSH_STORE_MAX_BYTES,
        ),
        store_max_records=getattr(
            settings,
            "telegram_push_store_max_records",
            DEFAULT_PUSH_STORE_MAX_RECORDS,
        ),
        unreachable_retention_seconds=getattr(
            settings,
            "telegram_push_unreachable_retention_seconds",
            DEFAULT_PUSH_UNREACHABLE_RETENTION_SECONDS,
        ),
        legacy_json_path=legacy_json_path,
        legacy_json_required=getattr(
            settings,
            "telegram_push_legacy_json_required",
            legacy_json_path is not None,
        ),
    )


def _read_store() -> dict[str, Any]:
    return _push_store().read()


def _save_record(record: dict[str, Any]) -> None:
    _push_store().replace_record(record)


def _update_record(
    telegram_id: int,
    updater: Callable[[dict[str, Any] | None], dict[str, Any]],
) -> dict[str, Any]:
    return _push_store().update_record(telegram_id, updater)


def request_pet_reset(telegram_id: int) -> dict[str, Any]:
    """Delete server-side pet data and request a one-time client reset for that pet."""

    now_iso = _iso()

    def reset_record(existing: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(existing, dict):
            raise TelegramPushError("SNAPSHOT_NOT_FOUND", "Snapshot пользователя не найден.")
        pet_id = existing.get("petId")
        if not isinstance(pet_id, str) or not pet_id.strip():
            raise TelegramPushError("SNAPSHOT_NOT_FOUND", "Персонаж пользователя не найден.")

        retained_keys = (
            "telegramId",
            "chatId",
            "username",
            "firstName",
            "languageCode",
            "chatStartedAt",
            "lastChatSeenAt",
            "chatReachable",
            DEBUG_SAVED_PET_SLOT_KEY,
        )
        record = {key: deepcopy(existing.get(key)) for key in retained_keys if key in existing}
        record["petResetRequest"] = {"petId": pet_id, "requestedAt": now_iso}
        return record

    result = _update_record(telegram_id, reset_record)
    reset_request = result.get("petResetRequest")
    reset_pet_id = reset_request.get("petId") if isinstance(reset_request, dict) else None
    if isinstance(reset_pet_id, str):
        _cleanup_background_story_media_for_records(
            [{"telegramId": telegram_id, "petId": reset_pet_id}]
        )
    return result


def _pet_reset_tombstones(record: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(record, dict):
        return []
    raw_tombstones = record.get("petResetTombstones")
    if not isinstance(raw_tombstones, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_tombstones:
        if not isinstance(item, dict):
            continue
        raw_pet_id = item.get("petId")
        pet_id = raw_pet_id.strip() if isinstance(raw_pet_id, str) else raw_pet_id
        requested_at = item.get("requestedAt")
        if (
            not isinstance(pet_id, str)
            or not 0 < len(pet_id) <= 120
            or pet_id in seen
            or not isinstance(requested_at, str)
            or not requested_at
        ):
            continue
        seen.add(pet_id)
        result.append({"petId": pet_id, "requestedAt": requested_at[:80]})
    return result


def save_debug_pet_slot(
    telegram_id: int,
    bundle: DebugSavedPetBundle,
) -> tuple[bool, DebugSavedPetBundle]:
    """Persist one immutable diagnostic pet slot beside the active snapshot."""

    created = False
    saved_bundle = bundle

    def save(existing: dict[str, Any] | None) -> dict[str, Any]:
        nonlocal created, saved_bundle
        record = deepcopy(existing) if isinstance(existing, dict) else {"telegramId": telegram_id}
        existing_slot = record.get(DEBUG_SAVED_PET_SLOT_KEY)
        if isinstance(existing_slot, dict):
            try:
                saved_bundle = DebugSavedPetBundle.model_validate(existing_slot.get("bundle"))
                return record
            except ValueError:
                pass

        server_record = {
            key: deepcopy(record.get(key))
            for key in _DEBUG_SAVED_SERVER_RECORD_KEYS
            if key in record
        }
        record[DEBUG_SAVED_PET_SLOT_KEY] = {
            "savedAt": _iso(),
            "petId": bundle.petId,
            "bundle": bundle.model_dump(mode="json"),
            "serverRecord": server_record,
        }
        created = True
        saved_bundle = bundle
        return record

    _update_record(telegram_id, save)
    return created, saved_bundle


def activate_debug_pet_slot(telegram_id: int) -> DebugSavedPetBundle:
    """Make the immutable diagnostic slot authoritative and fence the replaced pet."""

    activated_bundle: DebugSavedPetBundle | None = None

    def activate(existing: dict[str, Any] | None) -> dict[str, Any]:
        nonlocal activated_bundle
        if not isinstance(existing, dict):
            raise TelegramPushError("SAVED_PET_NOT_FOUND", "Сохранённый персонаж не найден.")
        slot = existing.get(DEBUG_SAVED_PET_SLOT_KEY)
        if not isinstance(slot, dict):
            raise TelegramPushError("SAVED_PET_NOT_FOUND", "Сохранённый персонаж не найден.")
        try:
            activated_bundle = DebugSavedPetBundle.model_validate(slot.get("bundle"))
        except ValueError as exc:
            raise TelegramPushError(
                "SAVED_PET_INVALID",
                "Сохранённый персонаж повреждён.",
            ) from exc

        raw_server_record = slot.get("serverRecord")
        record = deepcopy(raw_server_record) if isinstance(raw_server_record, dict) else {}
        for key in (
            "telegramId",
            "chatId",
            "username",
            "firstName",
            "languageCode",
            "chatStartedAt",
            "lastChatSeenAt",
            "chatReachable",
        ):
            if key in existing:
                record[key] = deepcopy(existing.get(key))

        restored_pet_id = activated_bundle.petId
        current_pet_id = existing.get("petId")
        tombstones = [
            item for item in _pet_reset_tombstones(existing) if item["petId"] != restored_pet_id
        ]
        if (
            isinstance(current_pet_id, str)
            and current_pet_id.strip()
            and current_pet_id.strip() != restored_pet_id
            and not any(item["petId"] == current_pet_id.strip() for item in tombstones)
        ):
            tombstones.append({"petId": current_pet_id.strip(), "requestedAt": _iso()})
        if tombstones:
            record["petResetTombstones"] = tombstones
        else:
            record.pop("petResetTombstones", None)
        record.pop("petResetRequest", None)
        record[DEBUG_SAVED_PET_SLOT_KEY] = deepcopy(slot)
        record["registeredAt"] = _iso()
        return record

    _update_record(telegram_id, activate)
    if activated_bundle is None:
        raise TelegramPushError("SAVED_PET_NOT_FOUND", "Сохранённый персонаж не найден.")
    return activated_bundle


def unregister_push_snapshot(telegram_id: int, pet_id: str) -> bool:
    """Remove one pet snapshot and fence late in-flight writes for that pet."""

    normalized_pet_id = pet_id.strip()
    if not 0 < len(normalized_pet_id) <= 120:
        raise ValueError("pet_id must contain between 1 and 120 characters")
    unregistered = False
    now_iso = _iso()

    def unregister(existing: dict[str, Any] | None) -> dict[str, Any]:
        nonlocal unregistered
        tombstones = _pet_reset_tombstones(existing)
        if any(item["petId"] == normalized_pet_id for item in tombstones):
            unregistered = True
            return (
                deepcopy(existing)
                if isinstance(existing, dict)
                else {
                    "telegramId": telegram_id,
                    "petResetTombstones": tombstones,
                }
            )

        tombstones.append({"petId": normalized_pet_id, "requestedAt": now_iso})
        existing_pet_id = existing.get("petId") if isinstance(existing, dict) else None
        existing_pet_id = (
            existing_pet_id.strip() if isinstance(existing_pet_id, str) else existing_pet_id
        )
        if isinstance(existing, dict) and existing_pet_id != normalized_pet_id:
            record = deepcopy(existing)
            record["petResetTombstones"] = tombstones
            unregistered = True
            return record

        retained_keys = (
            "telegramId",
            "chatId",
            "username",
            "firstName",
            "languageCode",
            "chatStartedAt",
            "lastChatSeenAt",
            "chatReachable",
            DEBUG_SAVED_PET_SLOT_KEY,
        )
        record = {
            key: deepcopy(existing.get(key))
            for key in retained_keys
            if isinstance(existing, dict) and key in existing
        }
        record["telegramId"] = telegram_id
        record["petResetTombstones"] = tombstones
        unregistered = True
        return record

    _update_record(telegram_id, unregister)
    _cleanup_background_story_media_for_records(
        [{"telegramId": telegram_id, "petId": normalized_pet_id}]
    )
    return unregistered


def _merge_character_bible(
    existing: Any,
    incoming: Any,
) -> dict[str, Any] | None:
    existing_record = existing if isinstance(existing, dict) else {}
    incoming_record = incoming if isinstance(incoming, dict) else {}
    if not existing_record and not incoming_record:
        return None

    # Old clients sent only `extensions.lite_overlay`. Keep their generated base
    # bible, but never recursively union arbitrary client extension keys. Modern
    # full bibles are authoritative and replace the previous client-owned shape.
    is_legacy_overlay_only = not incoming_record or set(incoming_record) <= {"extensions"}
    result = deepcopy(existing_record if is_legacy_overlay_only else incoming_record)

    incoming_extensions = (
        incoming_record.get("extensions")
        if isinstance(incoming_record.get("extensions"), dict)
        else {}
    )
    existing_extensions = (
        existing_record.get("extensions")
        if isinstance(existing_record.get("extensions"), dict)
        else {}
    )
    incoming_overlay = normalize_lite_overlay_patch(incoming_extensions.get("lite_overlay"))
    existing_overlay = normalize_lite_overlay_patch(existing_extensions.get("lite_overlay"))
    merged_overlay: dict[str, Any] = {}
    merge_lite_overlay_patch(merged_overlay, incoming_overlay)
    # The persisted copy contains server-authored background-story facts. Merge it
    # last so a stale/local snapshot cannot erase that server-owned state.
    merge_lite_overlay_patch(merged_overlay, existing_overlay)

    result_extensions = result.get("extensions")
    if not isinstance(result_extensions, dict):
        result_extensions = {}
    else:
        result_extensions = deepcopy(result_extensions)
    if merged_overlay:
        result_extensions["lite_overlay"] = merged_overlay
    else:
        result_extensions.pop("lite_overlay", None)
    if result_extensions:
        result["extensions"] = result_extensions
    else:
        result.pop("extensions", None)
    return result or None


def _preserve_pet_character_bible(
    incoming_pet: dict[str, Any],
    existing_pet: Any,
) -> dict[str, Any]:
    existing_record = existing_pet if isinstance(existing_pet, dict) else {}
    merged_bible = _merge_character_bible(
        existing_record.get("characterBible"),
        incoming_pet.get("characterBible"),
    )
    result = deepcopy(incoming_pet)
    if merged_bible:
        result["characterBible"] = merged_bible
    return result


def _telegram_id_from_record(record: dict[str, Any]) -> int:
    telegram_id = record.get("telegramId")
    if not isinstance(telegram_id, int):
        raise TelegramPushError("PUSH_TELEGRAM_ID_MISSING", "Telegram ID в push record не найден.")
    return telegram_id


def _record_lite_overlay_patch(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    pet = record.get("pet") if isinstance(record.get("pet"), dict) else {}
    bible = pet.get("characterBible") if isinstance(pet.get("characterBible"), dict) else {}
    extensions = bible.get("extensions") if isinstance(bible.get("extensions"), dict) else {}
    overlay = (
        extensions.get("lite_overlay") if isinstance(extensions.get("lite_overlay"), dict) else {}
    )
    if not isinstance(overlay, dict):
        return None
    patch = normalize_lite_overlay_patch(overlay)
    return patch or None


def _merge_record_lite_overlay_patch(
    record: dict[str, Any],
    patch: dict[str, Any] | None,
) -> None:
    if not isinstance(patch, dict):
        return
    pet = record.setdefault("pet", {})
    if not isinstance(pet, dict):
        pet = {}
        record["pet"] = pet
    bible = pet.setdefault("characterBible", {})
    if not isinstance(bible, dict):
        bible = {}
        pet["characterBible"] = bible
    extensions = bible.setdefault("extensions", {})
    if not isinstance(extensions, dict):
        extensions = {}
        bible["extensions"] = extensions
    overlay = normalize_lite_overlay_patch(extensions.get("lite_overlay"))
    merge_lite_overlay_patch(overlay, patch)
    if overlay:
        extensions["lite_overlay"] = overlay
    else:
        extensions.pop("lite_overlay", None)


def _compact_event_text(value: Any, *, limit: int = 500) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())[:limit].rstrip()


def _event_string_list(value: Any, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _compact_event_text(item, limit=item_limit)
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _event_status_changes(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        entity = _compact_event_text(item.get("entity"), limit=120)
        state = _compact_event_text(item.get("state"), limit=80)
        owner = _compact_event_text(item.get("owner"), limit=120)
        if entity and state:
            result.append({"entity": entity, "state": state, "owner": owner})
        if len(result) >= 5:
            break
    return result


def _event_stat_impacts(value: Any, *, legacy: Any = None) -> list[dict[str, Any]]:
    source = value if isinstance(value, list) else ([legacy] if isinstance(legacy, dict) else [])
    result: list[dict[str, Any]] = []
    for item in source:
        if not isinstance(item, dict):
            continue
        stat = item.get("stat")
        if stat not in STAT_KEYS:
            continue
        try:
            amount = float(item.get("amount"))
        except (TypeError, ValueError):
            continue
        if amount == 0:
            continue
        magnitude = max(1, min(STORY_STAT_MAX_SINGLE_DAMAGE, round(abs(amount))))
        result.append(
            {
                "stat": stat,
                "amount": magnitude if amount > 0 else -magnitude,
                "reason": _compact_event_text(item.get("reason"), limit=280),
            }
        )
        if len(result) >= STORY_STAT_MAX_ITEMS:
            break
    return result


def _normalize_recent_story_event_record(
    item: dict[str, Any],
    *,
    last_story: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    source_story = last_story if isinstance(last_story, dict) else {}
    summary = (
        _compact_event_text(item.get("summary"), limit=500)
        or _compact_event_text(item.get("compactText"), limit=500)
        or _compact_event_text(item.get("storyText"), limit=500)
        or _compact_event_text(source_story.get("storyText"), limit=500)
        or _compact_event_text(item.get("title") or source_story.get("title"), limit=120)
    )
    if not summary:
        return None
    compact_text = _compact_event_text(item.get("compactText"), limit=500) or summary
    actions = _event_string_list(item.get("actions"), limit=6, item_limit=80)
    outcome = _compact_event_text(item.get("outcome"), limit=260)
    canonical_facts = _event_string_list(item.get("canonicalFacts"), limit=5, item_limit=180)
    if not canonical_facts:
        canonical_facts = [fact for fact in [*actions[:4], outcome] if fact][:5]
    event = {
        **item,
        "title": _compact_event_text(item.get("title") or source_story.get("title"), limit=120),
        "summary": summary,
        "compactText": compact_text,
        "eventType": _compact_event_text(
            item.get("eventType") or source_story.get("eventType"),
            limit=60,
        ),
        "valence": _compact_event_text(
            item.get("valence") or source_story.get("valence"),
            limit=20,
        ),
        "participants": _event_string_list(item.get("participants"), limit=6, item_limit=80),
        "actions": actions,
        "objects": _event_string_list(item.get("objects"), limit=6, item_limit=80),
        "location": _compact_event_text(item.get("location"), limit=160),
        "outcome": outcome,
        "canonicalFacts": canonical_facts,
        "statusChanges": _event_status_changes(item.get("statusChanges")),
        "statImpacts": _event_stat_impacts(
            item.get("statImpacts") or source_story.get("statImpacts"),
            legacy=item.get("statImpact") or source_story.get("statImpact"),
        ),
        "tags": _event_string_list(
            item.get("tags") or source_story.get("tags"),
            limit=8,
            item_limit=60,
        ),
        "createdAt": _compact_event_text(item.get("createdAt"), limit=80) or _iso(),
        "source": _compact_event_text(item.get("source"), limit=80) or "background_story",
    }
    return {key: value for key, value in event.items() if value not in (None, "", [], {})}


def _record_recent_story_events(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(record, dict):
        return []
    raw_events = record.get("recentStoryEvents")
    events: list[dict[str, Any]] = []
    last_story = record.get("lastStory") if isinstance(record.get("lastStory"), dict) else None
    if isinstance(raw_events, list):
        for item in raw_events[-MAX_RECENT_STORY_EVENTS:]:
            if isinstance(item, dict):
                event = _normalize_recent_story_event_record(item, last_story=last_story)
                if event:
                    events.append(event)
    if events:
        return events
    if isinstance(last_story, dict):
        summary = (
            last_story.get("summary") or last_story.get("storyText") or last_story.get("title")
        )
        if isinstance(summary, str) and summary.strip():
            fallback = _normalize_recent_story_event_record(
                {
                    "title": last_story.get("title"),
                    "summary": summary.strip(),
                    "storyText": last_story.get("storyText"),
                    "imageUrl": last_story.get("imageUrl"),
                    "generatedAt": last_story.get("generatedAt"),
                    "createdAt": last_story.get("generatedAt") or record.get("lastStoryAt"),
                    "eventType": last_story.get("eventType"),
                    "tags": (
                        last_story.get("tags") if isinstance(last_story.get("tags"), list) else []
                    ),
                    "source": "last_story_fallback",
                },
                last_story=last_story,
            )
            return [fallback] if fallback else []
    return events


def _latest_fresh_story_event(
    record: dict[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for event in _record_recent_story_events(record):
        created_at = _parse_iso(event.get("createdAt"))
        if created_at is None or created_at > now:
            continue
        candidates.append((created_at, event))
    if not candidates:
        return None
    created_at, event = max(candidates, key=lambda item: item[0])
    if now - created_at > PUSH_STORY_MAX_AGE:
        return None
    return event


def _append_recent_story_event(
    record: dict[str, Any],
    event: dict[str, Any] | None,
    *,
    last_story: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    events = _record_recent_story_events(record)
    if isinstance(event, dict) and event:
        normalized_event = _normalize_recent_story_event_record(event, last_story=last_story)
        if normalized_event:
            events.append(normalized_event)
    return events[-MAX_RECENT_STORY_EVENTS:]


def _compact_story_novelty_item(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    title = _compact_event_text(value.get("title"), limit=120)
    tags = _event_string_list(value.get("tags"), limit=8, item_limit=60)
    if not title and not tags:
        return None
    structure = {
        field: _compact_event_text(value.get(field), limit=80)
        for field in (
            "plotMode",
            "incidentClass",
            "causalOrigin",
            "eventScale",
            "settingClass",
            "oppositionClass",
            "resolutionMode",
            "resolutionFamily",
            "valenceTarget",
            "arcVariant",
            "antagonistClass",
            "wonderClass",
            "locationClass",
        )
    }
    return {
        **({"id": str(value.get("id"))[:120]} if value.get("id") else {}),
        "title": title,
        "tags": tags,
        **{key: item for key, item in structure.items() if item},
        "createdAt": _compact_event_text(
            value.get("generatedAt") or value.get("createdAt"),
            limit=80,
        )
        or _iso(),
    }


def _record_story_novelty_history(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(record, dict):
        return []
    raw_history = record.get("storyNoveltyHistory")
    candidates = raw_history if isinstance(raw_history, list) else []
    candidates = [*candidates, *_record_recent_story_events(record)]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in candidates[-MAX_STORY_NOVELTY_HISTORY:]:
        item = _compact_story_novelty_item(value)
        if not item:
            continue
        key = str(item.get("id") or f"{item['createdAt']}:{item['title']}")
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[-MAX_STORY_NOVELTY_HISTORY:]


def _story_novelty_tokens(title: Any, tags: Any) -> set[str]:
    text = " ".join(
        [
            _compact_event_text(title, limit=120),
            *_event_string_list(tags, limit=8, item_limit=60),
        ]
    ).casefold()
    return set(re.findall(r"[0-9a-zа-яё]{3,}", text, flags=re.IGNORECASE))


def _story_is_lexical_duplicate(story: Any, history: list[dict[str, Any]]) -> bool:
    title = _compact_event_text(getattr(story, "title", None), limit=120).casefold()
    tokens = _story_novelty_tokens(title, getattr(story, "tags", ()))
    for item in history:
        previous_title = _compact_event_text(item.get("title"), limit=120).casefold()
        if title and title == previous_title:
            return True
        previous_tokens = _story_novelty_tokens(previous_title, item.get("tags"))
        union = tokens | previous_tokens
        if len(union) >= 3 and len(tokens & previous_tokens) / len(union) >= 0.55:
            return True
    return False


def _append_story_novelty_item(
    record: dict[str, Any],
    event: dict[str, Any],
) -> list[dict[str, Any]]:
    history = _record_story_novelty_history(record)
    item = _compact_story_novelty_item(event)
    if item:
        history.append(item)
    return history[-MAX_STORY_NOVELTY_HISTORY:]


def _compact_full_story_history_item(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    title = _compact_event_text(value.get("overallTitle"), limit=120)
    raw_plan = value.get("arcPlan") if isinstance(value.get("arcPlan"), dict) else {}
    goal = _compact_event_text(value.get("goal") or raw_plan.get("goal"), limit=240)
    raw_direction = (
        value.get("storyDirection") if isinstance(value.get("storyDirection"), dict) else value
    )
    direction = {
        field: _compact_event_text(raw_direction.get(field), limit=80)
        for field in (
            "plotMode",
            "incidentClass",
            "causalOrigin",
            "eventScale",
            "settingClass",
            "oppositionClass",
            "resolutionMode",
            "resolutionFamily",
            "valenceTarget",
        )
    }
    if not title and not goal:
        return None
    return {
        "overallTitle": title,
        "goal": goal,
        **{key: item for key, item in direction.items() if item},
        "generatedAt": _compact_event_text(value.get("generatedAt"), limit=80) or _iso(),
    }


def _record_full_story_history(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(record, dict):
        return []
    raw_history = record.get("fullStoryHistory")
    candidates = list(raw_history) if isinstance(raw_history, list) else []
    if isinstance(record.get("lastFullStory"), dict):
        candidates.append(record["lastFullStory"])
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in candidates[-(MAX_FULL_STORY_HISTORY + 1) :]:
        item = _compact_full_story_history_item(value)
        if not item:
            continue
        key = f"{item['generatedAt']}:{item['overallTitle']}"
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[-MAX_FULL_STORY_HISTORY:]


def _append_full_story_history(
    record: dict[str, Any],
    story: dict[str, Any],
) -> list[dict[str, Any]]:
    history = _record_full_story_history(record)
    item = _compact_full_story_history_item(story)
    if item:
        history.append(item)
    return history[-MAX_FULL_STORY_HISTORY:]


def _record_bot_generation_receipts(record: dict[str, Any]) -> list[dict[str, Any]]:
    raw_receipts = record.get("botGenerationReceipts")
    if not isinstance(raw_receipts, list):
        return []
    return [
        deepcopy(item)
        for item in raw_receipts[-MAX_BOT_GENERATION_RECEIPTS:]
        if isinstance(item, dict)
        and isinstance(item.get("requestKey"), str)
        and item.get("kind") in {"full_story", "story"}
        and isinstance(item.get("story"), dict)
    ]


def _bot_generation_receipt(
    record: dict[str, Any],
    *,
    request_key: str | None,
    kind: str,
) -> dict[str, Any] | None:
    if not request_key:
        return None
    return next(
        (
            receipt
            for receipt in reversed(_record_bot_generation_receipts(record))
            if receipt.get("requestKey") == request_key and receipt.get("kind") == kind
        ),
        None,
    )


def _append_bot_generation_receipt(
    record: dict[str, Any],
    receipt: dict[str, Any],
) -> list[dict[str, Any]]:
    request_key = receipt.get("requestKey")
    kind = receipt.get("kind")
    receipts = [
        item
        for item in _record_bot_generation_receipts(record)
        if item.get("requestKey") != request_key or item.get("kind") != kind
    ]
    receipts.append(deepcopy(receipt))
    return receipts[-MAX_BOT_GENERATION_RECEIPTS:]


def _recent_story_events_patch(record: dict[str, Any] | None) -> dict[str, Any] | None:
    events = _record_recent_story_events(record)
    return {"events": events} if events else None


def _legacy_background_story_output(record: dict[str, Any]) -> tuple[str, Path]:
    """Return the pre-owner-bound path for reading and garbage-collecting old media."""

    raw_pet_id = str(record.get("petId") or record.get("telegramId") or "story")
    safe_pet_id = (
        "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in raw_pet_id
        ).strip("-")[:120]
        or "story"
    )
    return safe_pet_id, generated_dir_for(safe_pet_id)


def _background_story_output(record: dict[str, Any]) -> tuple[str, Path]:
    """Return a collision-resistant media directory bound to one Telegram owner."""

    telegram_id = record.get("telegramId")
    if type(telegram_id) is not int:
        raise ValueError("background-story media requires an integer Telegram owner")
    raw_pet_id = record.get("petId")
    canonical_pet_id = raw_pet_id.strip() if isinstance(raw_pet_id, str) else ""
    if not canonical_pet_id:
        canonical_pet_id = str(telegram_id)
    owner_digest = hashlib.sha256(f"{telegram_id}\0{canonical_pet_id}".encode()).hexdigest()[:32]
    owner_name = f"story-{owner_digest}"
    return owner_name, generated_dir_for(owner_name)


def _background_story_output_candidates(
    record: dict[str, Any],
) -> tuple[tuple[str, Path], ...]:
    """Return the active namespace plus the legacy namespace for read/GC compatibility."""

    current = _background_story_output(record)
    legacy = _legacy_background_story_output(record)
    return (current,) if current == legacy else (current, legacy)


def _configured_generated_assets_root() -> Path:
    configured = Path(
        getattr(get_settings(), "storage_health_generated_assets_path", "static/generated")
    ).expanduser()
    if not configured.is_absolute():
        configured = Path.cwd() / configured
    configured = configured.resolve(strict=False)
    runtime_root = generated_dir_for(uuid.UUID(int=0)).parent.resolve(strict=False)
    if configured != runtime_root:
        raise RuntimeError("configured generated-assets path differs from media writer root")
    return configured


def _durable_background_story_media_values() -> list[Any]:
    values: list[Any] = [_read_store()]
    inbox_path = Path(
        getattr(get_settings(), "bot_command_inbox_path", "data/push/bot_command_inbox.sqlite3")
    ).expanduser()
    if not inbox_path.is_absolute():
        inbox_path = Path.cwd() / inbox_path
    inbox_path = inbox_path.resolve(strict=False)
    if not inbox_path.exists():
        return values

    with sqlite3.connect(
        f"{inbox_path.as_uri()}?mode=ro",
        uri=True,
        timeout=1,
    ) as connection:
        rows = connection.execute(
            "SELECT prepared_json FROM bot_command_inbox WHERE prepared_json IS NOT NULL"
        ).fetchall()
    for row in rows:
        serialized = row[0]
        if not isinstance(serialized, str):
            raise ValueError("durable bot media checkpoint is not text")
        decoded = json.loads(serialized)
        if not isinstance(decoded, dict):
            raise ValueError("durable bot media checkpoint is not an object")
        values.append(decoded)
    return values


def _run_background_story_media_cleanup(
    *,
    records: list[dict[str, Any]] | None,
    now: datetime,
) -> None:
    generated_root = _configured_generated_assets_root()
    owner_directories: dict[str, Path] | None = None
    if records is not None:
        owner_directories = {}
        for record in records:
            for owner_name, directory in _background_story_output_candidates(record):
                owner_directories[owner_name] = directory
        if not owner_directories:
            return
    result = cleanup_unreferenced_background_story_media(
        generated_root=generated_root,
        owner_directories=owner_directories,
        saved_values=_durable_background_story_media_values(),
        now=now,
        minimum_age=BACKGROUND_STORY_MEDIA_GC_MIN_AGE,
    )
    if result.removed:
        logger.info(
            "background_story_media_gc removed=%s referenced=%s tooYoung=%s",
            len(result.removed),
            result.referenced,
            result.too_young,
        )
    if result.failed or result.unsafe:
        logger.warning(
            "background_story_media_gc incomplete failed=%s unsafe=%s",
            len(result.failed),
            result.unsafe,
        )


def _cleanup_background_story_media_for_records(
    records: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> None:
    if not generated_media_cleanup_is_enabled(
        getattr(get_settings(), "generated_media_cleanup_enabled", None)
    ):
        return
    try:
        with _background_story_media_gc_lock:
            _run_background_story_media_cleanup(records=records, now=now or _now())
    except Exception as exc:
        # Missing/corrupt durable ownership state must disable deletion, not risk
        # removing media that a replay still owns.
        logger.warning(
            "background_story_media_gc skipped errorType=%s",
            type(exc).__name__,
        )


def _background_story_media_target(
    record: dict[str, Any],
    *,
    generated_at: datetime,
    suffix: str,
) -> tuple[Path, str]:
    if suffix not in {".mp4", ".png"}:
        raise ValueError("unsupported background-story media suffix")
    safe_pet_id, output_dir = _background_story_output(record)
    filename = f"background-story-{generated_at.strftime('%Y%m%dT%H%M%S%fZ')}{suffix}"
    version = int(generated_at.timestamp())
    return output_dir / filename, f"/static/generated/{safe_pet_id}/{filename}?v={version}"


def _atomic_write_background_story_media(path: Path, content: bytes) -> None:
    if not content:
        raise ValueError("background-story media must not be empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary_path.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def _existing_background_story_media(
    record: dict[str, Any],
    *,
    generated_at: datetime,
    suffix: str,
) -> tuple[str, bytes] | None:
    path, media_url = _background_story_media_target(
        record,
        generated_at=generated_at,
        suffix=suffix,
    )
    try:
        if path.is_symlink():
            return None
        # A durable retry can recover an old file immediately before checkpointing
        # its URL. Refresh mtime first so the GC grace period fences that race.
        os.utime(path, None, follow_symlinks=False)
        content = path.read_bytes()
    except OSError:
        return None
    return (media_url, content) if content else None


def _persist_background_story_image(
    record: dict[str, Any],
    image_bytes: bytes,
    *,
    generated_at: datetime,
) -> str:
    path, media_url = _background_story_media_target(
        record,
        generated_at=generated_at,
        suffix=".png",
    )
    _atomic_write_background_story_media(path, image_bytes)
    return media_url


def _persist_background_story_video(
    record: dict[str, Any],
    video_bytes: bytes,
    *,
    generated_at: datetime,
) -> str:
    path, media_url = _background_story_media_target(
        record,
        generated_at=generated_at,
        suffix=".mp4",
    )
    _atomic_write_background_story_media(path, video_bytes)
    return media_url


def _persisted_background_story_media_bytes(
    record: dict[str, Any],
    media_url: Any,
    *,
    suffix: str,
) -> bytes | None:
    if not isinstance(media_url, str):
        return None
    clean_path = media_url.split("?", maxsplit=1)[0]
    target: Path | None = None
    for owner_name, output_dir in _background_story_output_candidates(record):
        expected_prefix = f"/static/generated/{owner_name}/"
        if not clean_path.startswith(expected_prefix):
            continue
        filename = clean_path.removeprefix(expected_prefix)
        if Path(filename).name != filename or Path(filename).suffix.lower() != suffix:
            return None
        target = output_dir / filename
        break
    if target is None:
        return None
    try:
        if target.is_symlink():
            return None
        # Refresh before a durable inbox entry transitions into the push store.
        # Push JSON and SQLite cannot share one transaction; the grace-period
        # fence prevents a GC snapshot from falling into that ownership gap.
        os.utime(target, None, follow_symlinks=False)
        content = target.read_bytes()
        return content or None
    except OSError:
        return None


def load_persisted_story_video_bytes(
    *,
    pet_id: Any,
    telegram_id: int,
    media_url: Any,
) -> bytes | None:
    """Read only a video created for this pet from the generated-media directory."""
    record = {"telegramId": telegram_id}
    if isinstance(pet_id, (str, int)) and not isinstance(pet_id, bool):
        record["petId"] = pet_id
    return _persisted_background_story_media_bytes(record, media_url, suffix=".mp4")


def _clamp_stat(value: Any) -> int:
    numeric = value if isinstance(value, (int, float)) else 0
    return max(0, min(100, round(numeric)))


def _story_stat_damage(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0
    if amount == 0:
        return 0
    return max(1, min(STORY_STAT_MAX_SINGLE_DAMAGE, round(abs(amount))))


def _normalize_story_stat_impacts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        candidates = [item for item in value if isinstance(item, dict)]
    elif isinstance(value, dict):
        applies = value.get("applies") is True and value.get("isNegativeOutcome") is True
        stat = value.get("stat")
        candidates = [value] if applies and stat in STAT_KEYS else []
    else:
        candidates = []

    result: list[dict[str, Any]] = []
    seen_stats: set[str] = set()
    total_change = 0
    for item in candidates:
        stat = item.get("stat")
        if stat not in STAT_KEYS or stat in seen_stats:
            continue
        try:
            raw_amount = float(item.get("amount"))
        except (TypeError, ValueError):
            continue
        magnitude = _story_stat_damage(raw_amount)
        if magnitude <= 0:
            continue
        remaining_total = STORY_STAT_MAX_TOTAL_DAMAGE - total_change
        if remaining_total <= 0:
            break
        applied_magnitude = min(magnitude, remaining_total)
        result.append(
            {
                "stat": stat,
                "amount": applied_magnitude if raw_amount > 0 else -applied_magnitude,
            }
        )
        seen_stats.add(stat)
        total_change += applied_magnitude
        if len(result) >= STORY_STAT_MAX_ITEMS:
            break
    return result


def _stat_tick_map(record: dict[str, Any], fallback: datetime | None = None) -> dict[str, datetime]:
    fallback_tick = (
        _parse_iso(record.get("lastStatsTickAt"))
        or _parse_iso(record.get("updatedAt"))
        or fallback
        or _now()
    )
    raw_ticks = record.get("lastStatTickAt")
    raw_ticks = raw_ticks if isinstance(raw_ticks, dict) else {}
    ticks: dict[str, datetime] = {}
    for key in STAT_KEYS:
        ticks[key] = _parse_iso(raw_ticks.get(key)) or fallback_tick
    return ticks


def _stat_tick_iso_map(ticks: dict[str, datetime]) -> dict[str, str]:
    return {key: _iso(ticks[key]) for key in STAT_KEYS}


def _legacy_stats_tick(ticks: dict[str, datetime]) -> str:
    return _iso(min(ticks.values()))


def _record_current_stats(record: dict[str, Any], now: datetime) -> dict[str, int]:
    pet = record.get("pet") if isinstance(record.get("pet"), dict) else {}
    stats = pet.get("stats") if isinstance(pet.get("stats"), dict) else {}
    ticks = _stat_tick_map(record, fallback=now)
    current_stats: dict[str, int] = {}
    for key in STAT_KEYS:
        elapsed_hours = max(0.0, (now - ticks[key]).total_seconds() / 3600)
        current_stats[key] = _clamp_stat(stats.get(key, 0) - elapsed_hours * STAT_DECAY_PER_HOUR)
    return current_stats


def _record_death_at(record: dict[str, Any], now: datetime) -> datetime | None:
    explicit_death = _parse_iso(record.get("diedAt"))
    if explicit_death:
        return explicit_death
    if record.get("deathTrackingEnabled") is not True:
        return None

    pet = record.get("pet") if isinstance(record.get("pet"), dict) else {}
    stats = pet.get("stats") if isinstance(pet.get("stats"), dict) else {}
    ticks = _stat_tick_map(record, fallback=now)
    raw_zero_times = record.get("zeroStatSinceAt")
    zero_times = raw_zero_times if isinstance(raw_zero_times, dict) else {}
    death_candidates: list[datetime] = []

    for key in STAT_KEYS:
        raw_value = stats.get(key, 0)
        value = float(raw_value) if isinstance(raw_value, (int, float)) else 0.0
        zero_at = _parse_iso(zero_times.get(key))
        if value > 0:
            zero_at = ticks[key] + timedelta(hours=value / STAT_DECAY_PER_HOUR)
        elif zero_at is None:
            zero_at = ticks[key]
        death_at = zero_at + PET_DEATH_AFTER_ZERO
        if now > death_at:
            death_candidates.append(death_at)

    return min(death_candidates) if death_candidates else None


def _record_is_dead(record: dict[str, Any], now: datetime) -> bool:
    return _record_death_at(record, now) is not None


def _all_stat_ticks(now: datetime) -> dict[str, datetime]:
    return {key: now for key in STAT_KEYS}


def _stats_patch(
    *,
    stats: dict[str, int],
    ticks: dict[str, datetime],
    keys: tuple[str, ...] = STAT_KEYS,
) -> dict[str, Any]:
    tick_subset = {key: ticks[key] for key in keys}
    return {
        "stats": {key: _clamp_stat(stats.get(key, 0)) for key in keys},
        "lastStatsTickAt": _legacy_stats_tick(tick_subset),
        "lastStatTickAt": {key: _iso(ticks[key]) for key in keys},
    }


def _merge_snapshot_stats(
    incoming_record: dict[str, Any],
    existing_record: dict[str, Any] | None,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    if not isinstance(existing_record, dict):
        return None

    incoming_ticks = _stat_tick_map(incoming_record, fallback=now)
    existing_ticks = _stat_tick_map(existing_record, fallback=now)
    incoming_stats = _record_current_stats(incoming_record, now)
    existing_stats = _record_current_stats(existing_record, now)
    merged_ticks = incoming_ticks.copy()
    merged_stats = incoming_stats.copy()
    changed_keys: list[str] = []

    for key in STAT_KEYS:
        incoming_comparable = min(incoming_ticks[key], now)
        existing_comparable = min(existing_ticks[key], now)
        if existing_comparable > incoming_comparable:
            merged_ticks[key] = now
            merged_stats[key] = existing_stats[key]
            changed_keys.append(key)

    pet = incoming_record.get("pet") if isinstance(incoming_record.get("pet"), dict) else {}
    pet["stats"] = {key: _clamp_stat(merged_stats.get(key, 0)) for key in STAT_KEYS}
    incoming_record["pet"] = pet
    incoming_record["lastStatTickAt"] = _stat_tick_iso_map(merged_ticks)
    incoming_record["lastStatsTickAt"] = _legacy_stats_tick(merged_ticks)

    return (
        _stats_patch(stats=merged_stats, ticks=merged_ticks, keys=tuple(changed_keys))
        if changed_keys
        else None
    )


def _current_pet_record(record: dict[str, Any], now: datetime) -> dict[str, Any]:
    pet = deepcopy(record.get("pet")) if isinstance(record.get("pet"), dict) else {}
    pet["stats"] = _record_current_stats(record, now)

    created_at = _parse_iso(record.get("createdAt"))
    if created_at:
        age_days = max(0.0, (now - created_at).total_seconds() / 86_400)
        pet["stage"] = "baby" if age_days < 2 else "teen" if age_days < 7 else "adult"
    return pet


def register_push_snapshot(
    user: TelegramUserContext,
    payload: LocalPetPushSnapshotRequest,
) -> LocalPetPushSnapshotResponse:
    now = _now()
    now_iso = _iso(now)
    fallback_stat_tick = payload.lastStatsTickAt or payload.updatedAt or now_iso
    death_tracking_enabled = (
        "zeroStatSinceAt" in payload.model_fields_set or "diedAt" in payload.model_fields_set
    )
    incoming_record = {
        "telegramId": user.telegram_id,
        "chatId": user.telegram_id,
        "username": user.username,
        "firstName": user.first_name,
        "languageCode": user.language_code,
        "petId": payload.petId,
        "pet": payload.pet.model_dump(mode="json"),
        "history": [item.model_dump(mode="json") for item in payload.history[-12:]],
        "recentAmbientReplies": payload.recentAmbientReplies[-30:],
        "memoryContext": (
            payload.memoryContext.model_dump(mode="json") if payload.memoryContext else None
        ),
        "createdAt": payload.createdAt,
        "updatedAt": payload.updatedAt,
        "lastStatsTickAt": fallback_stat_tick,
        "lastStatTickAt": payload.lastStatTickAt or {key: fallback_stat_tick for key in STAT_KEYS},
        "zeroStatSinceAt": payload.zeroStatSinceAt or {},
        "diedAt": payload.diedAt,
        "deathTrackingEnabled": death_tracking_enabled,
        "timezone": payload.timezone,
        "registeredAt": now_iso,
    }
    if payload.snapshotWriterId is not None and payload.snapshotRevision is not None:
        incoming_record["snapshotWriterId"] = payload.snapshotWriterId
        incoming_record["snapshotRevision"] = payload.snapshotRevision
    stats_patch: LocalPetStatsPatch | None = None
    reset_pet = False

    def merge_snapshot(existing: dict[str, Any] | None) -> dict[str, Any]:
        nonlocal reset_pet, stats_patch
        reset_request = existing.get("petResetRequest") if isinstance(existing, dict) else None
        reset_tombstones = _pet_reset_tombstones(existing)
        if (isinstance(reset_request, dict) and reset_request.get("petId") == payload.petId) or any(
            item["petId"] == payload.petId for item in reset_tombstones
        ):
            reset_pet = True
            return deepcopy(existing)

        record = deepcopy(incoming_record)
        same_pet = isinstance(existing, dict) and existing.get("petId") == payload.petId
        existing_revision = existing.get("snapshotRevision") if isinstance(existing, dict) else None
        existing_revision = (
            existing_revision
            if isinstance(existing_revision, int) and not isinstance(existing_revision, bool)
            else None
        )
        existing_writer_id = (
            existing.get("snapshotWriterId") if isinstance(existing, dict) else None
        )
        existing_writer_id = existing_writer_id if isinstance(existing_writer_id, str) else None
        incoming_writer_id = payload.snapshotWriterId
        incoming_revision = payload.snapshotRevision
        stale_snapshot = False
        if isinstance(existing, dict):
            same_writer = (
                incoming_writer_id is not None
                and incoming_writer_id == existing_writer_id
                and incoming_revision is not None
                and existing_revision is not None
            )
            comparable_modern_orders = (
                incoming_writer_id is not None
                and existing_writer_id is not None
                and incoming_revision is not None
                and existing_revision is not None
            )

            def modern_order_is_stale() -> bool | None:
                if not comparable_modern_orders:
                    return None
                incoming_epoch_order = incoming_revision >= SNAPSHOT_EPOCH_REVISION_FLOOR
                existing_epoch_order = existing_revision >= SNAPSHOT_EPOCH_REVISION_FLOOR
                if incoming_epoch_order != existing_epoch_order:
                    return not incoming_epoch_order
                if incoming_epoch_order:
                    return (incoming_revision, incoming_writer_id) <= (
                        existing_revision,
                        existing_writer_id,
                    )
                if same_writer:
                    return incoming_revision <= existing_revision
                return None

            if not same_pet:
                # Pet replacement is ordered by the pet creation time even when
                # two tabs briefly use different writers (or race on the shared
                # revision counter). Otherwise a late snapshot for the deleted
                # pet can replace the new pet before its reset tombstone arrives.
                incoming_created_at = _parse_iso(payload.createdAt)
                existing_created_at = _parse_iso(existing.get("createdAt"))
                stale_snapshot = bool(
                    incoming_created_at is None
                    or (
                        existing_created_at is not None
                        and incoming_created_at < existing_created_at
                    )
                    or (
                        incoming_created_at is not None
                        and incoming_created_at == existing_created_at
                        and modern_order_is_stale() is True
                    )
                )
            elif (modern_order_stale := modern_order_is_stale()) is not None:
                stale_snapshot = modern_order_stale
            else:
                # Legacy clients do not provide a request-order fence. Keep their
                # timestamp behavior for legacy counters and mixed writers. Equal
                # timestamps remain accepted because history/memory may change
                # without mutating the pet itself.
                incoming_order_at = _parse_iso(payload.updatedAt if same_pet else payload.createdAt)
                existing_order_at = _parse_iso(
                    existing.get("updatedAt") if same_pet else existing.get("createdAt")
                )
                stale_snapshot = bool(
                    incoming_order_at is None
                    or (existing_order_at is not None and incoming_order_at < existing_order_at)
                )

        if stale_snapshot:
            # Requests are processed by a thread pool and can finish out of order. A
            # late snapshot must not roll the active pet, history, memory or ambient
            # context back. Same-pet callers still receive authoritative stat ticks.
            if same_pet:
                stats_probe = deepcopy(incoming_record)
                stats_patch = _merge_snapshot_stats(stats_probe, existing, now=now)
            return deepcopy(existing)
        record["pet"] = _preserve_pet_character_bible(
            record["pet"],
            existing.get("pet") if same_pet else None,
        )
        if same_pet:
            if payload.snapshotWriterId is None and existing_writer_id is not None:
                record["snapshotWriterId"] = existing_writer_id
                record["snapshotRevision"] = existing_revision
        stats_patch = _merge_snapshot_stats(record, existing, now=now) if same_pet else None
        if isinstance(existing, dict):
            saved_pet_slot = existing.get(DEBUG_SAVED_PET_SLOT_KEY)
            if isinstance(saved_pet_slot, dict):
                record[DEBUG_SAVED_PET_SLOT_KEY] = deepcopy(saved_pet_slot)
            if reset_tombstones:
                record["petResetTombstones"] = reset_tombstones
            for key in (
                "lastPushAt",
                "lastPushAttemptAt",
                "lastDebugPushAt",
                "lastPushReply",
                "lastPushError",
                "lastPushErrorCode",
                "lastPushErrorAt",
                "chatStartedAt",
                "lastChatSeenAt",
                "chatReachable",
                "lastStoryAt",
                "lastStoryAttemptAt",
                "lastStory",
                "lastStoryError",
                "lastStoryErrorCode",
                "lastStoryErrorAt",
            ):
                record[key] = existing.get(key)
            if same_pet:
                for key in (
                    "lastFullStoryAt",
                    "lastFullStory",
                    "fullStoryHistory",
                    "botGenerationReceipts",
                    "dailyFullStory",
                    "dailyFullStoryAttemptKey",
                    "dailyFullStoryAttemptCount",
                    "dailyFullStoryAttemptAt",
                    "lastScheduledShortStoryAt",
                    "lastScheduledShortStoryAttemptAt",
                    "lastScheduledShortStoryError",
                    "pendingInteractiveStory",
                    "automaticInteractiveStories",
                ):
                    record[key] = deepcopy(existing.get(key))
                record["recentStoryEvents"] = _record_recent_story_events(existing)
                _merge_record_lite_overlay_patch(record, _record_lite_overlay_patch(existing))
        return record

    record = _update_record(user.telegram_id, merge_snapshot)
    return LocalPetPushSnapshotResponse(
        registered=True,
        telegramId=user.telegram_id,
        updatedAt=now_iso,
        resetPet=reset_pet,
        statsPatch=stats_patch,
        liteOverlayPatch=None if reset_pet else _record_lite_overlay_patch(record),
        recentStoryEventsPatch=None if reset_pet else _recent_story_events_patch(record),
    )


def mark_chat_started(
    *,
    chat_id: int,
    username: str | None = None,
    first_name: str | None = None,
    language_code: str | None = None,
) -> dict[str, Any]:
    now_iso = _iso()

    def mark_started(existing: dict[str, Any] | None) -> dict[str, Any]:
        record = existing.copy() if isinstance(existing, dict) else {}
        record.update(
            {
                "chatId": chat_id,
                "chatReachable": True,
                "chatStartedAt": record.get("chatStartedAt") or now_iso,
                "lastChatSeenAt": now_iso,
            }
        )
        if username:
            record["username"] = username
        if first_name:
            record["firstName"] = first_name
        if language_code:
            record["languageCode"] = language_code
        if not record.get("registeredAt"):
            record["registeredAt"] = now_iso
        return record

    return _update_record(chat_id, mark_started)


def _has_snapshot(record: dict[str, Any]) -> bool:
    return bool(record.get("petId")) and isinstance(record.get("pet"), dict)


def _record_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "telegramId": record.get("telegramId"),
        "username": record.get("username"),
        "firstName": record.get("firstName"),
        "petId": record.get("petId"),
        "registeredAt": record.get("registeredAt"),
        "lastPushAt": record.get("lastPushAt"),
        "lastPushAttemptAt": record.get("lastPushAttemptAt"),
        "lastDebugPushAt": record.get("lastDebugPushAt"),
        "lastPushError": record.get("lastPushError"),
        "lastPushErrorCode": record.get("lastPushErrorCode"),
        "lastPushErrorAt": record.get("lastPushErrorAt"),
        "lastStoryAt": record.get("lastStoryAt"),
        "lastStoryImageStatus": record.get("lastStoryImageStatus"),
        "lastStoryImageError": record.get("lastStoryImageError"),
        "lastStoryImageErrorAt": record.get("lastStoryImageErrorAt"),
        "chatReachable": record.get("chatReachable") is True,
        "chatStartedAt": record.get("chatStartedAt"),
        "lastChatSeenAt": record.get("lastChatSeenAt"),
    }


def push_status() -> dict[str, Any]:
    records = _read_store().get("records", {})
    summaries: list[dict[str, Any]] = []
    for record in records.values():
        if not isinstance(record, dict) or not _has_snapshot(record):
            continue
        summaries.append(_record_summary(record))
    summaries.sort(key=lambda item: str(item.get("registeredAt", "")), reverse=True)
    return {
        "count": len(summaries),
        "snapshotCount": len(summaries),
        "reachableCount": sum(1 for item in summaries if item.get("chatReachable") is True),
        "latest": summaries[0] if summaries else None,
        "records": summaries,
    }


def _record_by_telegram_id(telegram_id: int | None = None) -> dict[str, Any]:
    records = _read_store().get("records", {})
    if telegram_id is not None:
        record = records.get(str(telegram_id))
        if isinstance(record, dict) and _has_snapshot(record):
            return record
        raise TelegramPushError(
            "PUSH_SNAPSHOT_NOT_FOUND",
            "Snapshot для этого Telegram ID не найден.",
        )

    latest = None
    for record in records.values():
        if not isinstance(record, dict) or not _has_snapshot(record):
            continue
        if latest is None or str(record.get("registeredAt", "")) > str(
            latest.get("registeredAt", "")
        ):
            latest = record
    if not latest:
        raise TelegramPushError(
            "PUSH_SNAPSHOT_NOT_FOUND",
            "Нет сохраненного snapshot питомца. Открой Mini App в Telegram после деплоя.",
        )
    return latest


def _build_push_payload(
    record: dict[str, Any],
    *,
    reason: str,
    include_debug: bool,
) -> LocalPushRequest:
    now = _now()
    if _record_is_dead(record, now):
        raise TelegramPushError("PET_DEAD", "Питомец умер и больше не может отправлять сообщения.")
    pet = LocalPetChatContext.model_validate(_current_pet_record(record, now))
    memory_context = None
    if isinstance(record.get("memoryContext"), dict):
        memory_context = LocalPetMemoryContext.model_validate(record["memoryContext"])
    return LocalPushRequest(
        pet=pet,
        memoryContext=memory_context,
        reason=reason,
        nowIso=_iso(now),
        timezone=record.get("timezone") if isinstance(record.get("timezone"), str) else None,
        includeDebug=include_debug,
    )


def _record_history(record: dict[str, Any]) -> list[LocalChatHistoryItem]:
    raw_history = record.get("history")
    if not isinstance(raw_history, list):
        return []
    history: list[LocalChatHistoryItem] = []
    for item in raw_history[-12:]:
        try:
            history.append(LocalChatHistoryItem.model_validate(item))
        except ValueError:
            continue
    return history


def _record_recent_replies(record: dict[str, Any]) -> list[str]:
    raw_replies = record.get("recentAmbientReplies")
    if not isinstance(raw_replies, list):
        return []
    replies: list[str] = []
    for item in raw_replies[-30:]:
        if isinstance(item, str) and item.strip():
            replies.append(item.strip()[:500])
    return replies


def _push_reason_for_record(record: dict[str, Any], now: datetime) -> str:
    pet = _current_pet_record(record, now)
    stats = pet.get("stats") if isinstance(pet.get("stats"), dict) else {}
    needs = sorted(
        (
            (
                _clamp_stat(stats.get("hunger")),
                "Скажи, что проголодался и хочешь кушать; мягко позови владельца.",
            ),
            (
                _clamp_stat(stats.get("happiness")),
                "Скажи, что у тебя плохое настроение и хочется внимания владельца.",
            ),
            (
                _clamp_stat(stats.get("energy")),
                "Скажи, что ты плохо себя чувствуешь и хочешь, чтобы владелец заглянул.",
            ),
        ),
        key=lambda item: item[0],
    )
    if needs[0][0] <= 35:
        return needs[0][1]

    latest_story = _latest_fresh_story_event(record, now)
    settings = get_settings()
    local_now = now.astimezone(_push_timezone(record, settings))
    hours = _daily_push_hours(settings)
    slot_index = max(0, sum(1 for hour in hours if hour <= local_now.hour) - 1)
    if latest_story and (local_now.date().toordinal() + slot_index) % 3 == 0:
        summary = _compact_event_text(latest_story.get("summary"), limit=280)
        if summary:
            return (
                "Свяжи пуш только с самой последней недавней историей. "
                "Начни с ясной связки «Недавно со мной произошло…» и не добавляй новых фактов: "
                f"{summary}"
            )

    return "Скажи, что скучаешь по владельцу и хочешь, чтобы он заглянул."


def _send_push_record(
    record: dict[str, Any],
    *,
    reason: str,
    manual: bool,
    include_debug: bool = False,
    assert_active: Callable[[], None] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.bot_token:
        raise TelegramPushError("BOT_TOKEN_MISSING", "BOT_TOKEN не настроен.")
    if not settings.webapp_url:
        raise TelegramPushError("WEBAPP_URL_MISSING", "WEBAPP_URL не настроен.")

    payload = _build_push_payload(record, reason=reason, include_debug=include_debug)
    response = generate_push_pet_message(payload)

    chat_id = record.get("chatId")
    if not isinstance(chat_id, int):
        raise TelegramPushError("PUSH_CHAT_ID_MISSING", "chat_id для Telegram push не найден.")

    with httpx.Client() as client:
        if assert_active is not None:
            assert_active()
        try:
            send_message(
                client,
                chat_id,
                response.reply,
                mini_app_keyboard(settings.webapp_url),
            )
        except TelegramAPIError as exc:
            push_error = _telegram_push_error(exc)
            if assert_active is not None:
                assert_active()
            _save_push_failure(record, push_error)
            raise push_error from exc
        except httpx.HTTPError as exc:
            push_error = TelegramPushError(
                "TELEGRAM_SEND_FAILED",
                f"Telegram sendMessage failed: {exc.__class__.__name__}",
            )
            if assert_active is not None:
                assert_active()
            _save_push_failure(record, push_error)
            raise push_error from exc

    now = _now()
    now_iso = _iso(now)
    stat_ticks = _all_stat_ticks(now)
    expected_pet_id = record.get("petId")
    expected_snapshot_updated_at = record.get("updatedAt")
    delivery_patch = {
        "lastPushReply": response.reply,
        "lastPushError": None,
        "lastPushErrorCode": None,
        "lastPushErrorAt": None,
        "lastPushAttemptAt": now_iso,
        "chatReachable": True,
    }
    if manual:
        delivery_patch["lastDebugPushAt"] = now_iso
    else:
        delivery_patch["lastPushAt"] = now_iso

    def save_delivery(current: dict[str, Any] | None) -> dict[str, Any]:
        next_record = current.copy() if isinstance(current, dict) else record.copy()
        next_record.update(delivery_patch)
        if (
            next_record.get("petId") == expected_pet_id
            and next_record.get("updatedAt") == expected_snapshot_updated_at
        ):
            next_record.update(
                {
                    "pet": payload.pet.model_dump(mode="json"),
                    "lastStatsTickAt": _legacy_stats_tick(stat_ticks),
                    "lastStatTickAt": _stat_tick_iso_map(stat_ticks),
                }
            )
        return next_record

    if assert_active is not None:
        assert_active()
    _update_record(_telegram_id_from_record(record), save_delivery)
    return {
        "sent": True,
        "manual": manual,
        "telegramId": record.get("telegramId"),
        "petId": record.get("petId"),
        "reply": response.reply,
        "sentAt": now_iso,
        "debug": response.debug.model_dump(mode="json") if response.debug else None,
    }


def _telegram_push_error(exc: TelegramAPIError) -> TelegramPushError:
    description = exc.description.strip()
    normalized_description = description.lower()
    chat_unreachable = exc.status_code == 403 or any(
        marker in normalized_description
        for marker in (
            "chat not found",
            "bot was blocked by the user",
            "user is deactivated",
            "bot can't initiate conversation",
        )
    )
    if chat_unreachable:
        return TelegramPushError(
            "TELEGRAM_CHAT_NOT_FOUND",
            (
                "Telegram-чат недоступен. Пользователь должен открыть диалог с ботом, "
                "нажать /start и затем повторить отправку."
            ),
        )
    return TelegramPushError(
        "TELEGRAM_SEND_FAILED",
        f"Telegram sendMessage failed: HTTP {exc.status_code}: {description}",
    )


def _save_push_failure(record: dict[str, Any], exc: Exception) -> None:
    now_iso = _iso()
    if isinstance(exc, TelegramPushError):
        error_code = exc.code
        message = _safe_error_message(exc.message)
    else:
        error_code = "PUSH_SEND_FAILED"
        message = _safe_error_message(exc)
    failure_patch = {
        "lastPushError": message,
        "lastPushErrorCode": error_code,
        "lastPushErrorAt": now_iso,
        "lastPushAttemptAt": now_iso,
    }
    if error_code == "TELEGRAM_CHAT_NOT_FOUND":
        failure_patch["chatReachable"] = False
        failure_patch["chatUnreachableAt"] = now_iso

    def save_failure(current: dict[str, Any] | None) -> dict[str, Any]:
        failed = current.copy() if isinstance(current, dict) else record.copy()
        failed.update(failure_patch)
        return failed

    _update_record(_telegram_id_from_record(record), save_failure)


def _save_story_failure(record: dict[str, Any], exc: Exception) -> None:
    now_iso = _iso()
    if isinstance(exc, TelegramPushError):
        error_code = exc.code
        message = _safe_error_message(exc.message)
    else:
        error_code = "STORY_GENERATION_FAILED"
        message = _safe_error_message(exc)
    failure_patch = {
        "lastStoryError": message,
        "lastStoryErrorCode": error_code,
        "lastStoryErrorAt": now_iso,
        "lastStoryAttemptAt": now_iso,
    }
    if error_code == "TELEGRAM_CHAT_NOT_FOUND":
        failure_patch["chatReachable"] = False
        failure_patch["chatUnreachableAt"] = now_iso

    def save_failure(current: dict[str, Any] | None) -> dict[str, Any]:
        failed = current.copy() if isinstance(current, dict) else record.copy()
        failed.update(failure_patch)
        return failed

    _update_record(_telegram_id_from_record(record), save_failure)


def send_manual_push(
    *,
    telegram_id: int | None = None,
    reason: str | None = None,
    include_debug: bool = True,
    assert_active: Callable[[], None] | None = None,
) -> dict[str, Any]:
    record = _record_by_telegram_id(telegram_id)
    return _send_push_record(
        record,
        reason=reason or _push_reason_for_record(record, _now()),
        manual=True,
        include_debug=include_debug,
        assert_active=assert_active,
    )


def _apply_story_stat_impact(
    record: dict[str, Any],
    stat_impact: Any,
    *,
    now: datetime,
) -> tuple[
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, datetime] | None,
    dict[str, int] | None,
]:
    pet = deepcopy(record.get("pet")) if isinstance(record.get("pet"), dict) else {}
    raw_impacts = _normalize_story_stat_impacts(stat_impact)
    if not raw_impacts:
        stats_delta = {key: 0 for key in STAT_KEYS} if stat_impact is not None else None
        return pet, None, None, stats_delta

    current_stats = _record_current_stats(record, now)
    stats_delta = {key: 0 for key in STAT_KEYS}
    changed_keys: list[str] = []
    for impact in raw_impacts:
        stat = impact["stat"]
        previous_value = current_stats[stat]
        current_stats[stat] = _clamp_stat(current_stats[stat] + impact["amount"])
        delta = current_stats[stat] - previous_value
        if delta == 0:
            continue
        stats_delta[stat] += delta
        changed_keys.append(stat)

    if not changed_keys:
        return pet, None, None, stats_delta

    stats = pet.setdefault("stats", {})
    if not isinstance(stats, dict):
        stats = {}
        pet["stats"] = stats
    for stat in changed_keys:
        stats[stat] = current_stats[stat]

    ticks = _stat_tick_map(record, fallback=now)
    for stat in changed_keys:
        ticks[stat] = now
    return (
        pet,
        _stats_patch(stats=current_stats, ticks=ticks, keys=tuple(changed_keys)),
        ticks,
        stats_delta,
    )


def _checkpoint_durable_progress(
    progress: dict[str, Any],
    key: str,
    value: Any,
    checkpoint: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    if key in progress:
        if progress[key] != value:
            raise TelegramPushError(
                "DURABLE_PROGRESS_CONFLICT",
                f"Сохранённая стадия {key} не совпадает с текущим результатом.",
            )
        return progress
    next_progress = {**progress, key: deepcopy(value)}
    if checkpoint is not None:
        checkpoint(next_progress)
    return next_progress


def _background_story_result_from_payload(payload: Any) -> BackgroundStoryResult:
    if not isinstance(payload, dict):
        raise TelegramPushError("DURABLE_STORY_INVALID", "Сохранённый текст истории повреждён.")

    def text_value(key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise TelegramPushError(
                "DURABLE_STORY_INVALID",
                f"Сохранённое поле истории {key} повреждено.",
            )
        return value

    raw_tags = payload.get("tags")
    raw_prompt_debug = payload.get("promptDebug")
    raw_stat_impacts = payload.get("statImpacts")
    if not isinstance(raw_tags, list) or not isinstance(raw_prompt_debug, list):
        raise TelegramPushError("DURABLE_STORY_INVALID", "Сохранённый текст истории повреждён.")
    if not isinstance(raw_stat_impacts, list):
        raw_stat_impacts = []
    return BackgroundStoryResult(
        title=text_value("title"),
        summary=text_value("summary"),
        story_text=text_value("storyText"),
        event_type=text_value("eventType"),
        valence=text_value("valence"),
        tags=tuple(str(item) for item in raw_tags),
        rag_text=text_value("ragText"),
        story_library_patch=(
            payload.get("storyLibraryPatch")
            if isinstance(payload.get("storyLibraryPatch"), dict)
            else None
        ),
        lite_overlay_patch=(
            payload.get("liteOverlayPatch")
            if isinstance(payload.get("liteOverlayPatch"), dict)
            else None
        ),
        recent_story_event=(
            payload.get("recentStoryEvent")
            if isinstance(payload.get("recentStoryEvent"), dict)
            else None
        ),
        prompt_debug=[item for item in raw_prompt_debug if isinstance(item, dict)],
        stat_impacts=tuple(item for item in raw_stat_impacts if isinstance(item, dict)),
        stat_impact=(
            payload.get("statImpact") if isinstance(payload.get("statImpact"), dict) else None
        ),
        stat_validation=(
            payload.get("statValidation")
            if isinstance(payload.get("statValidation"), dict)
            else None
        ),
        plot_mode=str(payload.get("plotMode") or ""),
        incident_class=str(payload.get("incidentClass") or ""),
        causal_origin=str(payload.get("causalOrigin") or ""),
        event_scale=str(payload.get("eventScale") or ""),
        setting_class=str(payload.get("settingClass") or ""),
        location=str(payload.get("location") or ""),
        opposition_class=str(payload.get("oppositionClass") or ""),
        resolution_mode=str(payload.get("resolutionMode") or ""),
        resolution_family=str(payload.get("resolutionFamily") or ""),
        valence_target=str(payload.get("valenceTarget") or ""),
    )


def _background_story_result_payload(result: Any) -> dict[str, Any]:
    stat_impacts = list(getattr(result, "stat_impacts", ()) or [])
    stat_impact = getattr(result, "stat_impact", None) or (
        stat_impacts[0] if stat_impacts else None
    )
    return {
        "title": str(result.title),
        "summary": str(result.summary),
        "storyText": str(result.story_text),
        "eventType": str(result.event_type),
        "valence": str(result.valence),
        "tags": list(result.tags),
        "ragText": str(result.rag_text),
        "storyLibraryPatch": getattr(result, "story_library_patch", None),
        "liteOverlayPatch": getattr(result, "lite_overlay_patch", None),
        "recentStoryEvent": getattr(result, "recent_story_event", None),
        # Debug prompts are not required to resume paid media and can be large.
        "promptDebug": [],
        "statImpacts": stat_impacts,
        "statImpact": stat_impact,
        "statValidation": getattr(result, "stat_validation", None),
        "plotMode": getattr(result, "plot_mode", ""),
        "incidentClass": getattr(result, "incident_class", ""),
        "causalOrigin": getattr(result, "causal_origin", ""),
        "eventScale": getattr(result, "event_scale", ""),
        "settingClass": getattr(result, "setting_class", ""),
        "location": getattr(result, "location", ""),
        "oppositionClass": getattr(result, "opposition_class", ""),
        "resolutionMode": getattr(result, "resolution_mode", ""),
        "resolutionFamily": getattr(result, "resolution_family", ""),
        "valenceTarget": getattr(result, "valence_target", ""),
    }


def generate_story_for_telegram_user(
    *,
    telegram_id: int,
    include_debug: bool = True,
    idempotency_key: str | None = None,
    durable_progress: dict[str, Any] | None = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
    assert_active: Callable[[], None] | None = None,
) -> dict[str, Any]:
    record = _record_by_telegram_id(telegram_id)
    payload = _build_push_payload(
        record,
        reason="Фоновое событие питомца.",
        include_debug=include_debug,
    )
    novelty_history = _record_story_novelty_history(record)
    progress = deepcopy(durable_progress) if isinstance(durable_progress, dict) else {}
    text_stage = progress.get("text")
    if "text" in progress and not isinstance(text_stage, dict):
        raise TelegramPushError("DURABLE_STORY_INVALID", "Сохранённый текст истории повреждён.")
    if isinstance(text_stage, dict):
        if text_stage.get("petId") != record.get("petId"):
            raise TelegramPushError(
                "PUSH_PET_CHANGED",
                "Питомец изменился во время генерации истории. Повтори запрос.",
            )
        now = _parse_iso(text_stage.get("generatedAt"))
        if now is None:
            raise TelegramPushError(
                "DURABLE_STORY_INVALID",
                "В сохранённой истории отсутствует время генерации.",
            )
        result = _background_story_result_from_payload(text_stage.get("result"))
    else:
        result = generate_background_story(
            pet=payload.pet,
            memory_context=payload.memoryContext,
            history=_record_history(record),
            recent_replies=_record_recent_replies(record),
            recent_story_events=novelty_history,
            now_iso=payload.nowIso,
            timezone=payload.timezone,
        )
        if _story_is_lexical_duplicate(result, novelty_history):
            rejected_candidate = {
                "title": result.title,
                "tags": list(result.tags),
                "plotMode": getattr(result, "plot_mode", ""),
                "incidentClass": getattr(result, "incident_class", ""),
                "causalOrigin": getattr(result, "causal_origin", ""),
                "eventScale": getattr(result, "event_scale", ""),
                "settingClass": getattr(result, "setting_class", ""),
                "location": getattr(result, "location", ""),
                "oppositionClass": getattr(result, "opposition_class", ""),
                "resolutionMode": getattr(result, "resolution_mode", ""),
                "resolutionFamily": getattr(result, "resolution_family", ""),
                "valenceTarget": getattr(result, "valence_target", ""),
                "createdAt": payload.nowIso or _iso(),
            }
            result = generate_background_story(
                pet=payload.pet,
                memory_context=payload.memoryContext,
                history=_record_history(record),
                recent_replies=_record_recent_replies(record),
                recent_story_events=[*novelty_history, rejected_candidate],
                now_iso=payload.nowIso,
                timezone=payload.timezone,
            )
            if _story_is_lexical_duplicate(result, novelty_history):
                raise TelegramPushError(
                    "STORY_NOVELTY_EXHAUSTED",
                    "Не удалось придумать достаточно новое событие. Повтори позже.",
                )
        now = _now()
        progress = _checkpoint_durable_progress(
            progress,
            "text",
            {
                "petId": record.get("petId"),
                "generatedAt": _iso(now),
                "result": _background_story_result_payload(result),
            },
            checkpoint,
        )
    now_iso = _iso(now)
    recent_story_event = getattr(result, "recent_story_event", None)
    history_event = {
        "title": result.title,
        "summary": result.summary,
        "eventType": result.event_type,
        "valence": result.valence,
        "tags": list(result.tags),
        "plotMode": getattr(result, "plot_mode", ""),
        "incidentClass": getattr(result, "incident_class", ""),
        "causalOrigin": getattr(result, "causal_origin", ""),
        "eventScale": getattr(result, "event_scale", ""),
        "settingClass": getattr(result, "setting_class", ""),
        "location": getattr(result, "location", ""),
        "oppositionClass": getattr(result, "opposition_class", ""),
        "resolutionMode": getattr(result, "resolution_mode", ""),
        "resolutionFamily": getattr(result, "resolution_family", ""),
        "valenceTarget": getattr(result, "valence_target", ""),
        **(recent_story_event if isinstance(recent_story_event, dict) else {}),
        "storyText": result.story_text,
        "generatedAt": now_iso,
        "createdAt": (
            recent_story_event.get("createdAt") if isinstance(recent_story_event, dict) else now_iso
        ),
        "source": "background_story",
    }
    stat_impacts = list(getattr(result, "stat_impacts", ()) or [])
    stat_impact = getattr(result, "stat_impact", None) or (
        stat_impacts[0] if stat_impacts else None
    )
    if not stat_impacts and stat_impact:
        stat_impacts = [stat_impact]
    if isinstance(recent_story_event, dict):
        result.prompt_debug.append(
            {
                "event": "background_story_recent_event_saved",
                "recentEventId": recent_story_event.get("id"),
                "title": recent_story_event.get("title"),
            }
        )
    last_story = {
        "title": result.title,
        "summary": result.summary,
        "storyText": result.story_text,
        "generatedAt": now_iso,
        "eventType": result.event_type,
        "valence": result.valence,
        "tags": list(result.tags),
        "ragText": result.rag_text,
        "statImpacts": stat_impacts,
        "statImpact": stat_impact,
        "statValidation": getattr(result, "stat_validation", None),
    }
    if idempotency_key:
        last_story["requestKey"] = idempotency_key
    stats_patch: LocalPetStatsPatch | None = None
    stats_delta: dict[str, int] | None = None
    committed_story: dict[str, Any] | None = None

    def save_story(current: dict[str, Any] | None) -> dict[str, Any]:
        nonlocal committed_story, stats_delta, stats_patch
        source_record = current.copy() if isinstance(current, dict) else record.copy()
        if source_record.get("petId") != record.get("petId"):
            raise TelegramPushError(
                "PUSH_PET_CHANGED",
                "Питомец изменился во время генерации истории. Повтори запрос.",
            )
        existing_receipt = _bot_generation_receipt(
            source_record,
            request_key=idempotency_key,
            kind="story",
        )
        if existing_receipt is not None:
            committed_story = deepcopy(existing_receipt["story"])
            existing_delta = committed_story.get("statsDelta")
            stats_delta = existing_delta if isinstance(existing_delta, dict) else None
            return source_record
        existing_story = source_record.get("lastStory")
        if (
            idempotency_key
            and isinstance(existing_story, dict)
            and existing_story.get("requestKey") == idempotency_key
        ):
            committed_story = deepcopy(existing_story)
            existing_delta = existing_story.get("statsDelta")
            stats_delta = existing_delta if isinstance(existing_delta, dict) else None
            return source_record
        next_pet, stats_patch, stat_ticks, stats_delta = _apply_story_stat_impact(
            source_record,
            stat_impacts,
            now=now,
        )
        persisted_story = deepcopy(last_story)
        if stats_delta is not None:
            persisted_story["statsDelta"] = stats_delta
        committed_story = deepcopy(persisted_story)
        next_record = {
            **source_record,
            "pet": next_pet,
            "lastStoryAt": now_iso,
            "lastStoryAttemptAt": now_iso,
            "lastStoryError": None,
            "lastStoryErrorCode": None,
            "lastStoryErrorAt": None,
            "lastStory": persisted_story,
            "recentStoryEvents": _append_recent_story_event(
                source_record,
                history_event,
                last_story=persisted_story,
            ),
            "storyNoveltyHistory": _append_story_novelty_item(
                source_record,
                history_event,
            ),
        }
        if idempotency_key:
            next_record["botGenerationReceipts"] = _append_bot_generation_receipt(
                source_record,
                {
                    "requestKey": idempotency_key,
                    "kind": "story",
                    "generatedAt": now_iso,
                    "story": persisted_story,
                },
            )
        _merge_record_lite_overlay_patch(next_record, result.lite_overlay_patch)
        if stat_ticks is not None:
            next_record["lastStatsTickAt"] = _legacy_stats_tick(stat_ticks)
            next_record["lastStatTickAt"] = _stat_tick_iso_map(stat_ticks)
        return next_record

    if assert_active is not None:
        assert_active()
    next_record = _update_record(_telegram_id_from_record(record), save_story)
    if "storyCommitted" not in progress:
        progress = _checkpoint_durable_progress(
            progress,
            "storyCommitted",
            {
                "generatedAt": now_iso,
                "story": (
                    deepcopy(committed_story)
                    if isinstance(committed_story, dict)
                    else deepcopy(last_story)
                ),
            },
            checkpoint,
        )
    story_image: dict[str, Any] | None = None
    story_image_error: str | None = None
    story_image_url: str | None = None
    story_video: dict[str, Any] | None = None
    story_video_error: str | None = None
    story_video_url: str | None = None
    story_image_direction: dict[str, str] = {}
    image_bytes: bytes | None = None
    image_stage = progress.get("image")
    if "image" in progress and not isinstance(image_stage, dict):
        raise TelegramPushError("DURABLE_STORY_INVALID", "Сохранённое изображение повреждено.")
    if isinstance(image_stage, dict):
        story_image_url = (
            image_stage.get("url") if isinstance(image_stage.get("url"), str) else None
        )
        stored_direction = image_stage.get("direction")
        if isinstance(stored_direction, dict):
            story_image_direction.update(
                {str(key): str(value) for key, value in stored_direction.items()}
            )
        image_bytes = _persisted_background_story_media_bytes(
            record,
            story_image_url,
            suffix=".png",
        )
        if image_bytes is None:
            story_image_error = "PERSISTED_STORY_IMAGE_MISSING"
    else:
        recovered_image = _existing_background_story_media(
            record,
            generated_at=now,
            suffix=".png",
        )
        if recovered_image is not None:
            story_image_url, image_bytes = recovered_image
        else:
            if assert_active is not None:
                assert_active()
            try:
                with reserve_background_story_image_bytes(
                    pet=payload.pet,
                    story=result,
                    recent_story_events=_record_recent_story_events(record),
                    direction_output=story_image_direction,
                ) as image_bytes:
                    story_image_url = _persist_background_story_image(
                        record,
                        image_bytes,
                        generated_at=now,
                    )
            except Exception as exc:
                logger.exception("background_story_image_generation failed")
                story_image_error = exc.__class__.__name__
                image_bytes = None

        if image_bytes is not None and story_image_url is not None:
            progress = _checkpoint_durable_progress(
                progress,
                "image",
                {"url": story_image_url, "direction": story_image_direction},
                checkpoint,
            )

    if image_bytes is not None:
        story_image = {
            "bytes": image_bytes,
            "mimeType": "image/png",
        }

    video_bytes: bytes | None = None
    video_stage = progress.get("video")
    if "video" in progress and not isinstance(video_stage, dict):
        raise TelegramPushError("DURABLE_STORY_INVALID", "Сохранённое видео повреждено.")
    if image_bytes is not None and isinstance(video_stage, dict):
        story_video_url = (
            video_stage.get("url") if isinstance(video_stage.get("url"), str) else None
        )
        video_bytes = _persisted_background_story_media_bytes(
            record,
            story_video_url,
            suffix=".mp4",
        )
        if video_bytes is None:
            story_video_error = "PERSISTED_STORY_VIDEO_MISSING"
    elif image_bytes is not None:
        recovered_video = _existing_background_story_media(
            record,
            generated_at=now,
            suffix=".mp4",
        )
        if recovered_video is not None:
            story_video_url, video_bytes = recovered_video
        else:
            if assert_active is not None:
                assert_active()
            try:
                with reserve_background_story_video_bytes(image_bytes) as video_bytes:
                    if not video_bytes:
                        raise RuntimeError("BACKGROUND_STORY_VIDEO_EMPTY")
                    story_video_url = _persist_background_story_video(
                        record,
                        video_bytes,
                        generated_at=now,
                    )
            except Exception as exc:
                logger.exception("background_story_video_generation failed")
                story_video_error = exc.__class__.__name__
                video_bytes = None

        if video_bytes is not None and story_video_url is not None:
            progress = _checkpoint_durable_progress(
                progress,
                "video",
                {"url": story_video_url},
                checkpoint,
            )

    if video_bytes is not None:
        story_video = {
            "bytes": video_bytes,
            "mimeType": "video/mp4",
        }

    image_status_at = _iso()

    def save_story_image_status(current: dict[str, Any] | None) -> dict[str, Any]:
        source_record = current.copy() if isinstance(current, dict) else next_record.copy()
        if source_record.get("petId") != record.get("petId"):
            return source_record
        if source_record.get("lastStoryAt") != now_iso:
            return source_record
        last_story_record = (
            source_record["lastStory"].copy()
            if isinstance(source_record.get("lastStory"), dict)
            else {}
        )
        recent_events = []
        for item in source_record.get("recentStoryEvents", []):
            if not isinstance(item, dict):
                continue
            next_item = item.copy()
            if story_image_url and item.get("generatedAt") == now_iso:
                next_item["imageUrl"] = story_image_url
                next_item["videoUrl"] = story_video_url
                next_item["imagePoseFamily"] = story_image_direction.get("poseFamily")
                next_item["imageHeroPose"] = story_image_direction.get("heroPose")
                next_item["imageCamera"] = story_image_direction.get("camera")
                next_item["imageColorPalette"] = story_image_direction.get("colorPalette")
                next_item["imageAccentColor"] = story_image_direction.get("accentColor")
                next_item["imagePaletteFamily"] = story_image_direction.get("paletteFamily")
            recent_events.append(next_item)
        if story_image_url:
            last_story_record["imageUrl"] = story_image_url
            last_story_record["videoUrl"] = story_video_url
            last_story_record["imagePoseFamily"] = story_image_direction.get("poseFamily")
            last_story_record["imageHeroPose"] = story_image_direction.get("heroPose")
            last_story_record["imageCamera"] = story_image_direction.get("camera")
            last_story_record["imageColorPalette"] = story_image_direction.get("colorPalette")
            last_story_record["imageAccentColor"] = story_image_direction.get("accentColor")
            last_story_record["imagePaletteFamily"] = story_image_direction.get("paletteFamily")
        return {
            **source_record,
            "lastStory": last_story_record,
            "recentStoryEvents": recent_events,
            "lastStoryImageStatus": "failed" if story_image_error else "generated",
            "lastStoryImageError": story_image_error,
            "lastStoryImageErrorAt": image_status_at if story_image_error else None,
            "lastStoryVideoStatus": "generated" if story_video else "failed",
            "lastStoryVideoError": story_video_error or story_image_error,
            "lastStoryVideoErrorAt": image_status_at if story_video is None else None,
        }

    if assert_active is not None:
        assert_active()
    next_record = _update_record(
        _telegram_id_from_record(record),
        save_story_image_status,
    )
    stored_recent_events = _record_recent_story_events(next_record)
    stored_recent_story_event = next(
        (item for item in reversed(stored_recent_events) if item.get("generatedAt") == now_iso),
        history_event,
    )
    current_story = next_record.get("lastStory")
    committed_stage = progress.get("storyCommitted")
    committed_stage_story = (
        committed_stage.get("story") if isinstance(committed_stage, dict) else None
    )
    if isinstance(current_story, dict) and (
        not idempotency_key or current_story.get("requestKey") == idempotency_key
    ):
        delivery_story = deepcopy(current_story)
    elif isinstance(committed_stage_story, dict):
        delivery_story = deepcopy(committed_stage_story)
        if story_image_url:
            delivery_story.update(
                {
                    "imageUrl": story_image_url,
                    "videoUrl": story_video_url,
                    "imagePoseFamily": story_image_direction.get("poseFamily"),
                    "imageHeroPose": story_image_direction.get("heroPose"),
                    "imageCamera": story_image_direction.get("camera"),
                    "imageColorPalette": story_image_direction.get("colorPalette"),
                    "imageAccentColor": story_image_direction.get("accentColor"),
                    "imagePaletteFamily": story_image_direction.get("paletteFamily"),
                }
            )
    else:
        delivery_story = deepcopy(last_story)
    prepared_result = {
        "petId": record.get("petId"),
        "story": delivery_story,
    }
    progress = _checkpoint_durable_progress(
        progress,
        "preparedResult",
        prepared_result,
        checkpoint,
    )
    _cleanup_background_story_media_for_records([next_record], now=now)
    return {
        "generated": True,
        "telegramId": record.get("telegramId"),
        "petId": record.get("petId"),
        "generatedAt": now_iso,
        "story": delivery_story,
        "storyImage": story_image,
        "storyImageError": story_image_error,
        "storyVideo": story_video,
        "storyVideoError": story_video_error or story_image_error,
        "storyLibraryPatch": None,
        "liteOverlayPatch": _record_lite_overlay_patch(next_record),
        "recentStoryEvent": stored_recent_story_event,
        "statsPatch": stats_patch,
        "statImpacts": stat_impacts,
        "statImpact": stat_impact,
        "debug": {"promptDebug": result.prompt_debug} if include_debug else None,
    }


def _prepare_full_story_for_telegram_user(
    telegram_id: int,
) -> tuple[dict[str, Any], datetime, LocalPetChatContext, Any, str]:
    record = _record_by_telegram_id(telegram_id)
    now = _now()
    if _record_is_dead(record, now):
        raise TelegramPushError("PET_DEAD", "Питомец умер и больше не может путешествовать.")
    pet = LocalPetChatContext.model_validate(_current_pet_record(record, now))
    full_story_history = _record_full_story_history(record)
    result = generate_full_story(pet=pet, recent_full_stories=full_story_history)
    return record, now, pet, result, _iso(now)


def _full_story_result_payload(result: Any) -> dict[str, Any]:
    return {
        "overallTitle": str(result.overall_title),
        "arcPlan": deepcopy(result.arc_plan),
        "storyDirection": deepcopy(result.story_direction),
        "parts": [part.model_dump() for part in result.parts],
        # Debug prompts are not required to resume paid media and can be large.
        "promptDebug": [],
    }


def _full_story_result_from_payload(payload: Any) -> FullStoryResult:
    if not isinstance(payload, dict):
        raise TelegramPushError(
            "DURABLE_FULL_STORY_INVALID",
            "Сохранённый текст большой истории повреждён.",
        )
    overall_title = payload.get("overallTitle")
    arc_plan = payload.get("arcPlan")
    story_direction = payload.get("storyDirection")
    raw_parts = payload.get("parts")
    if (
        not isinstance(overall_title, str)
        or not isinstance(arc_plan, dict)
        or not isinstance(story_direction, dict)
        or not isinstance(raw_parts, list)
        or not raw_parts
    ):
        raise TelegramPushError(
            "DURABLE_FULL_STORY_INVALID",
            "Сохранённый текст большой истории повреждён.",
        )
    parts: list[FullStoryPart] = []
    for raw_part in raw_parts:
        if not isinstance(raw_part, dict):
            raise TelegramPushError(
                "DURABLE_FULL_STORY_INVALID",
                "Сохранённая часть большой истории повреждена.",
            )
        part_number = raw_part.get("partNumber")
        if type(part_number) is not int:
            raise TelegramPushError(
                "DURABLE_FULL_STORY_INVALID",
                "Номер сохранённой части большой истории повреждён.",
            )
        raw_impacts = raw_part.get("statImpacts")
        parts.append(
            FullStoryPart(
                part_number=part_number,
                title=str(raw_part.get("title") or ""),
                summary=str(raw_part.get("summary") or ""),
                story_text=str(raw_part.get("storyText") or ""),
                valence=str(raw_part.get("valence") or "mixed"),
                stat_impacts=tuple(
                    item
                    for item in (raw_impacts if isinstance(raw_impacts, list) else [])
                    if isinstance(item, dict)
                ),
            )
        )
    return FullStoryResult(
        overall_title=overall_title,
        arc_plan={str(key): str(value) for key, value in arc_plan.items()},
        story_direction={str(key): str(value) for key, value in story_direction.items()},
        parts=tuple(parts),
        prompt_debug=[],
    )


def _full_story_draft(result: Any, generated_at: str) -> dict[str, Any]:
    return {
        "overallTitle": result.overall_title,
        "arcPlan": result.arc_plan,
        "storyDirection": result.story_direction,
        "parts": [part.model_dump() for part in result.parts],
        "generatedAt": generated_at,
        "source": "full_story_command",
    }


def _commit_full_story(
    *,
    telegram_id: int,
    initial_record: dict[str, Any],
    result: Any,
    generated_at: str,
    now: datetime,
    media_by_part: dict[int, dict[str, Any]] | None = None,
    include_debug: bool = False,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    committed_story: dict[str, Any] = {}

    def save_full_story(current: dict[str, Any] | None) -> dict[str, Any]:
        nonlocal committed_story
        source = current.copy() if isinstance(current, dict) else initial_record.copy()
        if source.get("petId") != initial_record.get("petId"):
            raise TelegramPushError(
                "PUSH_PET_CHANGED",
                "Питомец изменился во время генерации истории. Повтори запрос.",
            )
        existing_receipt = _bot_generation_receipt(
            source,
            request_key=idempotency_key,
            kind="full_story",
        )
        if existing_receipt is not None:
            committed_story = deepcopy(existing_receipt["story"])
            return source
        existing_story = source.get("lastFullStory")
        if (
            idempotency_key
            and isinstance(existing_story, dict)
            and existing_story.get("requestKey") == idempotency_key
        ):
            committed_story = deepcopy(existing_story)
            return source
        if _record_is_dead(source, now):
            raise TelegramPushError("PET_DEAD", "Питомец умер и больше не может путешествовать.")

        working_record = deepcopy(source)
        applied_parts: list[dict[str, Any]] = []
        aggregate_delta = {key: 0 for key in STAT_KEYS}

        for index, part in enumerate(result.parts, start=1):
            next_pet, _stats_patch_value, ticks, stats_delta = _apply_story_stat_impact(
                working_record,
                list(part.stat_impacts),
                now=now,
            )
            working_record["pet"] = next_pet
            if ticks is not None:
                working_record["lastStatsTickAt"] = _legacy_stats_tick(ticks)
                working_record["lastStatTickAt"] = _stat_tick_iso_map(ticks)
            actual_delta = stats_delta or {key: 0 for key in STAT_KEYS}
            for key in STAT_KEYS:
                aggregate_delta[key] += actual_delta.get(key, 0)
            part_payload = part.model_dump()
            part_number = part_payload.get("partNumber")
            if not isinstance(part_number, int):
                part_number = index
            part_payload.update((media_by_part or {}).get(part_number, {}))
            part_payload["statsDelta"] = actual_delta
            applied_parts.append(part_payload)

        committed_story = {
            "overallTitle": result.overall_title,
            "arcPlan": result.arc_plan,
            "storyDirection": result.story_direction,
            "parts": applied_parts,
            "generatedAt": generated_at,
            "statsDelta": aggregate_delta,
            "source": "full_story_command",
        }
        if idempotency_key:
            committed_story["requestKey"] = idempotency_key
        next_record = {
            **source,
            "pet": working_record["pet"],
            "lastStatsTickAt": working_record.get("lastStatsTickAt"),
            "lastStatTickAt": working_record.get("lastStatTickAt"),
            "lastFullStoryAt": generated_at,
            "lastFullStory": committed_story,
            "fullStoryHistory": _append_full_story_history(
                source,
                committed_story,
            ),
        }
        if idempotency_key:
            next_record["botGenerationReceipts"] = _append_bot_generation_receipt(
                source,
                {
                    "requestKey": idempotency_key,
                    "kind": "full_story",
                    "generatedAt": generated_at,
                    "story": committed_story,
                },
            )
        return next_record

    saved_record = _update_record(telegram_id, save_full_story)
    _cleanup_background_story_media_for_records([saved_record], now=now)
    final_stats = _record_current_stats(saved_record, now)
    return {
        "generated": True,
        "telegramId": telegram_id,
        "petId": initial_record.get("petId"),
        "generatedAt": generated_at,
        "story": committed_story,
        "statsPatch": {
            "stats": final_stats,
            "lastStatsTickAt": saved_record.get("lastStatsTickAt"),
            "lastStatTickAt": saved_record.get("lastStatTickAt"),
        },
        "debug": {"promptDebug": result.prompt_debug} if include_debug else None,
    }


def generate_full_story_for_telegram_user(
    *,
    telegram_id: int,
    include_debug: bool = False,
) -> dict[str, Any]:
    record, now, _pet, result, generated_at = _prepare_full_story_for_telegram_user(telegram_id)
    return _commit_full_story(
        telegram_id=telegram_id,
        initial_record=record,
        result=result,
        generated_at=generated_at,
        now=now,
        include_debug=include_debug,
    )


def prepare_full_story_for_telegram_delivery(
    *,
    telegram_id: int,
    idempotency_key: str | None = None,
    durable_progress: dict[str, Any] | None = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
    assert_active: Callable[[], None] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    progress = deepcopy(durable_progress) if isinstance(durable_progress, dict) else {}
    text_stage = progress.get("text")
    if "text" in progress and not isinstance(text_stage, dict):
        raise TelegramPushError(
            "DURABLE_FULL_STORY_INVALID",
            "Сохранённый текст большой истории повреждён.",
        )
    if isinstance(text_stage, dict):
        record = _record_by_telegram_id(telegram_id)
        if text_stage.get("petId") != record.get("petId"):
            raise TelegramPushError(
                "PUSH_PET_CHANGED",
                "Питомец изменился во время генерации истории. Повтори запрос.",
            )
        generated_at_time = _parse_iso(text_stage.get("generatedAt"))
        if generated_at_time is None:
            raise TelegramPushError(
                "DURABLE_FULL_STORY_INVALID",
                "В сохранённой большой истории отсутствует время генерации.",
            )
        if _record_is_dead(record, generated_at_time):
            raise TelegramPushError("PET_DEAD", "Питомец умер и больше не может путешествовать.")
        pet = LocalPetChatContext.model_validate(_current_pet_record(record, generated_at_time))
        generated_result = _full_story_result_from_payload(text_stage.get("result"))
        generated_at = _iso(generated_at_time)
    else:
        record, generated_at_time, pet, generated_result, generated_at = (
            _prepare_full_story_for_telegram_user(telegram_id)
        )
        progress = _checkpoint_durable_progress(
            progress,
            "text",
            {
                "petId": record.get("petId"),
                "generatedAt": generated_at,
                "result": _full_story_result_payload(generated_result),
            },
            checkpoint,
        )

    story = _full_story_draft(generated_result, generated_at)
    parts = story.get("parts") if isinstance(story.get("parts"), list) else []
    generated_parts: list[dict[str, Any]] = []
    pose_history = [*_record_recent_story_events(record)]

    for index, raw_part in enumerate(parts):
        if not isinstance(raw_part, dict):
            continue
        part = raw_part.copy()
        part_number = part.get("partNumber")
        if type(part_number) is not int:
            part_number = index + 1
        media_time = generated_at_time + timedelta(microseconds=index)
        image_key = f"part:{part_number}:image"
        image_stage = progress.get(image_key)
        if image_key in progress and not isinstance(image_stage, dict):
            raise TelegramPushError(
                "DURABLE_FULL_STORY_INVALID",
                f"Сохранённое изображение части {part_number} повреждено.",
            )
        direction: dict[str, str] = {}
        image_url: str | None = None
        image_bytes: bytes | None = None
        if isinstance(image_stage, dict):
            image_url = image_stage.get("url") if isinstance(image_stage.get("url"), str) else None
            stored_direction = image_stage.get("direction")
            if isinstance(stored_direction, dict):
                direction.update({str(key): str(value) for key, value in stored_direction.items()})
            image_bytes = _persisted_background_story_media_bytes(
                record,
                image_url,
                suffix=".png",
            )
            if image_bytes is None:
                raise TelegramPushError(
                    "FULL_STORY_MEDIA_MISSING",
                    f"Сохранённое изображение части {part_number} недоступно.",
                )
        else:
            recovered_image = _existing_background_story_media(
                record,
                generated_at=media_time,
                suffix=".png",
            )
            if recovered_image is not None:
                image_url, image_bytes = recovered_image
            else:
                if assert_active is not None:
                    assert_active()
                try:
                    with reserve_full_story_part_image_bytes(
                        pet=pet,
                        overall_title=str(story.get("overallTitle") or "История одного дня"),
                        part=part,
                        recent_story_events=pose_history,
                        direction_output=direction,
                    ) as image_bytes:
                        if not image_bytes:
                            raise RuntimeError("FULL_STORY_IMAGE_EMPTY")
                        image_url = _persist_background_story_image(
                            record,
                            image_bytes,
                            generated_at=media_time,
                        )
                except Exception as exc:
                    logger.exception("full_story_image_generation failed")
                    raise TelegramPushError(
                        "FULL_STORY_MEDIA_FAILED",
                        f"Не удалось создать изображение части: {exc.__class__.__name__}",
                    ) from exc
            progress = _checkpoint_durable_progress(
                progress,
                image_key,
                {"url": image_url, "direction": direction},
                checkpoint,
            )

        video_key = f"part:{part_number}:video"
        video_stage = progress.get(video_key)
        if video_key in progress and not isinstance(video_stage, dict):
            raise TelegramPushError(
                "DURABLE_FULL_STORY_INVALID",
                f"Сохранённое видео части {part_number} повреждено.",
            )
        video_url: str | None = None
        video_bytes: bytes | None = None
        if isinstance(video_stage, dict):
            video_url = video_stage.get("url") if isinstance(video_stage.get("url"), str) else None
            video_bytes = _persisted_background_story_media_bytes(
                record,
                video_url,
                suffix=".mp4",
            )
            if video_bytes is None:
                raise TelegramPushError(
                    "FULL_STORY_MEDIA_MISSING",
                    f"Сохранённое видео части {part_number} недоступно.",
                )
        else:
            recovered_video = _existing_background_story_media(
                record,
                generated_at=media_time,
                suffix=".mp4",
            )
            if recovered_video is not None:
                video_url, video_bytes = recovered_video
            else:
                if assert_active is not None:
                    assert_active()
                try:
                    with reserve_background_story_video_bytes(image_bytes) as video_bytes:
                        if not video_bytes:
                            raise RuntimeError("FULL_STORY_VIDEO_EMPTY")
                        video_url = _persist_background_story_video(
                            record,
                            video_bytes,
                            generated_at=media_time,
                        )
                except Exception as exc:
                    logger.exception("full_story_video_generation failed")
                    raise TelegramPushError(
                        "FULL_STORY_MEDIA_FAILED",
                        f"Не удалось создать видео части: {exc.__class__.__name__}",
                    ) from exc
            progress = _checkpoint_durable_progress(
                progress,
                video_key,
                {"url": video_url},
                checkpoint,
            )

        enriched_part = {
            **part,
            "imageUrl": image_url,
            "videoUrl": video_url,
            "imagePoseFamily": direction.get("poseFamily"),
            "imageHeroPose": direction.get("heroPose"),
            "imageCamera": direction.get("camera"),
            "imageColorPalette": direction.get("colorPalette"),
            "imageAccentColor": direction.get("accentColor"),
            "imagePaletteFamily": direction.get("paletteFamily"),
        }
        generated_parts.append(
            {
                "partNumber": part_number,
                "media": {key: value for key, value in enriched_part.items() if key not in part},
                "video": video_bytes,
            }
        )
        pose_history.append(enriched_part)

    if assert_active is not None:
        assert_active()
    result = _commit_full_story(
        telegram_id=telegram_id,
        initial_record=record,
        result=generated_result,
        generated_at=generated_at,
        now=generated_at_time,
        media_by_part={
            int(item["partNumber"]): item["media"]
            for item in generated_parts
            if isinstance(item.get("partNumber"), int) and isinstance(item.get("media"), dict)
        },
        idempotency_key=idempotency_key,
    )
    progress = _checkpoint_durable_progress(
        progress,
        "preparedResult",
        {"petId": result.get("petId"), "story": result.get("story")},
        checkpoint,
    )
    return result, generated_parts


def deliver_prepared_full_story_for_telegram_user(
    client: httpx.Client,
    *,
    telegram_id: int,
    keyboard: dict[str, Any],
    prepared_result: dict[str, Any],
    generated_parts: list[dict[str, Any]] | None = None,
    assert_active: Callable[[], None] | None = None,
) -> None:
    story = prepared_result.get("story")
    if not isinstance(story, dict):
        raise TelegramPushError("FULL_STORY_DELIVERY_INVALID", "Не найдена сохранённая история.")
    parts = story.get("parts")
    if not isinstance(parts, list) or not parts:
        raise TelegramPushError("FULL_STORY_DELIVERY_INVALID", "Не найдены части истории.")

    in_memory_videos = {
        item.get("partNumber"): item.get("video")
        for item in generated_parts or []
        if isinstance(item, dict)
        and isinstance(item.get("partNumber"), int)
        and isinstance(item.get("video"), bytes)
        and item.get("video")
    }
    pet_id = prepared_result.get("petId")
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_number = part.get("partNumber")
        video_bytes = in_memory_videos.get(part_number)
        if not isinstance(video_bytes, bytes) or not video_bytes:
            video_bytes = load_persisted_story_video_bytes(
                pet_id=pet_id,
                telegram_id=telegram_id,
                media_url=part.get("videoUrl"),
            )
        caption = format_full_story_part_message(story, part)
        if assert_active is not None:
            assert_active()
        if video_bytes:
            send_video(client, telegram_id, video_bytes, caption, keyboard)
        else:
            logger.error(
                "Persisted full-story video is unavailable telegramId=%s partNumber=%s",
                telegram_id,
                part_number,
            )
            send_message(client, telegram_id, caption, keyboard)


def send_full_story_for_telegram_user(
    client: httpx.Client,
    *,
    telegram_id: int,
    keyboard: dict[str, Any],
) -> dict[str, Any]:
    result, generated_parts = prepare_full_story_for_telegram_delivery(
        telegram_id=telegram_id,
    )
    deliver_prepared_full_story_for_telegram_user(
        client,
        telegram_id=telegram_id,
        keyboard=keyboard,
        prepared_result=result,
        generated_parts=generated_parts,
    )
    return result


def _daily_full_story_context(
    record: dict[str, Any],
    local_date: str,
    timezone_name: str,
) -> dict[str, Any]:
    hours = _background_story_hours(get_settings())
    return {
        "mode": "automatic_daily_delivery",
        "localDate": local_date,
        "timezone": timezone_name,
        "parts": [
            {
                "partNumber": index,
                "scheduledLocalTime": f"{hour:02d}:00",
                "dayPeriod": _day_period(hour),
            }
            for index, hour in enumerate(hours, start=1)
        ],
        "rule": (
            "Учитывай время только как мягкий контекст. Для уличной сцены согласуй "
            "естественный свет и окружение; не называй время без сюжетной необходимости."
        ),
    }


def _generate_daily_full_story(
    record: dict[str, Any],
    *,
    now: datetime,
    local_date: str,
    timezone_name: str,
) -> dict[str, Any]:
    pet = LocalPetChatContext.model_validate(_current_pet_record(record, now))
    history = _record_full_story_history(record)
    day_context = _daily_full_story_context(record, local_date, timezone_name)
    result = generate_full_story(
        pet=pet,
        recent_full_stories=history,
        day_context=day_context,
    )
    generated_at = _iso(now)
    schedule = day_context["parts"]
    story_payload = {
        "overallTitle": result.overall_title,
        "arcPlan": result.arc_plan,
        "storyDirection": result.story_direction,
        "parts": [
            {
                **part.model_dump(),
                "scheduledLocalTime": schedule[index]["scheduledLocalTime"],
                "dayPeriod": schedule[index]["dayPeriod"],
            }
            for index, part in enumerate(result.parts)
        ],
        "generatedAt": generated_at,
        "localDate": local_date,
        "timezone": timezone_name,
        "source": "automatic_daily_full_story",
    }

    def save_daily_story(current: dict[str, Any] | None) -> dict[str, Any]:
        source = current.copy() if isinstance(current, dict) else record.copy()
        if source.get("petId") != record.get("petId"):
            raise TelegramPushError(
                "PUSH_PET_CHANGED",
                "Питомец изменился во время генерации истории. Повтори запрос.",
            )
        return {
            **source,
            "dailyFullStory": story_payload,
            "lastFullStoryAt": generated_at,
            "lastFullStory": story_payload,
            "fullStoryHistory": _append_full_story_history(source, story_payload),
        }

    return _update_record(_telegram_id_from_record(record), save_daily_story)


def _daily_full_story_part(
    record: dict[str, Any],
    *,
    local_date: str,
    part_index: int,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    story = record.get("dailyFullStory")
    if not isinstance(story, dict) or story.get("localDate") != local_date:
        return None
    parts = story.get("parts") if isinstance(story.get("parts"), list) else []
    if part_index >= len(parts) or not isinstance(parts[part_index], dict):
        return None
    return story, parts[part_index]


def _daily_full_story_attempt_due(
    record: dict[str, Any],
    *,
    attempt_key: str,
    now: datetime,
) -> bool:
    if record.get("dailyFullStoryAttemptKey") != attempt_key:
        return True
    attempts = int(record.get("dailyFullStoryAttemptCount") or 0)
    if attempts >= DAILY_FULL_STORY_MAX_ATTEMPTS:
        return False
    last_attempt = _parse_iso(record.get("dailyFullStoryAttemptAt"))
    return last_attempt is None or now - last_attempt >= DAILY_FULL_STORY_RETRY_DELAY


def _mark_daily_full_story_attempt(
    record: dict[str, Any],
    *,
    attempt_key: str,
    now: datetime,
) -> dict[str, Any]:
    def save_attempt(current: dict[str, Any] | None) -> dict[str, Any]:
        source = current.copy() if isinstance(current, dict) else record.copy()
        previous_count = (
            int(source.get("dailyFullStoryAttemptCount") or 0)
            if source.get("dailyFullStoryAttemptKey") == attempt_key
            else 0
        )
        return {
            **source,
            "dailyFullStoryAttemptKey": attempt_key,
            "dailyFullStoryAttemptCount": previous_count + 1,
            "dailyFullStoryAttemptAt": _iso(now),
            "lastStoryAttemptAt": _iso(now),
        }

    return _update_record(_telegram_id_from_record(record), save_attempt)


def _apply_daily_full_story_part_stats(
    record: dict[str, Any],
    *,
    local_date: str,
    part_index: int,
    now: datetime,
) -> dict[str, Any]:
    def apply_part(current: dict[str, Any] | None) -> dict[str, Any]:
        source = current.copy() if isinstance(current, dict) else record.copy()
        current_part = _daily_full_story_part(
            source,
            local_date=local_date,
            part_index=part_index,
        )
        if current_part is None:
            raise TelegramPushError("DAILY_FULL_STORY_MISSING", "История дня не найдена.")
        story, part = current_part
        if part.get("statsAppliedAt"):
            return source
        next_pet, _stats_patch_value, ticks, stats_delta = _apply_story_stat_impact(
            source,
            part.get("statImpacts"),
            now=now,
        )
        next_story = deepcopy(story)
        next_part = next_story["parts"][part_index]
        next_part["statsAppliedAt"] = _iso(now)
        next_part["statsDelta"] = stats_delta or {key: 0 for key in STAT_KEYS}
        result = {**source, "pet": next_pet, "dailyFullStory": next_story}
        if ticks is not None:
            result["lastStatsTickAt"] = _legacy_stats_tick(ticks)
            result["lastStatTickAt"] = _stat_tick_iso_map(ticks)
        last_full_story = source.get("lastFullStory")
        if isinstance(last_full_story, dict) and last_full_story.get("generatedAt") == story.get(
            "generatedAt"
        ):
            result["lastFullStory"] = next_story
        return result

    return _update_record(_telegram_id_from_record(record), apply_part)


def _checkpoint_daily_full_story_media(
    record: dict[str, Any],
    *,
    local_date: str,
    part_index: int,
    story_generated_at: Any,
    image_url: str | None,
    video_url: str | None,
    image_direction: dict[str, str],
    now: datetime,
) -> dict[str, Any]:
    def save_media(current: dict[str, Any] | None) -> dict[str, Any]:
        source = current.copy() if isinstance(current, dict) else record.copy()
        current_part_value = _daily_full_story_part(
            source,
            local_date=local_date,
            part_index=part_index,
        )
        if current_part_value is None:
            raise TelegramPushError("DAILY_FULL_STORY_MISSING", "История дня не найдена.")
        current_story, _current_part = current_part_value
        if current_story.get("generatedAt") != story_generated_at:
            raise TelegramPushError(
                "DAILY_FULL_STORY_REPLACED",
                "История дня изменилась во время генерации медиа.",
            )
        next_story = deepcopy(current_story)
        next_part = next_story["parts"][part_index]
        if image_url is not None:
            next_part["imageUrl"] = image_url
            next_part["imagePreparedAt"] = _iso(now)
        if video_url is not None:
            next_part["videoUrl"] = video_url
            next_part["mediaPreparedAt"] = _iso(now)
        next_part["imagePoseFamily"] = image_direction.get("poseFamily")
        next_part["imageHeroPose"] = image_direction.get("heroPose")
        next_part["imageCamera"] = image_direction.get("camera")
        next_part["imageColorPalette"] = image_direction.get("colorPalette")
        next_part["imageAccentColor"] = image_direction.get("accentColor")
        next_part["imagePaletteFamily"] = image_direction.get("paletteFamily")
        result = {**source, "dailyFullStory": next_story}
        last_full_story = source.get("lastFullStory")
        if isinstance(last_full_story, dict) and last_full_story.get(
            "generatedAt"
        ) == current_story.get("generatedAt"):
            result["lastFullStory"] = next_story
        return result

    return _update_record(_telegram_id_from_record(record), save_media)


def _consume_background_story_paid_media_budget(*, stage: str) -> None:
    consume_background_story_paid_media_budget(get_settings(), stage=stage)


def _send_daily_full_story_part(
    record: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.bot_token:
        raise TelegramPushError("BOT_TOKEN_MISSING", "BOT_TOKEN не настроен.")
    if not settings.webapp_url:
        raise TelegramPushError("WEBAPP_URL_MISSING", "WEBAPP_URL не настроен.")
    slot = _background_story_slot(record, now)
    if slot is None:
        raise TelegramPushError("DAILY_FULL_STORY_NOT_DUE", "Сейчас нет окна отправки.")
    part_index, local_slot, timezone_name = slot
    local_date = local_slot.date().isoformat()
    attempt_key = f"{local_date}:{part_index + 1}"
    record = _mark_daily_full_story_attempt(record, attempt_key=attempt_key, now=now)
    current_part = _daily_full_story_part(
        record,
        local_date=local_date,
        part_index=part_index,
    )
    if current_part is None:
        if part_index != 0:
            raise TelegramPushError(
                "DAILY_FULL_STORY_NOT_STARTED",
                "Первая часть истории дня не была создана.",
            )
        record = _generate_daily_full_story(
            record,
            now=now,
            local_date=local_date,
            timezone_name=timezone_name,
        )
        current_part = _daily_full_story_part(
            record,
            local_date=local_date,
            part_index=part_index,
        )
    if current_part is None:
        raise TelegramPushError("DAILY_FULL_STORY_MISSING", "История дня не найдена.")
    story, part = current_part
    if part.get("deliveredAt"):
        raise TelegramPushError("DAILY_FULL_STORY_ALREADY_SENT", "Эта часть уже отправлена.")
    story_generated_at = story.get("generatedAt")
    story_generated_time = _parse_iso(story_generated_at)
    if story_generated_time is None:
        raise TelegramPushError(
            "DAILY_FULL_STORY_INVALID",
            "В сохранённой истории дня отсутствует время генерации.",
        )
    media_time = story_generated_time + timedelta(microseconds=part_index)

    pet = LocalPetChatContext.model_validate(_current_pet_record(record, now))
    image_url = part.get("imageUrl") if isinstance(part.get("imageUrl"), str) else None
    video_url = part.get("videoUrl") if isinstance(part.get("videoUrl"), str) else None
    image_bytes = _persisted_background_story_media_bytes(record, image_url, suffix=".png")
    video_bytes = _persisted_background_story_media_bytes(record, video_url, suffix=".mp4")
    image_error: str | None = None
    media_budget_error: _BackgroundStoryPaidMediaBudgetError | None = None
    image_direction = {
        key: value
        for key, field in (
            ("poseFamily", "imagePoseFamily"),
            ("heroPose", "imageHeroPose"),
            ("camera", "imageCamera"),
            ("colorPalette", "imageColorPalette"),
            ("accentColor", "imageAccentColor"),
            ("paletteFamily", "imagePaletteFamily"),
        )
        if isinstance((value := part.get(field)), str)
    }
    if image_bytes is None:
        recovered_image = _existing_background_story_media(
            record,
            generated_at=media_time,
            suffix=".png",
        )
        if recovered_image is not None:
            image_url, image_bytes = recovered_image
            record = _checkpoint_daily_full_story_media(
                record,
                local_date=local_date,
                part_index=part_index,
                story_generated_at=story_generated_at,
                image_url=image_url,
                video_url=None,
                image_direction=image_direction,
                now=now,
            )
    if video_bytes is None:
        recovered_video = _existing_background_story_media(
            record,
            generated_at=media_time,
            suffix=".mp4",
        )
        if recovered_video is not None:
            video_url, video_bytes = recovered_video
            record = _checkpoint_daily_full_story_media(
                record,
                local_date=local_date,
                part_index=part_index,
                story_generated_at=story_generated_at,
                image_url=image_url,
                video_url=video_url,
                image_direction=image_direction,
                now=now,
            )
    if video_bytes is None:
        try:
            if image_bytes is None:
                pose_history = [*_record_recent_story_events(record)]
                pose_history.extend(
                    item
                    for item in story.get("parts", [])
                    if isinstance(item, dict) and item.get("imagePoseFamily")
                )
                _consume_background_story_paid_media_budget(stage="image")
                with reserve_full_story_part_image_bytes(
                    pet=pet,
                    overall_title=str(story.get("overallTitle") or "История одного дня"),
                    part=part,
                    recent_story_events=pose_history,
                    direction_output=image_direction,
                ) as image_bytes:
                    if not image_bytes:
                        raise RuntimeError("DAILY_FULL_STORY_IMAGE_EMPTY")
                    image_url = _persist_background_story_image(
                        record,
                        image_bytes,
                        generated_at=media_time,
                    )
                record = _checkpoint_daily_full_story_media(
                    record,
                    local_date=local_date,
                    part_index=part_index,
                    story_generated_at=story_generated_at,
                    image_url=image_url,
                    video_url=None,
                    image_direction=image_direction,
                    now=now,
                )
            _consume_background_story_paid_media_budget(stage="video")
            with reserve_background_story_video_bytes(image_bytes) as video_bytes:
                if not video_bytes:
                    raise RuntimeError("DAILY_FULL_STORY_VIDEO_EMPTY")
                video_url = _persist_background_story_video(
                    record,
                    video_bytes,
                    generated_at=media_time,
                )
            record = _checkpoint_daily_full_story_media(
                record,
                local_date=local_date,
                part_index=part_index,
                story_generated_at=story_generated_at,
                image_url=image_url,
                video_url=video_url,
                image_direction=image_direction,
                now=now,
            )
        except _BackgroundStoryPaidMediaBudgetError as exc:
            media_budget_error = exc
            log_budget_state = logger.warning if exc.status == "exhausted" else logger.info
            log_budget_state(
                "scheduled_background_story_paid_media_budget "
                "status=%s code=%s stage=%s telegram_id=%s retry_after_seconds=%s",
                exc.status,
                exc.code,
                exc.stage,
                record.get("telegramId"),
                exc.retry_after_seconds,
            )
        except Exception as exc:
            logger.exception("daily_full_story_media_generation failed")
            raise TelegramPushError(
                "DAILY_FULL_STORY_MEDIA_FAILED",
                f"Не удалось создать видео части: {exc.__class__.__name__}",
            ) from exc

    record = _apply_daily_full_story_part_stats(
        record,
        local_date=local_date,
        part_index=part_index,
        now=now,
    )
    story, part = _daily_full_story_part(
        record,
        local_date=local_date,
        part_index=part_index,
    ) or ({}, {})
    chat_id = record.get("chatId")
    if not isinstance(chat_id, int):
        raise TelegramPushError("STORY_CHAT_ID_MISSING", "chat_id для Telegram story не найден.")
    keyboard = mini_app_keyboard(settings.webapp_url)
    caption = format_full_story_part_message(story, part)
    with httpx.Client() as client:
        try:
            if video_bytes:
                send_video(client, chat_id, video_bytes, caption, keyboard)
            elif image_bytes:
                send_photo(client, chat_id, image_bytes, caption, keyboard)
            else:
                send_message(client, chat_id, caption, keyboard)
        except TelegramAPIError as exc:
            raise _telegram_push_error(exc) from exc
        except httpx.HTTPError as exc:
            raise TelegramPushError(
                "TELEGRAM_SEND_FAILED",
                f"Telegram story send failed: {exc.__class__.__name__}",
            ) from exc

    delivered_at = _iso(now)
    media_status = (
        f"budget_{media_budget_error.status}" if media_budget_error is not None else "ready"
    )
    story_status = (
        f"delivered_media_budget_{media_budget_error.status}"
        if media_budget_error is not None
        else "delivered"
    )

    def save_delivery(current: dict[str, Any] | None) -> dict[str, Any]:
        source = current.copy() if isinstance(current, dict) else record.copy()
        current_part_value = _daily_full_story_part(
            source,
            local_date=local_date,
            part_index=part_index,
        )
        if current_part_value is None:
            return source
        current_story, _current_part = current_part_value
        next_story = deepcopy(current_story)
        next_part = next_story["parts"][part_index]
        next_part["deliveredAt"] = delivered_at
        next_part["imageUrl"] = image_url
        next_part["videoUrl"] = video_url
        next_part["imageError"] = image_error
        next_part["mediaStatus"] = media_status
        next_part["mediaErrorCode"] = (
            media_budget_error.code if media_budget_error is not None else None
        )
        next_part["mediaBudgetStage"] = (
            media_budget_error.stage if media_budget_error is not None else None
        )
        next_part["mediaBudgetRetryAfterSeconds"] = (
            media_budget_error.retry_after_seconds if media_budget_error is not None else None
        )
        next_part["imagePoseFamily"] = image_direction.get("poseFamily")
        next_part["imageHeroPose"] = image_direction.get("heroPose")
        next_part["imageCamera"] = image_direction.get("camera")
        next_part["imageColorPalette"] = image_direction.get("colorPalette")
        next_part["imageAccentColor"] = image_direction.get("accentColor")
        next_part["imagePaletteFamily"] = image_direction.get("paletteFamily")
        result = {
            **source,
            "dailyFullStory": next_story,
            "lastStoryAt": delivered_at,
            "lastStoryAttemptAt": delivered_at,
            "lastStoryStatus": story_status,
            "lastStoryMediaStatus": media_status,
            "lastStoryError": (
                media_budget_error.message if media_budget_error is not None else None
            ),
            "lastStoryErrorCode": (
                media_budget_error.code if media_budget_error is not None else None
            ),
            "lastStoryErrorAt": delivered_at if media_budget_error is not None else None,
        }
        last_full_story = source.get("lastFullStory")
        if isinstance(last_full_story, dict) and last_full_story.get(
            "generatedAt"
        ) == current_story.get("generatedAt"):
            result["lastFullStory"] = next_story
        return result

    saved = _update_record(_telegram_id_from_record(record), save_delivery)
    return {
        "sent": True,
        "telegramId": record.get("telegramId"),
        "localDate": local_date,
        "partNumber": part_index + 1,
        "story": saved.get("dailyFullStory"),
        "storyImageError": image_error,
        "storyMediaStatus": media_status,
        "storyMediaErrorCode": (
            media_budget_error.code if media_budget_error is not None else None
        ),
    }


def _fresh_record(record: dict[str, Any]) -> dict[str, Any]:
    telegram_id = record.get("telegramId")
    if isinstance(telegram_id, int):
        fresh = _read_store().get("records", {}).get(str(telegram_id))
        if isinstance(fresh, dict):
            return fresh
    return record


def _snapshot_records() -> list[dict[str, Any]]:
    records = _read_store().get("records", {})
    return [
        record for record in records.values() if isinstance(record, dict) and _has_snapshot(record)
    ]


def _bulk_error(record: dict[str, Any], exc: Exception) -> dict[str, Any]:
    if isinstance(exc, TelegramPushError):
        code = exc.code
        message = _safe_error_message(exc.message)
    else:
        code = "PUSH_SEND_FAILED"
        message = _safe_error_message(exc)
    return {
        "telegramId": record.get("telegramId"),
        "petId": record.get("petId"),
        "code": code,
        "message": message,
    }


def send_manual_push_to_reachable(
    *,
    reason: str | None = None,
    include_debug: bool = True,
) -> dict[str, Any]:
    records = _snapshot_records()
    now = _now()
    reachable = [
        record
        for record in records
        if record.get("chatReachable") is True and not _record_is_dead(record, now)
    ]
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for record in reachable:
        try:
            results.append(
                _send_push_record(
                    record,
                    reason=reason or _push_reason_for_record(record, _now()),
                    manual=True,
                    include_debug=include_debug,
                )
            )
        except Exception as exc:
            if not isinstance(exc, TelegramPushError):
                _save_push_failure(record, exc)
            errors.append(_bulk_error(record, exc))
    return {
        "sent": len(results) > 0,
        "manual": True,
        "sentCount": len(results),
        "failedCount": len(errors),
        "skippedCount": len(records) - len(reachable),
        "targetCount": len(reachable),
        "results": results,
        "errors": errors,
    }


def _due_records(now: datetime) -> list[dict[str, Any]]:
    records = _read_store().get("records", {})
    due: list[dict[str, Any]] = []
    for record in records.values():
        if not isinstance(record, dict):
            continue
        if not _has_snapshot(record) or record.get("chatReachable") is not True:
            continue
        if _record_is_dead(record, now):
            continue
        if _scheduled_push_slot(record, now) is not None:
            due.append(record)
    return due


def _scheduler_delivery_error(exc: Exception) -> str:
    return exc.code if isinstance(exc, TelegramPushError) else type(exc).__name__


def _scheduler_delivery_affects_health(exc: Exception) -> bool:
    return not (isinstance(exc, TelegramPushError) and exc.code == "TELEGRAM_CHAT_NOT_FOUND")


def _run_due_pushes() -> _SchedulerBatchResult:
    settings = get_settings()
    if not settings.telegram_daily_push_enabled:
        return _SchedulerBatchResult(
            results=[], attempted=0, failed=0, health_failed=0, last_error=None
        )
    now = _now()
    records = _due_records(now)
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    health_failures = 0
    for record in records:
        try:
            results.append(
                _send_push_record(
                    record,
                    reason=_push_reason_for_record(record, now),
                    manual=False,
                    include_debug=False,
                )
            )
        except Exception as exc:
            _save_push_failure(record, exc)
            errors.append(_scheduler_delivery_error(exc))
            health_failures += int(_scheduler_delivery_affects_health(exc))
    return _SchedulerBatchResult(
        results=results,
        attempted=len(records),
        failed=len(errors),
        health_failed=health_failures,
        last_error=errors[-1] if errors else None,
    )


def send_due_pushes() -> list[dict[str, Any]]:
    return _run_due_pushes().results


DEFAULT_SCHEDULED_SHORT_STORY_HOURS = tuple(range(10, 22))
DEFAULT_SCHEDULED_SHORT_STORY_TIMEZONE = "Europe/Moscow"


def _scheduled_short_story_hours(settings: Any) -> tuple[int, ...]:
    raw_hours = getattr(
        settings,
        "scheduled_short_story_hours",
        DEFAULT_SCHEDULED_SHORT_STORY_HOURS,
    )
    if not isinstance(raw_hours, (list, tuple, set)):
        return DEFAULT_SCHEDULED_SHORT_STORY_HOURS
    hours: list[int] = []
    for raw_hour in raw_hours:
        try:
            hour = int(raw_hour)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 23 and hour not in hours:
            hours.append(hour)
    return tuple(sorted(hours)) or DEFAULT_SCHEDULED_SHORT_STORY_HOURS


def _scheduled_short_story_timezone(settings: Any) -> ZoneInfo:
    timezone_name = str(
        getattr(
            settings,
            "scheduled_short_story_timezone",
            DEFAULT_SCHEDULED_SHORT_STORY_TIMEZONE,
        )
    )
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_SCHEDULED_SHORT_STORY_TIMEZONE)


def _scheduled_short_story_slot(record: dict[str, Any], now: datetime) -> datetime | None:
    settings = get_settings()
    local_now = now.astimezone(_scheduled_short_story_timezone(settings))
    if local_now.hour not in _scheduled_short_story_hours(settings):
        return None
    slot = local_now.replace(minute=0, second=0, microsecond=0).astimezone(UTC)
    latest = _latest_time(
        record.get("lastScheduledShortStoryAt"),
        record.get("lastScheduledShortStoryAttemptAt"),
    )
    return slot if latest is None or slot > latest else None


def _due_scheduled_short_story_records(now: datetime) -> list[dict[str, Any]]:
    settings = get_settings()
    target_ids = set(getattr(settings, "scheduled_short_story_telegram_ids", set()) or set())
    if not target_ids:
        return []
    records = _read_store().get("records", {})
    due: list[dict[str, Any]] = []
    for telegram_id in sorted(target_ids):
        record = records.get(str(telegram_id))
        if not isinstance(record, dict):
            continue
        if not _has_snapshot(record) or record.get("chatReachable") is not True:
            continue
        if _record_is_dead(record, now):
            continue
        if _scheduled_short_story_slot(record, now) is not None:
            due.append(record)
    return due


def _mark_scheduled_short_story_attempt(record: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    telegram_id = _telegram_id_from_record(record)
    attempt_at = _iso(now)

    def update(current: dict[str, Any] | None) -> dict[str, Any]:
        source = current.copy() if isinstance(current, dict) else record.copy()
        return {
            **source,
            "lastScheduledShortStoryAttemptAt": attempt_at,
            "lastScheduledShortStoryError": None,
        }

    return _update_record(telegram_id, update)


def _scheduled_short_story_provider_error_is_retryable(exc: Exception) -> bool:
    return scheduled_short_story_provider_error_is_retryable(exc)


def _run_scheduled_short_story_provider_job(
    label: str,
    operation: Callable[[], Any],
) -> Any:
    return run_scheduled_short_story_provider_job(label, operation)


def _send_scheduled_short_story(record: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    settings = get_settings()
    if not settings.bot_token:
        raise TelegramPushError("BOT_TOKEN_MISSING", "BOT_TOKEN не настроен.")
    if not settings.webapp_url:
        raise TelegramPushError("WEBAPP_URL_MISSING", "WEBAPP_URL не настроен.")
    record = _mark_scheduled_short_story_attempt(record, now=now)
    telegram_id = _telegram_id_from_record(record)
    payload = _build_push_payload(record, reason="Интерактивная история.", include_debug=False)
    travel_id = f"interactive-travel-auto-{uuid.uuid4().hex}"
    generated_episode = generate_scheduled_short_story_episode(
        pet=payload.pet,
        story_id=travel_id,
        run_provider_job=_run_scheduled_short_story_provider_job,
    )
    plan = generated_episode.plan
    video_bytes = (generated_dir_for(travel_id) / "interactive-travel-part-01.mp4").read_bytes()
    chat_id = record.get("chatId")
    if not isinstance(chat_id, int):
        raise TelegramPushError(
            "STORY_CHAT_ID_MISSING",
            "chat_id для короткой истории не найден.",
        )
    callback_token = uuid.uuid4().hex[:16]
    story_url = f"{settings.webapp_url.rstrip('/')}/auto-story/{callback_token}"
    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "Открыть приложение",
                    "web_app": {"url": story_url},
                }
            ]
        ]
    }
    caption = f"{plan['title']}\n\n{plan['storyText']}\n\n{plan['question']}"
    try:
        with httpx.Client() as client:
            send_video(client, chat_id, video_bytes, caption, keyboard)
    except TelegramAPIError as exc:
        raise _telegram_push_error(exc) from exc
    except httpx.HTTPError as exc:
        raise TelegramPushError(
            "TELEGRAM_SEND_FAILED",
            f"Telegram scheduled story send failed: {exc.__class__.__name__}",
        ) from exc

    delivered_at = _iso()

    def save_delivery(current: dict[str, Any] | None) -> dict[str, Any]:
        source = current.copy() if isinstance(current, dict) else record.copy()
        episode = {
            "token": callback_token,
            "travelId": travel_id,
            "title": plan["title"],
            "storyText": plan["storyText"],
            "question": plan["question"],
            "destination": plan["destination"],
            "choices": plan["choices"],
            "outcomes": plan["outcomes"],
            "correctChoice": plan["correctChoice"],
            "situationImageUrl": generated_episode.situation_image_url,
            "situationVideoUrl": generated_episode.situation_video_url,
            "outcomeImageUrls": list(generated_episode.outcome_image_urls),
            "outcomeVideoUrls": list(generated_episode.outcome_video_urls),
            "outcomeFiles": list(generated_episode.outcome_files),
            "createdAt": delivered_at,
        }
        history = source.get("automaticInteractiveStories")
        stories = (
            [item for item in history if isinstance(item, dict)]
            if isinstance(history, list)
            else []
        )
        stories = [item for item in stories if item.get("token") != callback_token]
        stories.append(episode)
        return {
            **source,
            "lastScheduledShortStoryAt": delivered_at,
            "lastScheduledShortStoryError": None,
            "pendingInteractiveStory": episode,
            "automaticInteractiveStories": stories[-MAX_AUTOMATIC_INTERACTIVE_STORIES:],
        }

    _update_record(telegram_id, save_delivery)
    return {
        "sent": True,
        "telegramId": telegram_id,
        "generatedAt": delivered_at,
        "deliveredAt": delivered_at,
        "story": plan,
    }


def send_scheduled_short_story_for_telegram_user(*, telegram_id: int) -> dict[str, Any]:
    record = _record_by_telegram_id(telegram_id)
    return _send_scheduled_short_story(record, now=_now())


def _run_due_scheduled_short_stories() -> _SchedulerBatchResult:
    settings = get_settings()
    if not getattr(settings, "scheduled_short_story_enabled", False):
        return _SchedulerBatchResult(
            results=[], attempted=0, failed=0, health_failed=0, last_error=None
        )
    now = _now()
    records = _due_scheduled_short_story_records(now)
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    health_failures = 0
    for record in records:
        try:
            results.append(_send_scheduled_short_story(record, now=now))
        except Exception as exc:
            errors.append(_scheduler_delivery_error(exc))
            health_failures += int(_scheduler_delivery_affects_health(exc))
            telegram_id = _telegram_id_from_record(record)
            fallback_record = record.copy()
            error_message = _safe_error_message(exc)

            def save_error(
                current: dict[str, Any] | None,
                fallback: dict[str, Any] = fallback_record,
                message: str = error_message,
            ) -> dict[str, Any]:
                source = current.copy() if isinstance(current, dict) else fallback.copy()
                return {
                    **source,
                    "lastScheduledShortStoryError": message,
                }

            _update_record(telegram_id, save_error)
    return _SchedulerBatchResult(
        results=results,
        attempted=len(records),
        failed=len(errors),
        health_failed=health_failures,
        last_error=errors[-1] if errors else None,
    )


def send_due_scheduled_short_stories() -> list[dict[str, Any]]:
    return _run_due_scheduled_short_stories().results


def _automatic_interactive_episode(record: dict[str, Any], token: str) -> dict[str, Any] | None:
    history = record.get("automaticInteractiveStories")
    if isinstance(history, list):
        for episode in reversed(history):
            if isinstance(episode, dict) and episode.get("token") == token:
                return episode
    episode = record.get("pendingInteractiveStory")
    if isinstance(episode, dict) and episode.get("token") == token:
        return episode
    return None


def interactive_story_outcome_for_callback(
    *, telegram_id: int, token: str, choice_index: int
) -> tuple[bytes, str]:
    record = _record_by_telegram_id(telegram_id)
    episode = _automatic_interactive_episode(record, token)
    if episode is None:
        raise TelegramPushError("INTERACTIVE_STORY_EXPIRED", "Эта история уже недоступна.")
    outcomes = episode.get("outcomes")
    files = episode.get("outcomeFiles")
    if (
        not isinstance(outcomes, list)
        or not isinstance(files, list)
        or choice_index not in range(len(outcomes))
        or choice_index not in range(len(files))
    ):
        raise TelegramPushError("INTERACTIVE_STORY_CHOICE_INVALID", "Неизвестный вариант.")
    travel_id = str(episode.get("travelId") or "")
    video_path = generated_dir_for(travel_id) / str(files[choice_index])
    video_bytes = video_path.read_bytes()
    return video_bytes, str(outcomes[choice_index])


def automatic_interactive_story(*, telegram_id: int, token: str) -> dict[str, Any]:
    record = _record_by_telegram_id(telegram_id)
    episode = _automatic_interactive_episode(record, token)
    if episode is None:
        raise TelegramPushError("INTERACTIVE_STORY_EXPIRED", "История не найдена.")
    travel_id = str(episode.get("travelId") or "")
    choices = episode.get("choices")
    outcomes = episode.get("outcomes")
    if not isinstance(choices, list) or len(choices) != 4:
        raise TelegramPushError("INTERACTIVE_STORY_INVALID", "История повреждена.")
    if not isinstance(outcomes, list) or len(outcomes) != len(choices):
        raise TelegramPushError("INTERACTIVE_STORY_INVALID", "Исходы истории повреждены.")
    situation_url = episode.get("situationVideoUrl") or (
        f"/static/generated/{travel_id}/interactive-travel-part-01.mp4"
    )
    outcome_urls = episode.get("outcomeVideoUrls")
    if not isinstance(outcome_urls, list) or len(outcome_urls) != len(choices):
        files = episode.get("outcomeFiles") or []
        outcome_urls = [f"/static/generated/{travel_id}/{name}" for name in files]
    return {
        "token": token,
        "title": str(episode.get("title") or "История путешествия"),
        "storyText": str(episode.get("storyText") or "Выбери, как поступить питомцу."),
        "question": str(episode.get("question") or "Как поступить?"),
        "choices": [str(value) for value in choices],
        "outcomes": [str(value) for value in outcomes],
        "selectedChoice": episode.get("selectedChoice"),
        "result": episode.get("result"),
        "travelId": travel_id,
        "destination": str(episode.get("destination") or "в путешествие"),
        "createdAt": str(episode.get("createdAt") or _iso()),
        "situationImageUrl": str(
            episode.get("situationImageUrl")
            or f"/static/generated/{travel_id}/interactive-travel-part-01.png"
        ),
        "situationVideoUrl": str(situation_url),
        "outcomeImageUrls": [
            str(value)
            for value in (
                episode.get("outcomeImageUrls")
                or [
                    f"/static/generated/{travel_id}/interactive-travel-part-01-outcome-{index}.png"
                    for index in range(len(choices))
                ]
            )
        ],
        "outcomeVideoUrls": [str(value) for value in outcome_urls],
    }


def select_automatic_interactive_story(
    *, telegram_id: int, token: str, selected_choice: str
) -> dict[str, Any]:
    def select(current: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(current, dict):
            raise TelegramPushError("INTERACTIVE_STORY_EXPIRED", "История не найдена.")
        episode = _automatic_interactive_episode(current, token)
        if episode is None:
            raise TelegramPushError("INTERACTIVE_STORY_EXPIRED", "История не найдена.")
        choices = episode.get("choices")
        outcomes = episode.get("outcomes")
        if not isinstance(choices, list) or selected_choice not in choices:
            raise TelegramPushError("INTERACTIVE_STORY_CHOICE_INVALID", "Неизвестный вариант.")
        if not isinstance(outcomes, list) or len(outcomes) != len(choices):
            raise TelegramPushError("INTERACTIVE_STORY_INVALID", "История повреждена.")
        if isinstance(episode.get("result"), dict):
            return current
        selected_index = choices.index(selected_choice)
        correct_choice = str(episode.get("correctChoice") or "") or (
            scheduled_interactive_episode_correct_choice(
                question=str(episode.get("question") or ""),
                choices=[str(value) for value in choices],
            )
        )
        result = scheduled_interactive_episode_result(
            situation=str(episode.get("storyText") or ""),
            question=str(episode.get("question") or ""),
            outcomes=[str(value) for value in outcomes],
            correct_choice=correct_choice,
            selected_choice=selected_choice,
        ).model_dump()
        result["text"] = str(outcomes[selected_index])
        updated_episode = {
            **episode,
            "correctChoice": correct_choice,
            "selectedChoice": selected_choice,
            "result": result,
        }
        history = current.get("automaticInteractiveStories")
        updated_history = (
            [
                updated_episode if isinstance(item, dict) and item.get("token") == token else item
                for item in history
            ]
            if isinstance(history, list)
            else [updated_episode]
        )
        pending = current.get("pendingInteractiveStory")
        return {
            **current,
            "automaticInteractiveStories": updated_history,
            "pendingInteractiveStory": (
                updated_episode
                if isinstance(pending, dict) and pending.get("token") == token
                else pending
            ),
        }

    _update_record(telegram_id, select)
    return automatic_interactive_story(telegram_id=telegram_id, token=token)


def _scheduled_background_story_order_key(
    record: dict[str, Any],
    *,
    now: datetime,
) -> tuple[bytes, int]:
    telegram_id = _telegram_id_from_record(record)
    utc_now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    budget_window_date = utc_now.date().isoformat()
    digest = hashlib.sha256(
        (
            f"{BACKGROUND_STORY_PAID_MEDIA_BUDGET_BUCKET}\0{budget_window_date}\0{telegram_id}"
        ).encode()
    ).digest()
    return digest, telegram_id


def _due_story_records(now: datetime) -> list[dict[str, Any]]:
    records = _read_store().get("records", {})
    due: list[dict[str, Any]] = []
    for record in records.values():
        if not isinstance(record, dict):
            continue
        if not _has_snapshot(record) or record.get("chatReachable") is not True:
            continue
        if _record_is_dead(record, now):
            continue
        slot = _background_story_slot(record, now)
        if slot is None:
            continue
        part_index, local_slot, _timezone_name = slot
        local_date = local_slot.date().isoformat()
        current_part = _daily_full_story_part(
            record,
            local_date=local_date,
            part_index=part_index,
        )
        if current_part is None and part_index != 0:
            continue
        if current_part is not None and part_index > 0:
            story_parts = current_part[0].get("parts")
            if not isinstance(story_parts, list) or any(
                not isinstance(previous, dict) or not previous.get("deliveredAt")
                for previous in story_parts[:part_index]
            ):
                continue
        if current_part is not None and current_part[1].get("deliveredAt"):
            continue
        attempt_key = f"{local_date}:{part_index + 1}"
        if _daily_full_story_attempt_due(record, attempt_key=attempt_key, now=now):
            due.append(record)
    due.sort(key=lambda record: _scheduled_background_story_order_key(record, now=now))
    return due


def _run_due_background_stories() -> _SchedulerBatchResult:
    settings = get_settings()
    if not settings.background_story_enabled:
        return _SchedulerBatchResult(
            results=[], attempted=0, failed=0, health_failed=0, last_error=None
        )
    now = _now()
    records = _due_story_records(now)
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    health_failures = 0
    for record in records:
        try:
            results.append(_send_daily_full_story_part(record, now=now))
        except Exception as exc:
            _save_story_failure(_fresh_record(record), exc)
            errors.append(_scheduler_delivery_error(exc))
            health_failures += int(_scheduler_delivery_affects_health(exc))
    if records:
        _cleanup_background_story_media_for_records(records, now=now)
    return _SchedulerBatchResult(
        results=results,
        attempted=len(records),
        failed=len(errors),
        health_failed=health_failures,
        last_error=errors[-1] if errors else None,
    )


def _run_generated_media_cleanup() -> _SchedulerBatchResult:
    now = _now()
    temp_result = cleanup_stale_generated_processing_temp_directories(
        generated_root=_configured_generated_assets_root(),
        now=now,
    )
    if temp_result.removed:
        logger.info("generated_processing_temp_gc removed=%s", len(temp_result.removed))
    if temp_result.failed or temp_result.unsafe:
        raise RuntimeError("generated processing temp cleanup was incomplete")
    with _background_story_media_gc_lock:
        _run_background_story_media_cleanup(records=None, now=now)
    return _SchedulerBatchResult(
        results=[],
        attempted=0,
        failed=0,
        health_failed=0,
        last_error=None,
    )


def send_due_background_stories() -> list[dict[str, Any]]:
    return _run_due_background_stories().results


async def _daily_push_loop() -> None:
    settings = get_settings()
    interval = max(60, int(settings.telegram_daily_push_interval_seconds))
    await _scheduler_leadership_loop("dailyPush", _run_due_pushes, interval)


async def _background_story_loop() -> None:
    settings = get_settings()
    interval = max(60, int(settings.background_story_interval_seconds))
    await _scheduler_leadership_loop(
        "backgroundStory",
        _run_due_background_stories,
        interval,
    )


async def _scheduled_short_story_loop() -> None:
    settings = get_settings()
    cadence = max(
        60,
        int(getattr(settings, "scheduled_short_story_interval_seconds", 60)),
    )
    await _scheduler_leadership_loop(
        "scheduledShortStory",
        _run_due_scheduled_short_stories,
        min(60, cadence),
    )


async def _generated_media_cleanup_loop() -> None:
    await _scheduler_leadership_loop(
        "generatedMediaCleanup",
        _run_generated_media_cleanup,
        GENERATED_MEDIA_CLEANUP_LOOP_INTERVAL_SECONDS,
    )


_scheduler_runtime: dict[str, dict[str, Any]] = {
    "dailyPush": {
        "running": False,
        "role": "stopped",
        "leaderSince": None,
        "lastLeadershipAttemptAt": None,
        "consecutiveFailures": 0,
        "lastRunAt": None,
        "lastAttempted": 0,
        "lastSucceeded": 0,
        "lastFailed": 0,
        "lastError": None,
        "degradedUntil": None,
    },
    "backgroundStory": {
        "running": False,
        "role": "stopped",
        "leaderSince": None,
        "lastLeadershipAttemptAt": None,
        "consecutiveFailures": 0,
        "lastRunAt": None,
        "lastAttempted": 0,
        "lastSucceeded": 0,
        "lastFailed": 0,
        "lastError": None,
        "degradedUntil": None,
    },
    "scheduledShortStory": {
        "running": False,
        "role": "stopped",
        "leaderSince": None,
        "lastLeadershipAttemptAt": None,
        "consecutiveFailures": 0,
        "lastRunAt": None,
        "lastAttempted": 0,
        "lastSucceeded": 0,
        "lastFailed": 0,
        "lastError": None,
        "degradedUntil": None,
    },
    "generatedMediaCleanup": {
        "running": False,
        "role": "stopped",
        "leaderSince": None,
        "lastLeadershipAttemptAt": None,
        "consecutiveFailures": 0,
        "lastRunAt": None,
        "lastAttempted": 0,
        "lastSucceeded": 0,
        "lastFailed": 0,
        "lastError": None,
        "degradedUntil": None,
    },
}
# Runtime telemetry and task guards are process-local. The lifetime ``flock`` below
# is stored beside the push state on the shared volume and elects one worker per
# scheduler for all paid generation and Telegram delivery.
_scheduler_runtime_lock = Lock()
_scheduler_tasks: dict[str, asyncio.Task[None]] = {}
_scheduler_tasks_lock = Lock()


def _scheduler_lock_path(name: str) -> Path:
    try:
        lock_name = SCHEDULER_LOCK_NAMES[name]
    except KeyError as exc:
        raise ValueError(f"unknown scheduler: {name}") from exc
    store_path = _store_path()
    return store_path.parent / f".{store_path.name}.{lock_name}.scheduler.lock"


def _try_acquire_scheduler_leadership(name: str) -> BinaryIO | None:
    lock_path = _scheduler_lock_path(name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("a+b")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock_handle.close()
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            return None
        raise
    return lock_handle


def _release_scheduler_leadership(lock_handle: BinaryIO) -> None:
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    finally:
        lock_handle.close()


async def _scheduler_leadership_loop(
    name: str,
    operation: Callable[[], Any],
    interval: float,
    *,
    retry_interval: float | None = None,
) -> None:
    state = _scheduler_runtime[name]
    retry_seconds = max(
        0.01,
        SCHEDULER_LEADERSHIP_RETRY_SECONDS if retry_interval is None else float(retry_interval),
    )
    with _scheduler_runtime_lock:
        state["running"] = True
        state["role"] = "standby"
        state["leaderSince"] = None
    try:
        while True:
            with _scheduler_runtime_lock:
                state["lastLeadershipAttemptAt"] = _iso()
            lock_handle = _try_acquire_scheduler_leadership(name)
            if lock_handle is None:
                with _scheduler_runtime_lock:
                    state["running"] = True
                    state["role"] = "standby"
                    state["leaderSince"] = None
                await asyncio.sleep(retry_seconds)
                continue

            try:
                with _scheduler_runtime_lock:
                    state["running"] = True
                    state["role"] = "leader"
                    state["leaderSince"] = _iso()
                # ``_scheduler_loop`` drains an already-started thread iteration on
                # cancellation. Keep the file descriptor locked until that drain is
                # complete, otherwise a standby could duplicate the same paid work.
                await _scheduler_loop(name, operation, interval)
            finally:
                _release_scheduler_leadership(lock_handle)

            with _scheduler_runtime_lock:
                state["running"] = True
                state["role"] = "standby"
                state["leaderSince"] = None
    finally:
        with _scheduler_runtime_lock:
            state["running"] = False
            state["role"] = "stopped"
            state["leaderSince"] = None


async def _scheduler_loop(
    name: str,
    operation: Callable[[], Any],
    interval: float,
) -> None:
    state = _scheduler_runtime[name]
    with _scheduler_runtime_lock:
        state["running"] = True
        if state.get("role") != "leader":
            state["leaderSince"] = _iso()
        state["role"] = "leader"
    try:
        while True:
            operation_task = asyncio.create_task(asyncio.to_thread(operation))
            try:
                result = await asyncio.shield(operation_task)
            except asyncio.CancelledError:
                # Cancelling ``to_thread`` does not stop the underlying call. Keep the
                # scheduler task alive until the accepted iteration has drained so a
                # replacement process cannot overlap the same delivery/generation.
                try:
                    await operation_task
                except Exception:
                    logger.exception(
                        "scheduler_iteration_failed_during_shutdown scheduler=%s",
                        name,
                    )
                raise
            except Exception as exc:
                with _scheduler_runtime_lock:
                    state["lastRunAt"] = _iso()
                    state["consecutiveFailures"] = int(state["consecutiveFailures"]) + 1
                    state["lastError"] = type(exc).__name__
                logger.exception("scheduler_iteration_failed scheduler=%s", name)
            else:
                with _scheduler_runtime_lock:
                    state["lastRunAt"] = _iso()
                    state["consecutiveFailures"] = 0
                    if isinstance(result, _SchedulerBatchResult):
                        if result.attempted > 0:
                            state["lastAttempted"] = result.attempted
                            state["lastSucceeded"] = result.succeeded
                            state["lastFailed"] = result.failed
                            state["lastError"] = result.last_error
                            state["degradedUntil"] = (
                                _iso(_now() + timedelta(seconds=max(600, int(interval * 2))))
                                if result.health_failed > 0
                                else None
                            )
                        elif int(state["lastAttempted"]) == 0:
                            state["lastError"] = None
                    else:
                        state["lastAttempted"] = 0
                        state["lastSucceeded"] = 0
                        state["lastFailed"] = 0
                        state["lastError"] = None
                        state["degradedUntil"] = None
            await asyncio.sleep(interval)
    finally:
        with _scheduler_runtime_lock:
            state["running"] = False
            state["role"] = "stopped"
            state["leaderSince"] = None


def scheduler_runtime_status() -> dict[str, dict[str, Any]]:
    with _scheduler_runtime_lock:
        result = {name: dict(state) for name, state in _scheduler_runtime.items()}
    now = _now()
    for state in result.values():
        degraded_until = _parse_iso(state.get("degradedUntil"))
        state["deliveryDegraded"] = degraded_until is not None and degraded_until > now
    return result


def _start_scheduler_task(
    name: str,
    coroutine_factory: Callable[[], Any],
) -> asyncio.Task[None] | None:
    with _scheduler_tasks_lock:
        existing = _scheduler_tasks.get(name)
        if existing is not None and not existing.done():
            return None
        task = asyncio.create_task(coroutine_factory())
        _scheduler_tasks[name] = task

    def forget(completed: asyncio.Task[None]) -> None:
        with _scheduler_tasks_lock:
            if _scheduler_tasks.get(name) is completed:
                _scheduler_tasks.pop(name, None)

    task.add_done_callback(forget)
    return task


def start_daily_push_scheduler() -> asyncio.Task[None] | None:
    settings = get_settings()
    if (
        not settings.telegram_daily_push_enabled
        or not settings.bot_token
        or not settings.webapp_url
    ):
        return None
    return _start_scheduler_task("dailyPush", _daily_push_loop)


def start_background_story_scheduler() -> asyncio.Task[None] | None:
    settings = get_settings()
    if not settings.background_story_enabled or not settings.bot_token or not settings.webapp_url:
        return None
    return _start_scheduler_task("backgroundStory", _background_story_loop)


def start_scheduled_short_story_scheduler() -> asyncio.Task[None] | None:
    settings = get_settings()
    if (
        not getattr(settings, "scheduled_short_story_enabled", False)
        or not getattr(settings, "scheduled_short_story_telegram_ids", set())
        or not settings.bot_token
        or not settings.webapp_url
    ):
        return None
    return _start_scheduler_task("scheduledShortStory", _scheduled_short_story_loop)


def start_generated_media_cleanup_scheduler() -> asyncio.Task[None] | None:
    settings = get_settings()
    if not generated_media_cleanup_is_enabled(
        getattr(settings, "generated_media_cleanup_enabled", None)
    ):
        return None
    return _start_scheduler_task(
        "generatedMediaCleanup",
        _generated_media_cleanup_loop,
    )
