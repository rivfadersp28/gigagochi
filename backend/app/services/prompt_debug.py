from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AI_PROMPT_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "ai-prompts.jsonl"
AI_RESPONSE_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "ai-responses.jsonl"
_prompt_log_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "prompt_log_context",
    default=None,
)
_last_prompt_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "last_prompt_context",
    default=None,
)


def set_prompt_log_context(context: dict[str, Any]) -> Any:
    _last_prompt_context.set(None)
    return _prompt_log_context.set(context)


def reset_prompt_log_context(token: Any) -> None:
    _prompt_log_context.reset(token)
    _last_prompt_context.set(None)


def current_ai_log_context() -> dict[str, Any]:
    context = dict(_prompt_log_context.get() or {})
    last_prompt = _last_prompt_context.get()
    if last_prompt:
        context["lastPrompt"] = last_prompt
    return context


def _response_format_summary(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value

    summary: dict[str, Any] = {"type": value.get("type")}
    json_schema = value.get("json_schema")
    if isinstance(json_schema, Mapping):
        summary["json_schema"] = {
            "name": json_schema.get("name"),
            "strict": json_schema.get("strict"),
        }
    return summary


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def write_prompt_log_line(payload: Mapping[str, Any]) -> dict[str, Any]:
    AI_PROMPT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    context = _prompt_log_context.get() or {}
    line_payload = {
        "timestamp": _now_iso(),
        **context,
        **payload,
    }
    with AI_PROMPT_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(line_payload, ensure_ascii=False, default=str))
        log_file.write("\n")
    return line_payload


def write_response_log_line(payload: Mapping[str, Any]) -> dict[str, Any]:
    AI_RESPONSE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    context = current_ai_log_context()
    line_payload = {
        "timestamp": _now_iso(),
        **context,
        **payload,
    }
    with AI_RESPONSE_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(line_payload, ensure_ascii=False, default=str))
        log_file.write("\n")
    return line_payload


def _object_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _usage_summary(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if isinstance(usage, Mapping):
        return {
            key: usage.get(key)
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "cost",
            )
            if usage.get(key) is not None
        }
    return None


def _first_choice(completion: Any) -> Any:
    choices = _object_value(completion, "choices") or []
    return choices[0] if choices else None


def log_chat_completion_response(label: str, completion: Any) -> dict[str, Any]:
    choice = _first_choice(completion)
    payload = {
        "event": "ai_response",
        "promptType": "chat_completion",
        "label": label,
        "providerGenerationId": _object_value(completion, "id"),
        "model": _object_value(completion, "model"),
        "finishReason": _object_value(choice, "finish_reason") if choice else None,
        "usage": _usage_summary(_object_value(completion, "usage")),
    }
    return write_response_log_line(payload)


def _message_content_length(messages: Any) -> int:
    if not isinstance(messages, list):
        return 0
    total = 0
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
    return total


