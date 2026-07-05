from __future__ import annotations

import json
import uuid
from typing import cast

from fastapi import status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.errors import public_error
from app.models import Message, Pet
from app.schemas import ChatResponse, LocalChatRequest, LocalChatResponse, PromptLayers
from app.services.character_cards import normalize_character_profile_v2
from app.services.game_service import select_visual_state, tick_pet
from app.services.memory_service import list_relevant_memories, save_memories
from app.services.pet_memory import (
    build_memory_context,
    handle_memory_control_message,
    is_no_memory_write_message,
    normalize_memory,
    resolve_memory_update,
)
from app.services.pet_memory.models import LocalChatDebug, PetMemoryPatch
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
from app.services.pet_reply_engine.models import PetAgeStage, PetMood, PetPromptLayers
from app.services.pet_reply_engine.proactivity_gate import apply_proactivity_gate
from app.services.pet_reply_engine.reply_validator import validate_reply
from app.services.pet_service import get_pet_or_404, image_url_for

PET_AGE_STAGES = ("baby", "teen", "adult")
PET_MOODS = ("idle", "happy", "hungry", "sad")


def _pet_prompt_layers(value: PromptLayers | None) -> PetPromptLayers:
    if value is None:
        return PetPromptLayers()
    return PetPromptLayers(
        age_style=value.ageStyle,
        mood_style=value.moodStyle,
        stat_needs=value.statNeeds,
        character_core=value.characterCore,
        imported_seedchat=value.importedSeedchat,
        lore=value.lore,
        character_book=value.characterBook,
        memory=value.memory,
        reference_cards=value.referenceCards,
        dialogue_moves=value.dialogueMoves,
        proactivity=value.proactivity,
        post_history_instructions=value.postHistoryInstructions,
    )


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
            character_profile_v2=normalize_character_profile_v2(
                character_bible,
                raw_description=pet.description,
            ),
        ),
        recent_messages=tuple(
            PetRecentMessage(role=item.role, text=item.text) for item in payload.history[-12:]
        ),
        lore_memories=lore_memories[-12:],
        memory_context=memory_context,
        prompt_layers=_pet_prompt_layers(payload.promptLayers),
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


def _persisted_memory_state(memories: list[object]) -> object:
    lore_memories = []
    for item in memories[:20]:
        fact = str(getattr(item, "fact", "")).strip()
        if fact:
            lore_memories.append(f"ЛОР: {fact}")
    return normalize_memory(None, lore_memories=tuple(lore_memories))


def _persisted_memories_to_save(
    result_lore_memories: tuple[str, ...],
    patch: PetMemoryPatch | None,
) -> list[dict]:
    values: list[dict] = []
    seen: set[str] = set()

    def add(fact: str, importance: float = 0.55) -> None:
        clean = fact.removeprefix("ЛОР:").removeprefix("LORE:").strip()
        key = normalized_key(clean)
        if not clean or key in seen:
            return
        seen.add(key)
        values.append({"fact": clean[:500], "importance": max(0.0, min(1.0, importance))})

    for item in result_lore_memories:
        add(item, 0.55)
    if patch:
        for fact in patch.canonUpserts:
            add(fact.text, fact.importance)
    return values[:10]


def _local_debug(
    result: object,
    *,
    proactivity_flags: tuple[str, ...] = (),
    rejected_memory_count: int | None = None,
) -> LocalChatDebug:
    return LocalChatDebug(
        usedFallback=bool(getattr(result, "used_fallback", False)),
        validationFlags=list(getattr(result, "validation_flags", ()) or ()),
        rejectedMemoryCount=rejected_memory_count,
        proactivityFlags=list(proactivity_flags),
        detectedIntent=getattr(result, "detected_intent", None),
        selectedReferenceCardIds=list(getattr(result, "reference_card_ids", ()) or ()),
        includedLayers=list(getattr(result, "included_layers", ()) or ()),
        excludedLayers=list(getattr(result, "excluded_layers", ()) or ()),
    )


