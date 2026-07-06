from __future__ import annotations

from app.schemas import LocalChatRequest, LocalChatResponse
from app.services.pet_reply_engine.lite_generator import generate_lite_pet_reply


def chat_with_local_pet(payload: LocalChatRequest) -> LocalChatResponse:
    return generate_lite_pet_reply(payload)
