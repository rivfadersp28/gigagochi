from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class TelegramAPIError(RuntimeError):
    def __init__(self, method: str, response: httpx.Response) -> None:
        self.method = method
        self.status_code = response.status_code
        self.description = _telegram_error_description(response)
        super().__init__(
            f"Telegram {method} failed: HTTP {self.status_code}: {self.description}"
        )


def _telegram_error_description(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500] or response.reason_phrase
    if isinstance(payload, dict):
        description = payload.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
    return response.reason_phrase


def telegram_api_url(method: str, bot_token: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}/{method}"


def mini_app_keyboard(webapp_url: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Открыть питомца",
                    "web_app": {"url": webapp_url},
                }
            ]
        ]
    }


def send_message(
    client: httpx.Client,
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any],
) -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    method = "sendMessage"
    response = client.post(
        telegram_api_url(method, settings.bot_token),
        json={
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
        },
        timeout=20,
    )
    if not response.is_success:
        raise TelegramAPIError(method, response)


def handle_update(client: httpx.Client, update: dict[str, Any]) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    text = str(message.get("text") or "")
    settings = get_settings()

    if not isinstance(chat_id, int) or not settings.webapp_url:
        return

    keyboard = mini_app_keyboard(settings.webapp_url)
    if text.startswith("/help"):
        send_message(
            client,
            chat_id,
            "Открой Mini App, создай AI-питомца и общайся с ним внутри Telegram.",
            keyboard,
        )
        return

    if text.startswith("/start") or text.startswith("/app"):
        from app.services.telegram_push_service import mark_chat_started

        username = sender.get("username")
        first_name = sender.get("first_name")
        language_code = sender.get("language_code")
        mark_chat_started(
            chat_id=chat_id,
            username=username if isinstance(username, str) else None,
            first_name=first_name if isinstance(first_name, str) else None,
            language_code=language_code if isinstance(language_code, str) else None,
        )
        send_message(client, chat_id, "Твой питомец ждет внутри Mini App.", keyboard)


def run_bot() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")
    if not settings.webapp_url:
        raise RuntimeError("WEBAPP_URL is not configured")

    offset: int | None = None
    with httpx.Client() as client:
        while True:
            try:
                response = client.get(
                    telegram_api_url("getUpdates", settings.bot_token),
                    params={"timeout": 30, "offset": offset, "allowed_updates": ["message"]},
                    timeout=40,
                )
                response.raise_for_status()
                payload = response.json()
                for update in payload.get("result", []):
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    handle_update(client, update)
            except Exception:
                logger.exception("Telegram bot polling failed")
                time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run_bot()
