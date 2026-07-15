from __future__ import annotations

import fcntl
import hashlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from threading import Lock
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi import Path as PathParameter
from pydantic import BaseModel

from app.config import get_settings
from app.dependencies import get_telegram_user
from app.errors import public_error
from app.schemas import (
    AnimateInteractiveTravelPartRequest,
    ContinueInteractiveTravelRequest,
    GeneratePetJobResponse,
    GeneratePetRequest,
    GenerationStatsResponse,
    IllustrateInteractiveTravelPartRequest,
    InteractiveTravelAnimationResponse,
    InteractiveTravelDemoResponse,
    InteractiveTravelIllustrationResponse,
    InteractiveTravelResponse,
    InteractiveTravelState,
    InteractiveTravelSuggestionsRequest,
    InteractiveTravelSuggestionsResponse,
    LiteFactExtractionRequest,
    LiteFactExtractionResponse,
    LocalAmbientRequest,
    LocalChatRequest,
    LocalChatResponse,
    LocalPetPushSnapshotDeleteResponse,
    LocalPetPushSnapshotRequest,
    LocalPetPushSnapshotResponse,
    LocalProactiveRequest,
    LocalProactiveResponse,
    MemoryConsolidationRequest,
    MemoryConsolidationResponse,
    MemoryExtractionRequest,
    MemoryExtractionResponse,
    StartInteractiveTravelRequest,
    TmaCapabilitiesResponse,
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
from app.services.generated_media_cleanup import (
    cleanup_owned_generated_asset_directory,
    generated_media_cleanup_is_enabled,
)
from app.services.generation_job_service import (
    GenerationIdempotencyConflictError,
    GenerationJobNotFoundError,
    GenerationJobService,
    GenerationOwnerActiveError,
    GenerationQueueFullError,
)
from app.services.generation_job_store import GenerationJobStore
from app.services.generation_notification_service import send_generation_ready_notification
from app.services.image_service import (
    build_pet_asset_set_response,
    comparison_asset_set_id,
    generate_kandinsky_pet_comparison_assets,
    generate_pet_happy_scene_path,
    generate_pet_happy_video_for_image_asset_set,
    generate_pet_image_asset_set,
    generate_pet_sad_scene_path,
    generate_pet_sad_video_for_image_asset_set,
    generate_pet_video_for_image_asset_set,
    generated_dir_for,
    generation_error_code,
    generation_job_asset_set_id,
)
from app.services.interactive_travel_demo_service import read_interactive_travel_demo
from app.services.interactive_travel_finale_service import (
    patch_interactive_travel_finale_media,
    save_interactive_travel_finale,
)
from app.services.interactive_travel_media_service import (
    cancel_interactive_travel_generation,
    reset_interactive_travel_generation,
)
from app.services.interactive_travel_service import (
    animate_interactive_travel_part,
    continue_interactive_travel,
    generate_interactive_travel_suggestions,
    illustrate_interactive_travel_part,
    start_interactive_travel,
)
from app.services.interactive_travel_session_store import (
    DEFAULT_INTERACTIVE_TRAVEL_MAX_RECORDS,
    DEFAULT_INTERACTIVE_TRAVEL_RETENTION,
    DEFAULT_INTERACTIVE_TRAVEL_STORE_PATH,
    InteractiveTravelActiveError,
    InteractiveTravelOwnerMissingError,
    InteractiveTravelPetMismatchError,
    InteractiveTravelSessionCancelledError,
    InteractiveTravelSessionCapacityError,
    InteractiveTravelSessionCompletedError,
    InteractiveTravelSessionError,
    InteractiveTravelSessionOwnerMismatchError,
    InteractiveTravelStateConflictError,
    fingerprint_payload,
    get_interactive_travel_session_store,
    interactive_travel_state_fingerprint,
)
from app.services.openai_service import MissingOpenAIAPIKey
from app.services.ops_alert_service import notify_ops
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
from app.services.rate_limit_service import (
    DEFAULT_RATE_LIMIT_STORE_PATH,
    RateLimitExceeded,
    RateLimitReservation,
    get_rate_limiter,
)
from app.services.request_admission_service import (
    RequestAdmissionLease,
    RequestAdmissionRejected,
    public_request_admission,
)
from app.services.telegram_auth_service import TelegramUserContext
from app.services.telegram_push_service import register_push_snapshot, unregister_push_snapshot
from app.services.telegram_push_store import (
    TelegramPushRecordTooLargeError,
    TelegramPushStoreCapacityError,
)

router = APIRouter(prefix="/api", tags=["telegram-mini-app"])
TelegramUser = Annotated[TelegramUserContext, Depends(get_telegram_user)]
GenerationIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        description=(
            "Required for paid generation. Reusing the same key for this Telegram user "
            "replays or resumes the original operation without restarting completed media."
        ),
    ),
]
logger = logging.getLogger(__name__)
DEFAULT_GENERATION_RATE_LIMIT_PER_DAY = 0
DEFAULT_CHAT_RATE_LIMIT_PER_HOUR = 0
DEFAULT_LITE_FACTS_RATE_LIMIT_PER_HOUR = 0
DEFAULT_MEMORY_RATE_LIMIT_PER_HOUR = 0
DEFAULT_PUSH_SNAPSHOT_RATE_LIMIT_PER_HOUR = 60
DEFAULT_PUSH_SNAPSHOT_ATTEMPT_RATE_LIMIT_PER_HOUR = 120
DEFAULT_PUSH_SNAPSHOT_DELETE_RATE_LIMIT_PER_HOUR = 10
DEFAULT_INTERACTIVE_TRAVEL_RATE_LIMIT_PER_DAY = 30
DEFAULT_HTTP_LLM_GLOBAL_CONCURRENCY = 16
DEFAULT_HTTP_LLM_PER_USER_CONCURRENCY = 2
DEFAULT_HTTP_MEDIA_GLOBAL_CONCURRENCY = 4
DEFAULT_HTTP_MEDIA_PER_USER_CONCURRENCY = 1
DEFAULT_HTTP_ADMISSION_RETRY_AFTER_SECONDS = 5
_generation_admission_thread_lock = Lock()


