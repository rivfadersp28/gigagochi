from __future__ import annotations

import logging
import math
import re
import signal
import uuid
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from threading import Event
from typing import Any

import httpx

from app.config import get_settings
from app.services.bot_command_runtime import (
    BotCommandInboxFull,
    BotCommandLeaseKeeper,
    BoundedBotCommandDispatcher,
    DurableBotCommand,
    SQLiteBotCommandInbox,
    load_bot_update_offset,
    normalize_bot_command_update,
    save_bot_update_offset,
)
from app.services.rate_limit_service import (
    DEFAULT_RATE_LIMIT_STORE_PATH,
    RateLimitExceeded,
    RateLimitReservation,
    SQLiteRateLimiter,
    get_rate_limiter,
)
from app.services.story_delivery_format import (
    format_story_caption,
    format_story_message,
)
from app.services.telegram_client import (
    TelegramAPIError,
    answer_callback_query,
    mini_app_keyboard,
    redact_telegram_token,
    send_message,
    send_video,
    telegram_api_url,
)

logger = logging.getLogger(__name__)
BOT_HEARTBEAT_PATH = Path("/tmp/gigagochi-bot-heartbeat")
BOT_COMMAND_BUSY_MESSAGE = (
    "Предыдущая команда ещё выполняется или очередь занята. Попробуй немного позже."
)


class RetryableBotCommandPreflightError(RuntimeError):
    """The callback failed before a provider or Telegram request could start."""


class RetryableBotCommandDurabilityError(RuntimeError):
    """Local staged progress was not durable, but deterministic replay is safe."""


def _log_bot_error(message: str, exc: BaseException) -> None:
    settings = get_settings()
    logger.error(
        "%s errorType=%s error=%s",
        message,
        type(exc).__name__,
        redact_telegram_token(exc, getattr(settings, "bot_token", None)),
    )


def _diagnostic_message(chat_id: int, public_message: str, diagnostic: str) -> str:
    if chat_id not in getattr(get_settings(), "diagnostic_telegram_ids", set()):
        return public_message
    return f"{public_message}\n\nТехнические детали: {diagnostic[:1200]}"


def _message_command(text: str) -> str:
    first = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    return first.split("@", 1)[0].lower()


def _generation_rate_limit_message(retry_after_seconds: int) -> str:
    if retry_after_seconds >= 3_600:
        wait = f"{math.ceil(retry_after_seconds / 3_600)} ч."
    elif retry_after_seconds >= 60:
        wait = f"{math.ceil(retry_after_seconds / 60)} мин."
    else:
        wait = f"{retry_after_seconds} сек."
    return f"Лимит генераций исчерпан. Попробуй снова через {wait}"


def _reserve_generation_quota(
    settings: Any,
    user_id: int,
    *,
    request_key: str | None = None,
) -> tuple[SQLiteRateLimiter, RateLimitReservation] | None:
    # Legacy env name remains the master switch; storage is durable now.
    if not getattr(settings, "enable_in_memory_rate_limit", False):
        return None
    limiter = get_rate_limiter(
        getattr(settings, "rate_limit_store_path", DEFAULT_RATE_LIMIT_STORE_PATH)
    )
    reservation = limiter.check(
        "generation",
        user_id,
        limit=getattr(settings, "generation_rate_limit_per_day", 0),
        window=timedelta(days=1),
        request_key=request_key,
    )
    if reservation is None:
        return None
    return limiter, reservation


def _handle_generation_command(
    client: httpx.Client,
    chat_id: int,
    keyboard: dict[str, Any],
    settings: Any,
    *,
    quota_user_id: int,
    quota_request_key: str | None,
    submit: Callable[[int, dict[str, Any]], bool | None] | None,
    send_response: Callable[[httpx.Client, int, dict[str, Any]], None],
) -> None:
    try:
        quota_reservation = _reserve_generation_quota(
            settings,
            quota_user_id,
            request_key=quota_request_key,
        )
    except RateLimitExceeded as exc:
        send_message(
            client,
            chat_id,
            _generation_rate_limit_message(exc.retry_after_seconds),
            keyboard,
        )
        return

    if submit is None:
        send_response(client, chat_id, keyboard)
        return

    try:
        accepted = submit(chat_id, keyboard)
    except BaseException:
        if quota_reservation is not None:
            quota_reservation[0].refund(quota_reservation[1])
        raise
    if accepted is False:
        if quota_reservation is not None:
            quota_reservation[0].refund(quota_reservation[1])
        send_message(client, chat_id, BOT_COMMAND_BUSY_MESSAGE, keyboard)


