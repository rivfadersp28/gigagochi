from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.services.telegram_client import mini_app_keyboard, send_message

logger = logging.getLogger(__name__)

GENERATION_READY_MESSAGE = "Ваш друг родился, скорее познакомьтесь с ним"
OUTFIT_READY_MESSAGE = "Ваш персонаж переоделся. Скорее посмотрите на него в обновках!"


def send_generation_ready_notification(telegram_id: int) -> None:
    _send_generation_notification(telegram_id, GENERATION_READY_MESSAGE)


def send_outfit_ready_notification(telegram_id: int) -> None:
    _send_generation_notification(telegram_id, OUTFIT_READY_MESSAGE)


def _send_generation_notification(telegram_id: int, message: str) -> None:
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
            message,
            mini_app_keyboard(settings.webapp_url),
        )
