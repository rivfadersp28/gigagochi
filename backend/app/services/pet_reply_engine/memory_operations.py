from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.llm.compat import complete_chat, response_log_value
from app.llm.runtime import resolve_llm_model
from app.schemas import (
    LocalChatDebug,
    MemoryConsolidationRequest,
    MemoryConsolidationResponse,
    MemoryExtractionRequest,
    MemoryExtractionResponse,
)
from app.services.openai_service import (
    chat_reasoning_effort_kwargs,
    get_chat_model,
)
from app.services.pet_reply_engine.speech_runtime import (
    speech_template,
    user_memory_consolidation_system_prompt,
    user_memory_extraction_system_prompt,
)
from app.services.prompt_debug import (
    log_chat_completion_prompt,
    log_chat_completion_response,
)

USER_MEMORY_KINDS = (
    "user_fact",
    "preference",
    "event",
    "deadline",
    "relationship",
    "routine",
    "goal",
    "promise",
    "emotion",
    "boundary",
)

MEMORY_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "operations": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "capture_learning",
                            "remember_user_fact",
                            "replace_user_fact",
                            "forget_user_fact",
                        ],
                    },
                    "observation": {"type": ["string", "null"], "maxLength": 500},
                    "patternKey": {"type": ["string", "null"], "maxLength": 120},
                    "kind": {
                        "type": ["string", "null"],
                        "enum": [*USER_MEMORY_KINDS, None],
                    },
                    "text": {"type": ["string", "null"], "maxLength": 500},
                    "normalizedKey": {"type": ["string", "null"], "maxLength": 160},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                    "occurredAt": {"type": ["string", "null"], "maxLength": 80},
                    "dueAt": {"type": ["string", "null"], "maxLength": 80},
                    "expiresAt": {"type": ["string", "null"], "maxLength": 80},
                    "tags": {
                        "type": "array",
                        "maxItems": 6,
                        "items": {"type": "string", "maxLength": 40},
                    },
                },
                "required": [
                    "type",
                    "observation",
                    "patternKey",
                    "kind",
                    "text",
                    "normalizedKey",
                    "confidence",
                    "importance",
                    "occurredAt",
                    "dueAt",
                    "expiresAt",
                    "tags",
                ],
            },
        }
    },
    "required": ["operations"],
}

MEMORY_CONSOLIDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "operations": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "promote_learning",
                            "prune_learning",
                            "rewrite_summary",
                            "rewrite_user_profile",
                        ],
                    },
                    "learningId": {"type": ["string", "null"], "maxLength": 120},
                    "reason": {"type": ["string", "null"], "maxLength": 240},
                    "content": {"type": ["string", "null"], "maxLength": 1000},
                    "memory": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "properties": {
                            "kind": {"type": "string", "enum": list(USER_MEMORY_KINDS)},
                            "text": {"type": "string", "maxLength": 500},
                            "normalizedKey": {"type": "string", "maxLength": 160},
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "importance": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "occurredAt": {"type": ["string", "null"], "maxLength": 80},
                            "dueAt": {"type": ["string", "null"], "maxLength": 80},
                            "expiresAt": {"type": ["string", "null"], "maxLength": 80},
                            "tags": {
                                "type": "array",
                                "maxItems": 6,
                                "items": {"type": "string", "maxLength": 40},
                            },
                        },
                        "required": [
                            "kind",
                            "text",
                            "normalizedKey",
                            "confidence",
                            "importance",
                            "occurredAt",
                            "dueAt",
                            "expiresAt",
                            "tags",
                        ],
                    },
                },
                "required": ["type", "learningId", "reason", "content", "memory"],
            },
        }
    },
    "required": ["operations"],
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _safe_json_context(value: Any, limit: int = 6000) -> str:
    return _truncate_text(json.dumps(value, ensure_ascii=False, default=str), limit)


