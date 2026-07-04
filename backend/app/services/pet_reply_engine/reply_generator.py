from __future__ import annotations

import json
import logging
from typing import Any

from app.config import get_settings
from app.services.openai_service import chat_reasoning_effort_kwargs, get_openai_client
from app.services.pet_reply_engine.fallbacks import fallback_reply
from app.services.pet_reply_engine.models import PetMood, PetReplyInput, PetReplyResult
from app.services.pet_reply_engine.prompt_builder import build_pet_reply_messages
from app.services.pet_reply_engine.reply_validator import validate_reply

logger = logging.getLogger(__name__)

PET_REPLY_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply", "moodHint", "loreMemoriesToSave"],
    "properties": {
        "reply": {
            "type": "string",
            "description": "One pet reply, without markdown or assistant-like phrasing.",
        },
        "moodHint": {
            "type": ["string", "null"],
            "enum": ["idle", "happy", "hungry", "sad", None],
            "description": "Optional visual mood hint for the frontend.",
        },
        "loreMemoriesToSave": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "New pet-world canon facts invented in this reply and worth remembering. "
                "Use Russian strings starting with 'ЛОР: '. Return [] if nothing new was "
                "established."
            ),
        },
    },
}


def _fallback_result(
    reply_input: PetReplyInput,
    flags: tuple[str, ...],
) -> PetReplyResult:
    return PetReplyResult(
        reply=fallback_reply(reply_input),
        mood_hint=reply_input.pet.mood,
        used_fallback=True,
        validation_flags=flags,
        lore_memories_to_save=(),
    )


def parse_pet_reply_payload(raw_json: str) -> tuple[str, PetMood | None, tuple[str, ...]]:
    payload = json.loads(raw_json)
    reply = str(payload.get("reply", "")).strip()
    mood_hint = payload.get("moodHint")
    if mood_hint not in ("idle", "happy", "hungry", "sad", None):
        mood_hint = None
    raw_memories = payload.get("loreMemoriesToSave", [])
    if not isinstance(raw_memories, list):
        raw_memories = []
    normalized_memories: list[str] = []
    for item in raw_memories:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text:
            continue
        if not text.startswith(("ЛОР:", "LORE:")):
            text = f"ЛОР: {text}"
        normalized_memories.append(text[:500])
    lore_memories = tuple(normalized_memories)
    return reply, mood_hint, lore_memories


def generate_pet_reply(
    reply_input: PetReplyInput,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> PetReplyResult:
    settings = get_settings()
    model = model or settings.openai_chat_model
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds

    try:
        openai_client = client or get_openai_client()
        completion = openai_client.chat.completions.create(
            model=model,
            messages=build_pet_reply_messages(reply_input),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "pet_reply_response",
                    "schema": PET_REPLY_RESPONSE_SCHEMA,
                    "strict": True,
                },
            },
            timeout=timeout,
            **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
        )
        reply, mood_hint, lore_memories = parse_pet_reply_payload(
            completion.choices[0].message.content or "{}"
        )
    except Exception as exc:
        logger.warning(
            "Pet reply generation used fallback after %s: %s",
            exc.__class__.__name__,
            exc,
        )
        return _fallback_result(reply_input, (f"generation_error:{exc.__class__.__name__}",))

    validation = validate_reply(
        reply,
        reply_input.pet.age_stage,
        reply_input.pet.name,
        reply_input.pet.mood,
        reply_input.user_text,
    )
    if not validation.is_valid:
        logger.info("Pet reply validation fallback flags=%s", validation.flags)
        return _fallback_result(reply_input, validation.flags)

    return PetReplyResult(
        reply=validation.normalized_reply,
        mood_hint=mood_hint,
        used_fallback=False,
        validation_flags=validation.flags,
        lore_memories_to_save=lore_memories,
    )
