from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import CreatePetRequest, CreatePetResponse, FeedResponse, PetResponse
from app.services.pet_service import create_pet, feed_pet_state, get_pet_state

router = APIRouter(prefix="/pets", tags=["pets"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=CreatePetResponse)
def post_pet(
    payload: CreatePetRequest,
    background_tasks: BackgroundTasks,
    db: DbSession,
) -> CreatePetResponse:
    pet = create_pet(db, payload.user_id, payload.description, background_tasks)
    return CreatePetResponse(id=pet.id, status=pet.status)


@router.get("/{pet_id}", response_model=PetResponse)
def get_pet(pet_id: uuid.UUID, db: DbSession) -> PetResponse:
    return get_pet_state(db, pet_id)


@router.post("/{pet_id}/feed", response_model=FeedResponse)
def feed_pet(pet_id: uuid.UUID, db: DbSession) -> FeedResponse:
    return feed_pet_state(db, pet_id)