class CaptureInteractiveTravelFinaleRequest(BaseModel):
    travel: InteractiveTravelState


@contextmanager
def _generation_admission_lock(store_path: str):
    database_path = Path(store_path).expanduser().resolve()
    lock_path = database_path.with_name(f"{database_path.name}.admission.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _generation_admission_thread_lock, lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def check_rate_limit(
    bucket: str,
    user: TelegramUserContext,
    *,
    request_key: str | None = None,
) -> RateLimitReservation | None:
    settings = get_settings()
    if not getattr(settings, "enable_in_memory_rate_limit", False):
        return None
    limit_and_window: tuple[int, timedelta] | None = None
    if bucket == "generation":
        limit_and_window = (
            getattr(
                settings,
                "generation_rate_limit_per_day",
                DEFAULT_GENERATION_RATE_LIMIT_PER_DAY,
            ),
            timedelta(days=1),
        )
    elif bucket == "interactive_travel":
        limit_and_window = (
            getattr(
                settings,
                "interactive_travel_rate_limit_per_day",
                DEFAULT_INTERACTIVE_TRAVEL_RATE_LIMIT_PER_DAY,
            ),
            timedelta(days=1),
        )
    elif bucket == "chat":
        limit_and_window = (
            getattr(settings, "chat_rate_limit_per_hour", DEFAULT_CHAT_RATE_LIMIT_PER_HOUR),
            timedelta(hours=1),
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
        limit_and_window = (
            getattr(settings, setting_name, default_limit),
            timedelta(hours=1),
        )
    elif bucket in {"push_snapshot", "push_snapshot_attempt", "push_snapshot_delete"}:
        setting_name, default_limit = {
            "push_snapshot": (
                "push_snapshot_rate_limit_per_hour",
                DEFAULT_PUSH_SNAPSHOT_RATE_LIMIT_PER_HOUR,
            ),
            "push_snapshot_attempt": (
                "push_snapshot_attempt_rate_limit_per_hour",
                DEFAULT_PUSH_SNAPSHOT_ATTEMPT_RATE_LIMIT_PER_HOUR,
            ),
            "push_snapshot_delete": (
                "push_snapshot_delete_rate_limit_per_hour",
                DEFAULT_PUSH_SNAPSHOT_DELETE_RATE_LIMIT_PER_HOUR,
            ),
        }[bucket]
        limit_and_window = (
            getattr(settings, setting_name, default_limit),
            timedelta(hours=1),
        )
    if limit_and_window is None:
        return None

    limiter = get_rate_limiter(
        getattr(settings, "rate_limit_store_path", DEFAULT_RATE_LIMIT_STORE_PATH)
    )
    try:
        if bucket in {"push_snapshot", "push_snapshot_attempt", "push_snapshot_delete"}:
            limiter.check_fixed_window(
                bucket,
                user.telegram_id,
                limit=limit_and_window[0],
                window=limit_and_window[1],
                request_key=request_key,
            )
            return None
        return limiter.check(
            bucket,
            user.telegram_id,
            limit=limit_and_window[0],
            window=limit_and_window[1],
            request_key=request_key,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "rate_limited",
                "message": "Слишком много запросов.",
                "retryAfterSeconds": exc.retry_after_seconds,
            },
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None


def refund_rate_limit(reservation: RateLimitReservation) -> None:
    settings = get_settings()
    limiter = get_rate_limiter(
        getattr(settings, "rate_limit_store_path", DEFAULT_RATE_LIMIT_STORE_PATH)
    )
    try:
        limiter.refund(reservation)
    except Exception:
        logger.exception(
            "rate_limit_refund_failed bucket=%s userId=%s eventId=%s",
            reservation.bucket,
            reservation.user_id,
            reservation.event_id,
        )


def _refund_optional_rate_limit(reservation: RateLimitReservation | None) -> None:
    if reservation is not None:
        refund_rate_limit(reservation)


def _acquire_public_request_admission(
    user: TelegramUserContext,
    *,
    bucket: str,
    global_setting: str,
    per_user_setting: str,
    default_global_limit: int,
    default_per_user_limit: int,
) -> RequestAdmissionLease:
    settings = get_settings()
    retry_after = max(
        1,
        min(
            300,
            int(
                getattr(
                    settings,
                    "http_admission_retry_after_seconds",
                    DEFAULT_HTTP_ADMISSION_RETRY_AFTER_SECONDS,
                )
            ),
        ),
    )
    try:
        return public_request_admission.acquire(
            bucket,
            user.telegram_id,
            global_limit=int(getattr(settings, global_setting, default_global_limit)),
            per_user_limit=int(getattr(settings, per_user_setting, default_per_user_limit)),
        )
    except RequestAdmissionRejected as exc:
        per_user = exc.scope == "user"
        raise HTTPException(
            status_code=(
                status.HTTP_429_TOO_MANY_REQUESTS
                if per_user
                else status.HTTP_503_SERVICE_UNAVAILABLE
            ),
            detail={
                "code": (
                    "REQUEST_ADMISSION_USER_LIMIT" if per_user else "REQUEST_ADMISSION_GLOBAL_LIMIT"
                ),
                "message": (
                    "Предыдущий запрос ещё выполняется. Попробуйте немного позже."
                    if per_user
                    else "Сервис занят. Попробуйте немного позже."
                ),
                "retryAfterSeconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        ) from None


async def _llm_request_admission(user: TelegramUser) -> AsyncIterator[None]:
    lease = _acquire_public_request_admission(
        user,
        bucket="llm",
        global_setting="http_llm_global_concurrency",
        per_user_setting="http_llm_per_user_concurrency",
        default_global_limit=DEFAULT_HTTP_LLM_GLOBAL_CONCURRENCY,
        default_per_user_limit=DEFAULT_HTTP_LLM_PER_USER_CONCURRENCY,
    )
    try:
        yield
    finally:
        lease.release()


async def _media_request_admission(user: TelegramUser) -> AsyncIterator[None]:
    lease = _acquire_public_request_admission(
        user,
        bucket="media",
        global_setting="http_media_global_concurrency",
        per_user_setting="http_media_per_user_concurrency",
        default_global_limit=DEFAULT_HTTP_MEDIA_GLOBAL_CONCURRENCY,
        default_per_user_limit=DEFAULT_HTTP_MEDIA_PER_USER_CONCURRENCY,
    )
    try:
        yield
    finally:
        lease.release()


def _without_untrusted_debug(payload: Any, user: TelegramUserContext) -> Any:
    if not getattr(payload, "includeDebug", False):
        return payload
    settings = get_settings()
    if getattr(settings, "allow_dev_tma_auth", False) and (
        user.telegram_id == 0
        or user.telegram_id in getattr(settings, "diagnostic_telegram_ids", set())
    ):
        return payload
    return payload.model_copy(update={"includeDebug": False})


def _is_diagnostic_user(user_id: int) -> bool:
    return user_id in getattr(get_settings(), "diagnostic_telegram_ids", set())


def _interactive_travel_allowed(user: TelegramUserContext) -> bool:
    settings = get_settings()
    return bool(
        getattr(settings, "allow_dev_tma_auth", False)
        and user.telegram_id == 0
        or user.telegram_id in getattr(settings, "interactive_travel_pilot_telegram_ids", set())
    )


def _require_interactive_travel_pilot(user: TelegramUser) -> None:
    if _interactive_travel_allowed(user):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "INTERACTIVE_TRAVEL_NOT_AVAILABLE",
            "message": "Путешествия пока недоступны для этого пользователя.",
        },
    )


@router.get("/capabilities", response_model=TmaCapabilitiesResponse)
def tma_capabilities(user: TelegramUser) -> TmaCapabilitiesResponse:
    return TmaCapabilitiesResponse(
        telegramUserId=user.telegram_id,
        debugMenu=_is_diagnostic_user(user.telegram_id),
        interactiveTravel=_interactive_travel_allowed(user),
    )


def _interactive_travel_session_store():
    settings = get_settings()
    return get_interactive_travel_session_store(
        getattr(
            settings,
            "interactive_travel_owner_store_path",
            DEFAULT_INTERACTIVE_TRAVEL_STORE_PATH,
        ),
        getattr(
            settings,
            "interactive_travel_owner_max_records",
            DEFAULT_INTERACTIVE_TRAVEL_MAX_RECORDS,
        ),
        getattr(
            settings,
            "interactive_travel_owner_retention_seconds",
            int(DEFAULT_INTERACTIVE_TRAVEL_RETENTION.total_seconds()),
        ),
    )


def _interactive_travel_pet_fingerprint(payload: Any) -> str:
    if payload.petId is not None:
        return fingerprint_payload({"petId": payload.petId})
    identity = {
        "name": payload.name,
        "description": payload.description,
        "characterBible": payload.characterBible,
        "assetImages": payload.assetImages,
    }
    return fingerprint_payload(identity)


def _interactive_travel_start_fingerprint(payload: StartInteractiveTravelRequest) -> str:
    return fingerprint_payload(payload.model_dump(mode="json", exclude_none=True))


def _interactive_travel_continue_fingerprint(payload: ContinueInteractiveTravelRequest) -> str:
    request_payload = payload.model_dump(mode="json", exclude_none=True)
    request_payload["travel"] = {
        "stateFingerprint": interactive_travel_state_fingerprint(payload.travel)
    }
    return fingerprint_payload(request_payload)


def _interactive_travel_session_http_exception(
    exc: InteractiveTravelSessionError,
) -> HTTPException:
    if isinstance(exc, InteractiveTravelOwnerMissingError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INTERACTIVE_TRAVEL_OWNER_UNKNOWN",
                "message": "Это старое путешествие нельзя безопасно продолжить. Начните новое.",
            },
        )
    if isinstance(exc, InteractiveTravelSessionOwnerMismatchError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "INTERACTIVE_TRAVEL_OWNER_MISMATCH",
                "message": "Это путешествие принадлежит другому пользователю.",
            },
        )
    if isinstance(exc, InteractiveTravelPetMismatchError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INTERACTIVE_TRAVEL_PET_MISMATCH",
                "message": "Это путешествие относится к другому персонажу.",
            },
        )
    if isinstance(exc, InteractiveTravelSessionCapacityError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "INTERACTIVE_TRAVEL_CAPACITY_REACHED",
                "message": "Хранилище путешествий временно переполнено.",
            },
            headers={"Retry-After": "60"},
        )
    if isinstance(exc, InteractiveTravelActiveError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INTERACTIVE_TRAVEL_ALREADY_ACTIVE",
                "message": "Сначала завершите или отмените текущее путешествие.",
                "travelId": exc.travel_id,
            },
        )
    if isinstance(exc, InteractiveTravelSessionCancelledError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INTERACTIVE_TRAVEL_CANCELLED",
                "message": "Путешествие уже сброшено.",
            },
        )
    if isinstance(exc, InteractiveTravelSessionCompletedError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INTERACTIVE_TRAVEL_ALREADY_COMPLETED",
                "message": "Путешествие уже завершено.",
            },
        )
    if isinstance(exc, InteractiveTravelStateConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "INTERACTIVE_TRAVEL_STATE_CONFLICT",
                "message": "Состояние путешествия уже изменилось в другой вкладке.",
            },
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "INTERACTIVE_TRAVEL_STATE_UNAVAILABLE",
            "message": "Не удалось надёжно сохранить состояние путешествия.",
        },
        headers={"Retry-After": "3"},
    )


