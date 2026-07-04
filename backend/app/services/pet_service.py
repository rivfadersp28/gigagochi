from __future__ import annotations

import uuid

from fastapi import BackgroundTasks, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.errors import public_error
from app.models import Message, Pet, PetImage, User
from app.prompts.pet_image_prompts import PROMPT_MAX_LENGTH
from app.schemas import FeedResponse, MessageResponse, PetImageResponse, PetResponse
from app.services.game_service import feed_pet, select_visual_state, tick_and_commit, tick_pet


def validate_description(description: str) -> str:
    normalized = description.strip()
    if not normalized:
        raise public_error("EMPTY_PROMPT")
    if len(normalized) > PROMPT_MAX_LENGTH:
        raise public_error("PROMPT_TOO_LONG")
    return normalized


def create_pet(
    db: Session,
    user_id: uuid.UUID,
    description: str,
    background_tasks: BackgroundTasks,
) -> Pet:
    normalized = validate_description(description)
    user = db.get(User, user_id)
    if user is None:
        raise public_error("DATABASE_ERROR", status.HTTP_400_BAD_REQUEST)

    pet = Pet(user_id=user_id, original_description=normalized)
    db.add(pet)
    db.commit()
    db.refresh(pet)

    from app.services.image_service import generate_pet_assets

    background_tasks.add_task(generate_pet_assets, pet.id)
    return pet


def get_pet_or_404(
    db: Session,
    pet_id: uuid.UUID,
    include_images: bool = False,
    include_messages: bool = False,
) -> Pet:
    query = select(Pet).where(Pet.id == pet_id)
    if include_images:
        query = query.options(selectinload(Pet.images))
    if include_messages:
        query = query.options(selectinload(Pet.messages))
    pet = db.scalar(query)
    if pet is None:
        raise public_error("PET_NOT_FOUND", status.HTTP_404_NOT_FOUND)
    return pet


def image_url_for(pet: Pet, stage: str, state: str) -> str | None:
    for image in pet.images:
        if image.stage == stage and image.state == state:
            return image.image_url
    return None


def intro_message_for(pet: Pet) -> Message | None:
    messages = sorted(pet.messages, key=lambda message: message.created_at)
    if not messages:
        return None
    first_message = messages[0]
    return first_message if first_message.role == "assistant" else None


def build_pet_response(pet: Pet) -> PetResponse:
    current_state = select_visual_state(pet.hunger, pet.mood)
    intro_message = intro_message_for(pet)
    return PetResponse(
        id=pet.id,
        status=pet.status,
        current_stage=pet.current_stage,
        current_state=current_state,
        hunger=pet.hunger,
        mood=pet.mood,
        image_url=image_url_for(pet, pet.current_stage, current_state),
        images=[
            PetImageResponse(stage=image.stage, state=image.state, image_url=image.image_url)
            for image in sorted(pet.images, key=lambda item: (item.stage, item.state))
        ],
        created_at=pet.created_at,
        generation_error=pet.generation_error,
        intro_message=MessageResponse.model_validate(intro_message) if intro_message else None,
    )


def get_pet_state(db: Session, pet_id: uuid.UUID) -> PetResponse:
    pet = get_pet_or_404(db, pet_id, include_images=True, include_messages=True)
    tick_and_commit(db, pet)
    db.refresh(pet, attribute_names=["images", "messages"])
    return build_pet_response(pet)


def feed_pet_state(db: Session, pet_id: uuid.UUID) -> FeedResponse:
    pet = get_pet_or_404(db, pet_id, include_images=True)
    if pet.status != "ready":
        raise public_error("PET_NOT_READY", status.HTTP_409_CONFLICT)
    feed_pet(db, pet)
    db.refresh(pet, attribute_names=["images"])
    state = select_visual_state(pet.hunger, pet.mood)
    return FeedResponse(
        id=pet.id,
        hunger=pet.hunger,
        mood=pet.mood,
        current_stage=pet.current_stage,
        current_state=state,
        image_url=image_url_for(pet, pet.current_stage, state),
    )


def upsert_pet_image(
    db: Session,
    pet_id: uuid.UUID,
    stage: str,
    state: str,
    image_url: str,
    generation_prompt: str,
) -> None:
    existing = db.scalar(
        select(PetImage).where(
            PetImage.pet_id == pet_id,
            PetImage.stage == stage,
            PetImage.state == state,
        )
    )
    if existing:
        existing.image_url = image_url
        existing.generation_prompt = generation_prompt
    else:
        db.add(
            PetImage(
                pet_id=pet_id,
                stage=stage,
                state=state,
                image_url=image_url,
                generation_prompt=generation_prompt,
            )
        )


def refresh_pet_stage(pet: Pet) -> None:
    tick_pet(pet)
