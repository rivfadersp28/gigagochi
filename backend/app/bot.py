from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from app.config import get_settings
from app.services.story_delivery_format import (
    format_story_caption,
    format_story_message,
)
from app.services.telegram_client import (
    TelegramAPIError,
    mini_app_keyboard,
    send_message,
    send_photo,
    telegram_api_url,
)

logger = logging.getLogger(__name__)


def _message_command(text: str) -> str:
    first = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    return first.split("@", 1)[0].lower()


def _send_story_response(
    client: httpx.Client,
    chat_id: int,
    keyboard: dict[str, Any],
) -> None:
    from app.services.openai_service import MissingOpenAIAPIKey
    from app.services.telegram_push_service import (
        TelegramPushError,
        generate_story_for_telegram_user,
    )

    try:
        result = generate_story_for_telegram_user(telegram_id=chat_id, include_debug=False)
    except TelegramPushError as exc:
        send_message(client, chat_id, exc.message, keyboard)
        return
    except MissingOpenAIAPIKey:
        send_message(client, chat_id, "На сервере не настроен AI API key.", keyboard)
        return
    except Exception:
        logger.exception("Telegram /story generation failed")
        send_message(
            client,
            chat_id,
            "Не удалось сгенерировать историю. Попробуй позже.",
            keyboard,
        )
        return

    story = result.get("story") if isinstance(result.get("story"), dict) else {}
    story_image = result.get("storyImage") if isinstance(result.get("storyImage"), dict) else {}
    image_bytes = story_image.get("bytes") if isinstance(story_image, dict) else None
    if isinstance(image_bytes, bytes) and image_bytes:
        try:
            send_photo(
                client,
                chat_id,
                image_bytes,
                format_story_caption(story),
                keyboard,
            )
            return
        except (TelegramAPIError, httpx.HTTPError):
            logger.exception("Telegram /story sendPhoto failed; falling back to sendMessage")
    send_message(client, chat_id, format_story_message(story), keyboard)


def _story_worker(chat_id: int, keyboard: dict[str, Any]) -> None:
    with httpx.Client() as client:
        _send_story_response(client, chat_id, keyboard)


def _send_full_story_response(
    client: httpx.Client,
    chat_id: int,
    keyboard: dict[str, Any],
) -> None:
    from app.services.full_story_service import FullStoryGenerationError
    from app.services.openai_service import MissingOpenAIAPIKey
    from app.services.telegram_push_service import (
        TelegramPushError,
        send_full_story_for_telegram_user,
    )

    try:
        send_full_story_for_telegram_user(
            client,
            telegram_id=chat_id,
            keyboard=keyboard,
        )
    except TelegramPushError as exc:
        send_message(client, chat_id, exc.message, keyboard)
    except MissingOpenAIAPIKey:
        send_message(client, chat_id, "На сервере не настроен AI API key.", keyboard)
    except FullStoryGenerationError:
        logger.exception("Telegram /full_story payload validation failed")
        send_message(
            client,
            chat_id,
            "Не удалось собрать четыре части. Попробуй ещё раз.",
            keyboard,
        )
    except Exception:
        logger.exception("Telegram /full_story generation failed")
        send_message(client, chat_id, "Не удалось сгенерировать большую историю.", keyboard)


def _full_story_worker(chat_id: int, keyboard: dict[str, Any]) -> None:
    with httpx.Client() as client:
        _send_full_story_response(client, chat_id, keyboard)


def _send_push_response(
    client: httpx.Client,
    chat_id: int,
    keyboard: dict[str, Any],
) -> None:
    from app.services.openai_service import MissingOpenAIAPIKey
    from app.services.telegram_push_service import TelegramPushError, send_manual_push

    try:
        send_manual_push(telegram_id=chat_id, include_debug=False)
    except TelegramPushError as exc:
        send_message(client, chat_id, exc.message, keyboard)
    except MissingOpenAIAPIKey:
        send_message(client, chat_id, "На сервере не настроен AI API key.", keyboard)
    except Exception:
        logger.exception("Telegram /push generation failed")
        send_message(client, chat_id, "Не удалось сгенерировать push. Попробуй позже.", keyboard)


def _push_worker(chat_id: int, keyboard: dict[str, Any]) -> None:
    with httpx.Client() as client:
        _send_push_response(client, chat_id, keyboard)


def handle_update(
    client: httpx.Client,
    update: dict[str, Any],
    *,
    submit_story: Callable[[int, dict[str, Any]], None] | None = None,
    submit_full_story: Callable[[int, dict[str, Any]], None] | None = None,
    submit_push: Callable[[int, dict[str, Any]], None] | None = None,
) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    text = str(message.get("text") or "")
    settings = get_settings()

    if not isinstance(chat_id, int) or not settings.webapp_url:
        return

    keyboard = mini_app_keyboard(settings.webapp_url)
    command = _message_command(text)
    if command == "/help":
        send_message(
            client,
            chat_id,
            "Открой Mini App, создай AI-питомца и общайся с ним внутри Telegram.",
            keyboard,
        )
        return

    if command == "/story":
        if submit_story is not None:
            submit_story(chat_id, keyboard)
        else:
            _send_story_response(client, chat_id, keyboard)
        return

    if command == "/full_story":
        if submit_full_story is not None:
            submit_full_story(chat_id, keyboard)
        else:
            _send_full_story_response(client, chat_id, keyboard)
        return

    if command == "/push":
        if submit_push is not None:
            submit_push(chat_id, keyboard)
        else:
            _send_push_response(client, chat_id, keyboard)
        return

    if command in {"/start", "/app"}:
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
    with (
        httpx.Client() as client,
        ThreadPoolExecutor(
            max_workers=settings.bot_story_workers,
            thread_name_prefix="telegram-command",
        ) as command_executor,
    ):

        def submit_story(chat_id: int, keyboard: dict[str, Any]) -> None:
            command_executor.submit(_story_worker, chat_id, keyboard)

        def submit_full_story(chat_id: int, keyboard: dict[str, Any]) -> None:
            command_executor.submit(_full_story_worker, chat_id, keyboard)

        def submit_push(chat_id: int, keyboard: dict[str, Any]) -> None:
            command_executor.submit(_push_worker, chat_id, keyboard)

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
                    handle_update(
                        client,
                        update,
                        submit_story=submit_story,
                        submit_full_story=submit_full_story,
                        submit_push=submit_push,
                    )
            except Exception:
                logger.exception("Telegram bot polling failed")
                time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run_bot()
