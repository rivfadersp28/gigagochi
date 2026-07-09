from __future__ import annotations

import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from openai import APIStatusError

from app.config import get_settings
from app.dependencies import get_telegram_user
from app.errors import public_error
from app.schemas import (
    GeneratePetAssetResponse,
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
from app.services.chat_service import chat_with_local_pet
from app.services.image_service import (
    PetAssetImageSet,
    build_pet_asset_set_response,
    generate_pet_image_asset_set,
    generate_pet_video_for_image_asset_set,
    generation_error_code,
)
from app.services.openai_service import MissingOpenAIAPIKey
from app.services.pet_reply_engine.lite_generator import (
    consolidate_user_memory,
    extract_lite_overlay_patch_from_reply,
    extract_user_memory_operations,
    generate_ambient_pet_message,
    generate_proactive_pet_message,
)
from app.services.prompt_debug import (
    current_ai_log_context,
    reset_prompt_log_context,
    set_prompt_log_context,
)
from app.services.rate_limit_service import rate_limiter
from app.services.telegram_auth_service import TelegramUserContext
from app.services.telegram_push_service import register_push_snapshot
from app.services.travel_service import generate_travel

router = APIRouter(prefix="/api", tags=["telegram-mini-app"])
TelegramUser = Annotated[TelegramUserContext, Depends(get_telegram_user)]
MAX_PROVIDER_ERROR_CHARS = 1200
GENERATION_JOB_TTL = timedelta(hours=1)
AI_FAILURE_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "ai-failures.jsonl"
logger = logging.getLogger(__name__)
generation_settings = get_settings()
generation_image_executor = ThreadPoolExecutor(
    max_workers=generation_settings.generation_image_workers,
    thread_name_prefix="pet-image",
)
generation_video_executor = ThreadPoolExecutor(
    max_workers=generation_settings.generation_video_workers,
    thread_name_prefix="pet-video",
)
DEFAULT_GENERATION_RATE_LIMIT_PER_DAY = 0
DEFAULT_CHAT_RATE_LIMIT_PER_HOUR = 0
DEFAULT_LITE_FACTS_RATE_LIMIT_PER_HOUR = 0
DEFAULT_MEMORY_RATE_LIMIT_PER_HOUR = 0


@dataclass
class GenerationJobRecord:
    owner_id: int
    username: str | None
    first_name: str | None
    response: GeneratePetJobResponse


generation_jobs: dict[str, GenerationJobRecord] = {}
generation_jobs_lock = Lock()


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


def generation_error_message(code: str) -> str:
    if code == "OPENAI_TIMEOUT":
        return "Генерация заняла больше времени, чем ожидалось. Попробуйте еще раз."
    if code == "OPENAI_RATE_LIMIT":
        return "AI-провайдер временно ограничил генерацию. Попробуйте позже."
    if code in {"OPENAI_AUTH_FAILED", "OPENAI_PERMISSION_DENIED"}:
        return "API key AI-провайдера не принят сервером. Проверьте настройки backend."
    if code == "MISSING_OPENAI_API_KEY":
        return "На сервере не настроен API key AI-провайдера."
    if code == "IMAGE_POSTPROCESS_FAILED":
        return "Картинка сгенерировалась, но backend не смог подготовить ее для питомца."
    return "Не удалось создать питомца. Попробуйте еще раз."


def chat_error_code(exc: Exception) -> str:
    code = generation_error_code(exc)
    if code in {"GENERATION_FAILED", "IMAGE_SAVE_FAILED", "IMAGE_PROMPT_REJECTED"}:
        return "CHAT_FAILED"
    return code


def chat_error_message(code: str) -> str:
    if code == "OPENAI_TIMEOUT":
        return "Ответ занял больше времени, чем ожидалось. Попробуйте еще раз."
    if code == "OPENAI_RATE_LIMIT":
        return "AI-провайдер временно ограничил чат. Попробуйте позже."
    if code in {"OPENAI_AUTH_FAILED", "OPENAI_PERMISSION_DENIED"}:
        return "API key AI-провайдера не принят сервером. Проверьте настройки backend."
    if code == "MISSING_OPENAI_API_KEY":
        return "На сервере не настроен API key AI-провайдера."
    if code == "OPENAI_BAD_REQUEST":
        return "AI-провайдер отклонил параметры chat-запроса. Проверьте настройки backend."
    if code.startswith("OPENAI_STATUS_"):
        return "AI-провайдер вернул ошибку при ответе питомца. Попробуйте позже."
    if code == "OPENAI_CONNECTION_FAILED":
        return "Backend не смог подключиться к AI-провайдеру. Попробуйте позже."
    return "Не удалось получить ответ питомца. Попробуйте еще раз."


def travel_error_message(code: str) -> str:
    if code == "OPENAI_TIMEOUT":
        return "Путешествие заняло больше времени, чем ожидалось. Попробуйте еще раз."
    if code == "OPENAI_RATE_LIMIT":
        return "AI-провайдер временно ограничил генерацию путешествий. Попробуйте позже."
    if code in {"OPENAI_AUTH_FAILED", "OPENAI_PERMISSION_DENIED"}:
        return "API key AI-провайдера не принят сервером. Проверьте настройки backend."
    if code == "MISSING_OPENAI_API_KEY":
        return "На сервере не настроен API key AI-провайдера."
    if code == "OPENAI_BAD_REQUEST":
        return "AI-провайдер отклонил параметры travel-запроса. Проверьте настройки backend."
    if code.startswith("OPENAI_STATUS_"):
        return "AI-провайдер вернул ошибку при генерации путешествия. Попробуйте позже."
    if code == "OPENAI_CONNECTION_FAILED":
        return "Backend не смог подключиться к AI-провайдеру. Попробуйте позже."
    if code == "IMAGE_SAVE_FAILED":
        return "Картинка путешествия сгенерировалась, но backend не смог ее сохранить."
    if code == "IMAGE_PROMPT_REJECTED":
        return "AI-провайдер отклонил описание картинки путешествия. Попробуйте еще раз."
    return "Не удалось создать путешествие. Попробуйте еще раз."


def _compact_error_text(value: str) -> str:
    return " ".join(value.split())[:MAX_PROVIDER_ERROR_CHARS]


def _payload_error_message(payload: object) -> str | None:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return None
    for key in ("message", "detail", "error_description"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    error = payload.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        return _payload_error_message(error)
    return None


def _provider_response_text(response: object) -> str | None:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    try:
        payload = response.json()  # type: ignore[attr-defined]
    except Exception:
        return None
    try:
        return json.dumps(payload, ensure_ascii=False)
    except TypeError:
        return str(payload)


def _provider_exception(exc: Exception) -> Exception | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError | APIStatusError):
            return current
        current = current.__cause__ or current.__context__
    return None


def provider_error_details(exc: Exception) -> dict[str, object]:
    provider_exc = _provider_exception(exc)
    if provider_exc is None:
        return {}

    response = None
    provider_status: int | None = None

    if isinstance(provider_exc, httpx.HTTPStatusError):
        response = provider_exc.response
        provider_status = provider_exc.response.status_code
    elif isinstance(provider_exc, APIStatusError):
        response = provider_exc.response
        provider_status = provider_exc.status_code

    details: dict[str, object] = {}
    if provider_status is not None:
        details["providerStatus"] = provider_status

    response_text = _provider_response_text(response) if response is not None else None
    if response_text:
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError:
            provider_message = response_text
        else:
            provider_message = _payload_error_message(payload) or response_text
        details["providerMessage"] = _compact_error_text(provider_message)

    request_id = None
    headers = getattr(response, "headers", None)
    if headers is not None:
        request_id = headers.get("x-request-id") or headers.get("cf-ray")
    if request_id:
        details["requestId"] = str(request_id)

    return details


def error_detail(error: str, code: str, message: str, exc: Exception) -> dict[str, object]:
    return {
        "code": code,
        "error": error,
        "message": message,
        **provider_error_details(exc),
    }


def _now_utc() -> datetime:
    return datetime.now(UTC)


def write_ai_failure_log_line(log_payload: dict[str, Any]) -> None:
    AI_FAILURE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line_payload = {
        "timestamp": _now_utc().isoformat(),
        **log_payload,
    }
    with AI_FAILURE_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(line_payload, ensure_ascii=False, default=str))
        log_file.write("\n")


