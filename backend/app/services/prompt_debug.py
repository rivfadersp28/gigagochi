from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


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
    print(f"\n=== OpenAI chat prompt: {label} ===", flush=True)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str), flush=True)
    print("=== End OpenAI chat prompt ===\n", flush=True)
    return payload