def _authorize_interactive_travel_side_effect(
    user: TelegramUserContext,
    **authorization: Any,
) -> str:
    try:
        return _interactive_travel_session_store().authorize_side_effect(
            telegram_id=user.telegram_id,
            **authorization,
        )
    except InteractiveTravelSessionError as exc:
        raise _interactive_travel_session_http_exception(exc) from None


def _interactive_travel_media_request_key(
    kind: str,
    travel_id: str,
    part_number: int,
) -> str:
    digest = hashlib.sha256(f"{travel_id}:{part_number}".encode()).hexdigest()
    return f"interactive-{kind}:{digest}"


def _interactive_travel_start_rate_request_key(request_fingerprint: str) -> str:
    return f"interactive-start:{request_fingerprint}"


def _interactive_travel_continue_rate_request_key(request_fingerprint: str) -> str:
    return f"interactive-continue:{request_fingerprint}"


def _require_diagnostic_user(user: TelegramUser) -> None:
    settings = get_settings()
    if _is_diagnostic_user(user.telegram_id) or (
        getattr(settings, "allow_dev_tma_auth", False) and user.telegram_id == 0
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "DEBUG_FORBIDDEN", "message": "Недостаточно прав."},
    )


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


def _cleanup_generated_asset_directory(asset_directory: Path, owner_name: str) -> None:
    if not generated_media_cleanup_is_enabled(
        getattr(get_settings(), "generated_media_cleanup_enabled", None)
    ):
        # Failed-job and incomplete-travel rows are the only durable proof that
        # their whole directory is disposable. Make callers retain those rows
        # while cleanup is disabled so re-enabling can retry safely.
        raise RuntimeError("generated media cleanup is disabled")
    configured_root = Path(
        getattr(get_settings(), "storage_health_generated_assets_path", "static/generated")
    ).expanduser()
    if not configured_root.is_absolute():
        configured_root = Path.cwd() / configured_root
    configured_root = configured_root.resolve(strict=False)
    runtime_root = asset_directory.parent.resolve(strict=False)
    if configured_root != runtime_root:
        raise RuntimeError("configured generated-assets path differs from media writer root")
    cleanup_owned_generated_asset_directory(
        generated_root=configured_root,
        asset_directory=asset_directory,
        expected_owner_name=owner_name,
    )