def log_ai_request_failure(
    endpoint: str,
    detail: dict[str, object],
    exc: Exception,
) -> None:
    log_payload = {
        **current_ai_log_context(),
        "event": "ai_request_failed",
        "endpoint": endpoint,
        "code": detail.get("code"),
        "error": detail.get("error"),
        "message": detail.get("message"),
        "providerStatus": detail.get("providerStatus"),
        "providerMessage": detail.get("providerMessage"),
        "requestId": detail.get("requestId"),
        "exceptionType": type(exc).__name__,
        "exceptionMessage": _compact_error_text(str(exc)) if str(exc) else None,
    }
    try:
        write_ai_failure_log_line(log_payload)
    except Exception:
        logger.warning("Could not write AI failure log line", exc_info=True)
    logger.exception(
        "AI request failed: %s",
        json.dumps(log_payload, ensure_ascii=False, default=str),
    )


def ai_failure_http_exception(
    endpoint: str,
    error: str,
    code: str,
    message: str,
    exc: Exception,
) -> HTTPException:
    detail = error_detail(error, code, message, exc)
    log_ai_request_failure(endpoint, detail, exc)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=detail,
    )


def cleanup_generation_jobs(now: datetime | None = None) -> None:
    cutoff = (now or _now_utc()) - GENERATION_JOB_TTL
    with generation_jobs_lock:
        expired_job_ids = [
            job_id
            for job_id, record in generation_jobs.items()
            if record.response.updatedAt < cutoff
        ]
        for job_id in expired_job_ids:
            generation_jobs.pop(job_id, None)