def _clamp_float(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _memory_key_from_text(text: str) -> str:
    words = re.findall(r"[\wа-яё]+", text.casefold(), flags=re.IGNORECASE)
    return "-".join(words[:12])[:160] or "memory"


def _optional_iso_text(value: Any) -> str | None:
    text = _compact_spaces(str(value or ""))
    return text[:80] if text else None


def _normalized_memory_operation(value: Any) -> dict[str, Any] | None:
    if not _is_record(value):
        return None
    operation_type = str(value.get("type") or "").strip()
    kind = str(value.get("kind") or "user_fact").strip()
    if kind not in USER_MEMORY_KINDS:
        kind = "user_fact"

    if operation_type == "capture_learning":
        observation = _compact_spaces(str(value.get("observation") or ""))
        if not observation:
            return None
        operation: dict[str, Any] = {
            "type": "capture_learning",
            "observation": _truncate_text(observation, 500),
            "confidence": _clamp_float(value.get("confidence"), 0.6),
            "importance": _clamp_float(value.get("importance"), 0.5),
        }
        pattern_key = _compact_spaces(str(value.get("patternKey") or ""))
        if pattern_key:
            operation["patternKey"] = _truncate_text(pattern_key, 120)
        operation["kind"] = kind
        occurred_at = _optional_iso_text(value.get("occurredAt"))
        if occurred_at:
            operation["occurredAt"] = occurred_at
        due_at = _optional_iso_text(value.get("dueAt"))
        if due_at:
            operation["dueAt"] = due_at
        return operation

    if operation_type in {"remember_user_fact", "replace_user_fact"}:
        text = _compact_spaces(str(value.get("text") or ""))
        if not text:
            return None
        normalized_key = _compact_spaces(str(value.get("normalizedKey") or ""))
        tags = value.get("tags") if isinstance(value.get("tags"), list) else []
        operation = {
            "type": operation_type,
            "kind": kind,
            "text": _truncate_text(text, 500),
            "normalizedKey": _truncate_text(normalized_key or _memory_key_from_text(text), 160),
            "confidence": _clamp_float(value.get("confidence"), 0.75),
            "importance": _clamp_float(value.get("importance"), 0.7),
            "tags": [
                _truncate_text(_compact_spaces(str(tag)), 40)
                for tag in tags[:6]
                if _compact_spaces(str(tag))
            ],
        }
        due_at = _optional_iso_text(value.get("dueAt"))
        occurred_at = _optional_iso_text(value.get("occurredAt"))
        expires_at = _optional_iso_text(value.get("expiresAt"))
        if occurred_at:
            operation["occurredAt"] = occurred_at
        if due_at:
            operation["dueAt"] = due_at
        if expires_at:
            operation["expiresAt"] = expires_at
        return operation

    if operation_type == "forget_user_fact":
        normalized_key = _compact_spaces(str(value.get("normalizedKey") or ""))
        match_text = _compact_spaces(str(value.get("text") or ""))
        if not normalized_key and not match_text:
            return None
        return {
            "type": "forget_user_fact",
            **({"normalizedKey": _truncate_text(normalized_key, 160)} if normalized_key else {}),
            **({"matchText": _truncate_text(match_text, 500)} if match_text else {}),
        }

    return None


def _parse_memory_extraction_payload(raw_content: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(raw_content or "{}")
    except json.JSONDecodeError:
        return []
    if not _is_record(parsed) or not isinstance(parsed.get("operations"), list):
        return []
    return [
        operation
        for raw_operation in parsed["operations"]
        if (operation := _normalized_memory_operation(raw_operation))
    ]


def build_memory_extraction_messages(payload: MemoryExtractionRequest) -> list[dict[str, str]]:
    memory_context = payload.memoryContext.model_dump(mode="json") if payload.memoryContext else {}
    history_context = [item.model_dump(mode="json") for item in payload.history[-8:]]
    return [
        {"role": "system", "content": user_memory_extraction_system_prompt()},
        {
            "role": "user",
            "content": speech_template(
                "userMemoryExtractionUserMessage",
                {
                    "now_iso": payload.nowIso or _now_iso(),
                    "timezone": payload.timezone or "Europe/Moscow",
                    "existing_memory": payload.existingMemoryBrief or speech_template("emptyValue"),
                    "memory_context": _safe_json_context(memory_context, 3000),
                    "history_context": _safe_json_context(history_context, 3000),
                    "message": payload.message,
                    "reply": payload.reply,
                },
            ),
        },
    ]


def extract_user_memory_operations(
    payload: MemoryExtractionRequest,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> MemoryExtractionResponse:
    settings = get_settings()
    if model is None:
        fallback_model = get_chat_model(settings)
        model = (
            fallback_model
            if client is not None
            else resolve_llm_model("memory_extraction", fallback_model)
        )
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_memory_extraction_messages(payload),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "user_memory_extraction",
                "schema": MEMORY_EXTRACTION_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    prompt_debug = [log_chat_completion_prompt("pet_reply/memory_extraction", request_kwargs)]
    completion = complete_chat("memory_extraction", request_kwargs, client=client)
    log_chat_completion_response("pet_reply/memory_extraction", response_log_value(completion))
    operations = _parse_memory_extraction_payload(completion.content or "{}")
    debug = None
    if payload.includeDebug:
        debug = LocalChatDebug(
            usedFallback=False,
            validationFlags=[],
            promptDebug=prompt_debug,
            memoryDebug={"extractionOperations": operations},
        )
    return MemoryExtractionResponse(operations=operations, debug=debug)


def _normalized_consolidation_operation(value: Any) -> dict[str, Any] | None:
    if not _is_record(value):
        return None
    operation_type = str(value.get("type") or "").strip()
    learning_id = _compact_spaces(str(value.get("learningId") or ""))

    if operation_type == "promote_learning":
        memory = _normalized_memory_operation(
            {
                "type": "remember_user_fact",
                **(value.get("memory") if _is_record(value.get("memory")) else {}),
            }
        )
        if not learning_id or not memory:
            return None
        return {
            "type": "promote_learning",
            "learningId": _truncate_text(learning_id, 120),
            "memory": {key: val for key, val in memory.items() if key != "type"},
        }

    if operation_type == "prune_learning":
        if not learning_id:
            return None
        reason = _compact_spaces(str(value.get("reason") or ""))
        return {
            "type": "prune_learning",
            "learningId": _truncate_text(learning_id, 120),
            **({"reason": _truncate_text(reason, 240)} if reason else {}),
        }

    if operation_type in {"rewrite_summary", "rewrite_user_profile"}:
        content = _compact_spaces(str(value.get("content") or ""))
        if not content:
            return None
        return {"type": operation_type, "content": _truncate_text(content, 1000)}

    return None


def _parse_consolidation_payload(raw_content: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(raw_content or "{}")
    except json.JSONDecodeError:
        return []
    if not _is_record(parsed) or not isinstance(parsed.get("operations"), list):
        return []
    return [
        operation
        for raw_operation in parsed["operations"]
        if (operation := _normalized_consolidation_operation(raw_operation))
    ]


def build_memory_consolidation_messages(
    payload: MemoryConsolidationRequest,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": user_memory_consolidation_system_prompt()},
        {
            "role": "user",
            "content": speech_template(
                "userMemoryConsolidationUserMessage",
                {
                    "now_iso": payload.nowIso or _now_iso(),
                    "timezone": payload.timezone or "Europe/Moscow",
                    "pending_learnings": _safe_json_context(payload.pendingLearnings, 9000),
                    "existing_memories": _safe_json_context(payload.existingMemories, 9000),
                    "summary": payload.summary or speech_template("emptyValue"),
                    "user_profile": payload.userProfile or speech_template("emptyValue"),
                },
            ),
        },
    ]


def consolidate_user_memory(
    payload: MemoryConsolidationRequest,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> MemoryConsolidationResponse:
    settings = get_settings()
    if model is None:
        fallback_model = get_chat_model(settings)
        model = (
            fallback_model
            if client is not None
            else resolve_llm_model("memory_consolidation", fallback_model)
        )
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_memory_consolidation_messages(payload),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "user_memory_consolidation",
                "schema": MEMORY_CONSOLIDATION_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    prompt_debug = [log_chat_completion_prompt("pet_reply/memory_consolidation", request_kwargs)]
    completion = complete_chat("memory_consolidation", request_kwargs, client=client)
    log_chat_completion_response("pet_reply/memory_consolidation", response_log_value(completion))
    operations = _parse_consolidation_payload(completion.content or "{}")
    debug = None
    if payload.includeDebug:
        debug = LocalChatDebug(
            usedFallback=False,
            validationFlags=[],
            promptDebug=prompt_debug,
            memoryDebug={"consolidationOperations": operations},
        )
    return MemoryConsolidationResponse(operations=operations, debug=debug)
