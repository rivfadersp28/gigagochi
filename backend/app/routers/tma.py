from __future__ import annotations

import logging
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import get_settings
from app.dependencies import get_telegram_user
from app.errors import public_error
from app.schemas import (
    GeneratePetJobResponse,
    GeneratePetRequest,
    GenerateTravelRequest,
    GenerateTravelResponse,
    LiteFactExtractionRequest,
    LiteFactExtractionResponse,
    LocalAmbientRequest,
    LocalChatRequest,
    LocalChatResponse,
    LocalPetPushSnapshotRequest,
    LocalPetPushSnapshotResponse,
    LocalProactiveRequest,
    LocalProactiveResponse,
    MemoryConsolidationRequest,
    MemoryConsolidationResponse,
    MemoryExtractionRequest,
    MemoryExtractionResponse,
)
from app.services.ai_error_service import (
    ai_failure_http_exception,
    chat_error_code,
    chat_error_message,
    error_detail,
    generation_error_message,
    log_ai_request_failure,
    public_error_detail,
    travel_error_message,
)
from app.services.chat_service import chat_with_local_pet
from app.services.generation_job_service import (
    GenerationJobNotFoundError,
    GenerationJobService,
)
from app.services.image_service import (
    build_pet_asset_set_response,
    generate_pet_happy_scene_path,
    generate_pet_happy_video_for_image_asset_set,
    generate_pet_image_asset_set,
    generate_pet_sad_scene_path,
    generate_pet_sad_video_for_image_asset_set,
    generate_pet_video_for_image_asset_set,
    generation_error_code,
)
from app.services.openai_service import MissingOpenAIAPIKey
from app.services.pet_reply_engine.lite_generator import (
    extract_lite_overlay_patch_from_reply,
    generate_ambient_pet_message,
    generate_proactive_pet_message,
)
from app.services.pet_reply_engine.memory_operations import (
    consolidate_user_memory,
    extract_user_memory_operations,
)
from app.services.prompt_debug import (
    reset_prompt_log_context,
    set_prompt_log_context,
)
from app.services.rate_limit_service import rate_limiter
from app.services.telegram_auth_service import TelegramUserContext
from app.services.telegram_push_service import register_push_snapshot
from app.services.travel_service import generate_travel

router = APIRouter(prefix="/api", tags=["telegram-mini-app"])
TelegramUser = Annotated[TelegramUserContext, Depends(get_telegram_user)]
logger = logging.getLogger(__name__)
DEFAULT_GENERATION_RATE_LIMIT_PER_DAY = 0
DEFAULT_CHAT_RATE_LIMIT_PER_HOUR = 0
DEFAULT_LITE_FACTS_RATE_LIMIT_PER_HOUR = 0
DEFAULT_MEMORY_RATE_LIMIT_PER_HOUR = 0


def check_rate_limit(bucket: str, user: TelegramUserContext) -> None:
    settings = get_settings()
    if not settings.enable_in_memory_rate_limit:
        return
    if bucket == "generation":
        rate_limiter.check(
            bucket,
            user.telegram_id,
            limit=getattr(
                settings,
                "generation_rate_limit_per_day",
                DEFAULT_GENERATION_RATE_LIMIT_PER_DAY,
            ),
            window=timedelta(days=1),
        )
    elif bucket == "chat":
        rate_limiter.check(
            bucket,
            user.telegram_id,
            limit=getattr(settings, "chat_rate_limit_per_hour", DEFAULT_CHAT_RATE_LIMIT_PER_HOUR),
            window=timedelta(hours=1),
        )
    elif bucket in {"lite_facts", "memory"}:
        setting_name = (
            "lite_facts_rate_limit_per_hour"
            if bucket == "lite_facts"
            else "memory_rate_limit_per_hour"
        )
        default_limit = (
            DEFAULT_LITE_FACTS_RATE_LIMIT_PER_HOUR
            if bucket == "lite_facts"
            else DEFAULT_MEMORY_RATE_LIMIT_PER_HOUR
        )
        rate_limiter.check(
            bucket,
            user.telegram_id,
            limit=getattr(settings, setting_name, default_limit),
            window=timedelta(hours=1),
        )