def _cleanup_failed_generation_job_assets(job_id: str) -> None:
    asset_set_id = generation_job_asset_set_id(job_id)
    _cleanup_generated_asset_directory(
        generated_dir_for(asset_set_id),
        str(asset_set_id),
    )


def _generation_job_service() -> GenerationJobService:
    global generation_job_service
    if generation_job_service is None:
        settings = get_settings()
        generation_job_service = GenerationJobService(
            store=GenerationJobStore(
                getattr(
                    settings,
                    "generation_job_store_path",
                    "data/push/generation_jobs.sqlite3",
                )
            ),
            image_workers=getattr(settings, "generation_image_workers", 4),
            video_workers=getattr(settings, "generation_video_workers", 2),
            generate_images=lambda description, image_provider: generate_pet_image_asset_set(
                description,
                image_provider=image_provider,
            ),
            generate_images_for_job=(
                lambda job_id, description, image_provider, existing_asset_set_id: (
                    generate_pet_image_asset_set(
                        description,
                        image_provider=image_provider,
                        asset_set_id=(
                            uuid.UUID(existing_asset_set_id)
                            if existing_asset_set_id is not None
                            else generation_job_asset_set_id(job_id)
                        ),
                    )
                )
            ),
            generate_video=lambda image_set: generate_pet_video_for_image_asset_set(image_set),
            generate_background_image=lambda image_set, image_provider: generate_pet_sad_scene_path(
                image_set,
                image_provider=image_provider,
            ),
            generate_background_video=lambda image_set, sad_scene_path: (
                generate_pet_sad_video_for_image_asset_set(image_set, sad_scene_path)
            ),
            generate_happy_image=lambda image_set, image_provider: generate_pet_happy_scene_path(
                image_set,
                image_provider=image_provider,
            ),
            generate_happy_video=lambda image_set, happy_scene_path: (
                generate_pet_happy_video_for_image_asset_set(image_set, happy_scene_path)
            ),
            build_response=build_pet_asset_set_response,
            build_failure=_build_generation_failure,
            cleanup_failed_job_assets=_cleanup_failed_generation_job_assets,
            generate_comparison_images=(
                (
                    lambda description, primary_image_set: generate_kandinsky_pet_comparison_assets(
                        description,
                        primary_image_set.character_bible,
                        asset_set_id=comparison_asset_set_id(primary_image_set.asset_set_id),
                    )
                )
                if getattr(settings, "pet_comparison_enabled", False)
                else None
            ),
            notify_ready=send_generation_ready_notification,
            max_queued_jobs=getattr(settings, "generation_max_queued_jobs", 40),
            stuck_after=timedelta(seconds=getattr(settings, "generation_job_stuck_seconds", 1800)),
        )
    return generation_job_service