def _send_story_response(
    client: httpx.Client,
    chat_id: int,
    keyboard: dict[str, Any],
) -> None:
    result = _generate_story_response(client, chat_id, keyboard)
    if result is not None:
        _deliver_story_response(client, chat_id, keyboard, result)


def _generate_story_response(
    client: httpx.Client,
    chat_id: int,
    keyboard: dict[str, Any],
    *,
    generation_kwargs: dict[str, Any] | None = None,
    assert_active: Callable[[], None] | None = None,
) -> dict[str, Any] | None:
    from app.services.openai_service import MissingOpenAIAPIKey
    from app.services.telegram_push_service import (
        TelegramPushError,
        generate_story_for_telegram_user,
    )

    try:
        result = generate_story_for_telegram_user(
            telegram_id=chat_id,
            include_debug=False,
            **(generation_kwargs or {}),
        )
    except RetryableBotCommandDurabilityError:
        raise
    except TelegramPushError as exc:
        if assert_active is not None:
            assert_active()
        send_message(
            client,
            chat_id,
            _diagnostic_message(
                chat_id,
                "Не получилось создать историю. Попробуй позже.",
                f"{exc.code}: {exc.message}",
            ),
            keyboard,
        )
        return None
    except MissingOpenAIAPIKey:
        if assert_active is not None:
            assert_active()
        send_message(
            client,
            chat_id,
            _diagnostic_message(
                chat_id,
                "Истории временно недоступны. Попробуй позже.",
                "MISSING_OPENAI_API_KEY",
            ),
            keyboard,
        )
        return None
    except Exception as exc:
        _log_bot_error("Telegram /story generation failed", exc)
        if assert_active is not None:
            assert_active()
        send_message(
            client,
            chat_id,
            "Не удалось сгенерировать историю. Попробуй позже.",
            keyboard,
        )
        return None
    return result


def _validate_prepared_result(payload: dict[str, Any], *, kind: str) -> None:
    if (
        payload.get("version") != 1
        or payload.get("kind") != kind
        or not isinstance(payload.get("story"), dict)
    ):
        raise ValueError(f"invalid durable {kind} prepared result")


class _DurableProgressWriter:
    def __init__(
        self,
        *,
        inbox: SQLiteBotCommandInbox,
        update_id: int,
        owner: str,
        kind: str,
        payload: dict[str, Any] | None,
        assert_active: Callable[[], None] | None = None,
    ) -> None:
        self._inbox = inbox
        self._update_id = update_id
        self._owner = owner
        self._kind = kind
        self._payload = payload
        self._assert_active = assert_active
        if payload is None:
            return
        if (
            payload.get("version") != 2
            or payload.get("kind") != kind
            or type(payload.get("checkpointRevision")) is not int
            or payload["checkpointRevision"] < 1
            or not isinstance(payload.get("progress"), dict)
        ):
            raise ValueError(f"invalid durable {kind} progress envelope")

    @property
    def progress(self) -> dict[str, Any]:
        if self._payload is None:
            return {}
        progress = self._payload.get("progress")
        if not isinstance(progress, dict):
            raise ValueError(f"invalid durable {self._kind} progress")
        return progress

    @property
    def prepared_result(self) -> dict[str, Any] | None:
        result = self.progress.get("preparedResult")
        if result is None:
            return None
        if not isinstance(result, dict) or not isinstance(result.get("story"), dict):
            raise ValueError(f"invalid durable {self._kind} prepared result")
        return result

    def checkpoint(self, progress: dict[str, Any]) -> None:
        if self._assert_active is not None:
            self._assert_active()
        previous_revision = (
            self._payload.get("checkpointRevision") if self._payload is not None else 0
        )
        if type(previous_revision) is not int:
            raise ValueError(f"invalid durable {self._kind} checkpoint revision")
        payload = {
            "version": 2,
            "kind": self._kind,
            "checkpointRevision": previous_revision + 1,
            "progress": progress,
        }
        try:
            checkpointed = self._inbox.checkpoint_prepared(
                self._update_id,
                owner=self._owner,
                payload=payload,
            )
        except Exception as exc:
            raise RetryableBotCommandDurabilityError(
                f"failed to checkpoint durable {self._kind} progress"
            ) from exc
        if not checkpointed:
            raise RetryableBotCommandDurabilityError(
                f"durable {self._kind} claim was lost before checkpoint"
            )
        self._payload = payload


