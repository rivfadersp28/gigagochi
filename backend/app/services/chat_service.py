from __future__ import annotations

import json
import uuid
from typing import cast

from fastapi import status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import public_error
from app.models import Message, Pet
from app.prompts.chat_prompts import (
    CHAT_RESPONSE_SCHEMA,
    build_chat_messages,
)
from app.schemas import ChatResponse, LocalChatRequest, LocalChatResponse
from app.services.game_service import select_visual_state, tick_pet
from app.services.memory_service import list_relevant_memories, save_memories
from app.services.openai_service import (
    MissingOpenAIAPIKey,
    chat_reasoning_effort_kwargs,
    get_openai_client,
)
from app.services.pet_memory import (
    build_memory_context,
    handle_memory_control_message,
    is_no_memory_write_message,
    normalize_memory,
    resolve_memory_update,
)
from app.services.pet_memory.models import PetMemoryPatch
from app.services.pet_memory.normalizer import normalized_key
from app.services.pet_reply_engine import (
    PetRecentMessage,
    PetReplyInput,
    PetReplyPet,
    PetStats,
    build_default_personality,
    build_visual_identity,
    generate_pet_reply,
)
from app.services.pet_reply_engine.fallbacks import fallback_reply
from app.services.pet_reply_engine.lore import extract_lore
from app.services.pet_reply_engine.models import PetAgeStage, PetMood
from app.services.pet_reply_engine.proactivity_gate import apply_proactivity_gate
from app.services.pet_reply_engine.reply_validator import validate_reply
from app.services.pet_service import get_pet_or_404, image_url_for

PET_AGE_STAGES = ("baby", "teen", "adult")
PET_MOODS = ("idle", "happy", "hungry", "sad")


def parse_chat_payload(raw_json: str) -> tuple[str, list[dict]]:
    payload = json.loads(raw_json)
    reply = str(payload.get("reply", "")).strip()
    memories = payload.get("memories_to_save", [])
    if not reply:
        raise ValueError("Missing reply")
    if not isinstance(memories, list):
        memories = []
    return reply, memories


def build_pet_reply_input(
    payload: LocalChatRequest,
    *,
    memory_context: object | None = None,
) -> PetReplyInput:
    pet = payload.pet
    character_bible = pet.characterBible
    visual_identity = build_visual_identity(pet.description, character_bible)
    personality = build_default_personality(pet.description, character_bible)
    lore_memories = tuple(item.strip()[:500] for item in pet.loreMemories if item.strip())

    return PetReplyInput(
        user_action="chat_message",
        user_text=payload.message,
        pet=PetReplyPet(
            name=pet.name,
            age_stage=pet.stage,
            mood=pet.mood,
            stats=PetStats(
                hunger=pet.stats.hunger,
                happiness=pet.stats.happiness,
                energy=pet.stats.energy,
                cleanliness=pet.stats.cleanliness,
            ),
            visual_identity=visual_identity,
            personality=personality,
            lore=extract_lore(character_bible),
        ),
        recent_messages=tuple(
            PetRecentMessage(role=item.role, text=item.text) for item in payload.history[-12:]
        ),
        lore_memories=lore_memories[-12:],
        memory_context=memory_context,
    )


def _is_empty_memory_patch(patch: PetMemoryPatch | None) -> bool:
    if patch is None:
        return True
    return not any(
        (
            patch.canonUpserts,
            patch.canonDeletes,
            patch.relationshipPatch,
            patch.threadUpserts,
            patch.threadDeletes,
            patch.reflectionUpserts,
            patch.reflectionDeletes,
            patch.activeGoalUpserts,
            patch.activeGoalDeletes,
            patch.developmentPatch,
            patch.eventAppends,
            patch.rejectedCandidateAppends,
        )
    )