def _without_untrusted_debug(payload: Any) -> Any:
    if not getattr(payload, "includeDebug", False):
        return payload
    if getattr(get_settings(), "allow_dev_tma_auth", False):
        return payload
    return payload.model_copy(update={"includeDebug": False})


def _is_diagnostic_user(user_id: int) -> bool:
    return user_id in getattr(get_settings(), "diagnostic_telegram_ids", {62943754})


def _build_generation_failure(
    job_id: str,
    phase: str,
    exc: Exception,
    owner_id: int,
) -> dict[str, object]:
    code = generation_error_code(exc)
    detail = error_detail(
        "generation_failed",
        code,
        generation_error_message(code),
        exc,
    )
    log_ai_request_failure("/api/generate-pet", detail, exc)
    logger.exception(
        "pet_generation_failed jobId=%s phase=%s code=%s errorType=%s",
        job_id,
        phase,
        code,
        type(exc).__name__,
    )
    return public_error_detail(detail, include_diagnostic=_is_diagnostic_user(owner_id))


generation_job_service: GenerationJobService | None = None


def _generation_job_service() -> GenerationJobService:
    global generation_job_service
    if generation_job_service is None:
        settings = get_settings()
        generation_job_service = GenerationJobService(
            image_workers=getattr(settings, "generation_image_workers", 3),
            video_workers=getattr(settings, "generation_video_workers", 4),
            generate_images=lambda description: generate_pet_image_asset_set(description),
            generate_video=lambda image_set: generate_pet_video_for_image_asset_set(image_set),
            generate_background_image=lambda image_set: generate_pet_sad_scene_path(image_set),
            generate_background_video=lambda image_set, sad_scene_path: (
                generate_pet_sad_video_for_image_asset_set(image_set, sad_scene_path)
            ),
            generate_happy_image=lambda image_set: generate_pet_happy_scene_path(image_set),
            generate_happy_video=lambda image_set, happy_scene_path: (
                generate_pet_happy_video_for_image_asset_set(image_set, happy_scene_path)
            ),
            build_response=build_pet_asset_set_response,
            build_failure=_build_generation_failure,
            derived_asset_owner_ids=settings.derived_asset_pilot_telegram_ids,
        )
    return generation_job_service


def submit_generation_job(description: str, user: TelegramUserContext) -> GeneratePetJobResponse:
    return _generation_job_service().submit(description, user)


def get_generation_job(job_id: str, user: TelegramUserContext) -> GeneratePetJobResponse:
    try:
        return _generation_job_service().get(job_id, user.telegram_id)
    except GenerationJobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "GENERATION_JOB_NOT_FOUND",
                "message": "Задача генерации не найдена. Запустите генерацию заново.",
            },
        ) from None


def shutdown_generation_jobs() -> None:
    global generation_job_service
    if generation_job_service is not None:
        generation_job_service.shutdown()
        generation_job_service = None