def _deliver_story_response(
    client: httpx.Client,
    chat_id: int,
    keyboard: dict[str, Any],
    result: dict[str, Any],
    *,
    video_bytes: bytes | None = None,
    assert_active: Callable[[], None] | None = None,
) -> None:
    from app.services.telegram_push_service import load_persisted_story_video_bytes

    story = result.get("story") if isinstance(result.get("story"), dict) else {}
    story_video = result.get("storyVideo") if isinstance(result.get("storyVideo"), dict) else {}
    if video_bytes is None:
        candidate = story_video.get("bytes") if isinstance(story_video, dict) else None
        video_bytes = candidate if isinstance(candidate, bytes) and candidate else None
    if video_bytes is None:
        video_bytes = load_persisted_story_video_bytes(
            pet_id=result.get("petId"),
            telegram_id=chat_id,
            media_url=story.get("videoUrl"),
        )
    if isinstance(video_bytes, bytes) and video_bytes:
        try:
            if assert_active is not None:
                assert_active()
            send_video(
                client,
                chat_id,
                video_bytes,
                format_story_caption(story),
                keyboard,
            )
            return
        except (TelegramAPIError, httpx.HTTPError) as exc:
            _log_bot_error("Telegram /story sendVideo failed; falling back to sendMessage", exc)
    if assert_active is not None:
        assert_active()
    send_message(client, chat_id, format_story_message(story), keyboard)


def _new_worker_http_client() -> httpx.Client:
    try:
        return httpx.Client()
    except Exception as exc:
        raise RetryableBotCommandPreflightError("failed to initialize HTTP client") from exc


def _story_worker(
    chat_id: int,
    keyboard: dict[str, Any],
    *,
    durable_inbox: SQLiteBotCommandInbox | None = None,
    durable_update_id: int | None = None,
    durable_owner: str | None = None,
    durable_claim_check: Callable[[], None] | None = None,
) -> None:
    with _new_worker_http_client() as client:
        if durable_inbox is None and durable_update_id is None and durable_owner is None:
            _send_story_response(client, chat_id, keyboard)
            return
        if durable_inbox is None or durable_update_id is None or durable_owner is None:
            raise ValueError("incomplete durable story context")

        if durable_claim_check is not None:
            durable_claim_check()
        prepared = durable_inbox.load_prepared(durable_update_id, owner=durable_owner)
        if prepared is not None and prepared.get("version") == 1:
            _validate_prepared_result(prepared, kind="story")
            _deliver_story_response(
                client,
                chat_id,
                keyboard,
                prepared,
                assert_active=durable_claim_check,
            )
            return

        progress_writer = _DurableProgressWriter(
            inbox=durable_inbox,
            update_id=durable_update_id,
            owner=durable_owner,
            kind="story",
            payload=prepared,
            assert_active=durable_claim_check,
        )
        prepared_result = progress_writer.prepared_result
        video_bytes: bytes | None = None
        if prepared_result is None:
            result = _generate_story_response(
                client,
                chat_id,
                keyboard,
                generation_kwargs={
                    "idempotency_key": f"telegram-update:{durable_update_id}",
                    "durable_progress": progress_writer.progress,
                    "checkpoint": progress_writer.checkpoint,
                    **(
                        {"assert_active": durable_claim_check}
                        if durable_claim_check is not None
                        else {}
                    ),
                },
                assert_active=durable_claim_check,
            )
            if result is None:
                return
            story_video = result.get("storyVideo")
            if isinstance(story_video, dict):
                candidate = story_video.get("bytes")
                if isinstance(candidate, bytes) and candidate:
                    video_bytes = candidate
            prepared_result = progress_writer.prepared_result
            if prepared_result is None:
                story = result.get("story")
                if not isinstance(story, dict):
                    raise ValueError("story generation returned no story")
                progress_writer.checkpoint(
                    {
                        **progress_writer.progress,
                        "preparedResult": {"petId": result.get("petId"), "story": story},
                    }
                )
                prepared_result = progress_writer.prepared_result
                if prepared_result is None:
                    raise RetryableBotCommandDurabilityError(
                        "story generation finished without a durable prepared result"
                    )
        _deliver_story_response(
            client,
            chat_id,
            keyboard,
            prepared_result,
            video_bytes=video_bytes,
            assert_active=durable_claim_check,
        )