def submit_generation_job(
    description: str,
    user: TelegramUserContext,
    request_key: str | None = None,
) -> GeneratePetJobResponse:
    try:
        return _generation_job_service().submit(
            description,
            user,
            "openai",
            request_key=request_key,
        )
    except GenerationIdempotencyConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "GENERATION_IDEMPOTENCY_CONFLICT",
                "message": "Этот ключ запроса уже использован для другого питомца.",
            },
        ) from None
    except GenerationOwnerActiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "GENERATION_ALREADY_ACTIVE",
                "message": "Создание питомца уже выполняется. Дождитесь его завершения.",
                "activeJobId": exc.job_id,
                "activeDescription": exc.description,
            },
            headers={"Retry-After": "15"},
        ) from None
    except GenerationQueueFullError:
        notify_ops(
            "generation:queue-full",
            "Generation queue is full. New pet requests receive HTTP 503.",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "GENERATION_QUEUE_FULL",
                "message": (
                    "Сейчас создаётся слишком много питомцев. Попробуйте через несколько минут."
                ),
                "retryAfterSeconds": 120,
            },
            headers={"Retry-After": "120"},
        ) from None


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


def find_generation_job_by_request_key(
    request_key: str,
    user: TelegramUserContext,
    description: str | None = None,
    image_provider: str | None = None,
) -> GeneratePetJobResponse | None:
    try:
        return _generation_job_service().find_by_request_key(
            request_key,
            user.telegram_id,
            description,
            image_provider,
        )
    except GenerationIdempotencyConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "GENERATION_IDEMPOTENCY_CONFLICT",
                "message": "Этот ключ запроса уже использован для другого питомца.",
            },
        ) from None


def shutdown_generation_jobs(*, wait: bool = False) -> None:
    global generation_job_service
    if generation_job_service is not None:
        generation_job_service.shutdown(wait=wait)
        generation_job_service = None


def start_generation_jobs() -> None:
    _generation_job_service()


def generation_job_runtime_status() -> dict[str, int]:
    return _generation_job_service().runtime_status()


@router.post(
    "/generate-pet",
    response_model=GeneratePetJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_pet(
    payload: GeneratePetRequest,
    user: TelegramUser,
    idempotency_key: GenerationIdempotencyKey,
) -> GeneratePetJobResponse:
    description = payload.description.strip()
    settings = get_settings()
    admission_context = _generation_admission_lock(
        getattr(settings, "generation_job_store_path", "data/push/generation_jobs.sqlite3")
    )
    with admission_context:
        existing = find_generation_job_by_request_key(
            idempotency_key,
            user,
            description,
            "openai",
        )
        if existing is not None:
            return existing
        if not str(getattr(settings, "openai_api_key", None) or "").strip():
            raise public_error(
                "MISSING_OPENAI_API_KEY",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                include_diagnostic=_is_diagnostic_user(user.telegram_id),
            ) from None
        reservation = check_rate_limit(
            "generation",
            user,
            request_key=f"generate-pet:{idempotency_key}",
        )
        try:
            return submit_generation_job(description, user, request_key=idempotency_key)
        except Exception:
            idempotency_lookup_failed = False
            try:
                existing = find_generation_job_by_request_key(
                    idempotency_key,
                    user,
                    description,
                    "openai",
                )
            except Exception:
                idempotency_lookup_failed = True
                logger.exception(
                    "generation_refund_idempotency_lookup_failed ownerId=%s",
                    user.telegram_id,
                )
            else:
                if existing is not None:
                    if reservation is not None:
                        refund_rate_limit(reservation)
                    return existing
            if reservation is not None and not idempotency_lookup_failed:
                refund_rate_limit(reservation)
            raise


@router.get("/generate-pet/jobs/{job_id}", response_model=GeneratePetJobResponse)
def generation_job(job_id: str, user: TelegramUser) -> GeneratePetJobResponse:
    return get_generation_job(job_id, user)


@router.get("/generation-stats", response_model=GenerationStatsResponse)
def generation_stats(
    user: TelegramUser,
    days: int = Query(default=30, ge=1, le=365),
    mine: bool = True,
) -> GenerationStatsResponse:
    if not _is_diagnostic_user(user.telegram_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "GENERATION_STATS_FORBIDDEN", "message": "Недостаточно прав."},
        )
    payload = _generation_job_service().metrics_summary(
        days=days,
        owner_id=user.telegram_id if mine else None,
    )
    return GenerationStatsResponse.model_validate(payload)


