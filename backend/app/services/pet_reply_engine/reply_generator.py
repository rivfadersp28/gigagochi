from __future__ import annotations

import json
import logging
from typing import Any

from app.config import get_settings
from app.services.openai_service import chat_reasoning_effort_kwargs, get_openai_client
from app.services.pet_memory.models import PetReplyModelOutputV2
from app.services.pet_memory.normalizer import normalize_text
from app.services.pet_reply_engine.fallbacks import fallback_reply
from app.services.pet_reply_engine.models import PetPromptContext, PetReplyInput, PetReplyResult
from app.services.pet_reply_engine.prompt_builder import (
    build_pet_prompt_context,
    build_pet_reply_messages,
)
from app.services.pet_reply_engine.reply_validator import validate_reply

logger = logging.getLogger(__name__)

PET_REPLY_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "reply",
        "moodHint",
        "proactiveIntent",
        "memoryCandidates",
        "relationshipPatch",
        "developmentPatch",
        "threadPatch",
        "goalPatch",
    ],
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
        "proactiveIntent": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "text", "priority"],
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "ask_user",
                                "continue_lore",
                                "return_to_thread",
                                "request_care",
                                "share_observation",
                                "none",
                            ],
                        },
                        "text": {"type": ["string", "null"]},
                        "priority": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                },
                {"type": "null"},
            ]
        },
        "memoryCandidates": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "text", "importance", "confidence", "sourceSpan"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "world_fact",
                            "home_fact",
                            "friend_fact",
                            "family_fact",
                            "origin_fact",
                            "preference_fact",
                            "fear_fact",
                            "habit_fact",
                            "voice_fact",
                            "milestone",
                            "user_fact",
                            "relationship_event",
                            "pet_canon_fact",
                            "pet_emotional_fact",
                            "open_thread",
                            "preference",
                            "boundary",
                        ],
                    },
                    "text": {"type": "string"},
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "sourceSpan": {"type": ["string", "null"]},
                },
            },
            "description": (
                "0-3 compact memory proposals. The backend resolver decides what is saved."
            ),
        },
        "relationshipPatch": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "userName",
                        "preferredAddress",
                        "trustDelta",
                        "attachmentDelta",
                        "familiarityDelta",
                        "sharedEvent",
                        "userFact",
                    ],
                    "properties": {
                        "userName": {"type": ["string", "null"]},
                        "preferredAddress": {"type": ["string", "null"]},
                        "trustDelta": {"type": ["integer", "null"], "minimum": -5, "maximum": 5},
                        "attachmentDelta": {
                            "type": ["integer", "null"],
                            "minimum": -5,
                            "maximum": 5,
                        },
                        "familiarityDelta": {
                            "type": ["integer", "null"],
                            "minimum": -5,
                            "maximum": 5,
                        },
                        "sharedEvent": {"type": ["string", "null"]},
                        "userFact": {"type": ["string", "null"]},
                    },
                },
                {"type": "null"},
            ]
        },
        "developmentPatch": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "trustDelta",
                        "attachmentDelta",
                        "curiosityDelta",
                        "confidenceDelta",
                        "lonelinessDelta",
                        "playfulnessDelta",
                        "reason",
                    ],
                    "properties": {
                        "trustDelta": {"type": ["integer", "null"], "minimum": -5, "maximum": 5},
                        "attachmentDelta": {
                            "type": ["integer", "null"],
                            "minimum": -5,
                            "maximum": 5,
                        },
                        "curiosityDelta": {
                            "type": ["integer", "null"],
                            "minimum": -5,
                            "maximum": 5,
                        },
                        "confidenceDelta": {
                            "type": ["integer", "null"],
                            "minimum": -5,
                            "maximum": 5,
                        },
                        "lonelinessDelta": {
                            "type": ["integer", "null"],
                            "minimum": -5,
                            "maximum": 5,
                        },
                        "playfulnessDelta": {
                            "type": ["integer", "null"],
                            "minimum": -5,
                            "maximum": 5,
                        },
                        "reason": {"type": ["string", "null"]},
                    },
                },
                {"type": "null"},
            ]
        },
        "threadPatch": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["open", "update"],
                    "properties": {
                        "open": {
                            "anyOf": [
                                {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "topic",
                                        "summary",
                                        "suggestedFollowUp",
                                        "priority",
                                    ],
                                    "properties": {
                                        "topic": {"type": "string"},
                                        "summary": {"type": "string"},
                                        "suggestedFollowUp": {"type": ["string", "null"]},
                                        "priority": {
                                            "type": "number",
                                            "minimum": 0,
                                            "maximum": 1,
                                        },
                                    },
                                },
                                {"type": "null"},
                            ]
                        },
                        "update": {
                            "anyOf": [
                                {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "threadId",
                                        "summary",
                                        "suggestedFollowUp",
                                        "status",
                                    ],
                                    "properties": {
                                        "threadId": {"type": "string"},
                                        "summary": {"type": ["string", "null"]},
                                        "suggestedFollowUp": {"type": ["string", "null"]},
                                        "status": {
                                            "type": ["string", "null"],
                                            "enum": ["open", "paused", "resolved", None],
                                        },
                                    },
                                },
                                {"type": "null"},
                            ]
                        },
                    },
                },
                {"type": "null"},
            ]
        },
        "goalPatch": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["open", "update"],
                    "properties": {
                        "open": {
                            "anyOf": [
                                {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "kind",
                                        "text",
                                        "priority",
                                        "expiresAt",
                                        "relatedThreadId",
                                    ],
                                    "properties": {
                                        "kind": {
                                            "type": "string",
                                            "enum": [
                                                "learn_about_user",
                                                "share_lore",
                                                "seek_care",
                                                "return_to_thread",
                                                "play",
                                                "comfort_user",
                                            ],
                                        },
                                        "text": {"type": "string"},
                                        "priority": {
                                            "type": "number",
                                            "minimum": 0,
                                            "maximum": 1,
                                        },
                                        "expiresAt": {"type": ["string", "null"]},
                                        "relatedThreadId": {"type": ["string", "null"]},
                                    },
                                },
                                {"type": "null"},
                            ]
                        },
                        "update": {
                            "anyOf": [
                                {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["goalId", "status", "priority"],
                                    "properties": {
                                        "goalId": {"type": "string"},
                                        "status": {
                                            "type": ["string", "null"],
                                            "enum": [
                                                "active",
                                                "paused",
                                                "completed",
                                                "expired",
                                                None,
                                            ],
                                        },
                                        "priority": {
                                            "type": ["number", "null"],
                                            "minimum": 0,
                                            "maximum": 1,
                                        },
                                    },
                                },
                                {"type": "null"},
                            ]
                        },
                    },
                },
                {"type": "null"},
            ]
        },
    },
}


