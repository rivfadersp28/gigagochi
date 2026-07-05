from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import get_settings
from app.dependencies import get_telegram_user
from app.errors import public_error
from app.schemas import (
    GeneratePetAssetResponse,
    GeneratePetRequest,
    LocalChatRequest,
    LocalChatResponse,
)
from app.services.chat_service import chat_with_local_pet
from app.services.image_service import generate_pet_asset_set, generation_error_code
from app.services.openai_service import MissingOpenAIAPIKey
from app.services.rate_limit_service import rate_limiter
from app.services.telegram_auth_service import TelegramUserContext

router = APIRouter(prefix="/api", tags=["telegram-mini-app"])
TelegramUser = Annotated[TelegramUserContext, Depends(get_telegram_user)]


def check_rate_limit(bucket: str, user: TelegramUserContext) -> None:
    settings = get_settings()
    if not settings.enable_in_memory_rate_limit:
        return
    if bucket == "generation":
        rate_limiter.check(bucket, user.telegram_id, limit=3, window=timedelta(days=1))
    elif bucket == "chat":
        rate_limiter.check(bucket, user.telegram_id, limit=30, window=timedelta(hours=1))


def generation_error_message(code: str) -> str:
    if code == "OPENAI_TIMEOUT":
        return "Генерация заняла больше времени, чем ожидалось. Попробуйте еще раз."
    if code == "OPENAI_RATE_LIMIT":
        return "OpenAI временно ограничил генерацию. Попробуйте позже."
    if code in {"OPENAI_AUTH_FAILED", "OPENAI_PERMISSION_DENIED"}:
        return "OpenAI API key не принят сервером. Проверьте настройки backend."
    if code == "MISSING_OPENAI_API_KEY":
        return "На сервере не настроен OpenAI API key."
    return "Не удалось создать питомца. Попробуйте еще раз."


@router.post("/generate-pet", response_model=GeneratePetAssetResponse)
def generate_pet(payload: GeneratePetRequest, user: TelegramUser) -> GeneratePetAssetResponse:
    check_rate_limit("generation", user)
    try:
        return GeneratePetAssetResponse.model_validate(
            generate_pet_asset_set(
                payload.description.strip(),
                use_template_presets=payload.useTemplatePresets,
            )
        )
    except MissingOpenAIAPIKey:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None
    except HTTPException:
        raise
    except Exception as exc:
        code = generation_error_code(exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": code,
                "error": "generation_failed",
                "message": generation_error_message(code),
            },
        ) from exc


@router.post("/chat", response_model=LocalChatResponse, response_model_exclude_none=True)
def chat(payload: LocalChatRequest, user: TelegramUser) -> LocalChatResponse:
    check_rate_limit("chat", user)
    return chat_with_local_pet(payload)
