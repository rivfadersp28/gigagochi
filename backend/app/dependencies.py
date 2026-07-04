from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, Request, status

from app.config import get_settings
from app.services.telegram_auth_service import (
    TelegramAuthError,
    TelegramUserContext,
    validate_init_data,
)


def _init_data_from_request(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("tma "):
        return authorization[4:].strip()
    return request.headers.get("x-telegram-init-data", "").strip()


async def get_telegram_user(request: Request) -> TelegramUserContext:
    settings = get_settings()
    init_data = _init_data_from_request(request)

    if settings.allow_dev_tma_auth and (not init_data or init_data == "dev"):
        return TelegramUserContext(
            telegram_id=0,
            username="dev",
            first_name="Dev",
            language_code="ru",
            auth_date=datetime.now(UTC),
        )

    try:
        return validate_init_data(
            init_data,
            settings.bot_token or "",
            max_age_seconds=settings.telegram_init_data_max_age_seconds,
        )
    except TelegramAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": exc.code, "message": "Telegram auth failed."},
        ) from exc
