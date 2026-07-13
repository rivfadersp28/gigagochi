from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.config import get_settings
from app.schemas import (
    LocalChatHistoryItem,
    LocalPetChatContext,
    LocalPetMemoryContext,
    LocalPetPushSnapshotRequest,
    LocalPetPushSnapshotResponse,
    LocalPetStatsPatch,
    LocalPushRequest,
)
from app.services.background_story_service import (
    generate_background_story,
    generate_background_story_image_bytes,
    generate_background_story_video_bytes,
)
from app.services.full_story_service import (
    generate_full_story,
    generate_full_story_part_image_bytes,
)
from app.services.image_service import generated_dir_for
from app.services.lite_overlay import merge_lite_overlay_patch
from app.services.pet_reply_engine.lite_generator import generate_push_pet_message
from app.services.story_delivery_format import (
    format_full_story_part_message,
)
from app.services.telegram_auth_service import TelegramUserContext
from app.services.telegram_client import (
    TelegramAPIError,
    mini_app_keyboard,
    send_message,
    send_video,
)
from app.services.telegram_push_store import JsonTelegramPushStore

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
STORY_STAT_MAX_ITEMS = 2
STORY_STAT_MAX_SINGLE_DAMAGE = 25
STORY_STAT_MAX_TOTAL_DAMAGE = 35

logger = logging.getLogger(__name__)

DEFAULT_DAILY_PUSH_HOURS = (9, 15, 21)
DEFAULT_DAILY_PUSH_WINDOW_MINUTES = 120
DEFAULT_PUSH_TIMEZONE = "Europe/Moscow"
DEFAULT_BACKGROUND_STORY_HOURS = (9, 13, 17, 21)
DEFAULT_BACKGROUND_STORY_WINDOW_MINUTES = 120
DAILY_FULL_STORY_RETRY_DELAY = timedelta(minutes=15)
DAILY_FULL_STORY_MAX_ATTEMPTS = 2


class TelegramPushError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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


def _push_store() -> JsonTelegramPushStore:
    return JsonTelegramPushStore(_store_path(), version=STORE_VERSION)


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
        )
        record = {key: deepcopy(existing.get(key)) for key in retained_keys if key in existing}
        record["petResetRequest"] = {"petId": pet_id, "requestedAt": now_iso}
        return record

    return _update_record(telegram_id, reset_record)


def _merge_character_bible(
    existing: Any,
    incoming: Any,
) -> dict[str, Any] | None:
    existing_record = existing if isinstance(existing, dict) else {}
    incoming_record = incoming if isinstance(incoming, dict) else {}
    if not existing_record and not incoming_record:
        return None

    def merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(base)
        for key, value in overlay.items():
            current = result.get(key)
            if isinstance(current, dict) and isinstance(value, dict):
                result[key] = merge(current, value)
            else:
                result[key] = deepcopy(value)
        return result

    return merge(existing_record, incoming_record)


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
    patch: dict[str, Any] = {}
    facts = overlay.get("facts")
    if isinstance(facts, list) and facts:
        patch["facts"] = facts
    spheres = overlay.get("spheres")
    if isinstance(spheres, dict) and spheres:
        patch["spheres"] = spheres
    world_seed = overlay.get("worldSeed")
    if isinstance(world_seed, dict):
        patch["worldSeed"] = world_seed
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
    overlay = extensions.setdefault("lite_overlay", {})
    if not isinstance(overlay, dict):
        overlay = {}
        extensions["lite_overlay"] = overlay
    merge_lite_overlay_patch(overlay, patch)


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


def _recent_story_events_patch(record: dict[str, Any] | None) -> dict[str, Any] | None:
    events = _record_recent_story_events(record)
    return {"events": events} if events else None


