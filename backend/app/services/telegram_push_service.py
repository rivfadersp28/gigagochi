from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

import httpx

from app.bot import TelegramAPIError, mini_app_keyboard, send_message, send_photo
from app.config import get_settings
from app.schemas import (
    LocalChatHistoryItem,
    LocalPetChatContext,
    LocalPetMemoryContext,
    LocalPetPushSnapshotRequest,
    LocalPetPushSnapshotResponse,
    LocalPushRequest,
)
from app.services.background_story_service import (
    generate_background_story,
    generate_background_story_image_bytes,
)
from app.services.lite_overlay import merge_lite_overlay_patch
from app.services.pet_reply_engine.lite_generator import generate_push_pet_message
from app.services.telegram_auth_service import TelegramUserContext

STORE_VERSION = 1
STAT_KEYS = ("hunger", "happiness", "energy")
STAT_FULL_DECAY_HOURS = 6
STAT_DECAY_PER_HOUR = 100 / STAT_FULL_DECAY_HOURS
DAILY_PUSH_REASON = "Ежедневный короткий пуш владельцу от питомца."
MANUAL_PUSH_REASON = "Ручной debug-триггер из админки."
MAX_RECENT_STORY_EVENTS = 10
TELEGRAM_PHOTO_CAPTION_LIMIT = 1024

logger = logging.getLogger(__name__)
_store_lock = Lock()


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