_DEFAULT_STORY_WORKER = _story_worker


def _send_full_story_response(
    client: httpx.Client,
    chat_id: int,
    keyboard: dict[str, Any],
) -> None:
    from app.services.telegram_push_service import send_full_story_for_telegram_user

    def send() -> None:
        send_full_story_for_telegram_user(
            client,
            telegram_id=chat_id,
            keyboard=keyboard,
        )

    _run_full_story_action(client, chat_id, keyboard, send)


def _run_full_story_action(
    client: httpx.Client,
    chat_id: int,
    keyboard: dict[str, Any],
    action: Callable[[], None],
    *,
    assert_active: Callable[[], None] | None = None,
) -> None:
    from app.services.full_story_service import FullStoryGenerationError
    from app.services.openai_service import MissingOpenAIAPIKey
    from app.services.telegram_push_service import TelegramPushError

    try:
        action()
    except RetryableBotCommandDurabilityError:
        raise
    except TelegramPushError as exc:
        if assert_active is not None:
            assert_active()
        send_message(
            client,
            chat_id,
            _diagnostic_message(
                chat_id,
                "Не получилось создать большую историю. Попробуй позже.",
                f"{exc.code}: {exc.message}",
            ),
            keyboard,
        )
    except MissingOpenAIAPIKey:
        if assert_active is not None:
            assert_active()
        send_message(
            client,
            chat_id,
            _diagnostic_message(
                chat_id,
                "Истории временно недоступны. Попробуй позже.",
                "MISSING_OPENAI_API_KEY",
            ),
            keyboard,
        )
    except FullStoryGenerationError as exc:
        _log_bot_error("Telegram /full_story payload validation failed", exc)
        if assert_active is not None:
            assert_active()
        send_message(
            client,
            chat_id,
            "Не удалось собрать четыре части. Попробуй ещё раз.",
            keyboard,
        )
    except Exception as exc:
        _log_bot_error("Telegram /full_story generation failed", exc)
        if assert_active is not None:
            assert_active()
        send_message(client, chat_id, "Не удалось сгенерировать большую историю.", keyboard)


def _full_story_worker(
    chat_id: int,
    keyboard: dict[str, Any],
    *,
    durable_inbox: SQLiteBotCommandInbox | None = None,
    durable_update_id: int | None = None,
    durable_owner: str | None = None,
    durable_claim_check: Callable[[], None] | None = None,
) -> None:
    with _new_worker_http_client() as client:
        if durable_inbox is None and durable_update_id is None and durable_owner is None:
            _send_full_story_response(client, chat_id, keyboard)
            return
        if durable_inbox is None or durable_update_id is None or durable_owner is None:
            raise ValueError("incomplete durable full-story context")

        def prepare_checkpoint_and_deliver() -> None:
            from app.services.telegram_push_service import (
                deliver_prepared_full_story_for_telegram_user,
                prepare_full_story_for_telegram_delivery,
            )

            if durable_claim_check is not None:
                durable_claim_check()
            prepared = durable_inbox.load_prepared(durable_update_id, owner=durable_owner)
            if prepared is not None and prepared.get("version") == 1:
                _validate_prepared_result(prepared, kind="full_story")
                deliver_prepared_full_story_for_telegram_user(
                    client,
                    telegram_id=chat_id,
                    keyboard=keyboard,
                    prepared_result=prepared,
                    **(
                        {"assert_active": durable_claim_check}
                        if durable_claim_check is not None
                        else {}
                    ),
                )
                return

            progress_writer = _DurableProgressWriter(
                inbox=durable_inbox,
                update_id=durable_update_id,
                owner=durable_owner,
                kind="full_story",
                payload=prepared,
                assert_active=durable_claim_check,
            )
            prepared_result = progress_writer.prepared_result
            generated_parts: list[dict[str, Any]] | None = None
            if prepared_result is None:
                _result, generated_parts = prepare_full_story_for_telegram_delivery(
                    telegram_id=chat_id,
                    idempotency_key=f"telegram-update:{durable_update_id}",
                    durable_progress=progress_writer.progress,
                    checkpoint=progress_writer.checkpoint,
                    **(
                        {"assert_active": durable_claim_check}
                        if durable_claim_check is not None
                        else {}
                    ),
                )
                prepared_result = progress_writer.prepared_result
                if prepared_result is None:
                    story = _result.get("story")
                    if not isinstance(story, dict):
                        raise ValueError("full-story generation returned no story")
                    progress_writer.checkpoint(
                        {
                            **progress_writer.progress,
                            "preparedResult": {
                                "petId": _result.get("petId"),
                                "story": story,
                            },
                        }
                    )
                    prepared_result = progress_writer.prepared_result
                    if prepared_result is None:
                        raise RetryableBotCommandDurabilityError(
                            "full-story generation finished without a durable prepared result"
                        )
            deliver_prepared_full_story_for_telegram_user(
                client,
                telegram_id=chat_id,
                keyboard=keyboard,
                prepared_result=prepared_result,
                generated_parts=generated_parts,
                **(
                    {"assert_active": durable_claim_check}
                    if durable_claim_check is not None
                    else {}
                ),
            )

        _run_full_story_action(
            client,
            chat_id,
            keyboard,
            prepare_checkpoint_and_deliver,
            assert_active=durable_claim_check,
        )


