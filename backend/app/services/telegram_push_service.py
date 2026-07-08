from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

import httpx

from app.bot import TelegramAPIError, mini_app_keyboard, send_message
from app.config import get_settings
from app.schemas import (
    LocalPetChatContext,
    LocalPetMemoryContext,
    LocalPetPushSnapshotRequest,
    LocalPetPushSnapshotResponse,
    LocalPushRequest,
)
from app.services.background_story_service import generate_background_story
from app.services.pet_reply_engine.lite_generator import generate_push_pet_message
from app.services.story_library import merge_story_library_patch
from app.services.telegram_auth_service import TelegramUserContext

STORE_VERSION = 1
STAT_DECAY_PER_HOUR = 100 / 24
DAILY_PUSH_REASON = "Ежедневный короткий пуш владельцу от питомца."
MANUAL_PUSH_REASON = "Ручной debug-триггер из админки."
DEBUG_PUSH_TARGET_TELEGRAM_ID = 62943754
DEBUG_PUSH_TARGET_FIRST_NAME = "Сергей"

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


def _record_story_library_patch(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    pet = record.get("pet") if isinstance(record.get("pet"), dict) else {}
    bible = pet.get("characterBible") if isinstance(pet.get("characterBible"), dict) else {}
    extensions = bible.get("extensions") if isinstance(bible.get("extensions"), dict) else {}
    overlay = (
        extensions.get("story_library_overlay")
        if isinstance(extensions.get("story_library_overlay"), dict)
        else {}
    )
    bricks = overlay.get("bricks") if isinstance(overlay, dict) else None
    if not isinstance(bricks, list) or not bricks:
        return None
    return {"version": 1, "bricks": bricks}


def _merge_record_story_library_patch(
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
    overlay = extensions.setdefault("story_library_overlay", {})
    if not isinstance(overlay, dict):
        overlay = {}
        extensions["story_library_overlay"] = overlay
    merge_story_library_patch(overlay, patch)


def _clamp_stat(value: Any) -> int:
    numeric = value if isinstance(value, (int, float)) else 0
    return max(0, min(100, round(numeric)))


def _current_pet_record(record: dict[str, Any], now: datetime) -> dict[str, Any]:
    pet = deepcopy(record.get("pet")) if isinstance(record.get("pet"), dict) else {}
    stats = pet.get("stats") if isinstance(pet.get("stats"), dict) else {}
    last_tick = _parse_iso(record.get("lastStatsTickAt")) or _parse_iso(record.get("updatedAt"))
    if last_tick:
        elapsed_hours = max(0.0, (now - last_tick).total_seconds() / 3600)
        decay = elapsed_hours * STAT_DECAY_PER_HOUR
        for key in ("hunger", "happiness", "energy"):
            stats[key] = _clamp_stat(stats.get(key, 0) - decay)
    pet["stats"] = {
        "hunger": _clamp_stat(stats.get("hunger", 0)),
        "happiness": _clamp_stat(stats.get("happiness", 0)),
        "energy": _clamp_stat(stats.get("energy", 0)),
    }

    created_at = _parse_iso(record.get("createdAt"))
    if created_at:
        age_days = max(0.0, (now - created_at).total_seconds() / 86_400)
        pet["stage"] = "baby" if age_days < 2 else "teen" if age_days < 7 else "adult"
    return pet


def register_push_snapshot(
    user: TelegramUserContext,
    payload: LocalPetPushSnapshotRequest,
) -> LocalPetPushSnapshotResponse:
    now_iso = _iso()
    record = {
        "telegramId": user.telegram_id,
        "chatId": user.telegram_id,
        "username": user.username,
        "firstName": user.first_name,
        "languageCode": user.language_code,
        "petId": payload.petId,
        "pet": payload.pet.model_dump(mode="json"),
        "memoryContext": (
            payload.memoryContext.model_dump(mode="json") if payload.memoryContext else None
        ),
        "createdAt": payload.createdAt,
        "updatedAt": payload.updatedAt,
        "lastStatsTickAt": payload.lastStatsTickAt or payload.updatedAt or now_iso,
        "timezone": payload.timezone,
        "registeredAt": now_iso,
    }
    existing = _read_store().get("records", {}).get(str(user.telegram_id))
    story_library_patch = _record_story_library_patch(existing)
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
            "lastStory",
        ):
            record[key] = existing.get(key)
    _merge_record_story_library_patch(record, story_library_patch)
    _save_record(record)
    return LocalPetPushSnapshotResponse(
        registered=True,
        telegramId=user.telegram_id,
        updatedAt=now_iso,
        storyLibraryPatch=story_library_patch,
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


def _send_push_record(
    record: dict[str, Any],
    *,
    reason: str,
    manual: bool,
    include_debug: bool = False,
) -> dict[str, Any]:
    if not _is_debug_push_target(record):
        raise TelegramPushError(
            "PUSH_TARGET_RESTRICTED",
            (
                "Debug push временно разрешен только пользователю "
                f"{DEBUG_PUSH_TARGET_FIRST_NAME} ({DEBUG_PUSH_TARGET_TELEGRAM_ID})."
            ),
        )

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

    now_iso = _iso()
    next_record = {
        **record,
        "pet": payload.pet.model_dump(mode="json"),
        "lastStatsTickAt": now_iso,
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
                "открыть диалог с ботом и нажать /start, затем повтори debug push."
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


def generate_story_for_telegram_user(
    *,
    telegram_id: int,
    include_debug: bool = True,
) -> dict[str, Any]:
    record = _record_by_telegram_id(telegram_id)
    if not _is_debug_push_target(record):
        raise TelegramPushError(
            "STORY_TARGET_RESTRICTED",
            (
                "Debug story временно разрешен только "
                "пользователю "
                f"{DEBUG_PUSH_TARGET_FIRST_NAME} ({DEBUG_PUSH_TARGET_TELEGRAM_ID})."
            ),
        )

    payload = _build_push_payload(
        record,
        reason="Фоновое событие питомца.",
        include_debug=include_debug,
    )
    result = generate_background_story(
        pet=payload.pet,
        memory_context=payload.memoryContext,
        now_iso=payload.nowIso,
        timezone=payload.timezone,
    )
    now_iso = _iso()
    next_record = {
        **record,
        "pet": payload.pet.model_dump(mode="json"),
        "lastStatsTickAt": now_iso,
        "lastStoryAt": now_iso,
        "lastStory": {
            "title": result.title,
            "summary": result.summary,
            "storyText": result.story_text,
            "eventType": result.event_type,
            "valence": result.valence,
            "tags": list(result.tags),
            "ragText": result.rag_text,
        },
    }
    _merge_record_story_library_patch(next_record, result.story_library_patch)
    _save_record(next_record)
    return {
        "generated": True,
        "telegramId": record.get("telegramId"),
        "petId": record.get("petId"),
        "generatedAt": now_iso,
        "story": next_record["lastStory"],
        "storyLibraryPatch": result.story_library_patch,
        "debug": {"promptDebug": result.prompt_debug} if include_debug else None,
    }


def _snapshot_records() -> list[dict[str, Any]]:
    records = _read_store().get("records", {})
    return [
        record
        for record in records.values()
        if isinstance(record, dict) and _has_snapshot(record)
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


def _is_debug_push_target(record: dict[str, Any]) -> bool:
    return record.get("telegramId") == DEBUG_PUSH_TARGET_TELEGRAM_ID


def send_manual_push_to_reachable(
    *,
    reason: str | None = None,
    include_debug: bool = True,
) -> dict[str, Any]:
    records = _snapshot_records()
    reachable = [
        record
        for record in records
        if record.get("chatReachable") is True and _is_debug_push_target(record)
    ]
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
            or not _is_debug_push_target(record)
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


async def _daily_push_loop() -> None:
    settings = get_settings()
    interval = max(60, int(settings.telegram_daily_push_interval_seconds))
    while True:
        await asyncio.to_thread(send_due_pushes)
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