def _fallback_result(
    reply_input: PetReplyInput,
    flags: tuple[str, ...],
    prompt_context: PetPromptContext | None = None,
) -> PetReplyResult:
    prompt_context = prompt_context or build_pet_prompt_context(reply_input)
    fallback_text = fallback_reply(reply_input)
    return PetReplyResult(
        reply=fallback_text,
        mood_hint=reply_input.pet.mood if reply_input.prompt_layers.mood_style else None,
        used_fallback=True,
        validation_flags=flags,
        lore_memories_to_save=(),
        detected_intent=prompt_context.detected_intent,
        reference_card_ids=tuple(card.id for card in prompt_context.reference_cards),
        included_layers=prompt_context.included_layers,
        excluded_layers=prompt_context.excluded_layers,
    )


def _legacy_lore_memories(payload: dict[str, Any]) -> tuple[str, ...]:
    raw_memories = payload.get("loreMemoriesToSave", [])
    if not isinstance(raw_memories, list):
        return ()
    normalized_memories: list[str] = []
    for item in raw_memories:
        if not isinstance(item, str):
            continue
        text = normalize_text(item)
        if not text:
            continue
        if not text.startswith(("ЛОР:", "LORE:")):
            text = f"ЛОР: {text}"
        normalized_memories.append(text[:500])
    return tuple(normalized_memories)


def parse_pet_reply_payload(raw_json: str) -> tuple[PetReplyModelOutputV2, tuple[str, ...]]:
    payload = json.loads(raw_json)
    if not isinstance(payload, dict):
        raise ValueError("Pet reply payload must be an object")
    lore_memories = _legacy_lore_memories(payload)
    normalized_payload = {
        "reply": str(payload.get("reply", "")).strip(),
        "moodHint": payload.get("moodHint"),
        "proactiveIntent": payload.get("proactiveIntent"),
        "memoryCandidates": payload.get("memoryCandidates", []),
        "relationshipPatch": payload.get("relationshipPatch"),
        "developmentPatch": payload.get("developmentPatch"),
        "threadPatch": payload.get("threadPatch"),
        "goalPatch": payload.get("goalPatch"),
    }
    if normalized_payload["moodHint"] not in ("idle", "happy", "hungry", "sad", None):
        normalized_payload["moodHint"] = None
    return PetReplyModelOutputV2.model_validate(normalized_payload), lore_memories


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
    prompt_context = build_pet_prompt_context(reply_input)

    try:
        openai_client = client or get_openai_client()
        completion = openai_client.chat.completions.create(
            model=model,
            messages=build_pet_reply_messages(reply_input, prompt_context=prompt_context),
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
        model_output, lore_memories = parse_pet_reply_payload(
            completion.choices[0].message.content or "{}"
        )
    except Exception as exc:
        logger.warning(
            "Pet reply generation used fallback after %s: %s",
            exc.__class__.__name__,
            exc,
        )
        return _fallback_result(
            reply_input,
            (f"generation_error:{exc.__class__.__name__}",),
            prompt_context,
        )

    validation = validate_reply(
        model_output.reply,
        reply_input.pet.age_stage if reply_input.prompt_layers.age_style else "teen",
        reply_input.pet.name,
        reply_input.pet.mood
        if reply_input.prompt_layers.mood_style or reply_input.prompt_layers.stat_needs
        else None,
        reply_input.user_text,
    )
    if not validation.is_valid:
        logger.info("Pet reply validation fallback flags=%s", validation.flags)
        return _fallback_result(reply_input, validation.flags, prompt_context)

    return PetReplyResult(
        reply=validation.normalized_reply,
        mood_hint=model_output.moodHint if reply_input.prompt_layers.mood_style else None,
        used_fallback=False,
        validation_flags=validation.flags,
        lore_memories_to_save=lore_memories,
        proactive_intent=(
            model_output.proactiveIntent if reply_input.prompt_layers.proactivity else None
        ),
        memory_candidates=(
            tuple(model_output.memoryCandidates) if reply_input.prompt_layers.memory else ()
        ),
        relationship_patch=(
            model_output.relationshipPatch if reply_input.prompt_layers.memory else None
        ),
        development_patch=(
            model_output.developmentPatch if reply_input.prompt_layers.memory else None
        ),
        thread_patch=model_output.threadPatch if reply_input.prompt_layers.memory else None,
        goal_patch=model_output.goalPatch if reply_input.prompt_layers.memory else None,
        detected_intent=prompt_context.detected_intent,
        reference_card_ids=tuple(card.id for card in prompt_context.reference_cards),
        included_layers=prompt_context.included_layers,
        excluded_layers=prompt_context.excluded_layers,
    )