def _store_path() -> Path:
    path = Path(get_settings().telegram_push_store_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _empty_store() -> dict[str, Any]:
    return {"version": STORE_VERSION, "records": {}}


def _read_store_unlocked() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return _empty_store()
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_store()
    if not isinstance(parsed, dict) or not isinstance(parsed.get("records"), dict):
        return _empty_store()
    parsed["version"] = STORE_VERSION
    return parsed


def _write_store_unlocked(store: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _read_store() -> dict[str, Any]:
    with _store_lock:
        return _read_store_unlocked()


def _save_record(record: dict[str, Any]) -> None:
    with _store_lock:
        store = _read_store_unlocked()
        records = store.setdefault("records", {})
        records[str(record["telegramId"])] = record
        _write_store_unlocked(store)


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


def _record_recent_story_events(record: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(record, dict):
        return []
    raw_events = record.get("recentStoryEvents")
    events: list[dict[str, Any]] = []
    if isinstance(raw_events, list):
        for item in raw_events[-MAX_RECENT_STORY_EVENTS:]:
            if isinstance(item, dict):
                events.append(item)
    if events:
        return events
    last_story = record.get("lastStory")
    if isinstance(last_story, dict):
        summary = (
            last_story.get("summary") or last_story.get("storyText") or last_story.get("title")
        )
        if isinstance(summary, str) and summary.strip():
            tags = last_story.get("tags")
            return [
                {
                    "title": last_story.get("title"),
                    "summary": summary.strip(),
                    "eventType": last_story.get("eventType"),
                    "tags": tags if isinstance(tags, list) else [],
                    "source": "last_story_fallback",
                }
            ]
    return events


def _append_recent_story_event(
    record: dict[str, Any],
    event: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    events = _record_recent_story_events(record)
    if isinstance(event, dict) and event:
        events.append(event)
    return events[-MAX_RECENT_STORY_EVENTS:]


def _recent_story_events_patch(record: dict[str, Any] | None) -> dict[str, Any] | None:
    events = _record_recent_story_events(record)
    return {"events": events} if events else None


def _clamp_stat(value: Any) -> int:
    numeric = value if isinstance(value, (int, float)) else 0
    return max(0, min(100, round(numeric)))


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
    record = {
        "telegramId": user.telegram_id,
        "chatId": user.telegram_id,
        "username": user.username,
        "firstName": user.first_name,
        "languageCode": user.language_code,
        "petId": payload.petId,
        "pet": payload.pet.model_dump(mode="json"),
        "history": [item.model_dump(mode="json") for item in payload.history[-12:]],
        "recentAmbientReplies": payload.recentAmbientReplies[-6:],
        "memoryContext": (
            payload.memoryContext.model_dump(mode="json") if payload.memoryContext else None
        ),
        "createdAt": payload.createdAt,
        "updatedAt": payload.updatedAt,
        "lastStatsTickAt": fallback_stat_tick,
        "lastStatTickAt": payload.lastStatTickAt
        or {key: fallback_stat_tick for key in STAT_KEYS},
        "timezone": payload.timezone,
        "registeredAt": now_iso,
    }
    existing = _read_store().get("records", {}).get(str(user.telegram_id))
    same_pet = isinstance(existing, dict) and existing.get("petId") == payload.petId
    lite_overlay_patch = _record_lite_overlay_patch(existing) if same_pet else None
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
    _merge_record_lite_overlay_patch(record, lite_overlay_patch)
    _save_record(record)
    return LocalPetPushSnapshotResponse(
        registered=True,
        telegramId=user.telegram_id,
        updatedAt=now_iso,
        statsPatch=stats_patch,
        liteOverlayPatch=lite_overlay_patch,
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
    existing = _read_store().get("records", {}).get(str(chat_id))
    record = existing.copy() if isinstance(existing, dict) else {}
    record.update(
        {
            "telegramId": record.get("telegramId") or chat_id,
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
    _save_record(record)
    return record


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
    for item in raw_replies[-6:]:
        if isinstance(item, str) and item.strip():
            replies.append(item.strip()[:500])
    return replies


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
    next_record = {
        **record,
        "pet": payload.pet.model_dump(mode="json"),
        "lastStatsTickAt": _legacy_stats_tick(stat_ticks),
        "lastStatTickAt": _stat_tick_iso_map(stat_ticks),
        "lastPushReply": response.reply,
        "lastPushError": None,
        "lastPushErrorCode": None,
        "lastPushErrorAt": None,
        "lastPushAttemptAt": now_iso,
        "chatReachable": True,
    }
    if manual:
        next_record["lastDebugPushAt"] = now_iso
    else:
        next_record["lastPushAt"] = now_iso
    _save_record(next_record)
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
    failed = {
        **record,
        "lastPushError": message,
        "lastPushErrorCode": error_code,
        "lastPushErrorAt": now_iso,
        "lastPushAttemptAt": now_iso,
    }
    if error_code == "TELEGRAM_CHAT_NOT_FOUND":
        failed["chatReachable"] = False
        failed["chatUnreachableAt"] = now_iso
    _save_record(failed)


def _save_story_failure(record: dict[str, Any], exc: Exception) -> None:
    now_iso = _iso()
    if isinstance(exc, TelegramPushError):
        error_code = exc.code
        message = exc.message
    else:
        error_code = "STORY_GENERATION_FAILED"
        message = str(exc)
    failed = {
        **record,
        "lastStoryError": message,
        "lastStoryErrorCode": error_code,
        "lastStoryErrorAt": now_iso,
        "lastStoryAttemptAt": now_iso,
    }
    if error_code == "TELEGRAM_CHAT_NOT_FOUND":
        failed["chatReachable"] = False
        failed["chatUnreachableAt"] = now_iso
    _save_record(failed)


def _format_story_message(story: dict[str, Any], *, limit: int = 3500) -> str:
    title = str(story.get("title") or "Фоновое событие").strip()
    story_text = str(story.get("storyText") or story.get("summary") or "").strip()
    if not story_text:
        story_text = "История сгенерировалась, но текст пустой."
    return f"{title}\n\n{story_text}"[:limit].rstrip()


def _format_story_caption(story: dict[str, Any]) -> str:
    return _format_story_message(story, limit=TELEGRAM_PHOTO_CAPTION_LIMIT)


def send_manual_push(
    *,
    telegram_id: int | None = None,
    reason: str | None = None,
    include_debug: bool = True,
) -> dict[str, Any]:
    record = _record_by_telegram_id(telegram_id)
    return _send_push_record(
        record,
        reason=reason or MANUAL_PUSH_REASON,
        manual=True,
        include_debug=include_debug,
    )


def _apply_story_stat_impact(
    record: dict[str, Any],
    stat_impact: dict[str, Any] | None,
    *,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, datetime] | None]:
    pet = deepcopy(record.get("pet")) if isinstance(record.get("pet"), dict) else {}
    if not isinstance(stat_impact, dict) or stat_impact.get("applies") is not True:
        return pet, None, None

    stat = stat_impact.get("stat")
    if stat not in STAT_KEYS:
        return pet, None, None

    amount = stat_impact.get("amount")
    impact_amount = amount if isinstance(amount, (int, float)) else 25
    impact_amount = max(0, min(100, impact_amount))
    if impact_amount <= 0:
        return pet, None, None

    current_stats = _record_current_stats(record, now)
    current_stats[stat] = _clamp_stat(current_stats[stat] - impact_amount)
    stats = pet.setdefault("stats", {})
    if not isinstance(stats, dict):
        stats = {}
        pet["stats"] = stats
    stats[stat] = current_stats[stat]

    ticks = _stat_tick_map(record, fallback=now)
    ticks[stat] = now
    return pet, _stats_patch(stats=current_stats, ticks=ticks, keys=(stat,)), ticks


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
    result = generate_background_story(
        pet=payload.pet,
        memory_context=payload.memoryContext,
        history=_record_history(record),
        recent_replies=_record_recent_replies(record),
        recent_story_events=_record_recent_story_events(record),
        now_iso=payload.nowIso,
        timezone=payload.timezone,
    )
    now = _now()
    now_iso = _iso(now)
    recent_story_event = getattr(result, "recent_story_event", None)
    stat_impact = getattr(result, "stat_impact", None)
    next_pet, stats_patch, stat_ticks = _apply_story_stat_impact(
        record,
        stat_impact,
        now=now,
    )
    next_record = {
        **record,
        "pet": next_pet,
        "lastStoryAt": now_iso,
        "lastStoryAttemptAt": now_iso,
        "lastStoryError": None,
        "lastStoryErrorCode": None,
        "lastStoryErrorAt": None,
        "lastStory": {
            "title": result.title,
            "summary": result.summary,
            "storyText": result.story_text,
            "eventType": result.event_type,
            "valence": result.valence,
            "tags": list(result.tags),
            "ragText": result.rag_text,
            "statImpact": stat_impact,
        },
        "recentStoryEvents": _append_recent_story_event(record, recent_story_event),
    }
    if stat_ticks is not None:
        next_record["lastStatsTickAt"] = _legacy_stats_tick(stat_ticks)
        next_record["lastStatTickAt"] = _stat_tick_iso_map(stat_ticks)
    _merge_record_lite_overlay_patch(next_record, result.lite_overlay_patch)
    _save_record(next_record)
    story_image: dict[str, Any] | None = None
    story_image_error: str | None = None
    try:
        image_bytes = generate_background_story_image_bytes(
            pet=payload.pet,
            story=result,
        )
        story_image = {
            "bytes": image_bytes,
            "mimeType": "image/png",
        }
    except Exception as exc:
        logger.exception("background_story_image_generation failed")
        story_image_error = exc.__class__.__name__
    return {
        "generated": True,
        "telegramId": record.get("telegramId"),
        "petId": record.get("petId"),
        "generatedAt": now_iso,
        "story": next_record["lastStory"],
        "storyImage": story_image,
        "storyImageError": story_image_error,
        "storyLibraryPatch": None,
        "liteOverlayPatch": result.lite_overlay_patch,
        "recentStoryEvent": recent_story_event,
        "statsPatch": stats_patch,
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
                send_photo(client, chat_id, image_bytes, _format_story_caption(story), keyboard)
            else:
                send_message(client, chat_id, _format_story_message(story), keyboard)
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
    reachable = [record for record in records if record.get("chatReachable") is True]
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for record in reachable:
        try:
            results.append(
                _send_push_record(
                    record,
                    reason=reason or MANUAL_PUSH_REASON,
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


def _daily_push_min_interval(settings: Any) -> timedelta:
    min_interval_seconds = getattr(settings, "telegram_daily_push_min_interval_seconds", None)
    if min_interval_seconds is not None:
        return timedelta(seconds=max(0, int(min_interval_seconds)))
    return timedelta(hours=settings.telegram_daily_push_min_interval_hours)


def _due_records(now: datetime) -> list[dict[str, Any]]:
    settings = get_settings()
    cutoff = now - _daily_push_min_interval(settings)
    records = _read_store().get("records", {})
    due: list[dict[str, Any]] = []
    for record in records.values():
        if not isinstance(record, dict):
            continue
        if (
            not _has_snapshot(record)
            or record.get("chatReachable") is not True
        ):
            continue
        base_time = _latest_time(record.get("lastPushAt"), record.get("lastPushAttemptAt"))
        if base_time is None:
            base_time = _parse_iso(record.get("registeredAt"))
        if base_time and base_time <= cutoff:
            due.append(record)
    return due


def send_due_pushes() -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.telegram_daily_push_enabled:
        return []
    results: list[dict[str, Any]] = []
    for record in _due_records(_now()):
        try:
            results.append(
                _send_push_record(
                    record,
                    reason=DAILY_PUSH_REASON,
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
    if (
        not settings.background_story_enabled
        or not settings.bot_token
        or not settings.webapp_url
    ):
        return None
    return asyncio.create_task(_background_story_loop())