def chat_with_local_pet(payload: LocalChatRequest) -> LocalChatResponse:
    pet = payload.pet
    prompt_layers = _pet_prompt_layers(payload.promptLayers)
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

    memory_context = build_memory_context(memory, payload.message) if prompt_layers.memory else None
    reply_input = build_pet_reply_input(payload, memory_context=memory_context)
    result = generate_pet_reply(reply_input)
    if result.used_fallback:
        return LocalChatResponse(
            reply=result.reply,
            moodHint=result.mood_hint,
            loreMemoriesToSave=[],
            debug=_local_debug(result) if payload.includeDebug else None,
        )

    if prompt_layers.proactivity:
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
        reply_text = proactivity.reply
        proactivity_flags = proactivity.flags
        proactivity_allowed = proactivity.allowed
    else:
        reply_text = result.reply
        proactivity_flags = ("layer_disabled:proactivity",)
        proactivity_allowed = False
    no_memory_write = is_no_memory_write_message(payload.message)
    memory_patch = (
        resolve_memory_update(
            memory,
            character_bible=pet.characterBible,
            memory_context=memory_context,
            user_text=payload.message,
            pet_reply=reply_text,
            memory_candidates=list(result.memory_candidates),
            legacy_lore_memories=result.lore_memories_to_save,
            relationship_patch=result.relationship_patch,
            development_patch=result.development_patch,
            thread_patch=result.thread_patch,
            goal_patch=result.goal_patch,
            no_memory_write=no_memory_write,
            proactivity_allowed=proactivity_allowed,
        )
        if prompt_layers.memory
        else None
    )
    model_candidate_keys = {
        normalized_key(candidate.text)
        for candidate in result.memory_candidates
        if getattr(candidate, "type", "") not in ("user_fact", "relationship_event")
    }
    lore_memories = (
        _legacy_lore_response(
            result.lore_memories_to_save,
            memory_patch,
            model_candidate_keys,
        )
        if prompt_layers.memory
        else []
    )
    return LocalChatResponse(
        reply=reply_text,
        moodHint=result.mood_hint,
        loreMemoriesToSave=lore_memories,
        memoryPatch=None if _is_empty_memory_patch(memory_patch) else memory_patch,
        debug=(
            _local_debug(
                result,
                proactivity_flags=proactivity_flags,
                rejected_memory_count=(
                    len(memory_patch.rejectedCandidateAppends) if memory_patch else 0
                ),
            )
            if payload.includeDebug
            else None
        ),
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
    *,
    memory_context: object | None = None,
    selected_stage: str | None = None,
    selected_state: str | None = None,
    prompt_layers: PetPromptLayers | None = None,
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
            character_profile_v2=normalize_character_profile_v2(
                character_bible,
                raw_description=pet.original_description,
            ),
        ),
        recent_messages=tuple(
            PetRecentMessage(
                role="pet" if item.role == "assistant" else "user",
                text=item.content,
            )
            for item in history[-12:]
            if item.role in ("assistant", "user")
        ),
        memory_context=memory_context,
        prompt_layers=prompt_layers or PetPromptLayers(),
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
    prompt_layers: PromptLayers | None = None,
) -> ChatResponse:
    message_text = content.strip()
    if not message_text:
        raise public_error("EMPTY_PROMPT")
    reply_layers = _pet_prompt_layers(prompt_layers)

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
    memory = _persisted_memory_state(memories)
    memory_context = build_memory_context(memory, message_text) if reply_layers.memory else None
    reply_input = build_persisted_pet_reply_input(
        pet,
        message_text,
        history,
        memory_context=memory_context,
        selected_stage=selected_stage,
        selected_state=selected_state,
        prompt_layers=reply_layers,
    )
    result = generate_pet_reply(reply_input)
    if reply_layers.proactivity:
        proactivity = apply_proactivity_gate(
            reply=result.reply,
            proactive_intent=result.proactive_intent,
            recent_messages=reply_input.recent_messages,
            memory=memory,
            user_text=message_text,
            age_stage=reply_input.pet.age_stage,
            mood=reply_input.pet.mood,
            stats=reply_input.pet.stats,
        )
        reply = proactivity.reply
        proactivity_allowed = proactivity.allowed
    else:
        reply = result.reply
        proactivity_allowed = False
    memory_patch = None
    memories_to_save: list[dict] = []
    if not result.used_fallback and reply_layers.memory:
        memory_patch = resolve_memory_update(
            memory,
            character_bible=pet.character_profile_json,
            memory_context=memory_context,
            user_text=message_text,
            pet_reply=reply,
            memory_candidates=list(result.memory_candidates),
            legacy_lore_memories=result.lore_memories_to_save,
            relationship_patch=result.relationship_patch,
            development_patch=result.development_patch,
            thread_patch=result.thread_patch,
            goal_patch=result.goal_patch,
            no_memory_write=is_no_memory_write_message(message_text),
            proactivity_allowed=proactivity_allowed,
        )
        memories_to_save = _persisted_memories_to_save(
            result.lore_memories_to_save,
            memory_patch,
        )

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
