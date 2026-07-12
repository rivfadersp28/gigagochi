from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException, status
from openai import APIStatusError

from app.services.image_service import generation_error_code
from app.services.ops_alert_service import notify_ops
from app.services.prompt_debug import current_ai_log_context

MAX_PROVIDER_ERROR_CHARS = 1200
AI_FAILURE_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "ai-failures.jsonl"
logger = logging.getLogger(__name__)


def generation_error_message(code: str) -> str:
    if code == "OPENAI_TIMEOUT":
        return "Создание питомца заняло больше времени, чем ожидалось. Попробуйте ещё раз."
    if code == "OPENAI_RATE_LIMIT":
        return "Создание питомцев временно ограничено. Попробуйте позже."
    if code in {
        "OPENAI_AUTH_FAILED",
        "OPENAI_PERMISSION_DENIED",
        "MISSING_OPENAI_API_KEY",
    }:
        return "Сервис временно недоступен. Попробуйте позже."
    if code in {"IMAGE_POSTPROCESS_FAILED", "IMAGE_SAVE_FAILED"}:
        return "Не получилось подготовить питомца. Попробуйте ещё раз."
    return "Не получилось создать питомца. Попробуйте ещё раз."


def chat_error_code(exc: Exception) -> str:
    code = generation_error_code(exc)
    if code in {"GENERATION_FAILED", "IMAGE_SAVE_FAILED", "IMAGE_PROMPT_REJECTED"}:
        return "CHAT_FAILED"
    return code


def chat_error_message(code: str) -> str:
    if code == "OPENAI_TIMEOUT":
        return "Ответ занял больше времени, чем ожидалось. Отправьте сообщение ещё раз."
    if code == "OPENAI_RATE_LIMIT":
        return "Ответы временно ограничены. Попробуйте позже."
    if code in {
        "OPENAI_AUTH_FAILED",
        "OPENAI_PERMISSION_DENIED",
        "MISSING_OPENAI_API_KEY",
        "OPENAI_BAD_REQUEST",
        "OPENAI_CONNECTION_FAILED",
    } or code.startswith("OPENAI_STATUS_"):
        return "Сервис временно недоступен. Попробуйте позже."
    return "Не получилось получить ответ. Отправьте сообщение ещё раз."


def travel_error_message(code: str) -> str:
    if code == "OPENAI_TIMEOUT":
        return "Путешествие заняло больше времени, чем ожидалось. Попробуйте ещё раз."
    if code == "OPENAI_RATE_LIMIT":
        return "Путешествия временно ограничены. Попробуйте позже."
    if code in {
        "OPENAI_AUTH_FAILED",
        "OPENAI_PERMISSION_DENIED",
        "MISSING_OPENAI_API_KEY",
        "OPENAI_BAD_REQUEST",
        "OPENAI_CONNECTION_FAILED",
    } or code.startswith("OPENAI_STATUS_"):
        return "Сервис временно недоступен. Попробуйте позже."
    if code in {"IMAGE_SAVE_FAILED", "IMAGE_PROMPT_REJECTED"}:
        return "Не получилось подготовить путешествие. Попробуйте ещё раз."
    return "Не получилось создать путешествие. Попробуйте ещё раз."


def _compact_error_text(value: str) -> str:
    return " ".join(value.split())[:MAX_PROVIDER_ERROR_CHARS]


def _payload_error_message(payload: object) -> str | None:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return None
    for key in ("message", "detail", "error_description"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    error = payload.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        return _payload_error_message(error)
    return None


def _provider_response_text(response: object) -> str | None:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    try:
        payload = response.json()  # type: ignore[attr-defined]
    except Exception:
        return None
    try:
        return json.dumps(payload, ensure_ascii=False)
    except TypeError:
        return str(payload)


def _provider_exception(exc: Exception) -> Exception | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError | APIStatusError):
            return current
        current = current.__cause__ or current.__context__
    return None


def provider_error_details(exc: Exception) -> dict[str, object]:
    provider_exc = _provider_exception(exc)
    if provider_exc is None:
        return {}

    response = None
    provider_status: int | None = None
    if isinstance(provider_exc, httpx.HTTPStatusError):
        response = provider_exc.response
        provider_status = provider_exc.response.status_code
    elif isinstance(provider_exc, APIStatusError):
        response = provider_exc.response
        provider_status = provider_exc.status_code

    details: dict[str, object] = {}
    if provider_status is not None:
        details["providerStatus"] = provider_status

    response_text = _provider_response_text(response) if response is not None else None
    if response_text:
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError:
            provider_message = response_text
        else:
            provider_message = _payload_error_message(payload) or response_text
        details["providerMessage"] = _compact_error_text(provider_message)

    headers = getattr(response, "headers", None)
    request_id = headers.get("x-request-id") or headers.get("cf-ray") if headers else None
    if request_id:
        details["requestId"] = str(request_id)
    return details


def error_detail(error: str, code: str, message: str, exc: Exception) -> dict[str, object]:
    return {
        "code": code,
        "error": error,
        "message": message,
        "exceptionType": type(exc).__name__,
        "exceptionMessage": _compact_error_text(str(exc)) if str(exc) else None,
        **provider_error_details(exc),
    }


def public_error_detail(
    detail: dict[str, object],
    *,
    include_diagnostic: bool = False,
) -> dict[str, object]:
    result = {key: detail[key] for key in ("code", "message") if detail.get(key) is not None}
    if include_diagnostic:
        result["diagnostic"] = {
            key: detail[key]
            for key in (
                "error",
                "exceptionType",
                "exceptionMessage",
                "providerStatus",
                "providerMessage",
                "requestId",
            )
            if detail.get(key) is not None
        }
    return result


def write_ai_failure_log_line(log_payload: dict[str, Any]) -> None:
    AI_FAILURE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line_payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        **log_payload,
    }
    with AI_FAILURE_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(line_payload, ensure_ascii=False, default=str))
        log_file.write("\n")


def log_ai_request_failure(
    endpoint: str,
    detail: dict[str, object],
    exc: Exception,
) -> None:
    log_payload = {
        **current_ai_log_context(),
        "event": "ai_request_failed",
        "endpoint": endpoint,
        "code": detail.get("code"),
        "error": detail.get("error"),
        "message": detail.get("message"),
        "providerStatus": detail.get("providerStatus"),
        "providerMessage": detail.get("providerMessage"),
        "requestId": detail.get("requestId"),
        "exceptionType": type(exc).__name__,
        "exceptionMessage": _compact_error_text(str(exc)) if str(exc) else None,
    }
    try:
        write_ai_failure_log_line(log_payload)
    except Exception:
        logger.warning("Could not write AI failure log line", exc_info=True)
    logger.exception(
        "AI request failed: %s",
        json.dumps(log_payload, ensure_ascii=False, default=str),
    )
    notify_ops(
        f"ai:{endpoint}:{detail.get('code')}",
        "\n".join(
            [
                f"AI error: {endpoint}",
                f"code: {detail.get('code')}",
                f"provider: {detail.get('providerStatus') or '-'}",
                f"exception: {type(exc).__name__}",
                f"request: {detail.get('requestId') or '-'}",
            ]
        ),
    )


def ai_failure_http_exception(
    endpoint: str,
    error: str,
    code: str,
    message: str,
    exc: Exception,
    *,
    include_diagnostic: bool = False,
) -> HTTPException:
    detail = error_detail(error, code, message, exc)
    log_ai_request_failure(endpoint, detail, exc)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=public_error_detail(detail, include_diagnostic=include_diagnostic),
    )