def _persist_background_story_image(
    record: dict[str, Any],
    image_bytes: bytes,
    *,
    generated_at: datetime,
) -> str:
    raw_pet_id = str(record.get("petId") or record.get("telegramId") or "story")
    safe_pet_id = (
        "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in raw_pet_id
        ).strip("-")[:120]
        or "story"
    )
    output_dir = generated_dir_for(safe_pet_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"background-story-{generated_at.strftime('%Y%m%dT%H%M%S%fZ')}.png"
    (output_dir / filename).write_bytes(image_bytes)
    version = int(generated_at.timestamp())
    return f"/static/generated/{safe_pet_id}/{filename}?v={version}"


def _persist_background_story_video(
    record: dict[str, Any],
    video_bytes: bytes,
    *,
    generated_at: datetime,
) -> str:
    raw_pet_id = str(record.get("petId") or record.get("telegramId") or "story")
    safe_pet_id = (
        "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in raw_pet_id
        ).strip("-")[:120]
        or "story"
    )
    output_dir = generated_dir_for(safe_pet_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"background-story-{generated_at.strftime('%Y%m%dT%H%M%S%fZ')}.mp4"
    (output_dir / filename).write_bytes(video_bytes)
    version = int(generated_at.timestamp())
    return f"/static/generated/{safe_pet_id}/{filename}?v={version}"


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
        "recentAmbientReplies": payload.recentAmbientReplies[-10:],
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
    stats_patch: LocalPetStatsPatch | None = None
    reset_pet = False

    def merge_snapshot(existing: dict[str, Any] | None) -> dict[str, Any]:
        nonlocal reset_pet, stats_patch
        reset_request = existing.get("petResetRequest") if isinstance(existing, dict) else None
        if isinstance(reset_request, dict) and reset_request.get("petId") == payload.petId:
            reset_pet = True
            return deepcopy(existing)

        record = deepcopy(incoming_record)
        same_pet = isinstance(existing, dict) and existing.get("petId") == payload.petId
        if same_pet:
            record["pet"] = _preserve_pet_character_bible(
                record["pet"],
                existing.get("pet"),
            )
        stats_patch = _merge_snapshot_stats(record, existing, now=now) if same_pet else None
        if isinstance(existing, dict):
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
                    "dailyFullStory",
                    "dailyFullStoryAttemptKey",
                    "dailyFullStoryAttemptCount",
                    "dailyFullStoryAttemptAt",
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
    for item in raw_replies[-10:]:
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
        try:
            send_message(
                client,
                chat_id,
                response.reply,
                mini_app_keyboard(settings.webapp_url),
            )
        except TelegramAPIError as exc:
            push_error = _telegram_push_error(exc)
            _save_push_failure(record, push_error)
            raise push_error from exc
        except httpx.HTTPError as exc:
            push_error = TelegramPushError(
                "TELEGRAM_SEND_FAILED",
                f"Telegram sendMessage failed: {exc.__class__.__name__}",
            )
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
    if "chat not found" in description.lower():
        return TelegramPushError(
            "TELEGRAM_CHAT_NOT_FOUND",
            (
                "Telegram не нашел чат с этим пользователем. Пользователь должен "
                "открыть диалог с ботом и нажать /start, затем повтори отправку."
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
        message = exc.message
    else:
        error_code = "PUSH_SEND_FAILED"
        message = str(exc)
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
        message = exc.message
    else:
        error_code = "STORY_GENERATION_FAILED"
        message = str(exc)
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
) -> dict[str, Any]:
    record = _record_by_telegram_id(telegram_id)
    return _send_push_record(
        record,
        reason=reason or _push_reason_for_record(record, _now()),
        manual=True,
        include_debug=include_debug,
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


def generate_story_for_telegram_user(
    *,
    telegram_id: int,
    include_debug: bool = True,
) -> dict[str, Any]:
    record = _record_by_telegram_id(telegram_id)
    payload = _build_push_payload(
        record,
        reason="Фоновое событие питомца.",
        include_debug=include_debug,
    )
    novelty_history = _record_story_novelty_history(record)
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
    stats_patch: LocalPetStatsPatch | None = None
    stats_delta: dict[str, int] | None = None

    def save_story(current: dict[str, Any] | None) -> dict[str, Any]:
        nonlocal stats_delta, stats_patch
        source_record = current.copy() if isinstance(current, dict) else record.copy()
        if source_record.get("petId") != record.get("petId"):
            raise TelegramPushError(
                "PUSH_PET_CHANGED",
                "Питомец изменился во время генерации истории. Повтори запрос.",
            )
        next_pet, stats_patch, stat_ticks, stats_delta = _apply_story_stat_impact(
            source_record,
            stat_impacts,
            now=now,
        )
        persisted_story = deepcopy(last_story)
        if stats_delta is not None:
            persisted_story["statsDelta"] = stats_delta
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
        _merge_record_lite_overlay_patch(next_record, result.lite_overlay_patch)
        if stat_ticks is not None:
            next_record["lastStatsTickAt"] = _legacy_stats_tick(stat_ticks)
            next_record["lastStatTickAt"] = _stat_tick_iso_map(stat_ticks)
        return next_record

    next_record = _update_record(_telegram_id_from_record(record), save_story)
    story_image: dict[str, Any] | None = None
    story_image_error: str | None = None
    story_image_url: str | None = None
    story_video: dict[str, Any] | None = None
    story_video_error: str | None = None
    story_video_url: str | None = None
    story_image_direction: dict[str, str] = {}
    try:
        image_bytes = generate_background_story_image_bytes(
            pet=payload.pet,
            story=result,
            recent_story_events=_record_recent_story_events(record),
            direction_output=story_image_direction,
        )
        story_image_url = _persist_background_story_image(
            record,
            image_bytes,
            generated_at=now,
        )
        story_image = {
            "bytes": image_bytes,
            "mimeType": "image/png",
        }
        video_bytes = generate_background_story_video_bytes(image_bytes)
        if not video_bytes:
            raise RuntimeError("BACKGROUND_STORY_VIDEO_EMPTY")
        story_video_url = _persist_background_story_video(
            record,
            video_bytes,
            generated_at=now,
        )
        story_video = {
            "bytes": video_bytes,
            "mimeType": "video/mp4",
        }
    except Exception as exc:
        logger.exception("background_story_media_generation failed")
        if story_image is None:
            story_image_error = exc.__class__.__name__
        else:
            story_video_error = exc.__class__.__name__

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

    next_record = _update_record(
        _telegram_id_from_record(record),
        save_story_image_status,
    )
    stored_recent_events = _record_recent_story_events(next_record)
    stored_recent_story_event = next(
        (item for item in reversed(stored_recent_events) if item.get("generatedAt") == now_iso),
        history_event,
    )
    return {
        "generated": True,
        "telegramId": record.get("telegramId"),
        "petId": record.get("petId"),
        "generatedAt": now_iso,
        "story": next_record["lastStory"],
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


def generate_full_story_for_telegram_user(
    *,
    telegram_id: int,
    include_debug: bool = False,
) -> dict[str, Any]:
    record = _record_by_telegram_id(telegram_id)
    now = _now()
    if _record_is_dead(record, now):
        raise TelegramPushError("PET_DEAD", "Питомец умер и больше не может путешествовать.")
    pet = LocalPetChatContext.model_validate(_current_pet_record(record, now))
    full_story_history = _record_full_story_history(record)
    result = generate_full_story(pet=pet, recent_full_stories=full_story_history)
    working_record = deepcopy(record)
    applied_parts: list[dict[str, Any]] = []
    aggregate_delta = {key: 0 for key in STAT_KEYS}

    for part in result.parts:
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
        part_payload["statsDelta"] = actual_delta
        applied_parts.append(part_payload)

    generated_at = _iso(now)
    story_payload = {
        "overallTitle": result.overall_title,
        "arcPlan": result.arc_plan,
        "storyDirection": result.story_direction,
        "parts": applied_parts,
        "generatedAt": generated_at,
        "statsDelta": aggregate_delta,
        "source": "full_story_command",
    }

    def save_full_story(current: dict[str, Any] | None) -> dict[str, Any]:
        source = current.copy() if isinstance(current, dict) else record.copy()
        if source.get("petId") != record.get("petId"):
            raise TelegramPushError(
                "PUSH_PET_CHANGED",
                "Питомец изменился во время генерации истории. Повтори запрос.",
            )
        return {
            **source,
            "pet": working_record["pet"],
            "lastStatsTickAt": working_record.get("lastStatsTickAt"),
            "lastStatTickAt": working_record.get("lastStatTickAt"),
            "lastFullStoryAt": generated_at,
            "lastFullStory": story_payload,
            "fullStoryHistory": _append_full_story_history(
                source,
                story_payload,
            ),
        }

    saved_record = _update_record(telegram_id, save_full_story)
    final_stats = _record_current_stats(saved_record, now)
    return {
        "generated": True,
        "telegramId": telegram_id,
        "petId": record.get("petId"),
        "generatedAt": generated_at,
        "story": story_payload,
        "statsPatch": {
            "stats": final_stats,
            "lastStatsTickAt": saved_record.get("lastStatsTickAt"),
            "lastStatTickAt": saved_record.get("lastStatTickAt"),
        },
        "debug": {"promptDebug": result.prompt_debug} if include_debug else None,
    }


def send_full_story_for_telegram_user(
    client: httpx.Client,
    *,
    telegram_id: int,
    keyboard: dict[str, Any],
) -> dict[str, Any]:
    result = generate_full_story_for_telegram_user(
        telegram_id=telegram_id,
        include_debug=False,
    )
    story = result.get("story") if isinstance(result.get("story"), dict) else {}
    record = _record_by_telegram_id(telegram_id)
    pet = LocalPetChatContext.model_validate(_current_pet_record(record, _now()))
    parts = story.get("parts") if isinstance(story.get("parts"), list) else []
    generated_parts: list[dict[str, Any]] = []
    pose_history = [*_record_recent_story_events(record)]

    try:
        for index, raw_part in enumerate(parts):
            if not isinstance(raw_part, dict):
                continue
            part = raw_part.copy()
            direction: dict[str, str] = {}
            image_bytes = generate_full_story_part_image_bytes(
                pet=pet,
                overall_title=str(story.get("overallTitle") or "История одного дня"),
                part=part,
                recent_story_events=pose_history,
                direction_output=direction,
            )
            if not image_bytes:
                raise RuntimeError("FULL_STORY_IMAGE_EMPTY")
            media_time = _now() + timedelta(microseconds=index)
            image_url = _persist_background_story_image(
                record,
                image_bytes,
                generated_at=media_time,
            )
            video_bytes = generate_background_story_video_bytes(image_bytes)
            if not video_bytes:
                raise RuntimeError("FULL_STORY_VIDEO_EMPTY")
            video_url = _persist_background_story_video(
                record,
                video_bytes,
                generated_at=media_time,
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
            generated_parts.append({"part": enriched_part, "video": video_bytes})
            pose_history.append(enriched_part)
    except Exception as exc:
        logger.exception("full_story_media_generation failed")
        raise TelegramPushError(
            "FULL_STORY_MEDIA_FAILED",
            f"Не удалось создать видео частей: {exc.__class__.__name__}",
        ) from exc

    next_story = {**story, "parts": [item["part"] for item in generated_parts]}

    def save_media(current: dict[str, Any] | None) -> dict[str, Any]:
        source = current.copy() if isinstance(current, dict) else record.copy()
        last_story = source.get("lastFullStory")
        if not isinstance(last_story, dict) or last_story.get("generatedAt") != story.get(
            "generatedAt"
        ):
            return source
        return {**source, "lastFullStory": next_story}

    _update_record(telegram_id, save_media)
    for item in generated_parts:
        send_video(
            client,
            telegram_id,
            item["video"],
            format_full_story_part_message(next_story, item["part"]),
            keyboard,
        )
    result["story"] = next_story
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

    pet = LocalPetChatContext.model_validate(_current_pet_record(record, now))
    image_bytes: bytes | None = None
    image_url: str | None = None
    video_bytes: bytes | None = None
    video_url: str | None = None
    image_error: str | None = None
    image_direction: dict[str, str] = {}
    try:
        pose_history = [*_record_recent_story_events(record)]
        pose_history.extend(
            item
            for item in story.get("parts", [])
            if isinstance(item, dict) and item.get("imagePoseFamily")
        )
        image_bytes = generate_full_story_part_image_bytes(
            pet=pet,
            overall_title=str(story.get("overallTitle") or "История одного дня"),
            part=part,
            recent_story_events=pose_history,
            direction_output=image_direction,
        )
        if not image_bytes:
            raise RuntimeError("DAILY_FULL_STORY_IMAGE_EMPTY")
        image_url = _persist_background_story_image(record, image_bytes, generated_at=now)
        video_bytes = generate_background_story_video_bytes(image_bytes)
        if not video_bytes:
            raise RuntimeError("DAILY_FULL_STORY_VIDEO_EMPTY")
        video_url = _persist_background_story_video(record, video_bytes, generated_at=now)
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
            "lastStoryError": None,
            "lastStoryErrorCode": None,
            "lastStoryErrorAt": None,
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
        message = exc.message
    else:
        code = "PUSH_SEND_FAILED"
        message = str(exc)
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


def send_due_pushes() -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.telegram_daily_push_enabled:
        return []
    now = _now()
    results: list[dict[str, Any]] = []
    for record in _due_records(now):
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
    return results


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
    return due


def send_due_background_stories() -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.background_story_enabled:
        return []
    now = _now()
    results: list[dict[str, Any]] = []
    for record in _due_story_records(now):
        try:
            results.append(_send_daily_full_story_part(record, now=now))
        except Exception as exc:
            _save_story_failure(_fresh_record(record), exc)
    return results


async def _daily_push_loop() -> None:
    settings = get_settings()
    interval = max(60, int(settings.telegram_daily_push_interval_seconds))
    await _scheduler_loop("dailyPush", send_due_pushes, interval)


async def _background_story_loop() -> None:
    settings = get_settings()
    interval = max(60, int(settings.background_story_interval_seconds))
    await _scheduler_loop("backgroundStory", send_due_background_stories, interval)


_scheduler_runtime: dict[str, dict[str, Any]] = {
    "dailyPush": {"running": False, "consecutiveFailures": 0, "lastError": None},
    "backgroundStory": {"running": False, "consecutiveFailures": 0, "lastError": None},
}


async def _scheduler_loop(
    name: str,
    operation: Callable[[], Any],
    interval: int,
) -> None:
    state = _scheduler_runtime[name]
    state["running"] = True
    try:
        while True:
            try:
                await asyncio.to_thread(operation)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state["consecutiveFailures"] = int(state["consecutiveFailures"]) + 1
                state["lastError"] = type(exc).__name__
                logger.exception("scheduler_iteration_failed scheduler=%s", name)
            else:
                state["consecutiveFailures"] = 0
                state["lastError"] = None
            await asyncio.sleep(interval)
    finally:
        state["running"] = False


def scheduler_runtime_status() -> dict[str, dict[str, Any]]:
    return {name: dict(state) for name, state in _scheduler_runtime.items()}


def start_daily_push_scheduler() -> asyncio.Task[None] | None:
    settings = get_settings()
    if (
        not settings.telegram_daily_push_enabled
        or not settings.bot_token
        or not settings.webapp_url
    ):
        return None
    return asyncio.create_task(_daily_push_loop())


def start_background_story_scheduler() -> asyncio.Task[None] | None:
    settings = get_settings()
    if not settings.background_story_enabled or not settings.bot_token or not settings.webapp_url:
        return None
    return asyncio.create_task(_background_story_loop())