_DEFAULT_FULL_STORY_WORKER = _full_story_worker


def _send_push_response(
    client: httpx.Client,
    chat_id: int,
    keyboard: dict[str, Any],
    *,
    assert_active: Callable[[], None] | None = None,
) -> None:
    from app.services.openai_service import MissingOpenAIAPIKey
    from app.services.telegram_push_service import TelegramPushError, send_manual_push

    try:
        send_manual_push(
            telegram_id=chat_id,
            include_debug=False,
            **({"assert_active": assert_active} if assert_active is not None else {}),
        )
    except RetryableBotCommandDurabilityError:
        raise
    except TelegramPushError as exc:
        if assert_active is not None:
            assert_active()
        send_message(
            client,
            chat_id,
            _diagnostic_message(
                chat_id,
                "Не получилось отправить сообщение питомца. Попробуй позже.",
                f"{exc.code}: {exc.message}",
            ),
            keyboard,
        )
    except MissingOpenAIAPIKey:
        if assert_active is not None:
            assert_active()
        send_message(
            client,
            chat_id,
            _diagnostic_message(
                chat_id,
                "Сообщения питомца временно недоступны. Попробуй позже.",
                "MISSING_OPENAI_API_KEY",
            ),
            keyboard,
        )
    except Exception as exc:
        _log_bot_error("Telegram /push generation failed", exc)
        if assert_active is not None:
            assert_active()
        send_message(
            client,
            chat_id,
            "Не получилось отправить сообщение питомца. Попробуй позже.",
            keyboard,
        )


def _push_worker(
    chat_id: int,
    keyboard: dict[str, Any],
    *,
    durable_claim_check: Callable[[], None] | None = None,
) -> None:
    with _new_worker_http_client() as client:
        _send_push_response(
            client,
            chat_id,
            keyboard,
            assert_active=durable_claim_check,
        )


_DEFAULT_PUSH_WORKER = _push_worker


