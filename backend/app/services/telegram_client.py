from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings
from app.services.story_delivery_format import TELEGRAM_PHOTO_CAPTION_LIMIT


class TelegramAPIError(RuntimeError):
    def __init__(self, method: str, response: httpx.Response) -> None:
        self.method = method
        self.status_code = response.status_code
        self.description = _telegram_error_description(response)
        super().__init__(f"Telegram {method} failed: HTTP {self.status_code}: {self.description}")


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


def redact_telegram_token(value: object, bot_token: str | None) -> str:
    text = str(value)
    token = str(bot_token or "").strip()
    return text.replace(token, "<redacted>") if token else text


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


def send_photo(
    client: httpx.Client,
    chat_id: int,
    photo: bytes,
    caption: str,
    reply_markup: dict[str, Any],
) -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    filename, mime_type = _photo_file_info(photo)
    method = "sendPhoto"
    response = client.post(
        telegram_api_url(method, settings.bot_token),
        data={
            "chat_id": str(chat_id),
            "caption": caption[:TELEGRAM_PHOTO_CAPTION_LIMIT].rstrip(),
            "reply_markup": json.dumps(reply_markup, ensure_ascii=False),
        },
        files={"photo": (filename, photo, mime_type)},
        timeout=30,
    )
    if not response.is_success:
        raise TelegramAPIError(method, response)


def send_video(
    client: httpx.Client,
    chat_id: int,
    video: bytes,
    caption: str,
    reply_markup: dict[str, Any],
) -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")

    method = "sendVideo"
    response = client.post(
        telegram_api_url(method, settings.bot_token),
        data={
            "chat_id": str(chat_id),
            "caption": caption[:TELEGRAM_PHOTO_CAPTION_LIMIT].rstrip(),
            "reply_markup": json.dumps(reply_markup, ensure_ascii=False),
            "supports_streaming": "true",
        },
        files={"video": ("story.mp4", video, "video/mp4")},
        timeout=60,
    )
    if not response.is_success:
        raise TelegramAPIError(method, response)


def _photo_file_info(photo: bytes) -> tuple[str, str]:
    if photo.startswith(b"\xff\xd8\xff"):
        return "story.jpg", "image/jpeg"
    if photo.startswith(b"\x89PNG\r\n\x1a\n"):
        return "story.png", "image/png"
    if photo.startswith(b"RIFF") and photo[8:12] == b"WEBP":
        return "story.webp", "image/webp"
    return "story.bin", "application/octet-stream"