def _legacy_lore_response(
    result_lore_memories: tuple[str, ...],
    patch: PetMemoryPatch | None,
    model_candidate_keys: set[str],
) -> list[str]:
    values = list(result_lore_memories[:10])
    seen = {normalized_key(item) for item in values}
    if patch:
        for fact in patch.canonUpserts:
            key = normalized_key(fact.text)
            if key not in model_candidate_keys or key in seen:
                continue
            seen.add(key)
            values.append(f"ЛОР: {fact.text}")
            if len(values) >= 10:
                break
    return values


def chat_with_local_pet(payload: LocalChatRequest) -> LocalChatResponse:
    pet = payload.pet
    memory = normalize_memory(pet.memory, lore_memories=pet.loreMemories)
    control = handle_memory_control_message(payload.message, memory)
    if control:
        patch = None if _is_empty_memory_patch(control.patch) else control.patch
        return LocalChatResponse(
            reply=control.reply,
            moodHint=pet.mood,
            loreMemoriesToSave=[],
            memoryPatch=patch,
        )

    memory_context = build_memory_context(memory, payload.message)
    reply_input = build_pet_reply_input(payload, memory_context=memory_context)
    result = generate_pet_reply(reply_input)
    if result.used_fallback:
        return LocalChatResponse(
            reply=result.reply,
            moodHint=result.mood_hint,
            loreMemoriesToSave=[],
        )

    proactivity = apply_proactivity_gate(
        reply=result.reply,
        proactive_intent=result.proactive_intent,
        recent_messages=reply_input.recent_messages,
        memory=memory,
        user_text=payload.message,
        age_stage=reply_input.pet.age_stage,
        mood=reply_input.pet.mood,
        stats=reply_input.pet.stats,
    )
    no_memory_write = is_no_memory_write_message(payload.message)
    memory_patch = resolve_memory_update(
        memory,
        character_bible=pet.characterBible,
        memory_context=memory_context,
        user_text=payload.message,
        pet_reply=proactivity.reply,
        memory_candidates=list(result.memory_candidates),
        legacy_lore_memories=result.lore_memories_to_save,
        relationship_patch=result.relationship_patch,
        development_patch=result.development_patch,
        thread_patch=result.thread_patch,
        goal_patch=result.goal_patch,
        no_memory_write=no_memory_write,
        proactivity_allowed=proactivity.allowed,
    )
    model_candidate_keys = {
        normalized_key(candidate.text)
        for candidate in result.memory_candidates
        if getattr(candidate, "type", "") not in ("user_fact", "relationship_event")
    }
    lore_memories = _legacy_lore_response(
        result.lore_memories_to_save,
        memory_patch,
        model_candidate_keys,
    )
    return LocalChatResponse(
        reply=proactivity.reply,
        moodHint=result.mood_hint,
        loreMemoriesToSave=lore_memories,
        memoryPatch=None if _is_empty_memory_patch(memory_patch) else memory_patch,
    )


def _pet_age_stage(value: str | None) -> PetAgeStage:
    if value in PET_AGE_STAGES:
        return cast(PetAgeStage, value)
    return "teen"


def _pet_mood(value: str | None) -> PetMood:
    if value in PET_MOODS:
        return cast(PetMood, value)
    return "idle"


def build_persisted_pet_reply_input(
    pet: Pet,
    message_text: str,
    history: list[Message],
    selected_stage: str | None = None,
    selected_state: str | None = None,
) -> PetReplyInput:
    character_bible = pet.character_profile_json
    visual_identity = build_visual_identity(pet.original_description, character_bible)
    personality = build_default_personality(pet.original_description, character_bible)
    mood = _pet_mood(selected_state or select_visual_state(pet.hunger, pet.mood))

    return PetReplyInput(
        user_action="chat_message",
        user_text=message_text,
        pet=PetReplyPet(
            name=None,
            age_stage=_pet_age_stage(selected_stage or pet.current_stage),
            mood=mood,
            stats=PetStats(
                hunger=pet.hunger,
                happiness=pet.mood,
                energy=60,
                cleanliness=90,
            ),
            visual_identity=visual_identity,
            personality=personality,
            lore=extract_lore(character_bible),
        ),
        recent_messages=tuple(
            PetRecentMessage(
                role="pet" if item.role == "assistant" else "user",
                text=item.content,
            )
            for item in history[-12:]
            if item.role in ("assistant", "user")
        ),
    )


