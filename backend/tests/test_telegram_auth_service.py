from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException, Request

from app.dependencies import get_telegram_user
from app.services.telegram_auth_service import (
    MAX_TELEGRAM_INIT_DATA_CHARS,
    TelegramAuthError,
    validate_init_data,
)

BOT_TOKEN = "123456:test-token"


def signed_init_data(data: dict[str, str], bot_token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**data, "hash": digest})


def valid_data(auth_date: datetime | None = None) -> dict[str, str]:
    auth_date = auth_date or datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    return {
        "auth_date": str(int(auth_date.timestamp())),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(
            {
                "id": 42,
                "first_name": "Serge",
                "username": "serge",
                "language_code": "ru",
            },
            separators=(",", ":"),
        ),
    }


def test_validate_init_data_accepts_valid_payload() -> None:
    now = datetime(2026, 7, 3, 12, 5, tzinfo=UTC)
    user = validate_init_data(signed_init_data(valid_data()), BOT_TOKEN, now=now)

    assert user.telegram_id == 42
    assert user.username == "serge"
    assert user.first_name == "Serge"
    assert user.language_code == "ru"


def test_validate_init_data_rejects_invalid_hash() -> None:
    payload = signed_init_data(valid_data()).replace("hash=", "hash=bad")

    with pytest.raises(TelegramAuthError) as error:
        validate_init_data(payload, BOT_TOKEN, now=datetime(2026, 7, 3, 12, 5, tzinfo=UTC))

    assert error.value.code == "invalid_hash"


def test_validate_init_data_rejects_oversized_header_before_parsing() -> None:
    with pytest.raises(TelegramAuthError) as error:
        validate_init_data("x" * (MAX_TELEGRAM_INIT_DATA_CHARS + 1), BOT_TOKEN)

    assert error.value.code == "init_data_too_large"


def test_validate_init_data_rejects_expired_auth_date() -> None:
    old_auth_date = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    payload = signed_init_data(valid_data(old_auth_date))

    with pytest.raises(TelegramAuthError) as error:
        validate_init_data(
            payload,
            BOT_TOKEN,
            now=old_auth_date + timedelta(days=2),
        )

    assert error.value.code == "expired"


def test_validate_init_data_rejects_auth_date_too_far_in_future() -> None:
    now = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    payload = signed_init_data(valid_data(now + timedelta(seconds=61)))

    with pytest.raises(TelegramAuthError) as error:
        validate_init_data(payload, BOT_TOKEN, now=now)

    assert error.value.code == "expired"


def test_validate_init_data_rejects_an_out_of_range_auth_date() -> None:
    data = valid_data()
    data["auth_date"] = "9" * 200
    payload = signed_init_data(data)

    with pytest.raises(TelegramAuthError) as error:
        validate_init_data(payload, BOT_TOKEN)

    assert error.value.code == "expired"


def test_validate_init_data_requires_user() -> None:
    data = valid_data()
    data.pop("user")
    payload = signed_init_data(data)

    with pytest.raises(TelegramAuthError) as error:
        validate_init_data(payload, BOT_TOKEN, now=datetime(2026, 7, 3, 12, 5, tzinfo=UTC))

    assert error.value.code == "missing_user"


def test_dev_fallback_auth(monkeypatch) -> None:
    class Settings:
        allow_dev_tma_auth = True
        bot_token = None
        telegram_init_data_max_age_seconds = 86_400

    monkeypatch.setattr("app.dependencies.get_settings", lambda: Settings())

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/chat",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    }
    request = Request(scope)
    user = asyncio.run(get_telegram_user(request))

    assert user.telegram_id == 0
    assert user.username == "dev"


def test_dev_fallback_auth_is_not_exposed_to_remote_clients(monkeypatch) -> None:
    class Settings:
        allow_dev_tma_auth = True
        bot_token = None
        telegram_init_data_max_age_seconds = 86_400

    monkeypatch.setattr("app.dependencies.get_settings", lambda: Settings())
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/chat",
            "headers": [],
            "client": ("203.0.113.7", 12345),
        }
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(get_telegram_user(request))

    assert error.value.status_code == 401
    assert error.value.detail["code"] == "missing_init_data"
