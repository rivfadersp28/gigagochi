from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _timezone(value: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or "Europe/Moscow"))
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _relative_day_label(day_delta: int) -> str:
    if day_delta == 0:
        return "сегодня"
    if day_delta == 1:
        return "вчера"
    if day_delta == 2:
        return "позавчера"
    if day_delta > 0:
        return f"{day_delta} дней назад"
    return f"через {abs(day_delta)} дней"


def format_temporal_reference(
    value: str | None,
    *,
    now_iso: str | None = None,
    timezone: str | None = None,
) -> str | None:
    occurred = _parse_iso(value)
    if occurred is None:
        return None
    current = _parse_iso(now_iso) or datetime.now(UTC)
    tz = _timezone(timezone)
    occurred_local = occurred.astimezone(tz)
    current_local = current.astimezone(tz)
    day_delta = (current_local.date() - occurred_local.date()).days
    absolute = occurred_local.strftime("%d.%m.%Y %H:%M")
    return f"{absolute} ({_relative_day_label(day_delta)})"


def format_current_time(
    now_iso: str | None = None,
    *,
    timezone: str | None = None,
) -> str:
    current = _parse_iso(now_iso) or datetime.now(UTC)
    tz = _timezone(timezone)
    return f"Текущее локальное время: {current.astimezone(tz).strftime('%d.%m.%Y %H:%M')} {tz.key}."


def temporal_age_days(
    value: str | None,
    *,
    now_iso: str | None = None,
    timezone: str | None = None,
) -> int | None:
    occurred = _parse_iso(value)
    if occurred is None:
        return None
    current = _parse_iso(now_iso) or datetime.now(UTC)
    tz = _timezone(timezone)
    return (current.astimezone(tz).date() - occurred.astimezone(tz).date()).days
