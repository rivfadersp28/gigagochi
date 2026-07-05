from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Message, Pet
from app.prompts.chat_prompts import BIRTH_MESSAGE_SCHEMA, build_pet_birth_message_prompt
from app.services.game_service import select_visual_state
from app.services.openai_service import get_openai_client


def parse_birth_message_payload(raw_json: str) -> str:
    payload = json.loads(raw_json)
    reply = str(payload.get("reply", "")).strip()
    if not reply:
        raise ValueError("Missing birth reply")
    return reply


def fallback_birth_message(pet: Pet) -> str:
    profile = pet.character_profile_json or {}
    species = str(profile.get("species") or "").strip()
    personality = str(profile.get("personality") or "").strip()

    if pet.current_stage == "baby":
        return "Ох... я проснулся. Всё вокруг новое, но ты уже рядом. Как тебя звать?"

    detail = species or personality or pet.original_description
    if pet.current_stage == "adult":
        return (
            f"Я здесь. Кажется, я {detail}, и мне важно понять, кто рядом со мной. "
            "Как тебя зовут?"
        )

    return f"Ого, я здесь! Я {detail}, и мне уже хочется осмотреться. Как тебя зовут?"


def generate_birth_message_text(pet: Pet) -> str:
    settings = get_settings()
    client = get_openai_client()
    visual_state = select_visual_state(pet.hunger, pet.mood)
    completion = client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=[
            {
                "role": "system",
                "content": build_pet_birth_message_prompt(pet, visual_state),
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "pet_birth_message",
                "schema": BIRTH_MESSAGE_SCHEMA,
                "strict": True,
            },
        },
        timeout=settings.openai_chat_timeout_seconds,
    )
    return parse_birth_message_payload(completion.choices[0].message.content or "{}")


def ensure_birth_message(db: Session, pet: Pet) -> Message:
    existing = db.scalar(
        select(Message)
        .where(Message.pet_id == pet.id, Message.role == "assistant")
        .order_by(Message.created_at.asc())
        .limit(1)
    )
    if existing:
        return existing

    try:
        content = generate_birth_message_text(pet)
    except Exception:
        content = fallback_birth_message(pet)

    message = Message(pet_id=pet.id, role="assistant", content=content)
    db.add(message)
    db.flush()
    return message
