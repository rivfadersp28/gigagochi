from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import ChatRequest, ChatResponse, MessagesResponse
from app.services.chat_service import chat_with_pet, list_messages

router = APIRouter(prefix="/pets", tags=["chat"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/{pet_id}/messages", response_model=MessagesResponse)
def get_messages(pet_id: uuid.UUID, db: DbSession) -> MessagesResponse:
    return MessagesResponse(messages=list_messages(db, pet_id))


@router.post("/{pet_id}/chat", response_model=ChatResponse)
def post_chat(
    pet_id: uuid.UUID,
    payload: ChatRequest,
    db: DbSession,
) -> ChatResponse:
    return chat_with_pet(
        db,
        pet_id,
        payload.message,
        selected_stage=payload.selected_stage,
        selected_state=payload.selected_state,
        prompt_layers=payload.promptLayers,
    )
