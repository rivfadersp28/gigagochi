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
from app.services.pet_reply_engine.lite_generator import generate_push_pet_message
from app.services.telegram_auth_service import TelegramUserContext

STORE_VERSION = 1
STAT_DECAY_PER_HOUR = 100 / 24
DAILY_PUSH_REASON = "Ежедневный короткий пуш владельцу от питомца."
MANUAL_PUSH_REASON = "Ручной debug-триггер из админки."

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
        "cleanliness": _clamp_stat(stats.get("cleanliness", 80)),
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
    if isinstance(existing, dict):
        record["lastPushAt"] = existing.get("lastPushAt")
        record["lastDebugPushAt"] = existing.get("lastDebugPushAt")
        record["lastPushReply"] = existing.get("lastPushReply")
    _save_record(record)
    return LocalPetPushSnapshotResponse(
        registered=True,
        telegramId=user.telegram_id,
        updatedAt=now_iso,
    )


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
    }


def push_status() -> dict[str, Any]:
    records = _read_store().get("records", {})
    summaries: list[dict[str, Any]] = []
    for record in records.values():
        if not isinstance(record, dict):
            continue
        summaries.append(_record_summary(record))
    summaries.sort(key=lambda item: str(item.get("registeredAt", "")), reverse=True)
    return {
        "count": len(records),
        "latest": summaries[0] if summaries else None,
        "records": summaries,
    }


def _record_by_telegram_id(telegram_id: int | None = None) -> dict[str, Any]:
    records = _read_store().get("records", {})
    if telegram_id is not None:
        record = records.get(str(telegram_id))
        if isinstance(record, dict):
            return record
        raise TelegramPushError(
            "PUSH_SNAPSHOT_NOT_FOUND",
            "Snapshot для этого Telegram ID не найден.",
        )

    latest = None
    for record in records.values():
        if not isinstance(record, dict):
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


def _due_records(now: datetime) -> list[dict[str, Any]]:
    settings = get_settings()
    cutoff = now - timedelta(hours=settings.telegram_daily_push_min_interval_hours)
    records = _read_store().get("records", {})
    due: list[dict[str, Any]] = []
    for record in records.values():
        if not isinstance(record, dict):
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