@router.post(
    "/chat",
    response_model=LocalChatResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(_llm_request_admission)],
)
def chat(payload: LocalChatRequest, user: TelegramUser) -> LocalChatResponse:
    check_rate_limit("chat", user)
    payload = _without_untrusted_debug(payload, user)
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
    request_key: str | None = None
    if payload.snapshotWriterId is not None and payload.snapshotRevision is not None:
        revision_identity = f"{payload.snapshotWriterId}:{payload.snapshotRevision}"
        request_key = f"push-snapshot:{hashlib.sha256(revision_identity.encode()).hexdigest()}"
    check_rate_limit("push_snapshot_attempt", user)
    check_rate_limit("push_snapshot", user, request_key=request_key)
    try:
        return register_push_snapshot(user, payload)
    except TelegramPushRecordTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "PUSH_RECORD_TOO_LARGE",
                "message": "Снимок питомца слишком большой. Обновите приложение и повторите.",
                "maxBytes": exc.max_bytes,
            },
        ) from None
    except TelegramPushStoreCapacityError:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail={
                "code": "PUSH_STORE_CAPACITY",
                "message": "Хранилище временно переполнено. Попробуйте позже.",
            },
            headers={"Retry-After": "300"},
        ) from None


@router.delete(
    "/push/snapshot/{pet_id}",
    response_model=LocalPetPushSnapshotDeleteResponse,
)
def delete_push_snapshot(
    pet_id: Annotated[str, PathParameter(min_length=1, max_length=120)],
    user: TelegramUser,
) -> LocalPetPushSnapshotDeleteResponse:
    normalized_pet_id = pet_id.strip()
    if not normalized_pet_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INVALID_PET_ID",
                "message": "Идентификатор питомца не может быть пустым.",
            },
        )
    check_rate_limit("push_snapshot_delete", user)
    try:
        unregistered = unregister_push_snapshot(user.telegram_id, normalized_pet_id)
    except TelegramPushRecordTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "PUSH_RECORD_TOO_LARGE",
                "message": "Не удалось сохранить отметку сброса питомца.",
                "maxBytes": exc.max_bytes,
            },
        ) from None
    except TelegramPushStoreCapacityError:
        raise HTTPException(
            status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
            detail={
                "code": "PUSH_STORE_CAPACITY",
                "message": "Хранилище временно переполнено. Попробуйте позже.",
            },
            headers={"Retry-After": "300"},
        ) from None
    return LocalPetPushSnapshotDeleteResponse(
        unregistered=unregistered,
        petId=normalized_pet_id,
    )


@router.post(
    "/chat/ambient",
    response_model=LocalChatResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(_llm_request_admission)],
)
def ambient_chat(
    payload: LocalAmbientRequest,
    user: TelegramUser,
) -> LocalChatResponse:
    check_rate_limit("chat", user)
    payload = _without_untrusted_debug(payload, user)
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
    dependencies=[Depends(_llm_request_admission)],
)
def extract_lite_facts(
    payload: LiteFactExtractionRequest,
    user: TelegramUser,
) -> LiteFactExtractionResponse:
    check_rate_limit("lite_facts", user)
    payload = _without_untrusted_debug(payload, user)
    patch, debug = extract_lite_overlay_patch_from_reply(payload)
    return LiteFactExtractionResponse(liteOverlayPatch=patch, debug=debug)


@router.post(
    "/chat/memory-extract",
    response_model=MemoryExtractionResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(_llm_request_admission)],
)
def extract_memory(
    payload: MemoryExtractionRequest,
    user: TelegramUser,
) -> MemoryExtractionResponse:
    check_rate_limit("memory", user)
    payload = _without_untrusted_debug(payload, user)
    return extract_user_memory_operations(payload)


@router.post(
    "/chat/memory-consolidate",
    response_model=MemoryConsolidationResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(_llm_request_admission)],
)
def consolidate_memory(
    payload: MemoryConsolidationRequest,
    user: TelegramUser,
) -> MemoryConsolidationResponse:
    check_rate_limit("memory", user)
    payload = _without_untrusted_debug(payload, user)
    return consolidate_user_memory(payload)


@router.post(
    "/chat/proactive",
    response_model=LocalProactiveResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(_llm_request_admission)],
)
def proactive_chat(
    payload: LocalProactiveRequest,
    user: TelegramUser,
) -> LocalProactiveResponse:
    check_rate_limit("memory", user)
    payload = _without_untrusted_debug(payload, user)
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