def _prompt_hash(messages: Any) -> str:
    encoded = json.dumps(
        messages if isinstance(messages, list) else [],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _full_prompt_logging_enabled() -> bool:
    return os.getenv("AI_PROMPT_LOG_FULL", "").strip().lower() in {"1", "true", "yes", "on"}


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _truncate_log_text(value: str, limit: int = 1000) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def log_ambient_reply_diagnostic(
    label: str,
    request_kwargs: Mapping[str, Any],
    *,
    raw_reply: str,
    visible_reply: str,
) -> dict[str, Any]:
    messages = request_kwargs.get("messages", [])
    payload: dict[str, Any] = {
        "event": "ambient_reply_diagnostic",
        "promptType": "chat_completion",
        "label": label,
        "model": request_kwargs.get("model"),
        "promptHash": _prompt_hash(messages),
        "promptMessageCount": len(messages) if isinstance(messages, list) else 0,
        "promptContentChars": _message_content_length(messages),
        "rawReplyChars": len(raw_reply),
        "visibleReplyChars": len(visible_reply),
        "rawReplyHash": _text_hash(raw_reply),
        "visibleReplyHash": _text_hash(visible_reply),
    }
    if _full_prompt_logging_enabled():
        payload["rawReply"] = _truncate_log_text(raw_reply)
        payload["visibleReply"] = _truncate_log_text(visible_reply)
    line_payload = write_response_log_line(payload)
    if _full_prompt_logging_enabled():
        print("\n=== AI ambient diagnostic ===", flush=True)
        print(json.dumps(line_payload, ensure_ascii=False, default=str), flush=True)
        print("=== End AI ambient diagnostic ===\n", flush=True)
    return line_payload


def _response_id_from_payload(payload: Mapping[str, Any]) -> str | None:
    for key in ("id", "generation_id", "generationId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _interesting_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    if headers is None:
        return {}
    interesting: dict[str, str] = {}
    for key, value in headers.items():
        lower_key = str(key).lower()
        if lower_key in {
            "x-request-id",
            "cf-ray",
            "x-openrouter-generation-id",
            "x-generation-id",
        }:
            interesting[str(key)] = str(value)
    return interesting


def log_image_generation_response(
    label: str,
    request_kwargs: Mapping[str, Any],
    response_payload: Mapping[str, Any],
    *,
    headers: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "event": "ai_response",
        "promptType": "image_generation",
        "label": label,
        "providerGenerationId": _response_id_from_payload(response_payload),
        "model": request_kwargs.get("model"),
        "created": response_payload.get("created"),
        "usage": _usage_summary(response_payload.get("usage")),
        "responseKeys": sorted(response_payload.keys()),
        "headers": _interesting_headers(headers),
    }
    return write_response_log_line(payload)


def chat_completion_prompt_snapshot(
    label: str,
    request_kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "label": label,
        "model": request_kwargs.get("model"),
        "messages": request_kwargs.get("messages", []),
    }
    if "tools" in request_kwargs:
        payload["tools"] = request_kwargs["tools"]
        payload["tool_choice"] = request_kwargs.get("tool_choice")
    if "response_format" in request_kwargs:
        payload["response_format"] = _response_format_summary(request_kwargs["response_format"])
    return payload


def log_chat_completion_prompt(label: str, request_kwargs: Mapping[str, Any]) -> dict[str, Any]:
    payload = chat_completion_prompt_snapshot(label, request_kwargs)
    messages = payload.get("messages", [])
    log_payload = (
        payload
        if _full_prompt_logging_enabled()
        else {
            "label": label,
            "model": payload.get("model"),
            "promptHash": _prompt_hash(messages),
            "promptMessageCount": len(messages) if isinstance(messages, list) else 0,
            "promptContentChars": _message_content_length(messages),
            "hasTools": "tools" in payload,
            "responseFormat": payload.get("response_format"),
        }
    )
    line_payload = write_prompt_log_line(
        {
            "event": "ai_prompt",
            "promptType": "chat_completion",
            **log_payload,
        }
    )
    _last_prompt_context.set(
        {
            "timestamp": line_payload["timestamp"],
            "promptType": "chat_completion",
            "label": label,
            "model": payload.get("model"),
        }
    )
    if _full_prompt_logging_enabled():
        print(f"\n=== AI chat prompt: {label} ===", flush=True)
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str), flush=True)
        print("=== End AI chat prompt ===\n", flush=True)
    return payload


def image_generation_prompt_snapshot(
    label: str,
    request_kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "label": label,
        "model": request_kwargs.get("model"),
        "prompt": request_kwargs.get("prompt"),
        "size": request_kwargs.get("size"),
        "resolution": request_kwargs.get("resolution"),
        "aspect_ratio": request_kwargs.get("aspect_ratio"),
        "quality": request_kwargs.get("quality"),
        "n": request_kwargs.get("n"),
        "output_format": request_kwargs.get("output_format"),
        "inputReferenceCount": len(request_kwargs.get("input_references") or []),
    }


def log_image_generation_prompt(label: str, request_kwargs: Mapping[str, Any]) -> dict[str, Any]:
    payload = image_generation_prompt_snapshot(label, request_kwargs)
    prompt = str(payload.get("prompt") or "")
    log_payload = (
        payload
        if _full_prompt_logging_enabled()
        else {key: value for key, value in payload.items() if key != "prompt"}
    )
    if not _full_prompt_logging_enabled():
        log_payload["promptHash"] = _text_hash(prompt)
        log_payload["promptChars"] = len(prompt)
    line_payload = write_prompt_log_line(
        {
            "event": "ai_prompt",
            "promptType": "image_generation",
            **log_payload,
        }
    )
    _last_prompt_context.set(
        {
            "timestamp": line_payload["timestamp"],
            "promptType": "image_generation",
            "label": label,
            "model": payload.get("model"),
        }
    )
    if _full_prompt_logging_enabled():
        print(f"\n=== AI image prompt: {label} ===", flush=True)
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str), flush=True)
        print("=== End AI image prompt ===\n", flush=True)
    return payload