def handle_update(
    client: httpx.Client,
    update: dict[str, Any],
    *,
    submit_story: Callable[[int, dict[str, Any]], bool | None] | None = None,
    submit_full_story: Callable[[int, dict[str, Any]], bool | None] | None = None,
    submit_push: Callable[[int, dict[str, Any]], bool | None] | None = None,
) -> None:
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        data = str(callback.get("data") or "")
        match = re.fullmatch(r"it:([a-f0-9]{16}):([01])", data)
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        if match and isinstance(chat_id, int):
            from app.services.telegram_push_service import interactive_story_outcome_for_callback
            video, caption = interactive_story_outcome_for_callback(
                telegram_id=chat_id, token=match.group(1), choice_index=int(match.group(2))
            )
            answer_callback_query(client, str(callback.get("id") or ""))
            webapp_url = get_settings().webapp_url
            if not webapp_url:
                return
            send_video(client, chat_id, video, caption, mini_app_keyboard(webapp_url))
        return
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
    sender_id = sender.get("id")
    quota_user_id = sender_id if isinstance(sender_id, int) else chat_id
    update_id = update.get("update_id")
    quota_request_key = (
        f"telegram-update:{update_id}" if type(update_id) is int and update_id >= 0 else None
    )
    if command == "/help":
        send_message(
            client,
            chat_id,
            (
                "Открой Mini App, создай AI-питомца и общайся с ним внутри Telegram.\n\n"
                "/easy — включить простой банк задач для всех\n"
                "/hard — включить сложный банк задач для всех"
            ),
            keyboard,
        )
        return

    if command in {"/easy", "/hard"}:
        from app.services.task_bank_mode import write_task_bank_mode

        mode = "easy" if command == "/easy" else "hard"
        write_task_bank_mode(mode)
        label = "простой" if mode == "easy" else "сложный"
        send_message(
            client,
            chat_id,
            f"Включён {label} банк задач для всех. Он применится к следующей истории.",
            keyboard,
        )
        return

    if command == "/full_story":
        _handle_generation_command(
            client,
            chat_id,
            keyboard,
            settings,
            quota_user_id=quota_user_id,
            quota_request_key=quota_request_key,
            submit=submit_full_story,
            send_response=_send_full_story_response,
        )
        return

    if command == "/push":
        if submit_push is not None:
            if submit_push(chat_id, keyboard) is False:
                send_message(client, chat_id, BOT_COMMAND_BUSY_MESSAGE, keyboard)
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


def _persist_polled_updates(
    inbox: SQLiteBotCommandInbox,
    offset_path: Path,
    offset: int | None,
    updates: list[object],
) -> tuple[int | None, bool]:
    """Persist replayable work before acknowledging it through the local offset."""
    ordered_updates = sorted(
        (
            update
            for update in updates
            if isinstance(update, dict)
            and type(update.get("update_id")) is int
            and update["update_id"] >= 0
        ),
        key=lambda update: update["update_id"],
    )
    for update in ordered_updates:
        update_id = update["update_id"]
        next_offset = update_id + 1
        if offset is not None and next_offset <= offset:
            continue

        command = normalize_bot_command_update(update)
        if command is not None:
            try:
                inbox.enqueue(command)
            except BotCommandInboxFull:
                logger.warning("Durable Telegram command inbox is full")
                return offset, True

        # A crash after enqueue but before this replace safely redelivers the same
        # update; the durable update_id marker keeps the decision idempotent.
        offset = save_bot_update_offset(offset_path, next_offset)
    return offset, False


def _finish_durable_worker(
    worker: Callable[[int, dict[str, Any]], None],
    chat_id: int,
    keyboard: dict[str, Any],
    *,
    command: DurableBotCommand,
    inbox: SQLiteBotCommandInbox,
    owner: str,
    lease_keeper: BotCommandLeaseKeeper,
) -> None:
    with inbox.command_lock(command.update_id):
        _finish_locked_durable_worker(
            worker,
            chat_id,
            keyboard,
            command=command,
            inbox=inbox,
            owner=owner,
            lease_keeper=lease_keeper,
        )


