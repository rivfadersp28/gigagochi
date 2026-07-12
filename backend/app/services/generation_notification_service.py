from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.services.telegram_client import mini_app_keyboard, send_message

logger = logging.getLogger(__name__)

GENERATION_READY_MESSAGE = "Ваш друг родился, скорее познакомьтесь с ним"


def send_generation_ready_notification(telegram_id: int) -> None:
    settings = get_settings()
    if not settings.bot_token or not settings.webapp_url:
        logger.info(
            "pet_generation_notification_skipped ownerId=%s reason=telegram_not_configured",
            telegram_id,
        )
        return

    with httpx.Client() as client:
        send_message(
            client,
            telegram_id,
            GENERATION_READY_MESSAGE,
            mini_app_keyboard(settings.webapp_url),
        )
