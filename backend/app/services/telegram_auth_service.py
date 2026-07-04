from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl

from pydantic import BaseModel


class TelegramAuthError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TelegramUserContext(BaseModel):
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    language_code: str | None = None
    auth_date: datetime


def _build_data_check_string(init_data: str) -> tuple[str, str | None, dict[str, str]]:
    pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = pairs.pop("hash", None)
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    return data_check_string, received_hash, pairs


def _calculate_hash(data_check_string: str, bot_token: str) -> str:
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int = 60 * 60 * 24,
    now: datetime | None = None,
) -> TelegramUserContext:
    if not init_data:
        raise TelegramAuthError("missing_init_data")
    if not bot_token:
        raise TelegramAuthError("missing_bot_token")

    data_check_string, received_hash, pairs = _build_data_check_string(init_data)
    if not received_hash:
        raise TelegramAuthError("invalid_hash")

    calculated_hash = _calculate_hash(data_check_string, bot_token)
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise TelegramAuthError("invalid_hash")

    auth_date_raw = pairs.get("auth_date")
    if not auth_date_raw or not auth_date_raw.isdigit():
        raise TelegramAuthError("expired")

    auth_date = datetime.fromtimestamp(int(auth_date_raw), tz=UTC)
    current_time = now or datetime.now(UTC)
    if current_time - auth_date > timedelta(seconds=max_age_seconds):
        raise TelegramAuthError("expired")

    user_raw = pairs.get("user")
    if not user_raw:
        raise TelegramAuthError("missing_user")

    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise TelegramAuthError("missing_user") from exc

    telegram_id = user.get("id")
    if not isinstance(telegram_id, int):
        raise TelegramAuthError("missing_user")

    language_code = user.get("language_code")

    return TelegramUserContext(
        telegram_id=telegram_id,
        username=user.get("username") if isinstance(user.get("username"), str) else None,
        first_name=user.get("first_name") if isinstance(user.get("first_name"), str) else None,
        language_code=language_code if isinstance(language_code, str) else None,
        auth_date=auth_date,
    )