def _finish_locked_durable_worker(
    worker: Callable[[int, dict[str, Any]], None],
    chat_id: int,
    keyboard: dict[str, Any],
    *,
    command: DurableBotCommand,
    inbox: SQLiteBotCommandInbox,
    owner: str,
    lease_keeper: BotCommandLeaseKeeper,
) -> None:
    # Delivery is intentionally at-least-once across process death. Telegram does
    # not expose an idempotency key for sendMessage/sendVideo, so a crash after its
    # server accepted the send but before inbox.complete() can replay one command.
    should_complete = True
    should_release = False
    try:
        if not lease_keeper.ensure_owned(command.update_id):
            raise RetryableBotCommandDurabilityError(
                "durable Telegram command claim was lost before callback"
            )
        worker(chat_id, keyboard)
    except RetryableBotCommandPreflightError as exc:
        # This error is raised only before an HTTP client exists, so no provider or
        # Telegram request could have started and an immediate retry is safe.
        should_complete = False
        should_release = True
        _log_bot_error(
            f"Durable Telegram {command.command} preflight failed updateId={command.update_id}",
            exc,
        )
    except RetryableBotCommandDurabilityError as exc:
        # Text can be recomputed for free. Paid media is atomically persisted at a
        # deterministic path before its stage checkpoint, so replay can recover it.
        should_complete = False
        should_release = True
        _log_bot_error(
            f"Durable Telegram {command.command} checkpoint failed updateId={command.update_id}",
            exc,
        )
    except Exception as exc:
        # Once the callback starts, an exception can be after a paid provider call
        # or accepted Telegram delivery. Complete rather than blindly duplicating it.
        _log_bot_error(
            f"Durable Telegram {command.command} callback failed updateId={command.update_id}",
            exc,
        )
    except BaseException as exc:
        # Fatal control-flow exceptions are not ordinary terminal outcomes. Stop
        # renewing and leave the lease for crash-style recovery by another process.
        should_complete = False
        _log_bot_error(
            f"Durable Telegram {command.command} callback aborted updateId={command.update_id}",
            exc,
        )
        raise
    finally:
        try:
            if should_release:
                released = inbox.release(command.update_id, owner=owner)
                if not released:
                    logger.error(
                        "Lost retryable durable Telegram command claim updateId=%s",
                        command.update_id,
                    )
            elif should_complete:
                completed = inbox.complete(command.update_id, owner=owner)
                if not completed:
                    logger.error(
                        "Lost durable Telegram command claim updateId=%s",
                        command.update_id,
                    )
        except Exception as exc:
            _log_bot_error(
                f"Durable Telegram command finalization failed updateId={command.update_id}",
                exc,
            )
        finally:
            lease_keeper.remove(command.update_id)