def update_generation_job(
    job_id: str,
    *,
    status_value: str,
    phase: str | None = None,
    result: GeneratePetAssetResponse | None = None,
    error: dict[str, object] | None = None,
) -> None:
    with generation_jobs_lock:
        record = generation_jobs.get(job_id)
        if record is None:
            return
        updates: dict[str, object] = {
            "status": status_value,
            "updatedAt": _now_utc(),
            "result": result,
            "error": error,
        }
        if phase is not None:
            updates["phase"] = phase
        record.response = record.response.model_copy(update=updates)


def fail_generation_job(job_id: str, exc: Exception, *, phase: str) -> None:
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
    update_generation_job(job_id, status_value="failed", phase=phase, error=detail)


def run_generation_video_job(job_id: str, image_set: PetAssetImageSet) -> None:
    started_at = time.monotonic()
    logger.info(
        "pet_generation_stage_started jobId=%s phase=generating_video assetSetId=%s",
        job_id,
        image_set.asset_set_id,
    )
    prompt_log_token = set_prompt_log_context(
        {
            "jobId": job_id,
            "endpoint": "/api/generate-pet",
        }
    )
    try:
        video_path = generate_pet_video_for_image_asset_set(image_set)
        result = GeneratePetAssetResponse.model_validate(
            build_pet_asset_set_response(image_set, video_path)
        )
    except Exception as exc:
        fail_generation_job(job_id, exc, phase="generating_video")
        return
    finally:
        reset_prompt_log_context(prompt_log_token)

    logger.info(
        "pet_generation_stage_completed jobId=%s phase=generating_video "
        "durationSeconds=%.3f assetSetId=%s",
        job_id,
        time.monotonic() - started_at,
        image_set.asset_set_id,
    )
    update_generation_job(
        job_id,
        status_value="succeeded",
        phase="completed",
        result=result,
    )


