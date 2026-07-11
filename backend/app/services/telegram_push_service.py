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
)
from app.services.image_service import generated_dir_for
from app.services.lite_overlay import merge_lite_overlay_patch
from app.services.pet_reply_engine.lite_generator import generate_push_pet_message
from app.services.story_delivery_format import (
    format_story_caption,
    format_story_message,
)
from app.services.telegram_auth_service import TelegramUserContext
from app.services.telegram_client import (
    TelegramAPIError,
    mini_app_keyboard,
    send_message,
    send_photo,
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
STORY_STAT_MAX_ITEMS = 2
STORY_STAT_MAX_SINGLE_DAMAGE = 25
STORY_STAT_MAX_TOTAL_DAMAGE = 35

logger = logging.getLogger(__name__)

DEFAULT_DAILY_PUSH_HOURS = (9, 15, 21)
DEFAULT_DAILY_PUSH_WINDOW_MINUTES = 120
DEFAULT_PUSH_TIMEZONE = "Europe/Moscow"


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
    return {
        **({"id": str(value.get("id"))[:120]} if value.get("id") else {}),
        "title": title,
        "tags": tags,
        "createdAt": _compact_event_text(
            value.get("generatedAt") or value.get("createdAt"),
            limit=80,
        ) or _iso(),
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
    safe_pet_id = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in raw_pet_id
    ).strip("-")[:120] or "story"
    output_dir = generated_dir_for(safe_pet_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"background-story-{generated_at.strftime('%Y%m%dT%H%M%S%fZ')}.png"
    (output_dir / filename).write_bytes(image_bytes)
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
        "zeroStatSinceAt" in payload.model_fields_set
        or "diedAt" in payload.model_fields_set
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

    def merge_snapshot(existing: dict[str, Any] | None) -> dict[str, Any]:
        nonlocal stats_patch
        record = deepcopy(incoming_record)
        same_pet = isinstance(existing, dict) and existing.get("petId") == payload.petId
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
                record["recentStoryEvents"] = _record_recent_story_events(existing)
                _merge_record_lite_overlay_patch(record, _record_lite_overlay_patch(existing))
        return record

    record = _update_record(user.telegram_id, merge_snapshot)
    return LocalPetPushSnapshotResponse(
        registered=True,
        telegramId=user.telegram_id,
        updatedAt=now_iso,
        statsPatch=stats_patch,
        liteOverlayPatch=_record_lite_overlay_patch(record),
        recentStoryEventsPatch=_recent_story_events_patch(record),
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
        **(recent_story_event if isinstance(recent_story_event, dict) else {}),
        "storyText": result.story_text,
        "generatedAt": now_iso,
        "createdAt": (
            recent_story_event.get("createdAt")
            if isinstance(recent_story_event, dict)
            else now_iso
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
    try:
        image_bytes = generate_background_story_image_bytes(
            pet=payload.pet,
            story=result,
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
    except Exception as exc:
        logger.exception("background_story_image_generation failed")
        story_image_error = exc.__class__.__name__

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
            recent_events.append(next_item)
        if story_image_url:
            last_story_record["imageUrl"] = story_image_url
        return {
            **source_record,
            "lastStory": last_story_record,
            "recentStoryEvents": recent_events,
            "lastStoryImageStatus": "failed" if story_image_error else "generated",
            "lastStoryImageError": story_image_error,
            "lastStoryImageErrorAt": image_status_at if story_image_error else None,
        }

    next_record = _update_record(
        _telegram_id_from_record(record),
        save_story_image_status,
    )
    stored_recent_events = _record_recent_story_events(next_record)
    stored_recent_story_event = next(
        (
            item
            for item in reversed(stored_recent_events)
            if item.get("generatedAt") == now_iso
        ),
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
        "storyLibraryPatch": None,
        "liteOverlayPatch": _record_lite_overlay_patch(next_record),
        "recentStoryEvent": stored_recent_story_event,
        "statsPatch": stats_patch,
        "statImpacts": stat_impacts,
        "statImpact": stat_impact,
        "debug": {"promptDebug": result.prompt_debug} if include_debug else None,
    }


def _fresh_record(record: dict[str, Any]) -> dict[str, Any]:
    telegram_id = record.get("telegramId")
    if isinstance(telegram_id, int):
        fresh = _read_store().get("records", {}).get(str(telegram_id))
        if isinstance(fresh, dict):
            return fresh
    return record


def _send_story_record(
    record: dict[str, Any],
    *,
    include_debug: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.bot_token:
        raise TelegramPushError("BOT_TOKEN_MISSING", "BOT_TOKEN не настроен.")
    if not settings.webapp_url:
        raise TelegramPushError("WEBAPP_URL_MISSING", "WEBAPP_URL не настроен.")

    telegram_id = record.get("telegramId")
    if not isinstance(telegram_id, int):
        raise TelegramPushError("STORY_TELEGRAM_ID_MISSING", "Telegram ID не найден.")
    chat_id = record.get("chatId")
    if not isinstance(chat_id, int):
        raise TelegramPushError("STORY_CHAT_ID_MISSING", "chat_id для Telegram story не найден.")

    result = generate_story_for_telegram_user(
        telegram_id=telegram_id,
        include_debug=include_debug,
    )
    story = result.get("story") if isinstance(result.get("story"), dict) else {}
    story_image = result.get("storyImage") if isinstance(result.get("storyImage"), dict) else {}
    image_bytes = story_image.get("bytes") if isinstance(story_image, dict) else None
    keyboard = mini_app_keyboard(settings.webapp_url)

    with httpx.Client() as client:
        try:
            if isinstance(image_bytes, bytes) and image_bytes:
                send_photo(client, chat_id, image_bytes, format_story_caption(story), keyboard)
            else:
                send_message(client, chat_id, format_story_message(story), keyboard)
        except TelegramAPIError as exc:
            story_error = _telegram_push_error(exc)
            _save_story_failure(_fresh_record(record), story_error)
            raise story_error from exc
        except httpx.HTTPError as exc:
            story_error = TelegramPushError(
                "TELEGRAM_SEND_FAILED",
                f"Telegram story send failed: {exc.__class__.__name__}",
            )
            _save_story_failure(_fresh_record(record), story_error)
            raise story_error from exc

    sent_at = _iso()
    return {
        **result,
        "sent": True,
        "sentAt": sent_at,
    }


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


def _background_story_min_interval(settings: Any) -> timedelta:
    min_interval_seconds = getattr(settings, "background_story_min_interval_seconds", None)
    if min_interval_seconds is not None:
        return timedelta(seconds=max(0, int(min_interval_seconds)))
    return timedelta(hours=settings.background_story_min_interval_hours)


def _due_story_records(now: datetime) -> list[dict[str, Any]]:
    settings = get_settings()
    cutoff = now - _background_story_min_interval(settings)
    records = _read_store().get("records", {})
    due: list[dict[str, Any]] = []
    for record in records.values():
        if not isinstance(record, dict):
            continue
        if not _has_snapshot(record) or record.get("chatReachable") is not True:
            continue
        if _record_is_dead(record, now):
            continue
        base_time = _latest_time(record.get("lastStoryAt"), record.get("lastStoryAttemptAt"))
        if base_time is None:
            base_time = _parse_iso(record.get("registeredAt"))
        if base_time and base_time <= cutoff:
            due.append(record)
    return due


def send_due_background_stories() -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.background_story_enabled:
        return []
    results: list[dict[str, Any]] = []
    for record in _due_story_records(_now()):
        try:
            results.append(_send_story_record(record, include_debug=False))
        except Exception as exc:
            _save_story_failure(_fresh_record(record), exc)
    return results


async def _daily_push_loop() -> None:
    settings = get_settings()
    interval = max(60, int(settings.telegram_daily_push_interval_seconds))
    while True:
        await asyncio.to_thread(send_due_pushes)
        await asyncio.sleep(interval)


async def _background_story_loop() -> None:
    settings = get_settings()
    interval = max(60, int(settings.background_story_interval_seconds))
    while True:
        await asyncio.to_thread(send_due_background_stories)
        await asyncio.sleep(interval)


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