def _dispatch_pending_commands(
    client: httpx.Client,
    inbox: SQLiteBotCommandInbox,
    dispatcher: BoundedBotCommandDispatcher,
    lease_keeper: BotCommandLeaseKeeper,
    *,
    owner: str,
    lease_seconds: int,
    ready_limit: int,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    submitted_count = 0
    async_commands = {"/full_story", "/push"}
    for command in inbox.list_ready(limit=ready_limit):
        if should_stop is not None and should_stop():
            break
        if command.command in async_commands and not dispatcher.can_submit(command.chat_id):
            continue
        if not inbox.claim(command.update_id, owner=owner, lease_seconds=lease_seconds):
            continue
        lease_keeper.add(command.update_id)
        async_submission: bool | None = None

        def make_submit(
            worker: Callable[[int, dict[str, Any]], None],
            durable_command: DurableBotCommand = command,
        ) -> Callable[[int, dict[str, Any]], bool]:
            def submit(chat_id: int, keyboard: dict[str, Any]) -> bool:
                nonlocal async_submission

                def durable_worker(
                    worker_chat_id: int,
                    worker_keyboard: dict[str, Any],
                ) -> None:
                    worker_callback = worker
                    if worker in {
                        _DEFAULT_STORY_WORKER,
                        _DEFAULT_FULL_STORY_WORKER,
                        _DEFAULT_PUSH_WORKER,
                    }:
                        worker_with_context: Any = worker

                        def assert_active() -> None:
                            if not lease_keeper.ensure_owned(durable_command.update_id):
                                raise RetryableBotCommandDurabilityError(
                                    "durable Telegram command claim was lost"
                                )

                        def checkpointed_worker(
                            checkpointed_chat_id: int,
                            checkpointed_keyboard: dict[str, Any],
                        ) -> None:
                            worker_with_context(
                                checkpointed_chat_id,
                                checkpointed_keyboard,
                                durable_inbox=inbox,
                                durable_update_id=durable_command.update_id,
                                durable_owner=owner,
                                durable_claim_check=assert_active,
                            )

                        worker_callback = checkpointed_worker
                    _finish_durable_worker(
                        worker_callback,
                        worker_chat_id,
                        worker_keyboard,
                        command=durable_command,
                        inbox=inbox,
                        owner=owner,
                        lease_keeper=lease_keeper,
                    )

                async_submission = dispatcher.submit(durable_worker, chat_id, keyboard)
                return async_submission

            return submit

        try:
            handle_update(
                client,
                command.update,
                submit_story=make_submit(_story_worker),
                submit_full_story=make_submit(_full_story_worker),
                submit_push=make_submit(_push_worker),
            )
        except BaseException as exc:
            lease_keeper.remove(command.update_id)
            try:
                inbox.release(command.update_id, owner=owner)
            except Exception as release_exc:
                _log_bot_error(
                    f"Durable Telegram command release failed updateId={command.update_id}",
                    release_exc,
                )
            _log_bot_error(
                f"Durable Telegram command dispatch failed updateId={command.update_id}",
                exc,
            )
            if not isinstance(exc, Exception):
                raise
            continue

        if async_submission is True:
            submitted_count += 1
            continue
        if async_submission is False:
            # Capacity can only shrink through this loop; this is a defensive
            # fallback for an unexpected concurrent submission.
            lease_keeper.remove(command.update_id)
            inbox.release(command.update_id, owner=owner)
            continue

        # Synchronous commands and quota rejections finish inside handle_update.
        try:
            inbox.complete(command.update_id, owner=owner)
        finally:
            lease_keeper.remove(command.update_id)
        submitted_count += 1
    return submitted_count


def run_bot() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured")
    if not settings.webapp_url:
        raise RuntimeError("WEBAPP_URL is not configured")

    offset_path = Path(settings.bot_update_offset_path)
    offset = load_bot_update_offset(offset_path)
    inbox = SQLiteBotCommandInbox(
        settings.bot_command_inbox_path,
        max_pending=settings.bot_command_inbox_max_pending,
        max_pending_per_chat=settings.bot_command_inbox_max_pending_per_chat,
        max_completed=settings.bot_command_inbox_max_completed,
        completed_retention_seconds=settings.bot_command_inbox_completed_retention_seconds,
    )
    owner = uuid.uuid4().hex
    lease_seconds = settings.bot_command_inbox_lease_seconds
    lease_keeper = BotCommandLeaseKeeper(
        inbox,
        owner=owner,
        lease_seconds=lease_seconds,
    )
    stop_requested = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_requested.set()

    previous_sigterm = signal.signal(signal.SIGTERM, request_stop)
    previous_sigint = signal.signal(signal.SIGINT, request_stop)
    BOT_HEARTBEAT_PATH.touch()
    lease_keeper.start()
    try:
        with (
            httpx.Client() as client,
            BoundedBotCommandDispatcher(
                max_workers=settings.bot_story_workers,
                max_queued_commands=settings.bot_command_max_queued,
            ) as command_executor,
        ):
            while not stop_requested.is_set():
                try:
                    _dispatch_pending_commands(
                        client,
                        inbox,
                        command_executor,
                        lease_keeper,
                        owner=owner,
                        lease_seconds=lease_seconds,
                        ready_limit=settings.bot_command_inbox_max_pending,
                        should_stop=stop_requested.is_set,
                    )
                    response = client.get(
                        telegram_api_url("getUpdates", settings.bot_token),
                        params={
                            "timeout": 30,
                            "offset": offset,
                            "allowed_updates": ["message", "callback_query"],
                        },
                        timeout=40,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    raw_updates = payload.get("result", []) if isinstance(payload, dict) else []
                    updates = raw_updates if isinstance(raw_updates, list) else []
                    offset, inbox_full = _persist_polled_updates(
                        inbox,
                        offset_path,
                        offset,
                        updates,
                    )
                    if not inbox_full:
                        BOT_HEARTBEAT_PATH.touch()
                    if not stop_requested.is_set():
                        _dispatch_pending_commands(
                            client,
                            inbox,
                            command_executor,
                            lease_keeper,
                            owner=owner,
                            lease_seconds=lease_seconds,
                            ready_limit=settings.bot_command_inbox_max_pending,
                            should_stop=stop_requested.is_set,
                        )
                    if inbox_full:
                        stop_requested.wait(1)
                except Exception as exc:
                    _log_bot_error("Telegram bot polling failed", exc)
                    stop_requested.wait(5)
    finally:
        # Dispatcher shutdown drains accepted callbacks while leases are still
        # renewed. Pending/unaccepted commands remain durable for the next run.
        lease_keeper.stop()
        signal.signal(signal.SIGTERM, previous_sigterm)
        signal.signal(signal.SIGINT, previous_sigint)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    run_bot()