@router.post(
    "/generate-pet",
    response_model=GeneratePetJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_pet(payload: GeneratePetRequest, user: TelegramUser) -> GeneratePetJobResponse:
    check_rate_limit("generation", user)
    description = payload.description.strip()
    settings = get_settings()
    has_ai_key = bool(
        getattr(settings, "openai_api_key", None) or getattr(settings, "openrouter_api_key", None)
    )
    if not has_ai_key:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from None
    return submit_generation_job(description, user)


@router.get("/generate-pet/jobs/{job_id}", response_model=GeneratePetJobResponse)
def generation_job(job_id: str, user: TelegramUser) -> GeneratePetJobResponse:
    return get_generation_job(job_id, user)


@router.post("/chat", response_model=LocalChatResponse, response_model_exclude_none=True)
def chat(payload: LocalChatRequest, user: TelegramUser) -> LocalChatResponse:
    check_rate_limit("chat", user)
    payload = _without_untrusted_debug(payload)
    prompt_log_token = set_prompt_log_context({"endpoint": "/api/chat"})
    try:
        return chat_with_local_pet(payload)
    except MissingOpenAIAPIKey:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from None
    except HTTPException:
        raise
    except Exception as exc:
        code = chat_error_code(exc)
        raise ai_failure_http_exception(
            "/api/chat",
            "chat_failed",
            code,
            chat_error_message(code),
            exc,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from exc
    finally:
        reset_prompt_log_context(prompt_log_token)


@router.post("/push/snapshot", response_model=LocalPetPushSnapshotResponse)
def push_snapshot(
    payload: LocalPetPushSnapshotRequest,
    user: TelegramUser,
) -> LocalPetPushSnapshotResponse:
    return register_push_snapshot(user, payload)


@router.post("/travel", response_model=GenerateTravelResponse, response_model_exclude_none=True)
def travel(payload: GenerateTravelRequest, user: TelegramUser) -> GenerateTravelResponse:
    check_rate_limit("generation", user)
    payload = _without_untrusted_debug(payload)
    settings = get_settings()
    has_ai_key = bool(
        getattr(settings, "openai_api_key", None) or getattr(settings, "openrouter_api_key", None)
    )
    if not has_ai_key:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from None

    prompt_log_token = set_prompt_log_context({"endpoint": "/api/travel"})
    try:
        return generate_travel(payload)
    except MissingOpenAIAPIKey:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from None
    except HTTPException:
        raise
    except Exception as exc:
        code = chat_error_code(exc)
        raise ai_failure_http_exception(
            "/api/travel",
            "travel_failed",
            code,
            travel_error_message(code),
            exc,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from exc
    finally:
        reset_prompt_log_context(prompt_log_token)


@router.post(
    "/chat/ambient",
    response_model=LocalChatResponse,
    response_model_exclude_none=True,
)
def ambient_chat(
    payload: LocalAmbientRequest,
    user: TelegramUser,
) -> LocalChatResponse:
    check_rate_limit("chat", user)
    payload = _without_untrusted_debug(payload)
    prompt_log_token = set_prompt_log_context({"endpoint": "/api/chat/ambient"})
    try:
        return generate_ambient_pet_message(payload)
    except MissingOpenAIAPIKey:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from None
    except HTTPException:
        raise
    except Exception as exc:
        code = chat_error_code(exc)
        raise ai_failure_http_exception(
            "/api/chat/ambient",
            "ambient_chat_failed",
            code,
            chat_error_message(code),
            exc,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from exc
    finally:
        reset_prompt_log_context(prompt_log_token)


@router.post(
    "/chat/lite-facts",
    response_model=LiteFactExtractionResponse,
    response_model_exclude_none=True,
)
def extract_lite_facts(
    payload: LiteFactExtractionRequest,
    user: TelegramUser,
) -> LiteFactExtractionResponse:
    check_rate_limit("lite_facts", user)
    payload = _without_untrusted_debug(payload)
    patch, debug = extract_lite_overlay_patch_from_reply(payload)
    return LiteFactExtractionResponse(liteOverlayPatch=patch, debug=debug)


@router.post(
    "/chat/memory-extract",
    response_model=MemoryExtractionResponse,
    response_model_exclude_none=True,
)
def extract_memory(
    payload: MemoryExtractionRequest,
    user: TelegramUser,
) -> MemoryExtractionResponse:
    check_rate_limit("memory", user)
    payload = _without_untrusted_debug(payload)
    return extract_user_memory_operations(payload)


@router.post(
    "/chat/memory-consolidate",
    response_model=MemoryConsolidationResponse,
    response_model_exclude_none=True,
)
def consolidate_memory(
    payload: MemoryConsolidationRequest,
    user: TelegramUser,
) -> MemoryConsolidationResponse:
    check_rate_limit("memory", user)
    payload = _without_untrusted_debug(payload)
    return consolidate_user_memory(payload)


@router.post(
    "/chat/proactive",
    response_model=LocalProactiveResponse,
    response_model_exclude_none=True,
)
def proactive_chat(
    payload: LocalProactiveRequest,
    user: TelegramUser,
) -> LocalProactiveResponse:
    check_rate_limit("memory", user)
    payload = _without_untrusted_debug(payload)
    try:
        return generate_proactive_pet_message(payload)
    except MissingOpenAIAPIKey:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from None
    except HTTPException:
        raise
    except Exception as exc:
        code = chat_error_code(exc)
        raise ai_failure_http_exception(
            "/api/chat/proactive",
            "proactive_chat_failed",
            code,
            chat_error_message(code),
            exc,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from exc