def validate_or_fallback_persisted_reply(
    reply: str,
    pet: Pet,
    message_text: str,
    history: list[Message],
    selected_stage: str | None = None,
    selected_state: str | None = None,
) -> tuple[str, bool]:
    reply_input = build_persisted_pet_reply_input(
        pet,
        message_text,
        history,
        selected_stage=selected_stage,
        selected_state=selected_state,
    )
    validation = validate_reply(
        reply,
        reply_input.pet.age_stage,
        reply_input.pet.name,
        reply_input.pet.mood,
        message_text,
    )
    if validation.is_valid:
        return validation.normalized_reply, False
    return fallback_reply(reply_input), True


def list_messages(db: Session, pet_id: uuid.UUID) -> list[Message]:
    get_pet_or_404(db, pet_id)
    return list(
        db.scalars(
            select(Message).where(Message.pet_id == pet_id).order_by(Message.created_at.asc())
        )
    )


def recent_messages(db: Session, pet_id: uuid.UUID, limit: int = 12) -> list[Message]:
    rows = list(
        db.scalars(
            select(Message)
            .where(Message.pet_id == pet_id)
            .order_by(desc(Message.created_at))
            .limit(limit)
        )
    )
    return list(reversed(rows))


def chat_with_pet(
    db: Session,
    pet_id: uuid.UUID,
    content: str,
    selected_stage: str | None = None,
    selected_state: str | None = None,
) -> ChatResponse:
    message_text = content.strip()
    if not message_text:
        raise public_error("EMPTY_PROMPT")

    pet = get_pet_or_404(db, pet_id, include_images=True)
    if pet.status != "ready":
        raise public_error("PET_NOT_READY", status.HTTP_409_CONFLICT)

    tick_pet(pet)
    user_message = Message(pet_id=pet.id, role="user", content=message_text)
    db.add(pet)
    db.add(user_message)
    db.commit()
    db.refresh(user_message)
    db.refresh(pet, attribute_names=["images"])

    memories = list_relevant_memories(db, pet.id)
    history = recent_messages(db, pet.id)

    try:
        settings = get_settings()
        client = get_openai_client()
        completion = client.chat.completions.create(
            model=settings.openai_chat_model,
            messages=build_chat_messages(
                pet,
                history,
                memories,
                selected_stage=selected_stage,
                selected_state=selected_state,
            ),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "pet_chat_response",
                    "schema": CHAT_RESPONSE_SCHEMA,
                    "strict": True,
                },
            },
            timeout=settings.openai_chat_timeout_seconds,
            **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
        )
        reply, memories_to_save = parse_chat_payload(completion.choices[0].message.content or "{}")
    except MissingOpenAIAPIKey:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None
    except Exception:
        raise public_error("CHAT_FAILED", status.HTTP_502_BAD_GATEWAY) from None

    reply, used_fallback = validate_or_fallback_persisted_reply(
        reply,
        pet,
        message_text,
        history,
        selected_stage=selected_stage,
        selected_state=selected_state,
    )
    if used_fallback:
        memories_to_save = []

    assistant_message = Message(pet_id=pet.id, role="assistant", content=reply)
    db.add(assistant_message)
    save_memories(db, pet.id, memories_to_save, source_message_id=user_message.id)
    pet.mood = min(100, pet.mood + 10)
    db.add(pet)
    db.commit()
    db.refresh(pet, attribute_names=["images"])

    current_state = select_visual_state(pet.hunger, pet.mood)
    return ChatResponse(
        reply=reply,
        mood=pet.mood,
        hunger=pet.hunger,
        current_stage=pet.current_stage,
        current_state=current_state,
        image_url=image_url_for(pet, pet.current_stage, current_state),
    )