@router.post(
    "/travel/interactive/suggestions",
    response_model=InteractiveTravelSuggestionsResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(_llm_request_admission)],
)
def interactive_travel_suggestions(
    payload: InteractiveTravelSuggestionsRequest,
    user: TelegramUser,
) -> InteractiveTravelSuggestionsResponse:
    _require_interactive_travel_pilot(user)
    check_rate_limit("chat", user)
    payload = _without_untrusted_debug(payload, user)
    prompt_log_token = set_prompt_log_context({"endpoint": "/api/travel/interactive/suggestions"})
    try:
        return generate_interactive_travel_suggestions(
            pet=payload.pet,
            include_debug=payload.includeDebug,
        )
    except HTTPException:
        raise
    except Exception as exc:
        code = chat_error_code(exc)
        raise ai_failure_http_exception(
            "/api/travel/interactive/suggestions",
            "interactive_travel_suggestions_failed",
            code,
            travel_error_message(code),
            exc,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from exc
    finally:
        reset_prompt_log_context(prompt_log_token)


@router.get(
    "/travel/interactive/debug/demo",
    response_model=InteractiveTravelDemoResponse,
)
def interactive_travel_demo(user: TelegramUser) -> InteractiveTravelDemoResponse:
    _require_diagnostic_user(user)
    return read_interactive_travel_demo()


@router.post(
    "/travel/interactive/start",
    response_model=InteractiveTravelResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(_llm_request_admission)],
)
def interactive_travel_start(
    payload: StartInteractiveTravelRequest,
    user: TelegramUser,
) -> InteractiveTravelResponse:
    _require_interactive_travel_pilot(user)
    payload = _without_untrusted_debug(payload, user)
    session_store = _interactive_travel_session_store()
    request_fingerprint = _interactive_travel_start_fingerprint(payload)
    try:
        attempt = session_store.preflight_start(
            telegram_id=user.telegram_id,
            pet_fingerprint=_interactive_travel_pet_fingerprint(payload.pet),
            request_fingerprint=request_fingerprint,
        )
    except InteractiveTravelSessionError as exc:
        raise _interactive_travel_session_http_exception(exc) from None
    if attempt.replay is not None:
        return attempt.replay
    rate_reservation = check_rate_limit(
        "interactive_travel",
        user,
        request_key=_interactive_travel_start_rate_request_key(request_fingerprint),
    )
    prompt_log_token = set_prompt_log_context({"endpoint": "/api/travel/interactive/start"})
    try:
        response = start_interactive_travel(
            pet=payload.pet,
            destination=payload.destination,
            travel_id=attempt.travel_id,
            history=payload.history,
            memory_context=payload.memoryContext,
            include_debug=payload.includeDebug,
        )
        return session_store.commit_start(attempt, response).response
    except InteractiveTravelSessionError as exc:
        _refund_optional_rate_limit(rate_reservation)
        raise _interactive_travel_session_http_exception(exc) from None
    except HTTPException:
        _refund_optional_rate_limit(rate_reservation)
        raise
    except Exception as exc:
        _refund_optional_rate_limit(rate_reservation)
        code = chat_error_code(exc)
        raise ai_failure_http_exception(
            "/api/travel/interactive/start",
            "interactive_travel_start_failed",
            code,
            travel_error_message(code),
            exc,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from exc
    finally:
        reset_prompt_log_context(prompt_log_token)


@router.post(
    "/travel/interactive/illustrate",
    response_model=InteractiveTravelIllustrationResponse,
    dependencies=[Depends(_media_request_admission)],
)
def interactive_travel_illustrate(
    payload: IllustrateInteractiveTravelPartRequest,
    user: TelegramUser,
) -> InteractiveTravelIllustrationResponse:
    _require_interactive_travel_pilot(user)
    authorization = {
        "travel_id": payload.travelId,
        "kind": "illustrate",
        "pet_fingerprint": _interactive_travel_pet_fingerprint(payload.pet),
        "destination": payload.destination,
        "part_number": payload.partNumber,
        "title": payload.title,
        "story_text": payload.storyText,
    }
    authorization_fingerprint = _authorize_interactive_travel_side_effect(
        user,
        **authorization,
    )
    check_rate_limit(
        "interactive_travel",
        user,
        request_key=_interactive_travel_media_request_key(
            "image", payload.travelId, payload.partNumber
        ),
    )
    prompt_log_token = set_prompt_log_context({"endpoint": "/api/travel/interactive/illustrate"})
    try:
        response = illustrate_interactive_travel_part(
            pet=payload.pet,
            travel_id=payload.travelId,
            destination=payload.destination,
            part_number=payload.partNumber,
            title=payload.title,
            story_text=payload.storyText,
        )
        if response.partNumber != payload.partNumber:
            raise _interactive_travel_session_http_exception(
                InteractiveTravelStateConflictError(payload.travelId)
            )
        _authorize_interactive_travel_side_effect(
            user,
            expected_state_fingerprint=authorization_fingerprint,
            **authorization,
        )
        patch_interactive_travel_finale_media(
            payload.travelId,
            part_number=response.partNumber,
            image_url=response.imageUrl,
        )
        return response
    except HTTPException:
        raise
    except Exception as exc:
        code = chat_error_code(exc)
        raise ai_failure_http_exception(
            "/api/travel/interactive/illustrate",
            "interactive_travel_illustration_failed",
            code,
            travel_error_message(code),
            exc,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from exc
    finally:
        reset_prompt_log_context(prompt_log_token)


@router.post(
    "/travel/interactive/animate",
    response_model=InteractiveTravelAnimationResponse,
    dependencies=[Depends(_media_request_admission)],
)
def interactive_travel_animate(
    payload: AnimateInteractiveTravelPartRequest,
    user: TelegramUser,
) -> InteractiveTravelAnimationResponse:
    _require_interactive_travel_pilot(user)
    authorization = {
        "travel_id": payload.travelId,
        "kind": "animate",
        "part_number": payload.partNumber,
    }
    authorization_fingerprint = _authorize_interactive_travel_side_effect(
        user,
        **authorization,
    )
    check_rate_limit(
        "interactive_travel",
        user,
        request_key=_interactive_travel_media_request_key(
            "video", payload.travelId, payload.partNumber
        ),
    )
    prompt_log_token = set_prompt_log_context({"endpoint": "/api/travel/interactive/animate"})
    try:
        response = animate_interactive_travel_part(
            travel_id=payload.travelId,
            part_number=payload.partNumber,
        )
        if response.partNumber != payload.partNumber:
            raise _interactive_travel_session_http_exception(
                InteractiveTravelStateConflictError(payload.travelId)
            )
        _authorize_interactive_travel_side_effect(
            user,
            expected_state_fingerprint=authorization_fingerprint,
            **authorization,
        )
        patch_interactive_travel_finale_media(
            payload.travelId,
            part_number=response.partNumber,
            video_url=response.videoUrl,
        )
        return response
    except HTTPException:
        raise
    except Exception as exc:
        code = chat_error_code(exc)
        raise ai_failure_http_exception(
            "/api/travel/interactive/animate",
            "interactive_travel_animation_failed",
            code,
            travel_error_message(code),
            exc,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from exc
    finally:
        reset_prompt_log_context(prompt_log_token)


@router.post(
    "/travel/interactive/continue",
    response_model=InteractiveTravelResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(_llm_request_admission)],
)
def interactive_travel_continue(
    payload: ContinueInteractiveTravelRequest,
    user: TelegramUser,
) -> InteractiveTravelResponse:
    _require_interactive_travel_pilot(user)
    payload = _without_untrusted_debug(payload, user)
    session_store = _interactive_travel_session_store()
    request_fingerprint = _interactive_travel_continue_fingerprint(payload)
    try:
        attempt = session_store.preflight_continue(
            telegram_id=user.telegram_id,
            pet_fingerprint=_interactive_travel_pet_fingerprint(payload.pet),
            travel=payload.travel,
            request_fingerprint=request_fingerprint,
        )
    except InteractiveTravelSessionError as exc:
        raise _interactive_travel_session_http_exception(exc) from None
    if attempt.replay is not None:
        return attempt.replay
    rate_reservation = check_rate_limit(
        "interactive_travel",
        user,
        request_key=_interactive_travel_continue_rate_request_key(request_fingerprint),
    )
    prompt_log_token = set_prompt_log_context({"endpoint": "/api/travel/interactive/continue"})
    try:
        response = continue_interactive_travel(
            pet=payload.pet,
            travel=payload.travel,
            advice=payload.advice,
            history=payload.history,
            memory_context=payload.memoryContext,
            include_debug=payload.includeDebug,
        )
        committed = session_store.commit_continue(attempt, response)
        if committed.response.travel.completed:
            try:
                save_interactive_travel_finale(
                    committed.response.travel,
                    telegram_id=user.telegram_id,
                    username=user.username,
                    first_name=user.first_name,
                )
            except (OSError, ValueError):
                logger.exception(
                    "interactive_travel_finale_save_failed travelId=%s",
                    committed.response.travel.travelId,
                )
        return committed.response
    except InteractiveTravelSessionError as exc:
        _refund_optional_rate_limit(rate_reservation)
        raise _interactive_travel_session_http_exception(exc) from None
    except HTTPException:
        _refund_optional_rate_limit(rate_reservation)
        raise
    except Exception as exc:
        _refund_optional_rate_limit(rate_reservation)
        code = chat_error_code(exc)
        raise ai_failure_http_exception(
            "/api/travel/interactive/continue",
            "interactive_travel_continue_failed",
            code,
            travel_error_message(code),
            exc,
            include_diagnostic=_is_diagnostic_user(user.telegram_id),
        ) from exc
    finally:
        reset_prompt_log_context(prompt_log_token)


@router.post("/travel/interactive/finale/capture")
def interactive_travel_finale_capture(
    payload: CaptureInteractiveTravelFinaleRequest,
    user: TelegramUser,
) -> dict[str, bool]:
    _require_interactive_travel_pilot(user)
    authorization = {
        "travel_id": payload.travel.travelId,
        "kind": "finale",
        "travel": payload.travel,
    }
    authorization_fingerprint = _authorize_interactive_travel_side_effect(
        user,
        **authorization,
    )
    save_interactive_travel_finale(
        payload.travel,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
    )
    _authorize_interactive_travel_side_effect(
        user,
        expected_state_fingerprint=authorization_fingerprint,
        **authorization,
    )
    return {"saved": True}


@router.post("/travel/interactive/{travel_id}/debug/reset")
def interactive_travel_debug_reset(
    travel_id: str,
    user: TelegramUser,
) -> dict[str, bool]:
    _require_interactive_travel_pilot(user)
    _require_diagnostic_user(user)
    try:
        _interactive_travel_session_store().cancel(travel_id, user.telegram_id)
    except InteractiveTravelSessionError as exc:
        raise _interactive_travel_session_http_exception(exc) from None
    reset_interactive_travel_generation(travel_id)
    settings = get_settings()
    get_rate_limiter(
        getattr(settings, "rate_limit_store_path", DEFAULT_RATE_LIMIT_STORE_PATH)
    ).clear("interactive_travel", user.telegram_id)
    return {"reset": True}


@router.post("/travel/interactive/{travel_id}/cancel")
def interactive_travel_cancel(
    travel_id: Annotated[
        str,
        PathParameter(
            min_length=20,
            max_length=160,
            pattern=r"^interactive-travel-[A-Za-z0-9_-]+$",
        ),
    ],
    user: TelegramUser,
) -> dict[str, bool]:
    _require_interactive_travel_pilot(user)
    try:
        _interactive_travel_session_store().cancel(travel_id, user.telegram_id)
    except InteractiveTravelSessionError as exc:
        raise _interactive_travel_session_http_exception(exc) from None
    cancel_interactive_travel_generation(travel_id)
    return {"cancelled": True}