def run_generation_image_job(job_id: str, description: str) -> None:
    started_at = time.monotonic()
    update_generation_job(
        job_id,
        status_value="running",
        phase="generating_images",
    )
    logger.info("pet_generation_stage_started jobId=%s phase=generating_images", job_id)
    prompt_log_token = set_prompt_log_context(
        {
            "jobId": job_id,
            "endpoint": "/api/generate-pet",
        }
    )
    try:
        image_set = generate_pet_image_asset_set(description)
    except Exception as exc:
        fail_generation_job(job_id, exc, phase="generating_images")
        return
    finally:
        reset_prompt_log_context(prompt_log_token)

    logger.info(
        "pet_generation_stage_completed jobId=%s phase=generating_images "
        "durationSeconds=%.3f assetSetId=%s",
        job_id,
        time.monotonic() - started_at,
        image_set.asset_set_id,
    )
    update_generation_job(
        job_id,
        status_value="running",
        phase="generating_video",
    )
    try:
        generation_video_executor.submit(run_generation_video_job, job_id, image_set)
    except Exception as exc:
        fail_generation_job(job_id, exc, phase="generating_video")


def submit_generation_job(description: str, user: TelegramUserContext) -> GeneratePetJobResponse:
    cleanup_generation_jobs()
    now = _now_utc()
    job_id = str(uuid.uuid4())
    response = GeneratePetJobResponse(
        jobId=job_id,
        status="queued",
        phase="queued",
        createdAt=now,
        updatedAt=now,
    )
    with generation_jobs_lock:
        generation_jobs[job_id] = GenerationJobRecord(
            owner_id=user.telegram_id,
            username=user.username,
            first_name=user.first_name,
            response=response,
        )
    logger.info(
        "pet_generation_queued jobId=%s ownerId=%s username=%s firstName=%s "
        "imageWorkers=%s videoWorkers=%s",
        job_id,
        user.telegram_id,
        user.username,
        user.first_name,
        generation_settings.generation_image_workers,
        generation_settings.generation_video_workers,
    )
    generation_image_executor.submit(run_generation_image_job, job_id, description)
    return response


def get_generation_job(job_id: str, user: TelegramUserContext) -> GeneratePetJobResponse:
    cleanup_generation_jobs()
    with generation_jobs_lock:
        record = generation_jobs.get(job_id)
        if record is None or record.owner_id != user.telegram_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "GENERATION_JOB_NOT_FOUND",
                    "message": "Задача генерации не найдена. Запустите генерацию заново.",
                },
            )
        return record.response


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
        ) from None
    return submit_generation_job(description, user)


@router.get("/generate-pet/jobs/{job_id}", response_model=GeneratePetJobResponse)
def generation_job(job_id: str, user: TelegramUser) -> GeneratePetJobResponse:
    return get_generation_job(job_id, user)


@router.post("/chat", response_model=LocalChatResponse, response_model_exclude_none=True)
def chat(payload: LocalChatRequest, user: TelegramUser) -> LocalChatResponse:
    check_rate_limit("chat", user)
    prompt_log_token = set_prompt_log_context({"endpoint": "/api/chat"})
    try:
        return chat_with_local_pet(payload)
    except MissingOpenAIAPIKey:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
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
    settings = get_settings()
    has_ai_key = bool(
        getattr(settings, "openai_api_key", None) or getattr(settings, "openrouter_api_key", None)
    )
    if not has_ai_key:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    prompt_log_token = set_prompt_log_context({"endpoint": "/api/travel"})
    try:
        return generate_travel(payload)
    except MissingOpenAIAPIKey:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
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
    prompt_log_token = set_prompt_log_context({"endpoint": "/api/chat/ambient"})
    try:
        return generate_ambient_pet_message(payload)
    except MissingOpenAIAPIKey:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
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
    try:
        return generate_proactive_pet_message(payload)
    except MissingOpenAIAPIKey:
        raise public_error(
            "MISSING_OPENAI_API_KEY",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        ) from exc
