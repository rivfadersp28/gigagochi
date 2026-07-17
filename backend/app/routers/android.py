from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any, Literal, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import get_settings
from app.dependencies import get_google_account_identity
from app.schemas import (
    GeneratePetJobResponse,
    InteractiveTravelResult,
    LocalChatRequest,
    LocalChatResponse,
    LocalPetChatContext,
    OutfitSimplificationResponse,
    TravelVideoPrototypeResponse,
)
from app.services.android_feature_store import (
    AndroidFeatureIdempotencyConflictError,
    AndroidFeatureRequestAttempt,
    AndroidFeatureSessionBusyError,
    AndroidFeatureSessionOutcomeUnknownError,
    AndroidFeatureStore,
    AndroidScheduledStoryRecord,
)
from app.services.background_story_paid_media_budget import (
    consume_background_story_paid_media_budget,
)
from app.services.chat_service import chat_with_local_pet
from app.services.feature_owner import FeatureOwner
from app.services.generation_job_service import (
    GenerationIdempotencyConflictError,
    GenerationJobNotFoundError,
    GenerationJobService,
    GenerationOwnerActiveError,
    GenerationQueueFullError,
)
from app.services.google_auth_session_store import GoogleUserIdentity
from app.services.interactive_travel_service import scheduled_interactive_episode_result
from app.services.outfit_service import (
    encode_outfit_generation_description,
    simplify_outfit_request,
)
from app.services.rate_limit_service import (
    RateLimitExceeded,
    RateLimitReservation,
    get_rate_limiter,
)
from app.services.scheduled_short_story_service import (
    generate_scheduled_short_story_episode,
    run_scheduled_short_story_provider_job,
)
from app.services.travel_video_prototype_service import (
    TravelVideoPrototypeIdempotencyConflictError,
    TravelVideoPrototypeNotFoundError,
    create_travel_video_prototype_for_owner,
    generate_travel_video_prototype_for_owner,
    read_travel_video_prototype_for_owner,
    should_resume_travel_video_prototype_for_owner,
    travel_video_job_id_for_owner,
)

router = APIRouter(prefix="/api/android", tags=["android-features"], include_in_schema=False)
GoogleIdentity = Annotated[GoogleUserIdentity, Depends(get_google_account_identity)]
UUID4_PATTERN = r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
RequestKey = Annotated[str, Field(min_length=36, max_length=36, pattern=UUID4_PATTERN)]
PetId = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")]
T = TypeVar("T", bound=BaseModel)
DEFAULT_ANDROID_SCHEDULED_STORY_HOUR = 18
DEFAULT_ANDROID_SCHEDULED_STORY_TIMEZONE = "Europe/Moscow"


def _android_scheduled_story_slot(settings: Any, now: datetime) -> datetime | None:
    raw_hours = getattr(
        settings,
        "android_scheduled_story_hours",
        [DEFAULT_ANDROID_SCHEDULED_STORY_HOUR],
    )
    if not isinstance(raw_hours, (list, tuple, set)):
        raw_hours = []
    hours = {
        value
        for value in raw_hours
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 23
    }
    if not hours:
        hours = {DEFAULT_ANDROID_SCHEDULED_STORY_HOUR}
    timezone_name = str(
        getattr(
            settings,
            "android_scheduled_story_timezone",
            DEFAULT_ANDROID_SCHEDULED_STORY_TIMEZONE,
        )
    )
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo(DEFAULT_ANDROID_SCHEDULED_STORY_TIMEZONE)
    local_now = now.astimezone(timezone)
    due_hours = [hour for hour in hours if hour <= local_now.hour]
    if not due_hours:
        return None
    return local_now.replace(
        hour=max(due_hours),
        minute=0,
        second=0,
        microsecond=0,
    ).astimezone(UTC)


class AndroidCreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestKey: RequestKey
    petId: PetId
    description: str = Field(min_length=1, max_length=300)


class AndroidGenerationJobEnvelope(BaseModel):
    requestKey: str | None = None
    petId: str | None = None
    job: GeneratePetJobResponse


class AndroidChatRequest(LocalChatRequest):
    model_config = ConfigDict(extra="forbid")
    requestKey: RequestKey


class AndroidOutfitSimplifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestKey: RequestKey
    request: str = Field(min_length=1, max_length=1000)
    petDescription: str = Field(min_length=1, max_length=300)


class AndroidOutfitJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestKey: RequestKey
    petId: PetId
    prompt: str = Field(min_length=1, max_length=300)
    idleImageUrl: str = Field(min_length=1, max_length=1000)
    sadImageUrl: str = Field(min_length=1, max_length=1000)
    happyImageUrl: str = Field(min_length=1, max_length=1000)


class AndroidTravelVideoJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestKey: RequestKey
    pet: LocalPetChatContext
    prompt: str = Field(min_length=1, max_length=1000)


StoryId = Annotated[
    str,
    Field(min_length=46, max_length=46, pattern=r"^android-story-[a-f0-9]{32}$"),
]
StoryMediaUrl = Annotated[
    str,
    Field(
        min_length=1,
        max_length=1000,
        pattern=r"^/static/[A-Za-z0-9_./-]+(?:\?v=[A-Za-z0-9_-]{1,64})?$",
    ),
]


class AndroidDueStoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pet: LocalPetChatContext

    @model_validator(mode="after")
    def require_pet_id(self) -> AndroidDueStoryRequest:
        if self.pet.petId is None:
            raise ValueError("petId is required")
        return self


class AndroidScheduledStoryChoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestKey: RequestKey
    choice: str = Field(min_length=1, max_length=280)


StoryChoice = Annotated[str, Field(min_length=1, max_length=280)]


class AndroidScheduledStory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    storyId: StoryId
    petId: PetId
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=700)
    question: str = Field(min_length=1, max_length=280)
    choices: list[StoryChoice] = Field(min_length=4, max_length=4)
    createdAt: datetime
    imageUrl: StoryMediaUrl | None = None
    videoUrl: StoryMediaUrl | None = None
    selectedChoice: str | None = Field(default=None, min_length=1, max_length=280)
    result: InteractiveTravelResult | None = None
    resultImageUrl: StoryMediaUrl | None = None
    resultVideoUrl: StoryMediaUrl | None = None

    @model_validator(mode="after")
    def validate_selection(self) -> AndroidScheduledStory:
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("story choices must be unique")
        if (self.selectedChoice is None) != (self.result is None):
            raise ValueError("story selection and result must appear together")
        if self.selectedChoice is not None and self.selectedChoice not in self.choices:
            raise ValueError("selected story choice is invalid")
        if self.selectedChoice is None and (
            self.resultImageUrl is not None or self.resultVideoUrl is not None
        ):
            raise ValueError("story result media requires a selection")
        return self


class AndroidDueStoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    story: AndroidScheduledStory | None = None


@lru_cache(maxsize=1)
def get_android_feature_store() -> AndroidFeatureStore:
    return AndroidFeatureStore(get_settings().android_feature_store_path)


def get_android_generation_service() -> GenerationJobService:
    # The durable worker pool remains single/shared with TMA; only HTTP identity adaptation differs.
    from app.routers.tma import _generation_job_service

    return _generation_job_service()


FeatureStore = Annotated[AndroidFeatureStore, Depends(get_android_feature_store)]
GenerationService = Annotated[GenerationJobService, Depends(get_android_generation_service)]


def _owner(identity: GoogleUserIdentity) -> FeatureOwner:
    return FeatureOwner.from_google(identity)


def _scheduled_story_id(owner: FeatureOwner, pet_id: str, slot_utc: str) -> str:
    digest = hashlib.sha256(f"{owner.storage_key}\0{pet_id}\0{slot_utc}".encode()).hexdigest()[:32]
    return f"android-story-{digest}"


def _story_episode(record: AndroidScheduledStoryRecord) -> dict[str, Any]:
    if record.episode_json is None:
        raise ValueError("scheduled story is not ready")
    value = json.loads(record.episode_json)
    if not isinstance(value, dict):
        raise ValueError("scheduled story episode is invalid")
    return value


def _scheduled_story_model(record: AndroidScheduledStoryRecord) -> AndroidScheduledStory:
    episode = _story_episode(record)
    choices = episode.get("choices")
    if (
        not isinstance(choices, list)
        or len(choices) != 4
        or any(not isinstance(choice, str) or not choice.strip() for choice in choices)
        or len(set(choices)) != 4
    ):
        raise ValueError("scheduled story choices are invalid")
    selected = record.selected_choice
    if selected is not None and selected not in choices:
        raise ValueError("scheduled story selected choice is invalid")
    result = (
        InteractiveTravelResult.model_validate_json(record.result_json)
        if record.result_json is not None
        else None
    )
    selected_index = choices.index(selected) if selected is not None else None
    outcome_images = episode.get("outcomeImageUrls")
    outcome_videos = episode.get("outcomeVideoUrls")
    outcomes = episode.get("outcomes")
    if (
        not isinstance(outcomes, list)
        or len(outcomes) != 4
        or any(not isinstance(value, str) or not value.strip() for value in outcomes)
        or not isinstance(outcome_images, list)
        or len(outcome_images) != 4
        or not isinstance(outcome_videos, list)
        or len(outcome_videos) != 4
    ):
        raise ValueError("scheduled story outcomes are invalid")
    return AndroidScheduledStory(
        storyId=record.story_id,
        petId=record.pet_id,
        title=episode["title"],
        text=episode["storyText"],
        question=episode["question"],
        choices=choices,
        createdAt=record.created_at,
        imageUrl=episode.get("situationImageUrl"),
        videoUrl=episode.get("situationVideoUrl"),
        selectedChoice=selected,
        result=result,
        resultImageUrl=(
            outcome_images[selected_index]
            if selected_index is not None and isinstance(outcome_images, list)
            else None
        ),
        resultVideoUrl=(
            outcome_videos[selected_index]
            if selected_index is not None and isinstance(outcome_videos, list)
            else None
        ),
    )


def _story_now() -> datetime:
    return datetime.now(UTC)


def _generate_scheduled_story_background(
    *,
    store: AndroidFeatureStore,
    owner: FeatureOwner,
    pet: LocalPetChatContext,
    pet_id: str,
    slot_utc: str,
    story_id: str,
    created_at: str,
) -> None:
    try:
        settings = get_settings()

        def run_provider_job(label: str, operation: Callable[[], Any]) -> Any:
            def admitted_operation() -> Any:
                consume_background_story_paid_media_budget(settings, stage=label)
                return operation()

            return run_scheduled_short_story_provider_job(label, admitted_operation)

        episode = generate_scheduled_short_story_episode(
            pet=pet,
            story_id=story_id,
            run_provider_job=run_provider_job,
        )
        plan = episode.plan
        episode_json = json.dumps(
            {
                "title": plan["title"],
                "storyText": plan["storyText"],
                "question": plan["question"],
                "choices": plan["choices"],
                "outcomes": plan["outcomes"],
                "correctChoice": plan["correctChoice"],
                "situationImageUrl": episode.situation_image_url,
                "situationVideoUrl": episode.situation_video_url,
                "outcomeImageUrls": list(episode.outcome_image_urls),
                "outcomeVideoUrls": list(episode.outcome_video_urls),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        store.commit_scheduled_story(
            owner=owner,
            pet_id=pet_id,
            slot_utc=slot_utc,
            story_id=story_id,
            episode_json=episode_json,
            created_at=created_at,
        )
    except Exception:
        store.mark_scheduled_story_outcome_unknown(
            owner=owner,
            pet_id=pet_id,
            slot_utc=slot_utc,
            story_id=story_id,
        )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _request_payload(payload: BaseModel) -> dict[str, Any]:
    return payload.model_dump(mode="json", exclude={"requestKey", "includeDebug"})


def _idempotency_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AndroidFeatureIdempotencyConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "IDEMPOTENCY_CONFLICT", "message": "Ключ уже использован."},
        )
    if isinstance(exc, AndroidFeatureSessionBusyError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "REQUEST_IN_PROGRESS", "message": "Запрос ещё обрабатывается."},
            headers={"Retry-After": "5"},
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "OUTCOME_UNKNOWN",
            "message": "Исход предыдущего вызова неизвестен; автоматически повторять нельзя.",
        },
    )


def _begin_sync(
    store: AndroidFeatureStore,
    *,
    owner: FeatureOwner,
    operation: str,
    request_key: str,
    payload: dict[str, Any],
) -> AndroidFeatureRequestAttempt:
    try:
        attempt = store.begin_request(
            owner=owner,
            operation=operation,
            request_key=request_key,
            payload=payload,
        )
    except AndroidFeatureIdempotencyConflictError as exc:
        raise _idempotency_error(exc) from None
    if attempt.state == "in_progress":
        raise _idempotency_error(AndroidFeatureSessionBusyError(request_key))
    if attempt.state == "outcome_unknown":
        raise _idempotency_error(AndroidFeatureSessionOutcomeUnknownError(request_key))
    return attempt


def _commit_model(
    store: AndroidFeatureStore,
    *,
    owner: FeatureOwner,
    operation: str,
    request_key: str,
    payload: dict[str, Any],
    response: BaseModel,
) -> None:
    store.commit_response(
        owner=owner,
        operation=operation,
        request_key=request_key,
        payload=payload,
        response_json=response.model_dump_json(),
    )


def _check_rate_limit(
    bucket: Literal["generation", "chat", "interactive_travel"],
    owner: FeatureOwner,
    *,
    request_key: str,
) -> RateLimitReservation | None:
    settings = get_settings()
    if not settings.enable_in_memory_rate_limit:
        return None
    if bucket == "generation":
        limit, window_seconds = settings.generation_rate_limit_per_day, 86_400
    elif bucket == "interactive_travel":
        limit, window_seconds = settings.interactive_travel_rate_limit_per_day, 86_400
    else:
        limit, window_seconds = settings.chat_rate_limit_per_hour, 3_600
    from datetime import timedelta

    try:
        return get_rate_limiter(settings.rate_limit_store_path).check(
            bucket,
            owner.storage_key,
            limit,
            timedelta(seconds=window_seconds),
            request_key=request_key,
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMITED", "message": "Слишком много запросов."},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None


def _check_rate_limit_before_downstream(
    store: AndroidFeatureStore,
    *,
    attempt: AndroidFeatureRequestAttempt,
    owner: FeatureOwner,
    operation: str,
    request_key: str,
    bucket: Literal["generation", "chat", "interactive_travel"],
) -> RateLimitReservation | None:
    """Release only reservations rejected before any downstream side effect starts."""
    try:
        return _check_rate_limit(
            bucket,
            owner,
            request_key=f"android:{operation}:{request_key}",
        )
    except Exception:
        if attempt.created:
            store.abort_request(
                owner=owner,
                operation=operation,
                request_key=request_key,
            )
        raise


def _refund_rate(reservation: RateLimitReservation | None) -> None:
    if reservation is not None:
        get_rate_limiter(get_settings().rate_limit_store_path).refund(reservation)


def _abort_pre_dispatch(
    store: AndroidFeatureStore,
    *,
    owner: FeatureOwner,
    operation: str,
    request_key: str,
    reservation: RateLimitReservation | None,
) -> None:
    store.abort_request(owner=owner, operation=operation, request_key=request_key)
    _refund_rate(reservation)


def _generation_attempt(
    *,
    store: AndroidFeatureStore,
    service: GenerationJobService,
    owner: FeatureOwner,
    operation: str,
    request_key: str,
    payload: dict[str, Any],
    pet_id: str,
    description: str,
) -> AndroidGenerationJobEnvelope:
    try:
        attempt = store.begin_request(
            owner=owner,
            operation=operation,
            request_key=request_key,
            payload=payload,
        )
    except AndroidFeatureIdempotencyConflictError as exc:
        raise _idempotency_error(exc) from None
    if attempt.response_json is not None:
        return AndroidGenerationJobEnvelope.model_validate_json(attempt.response_json)
    if not attempt.created:
        existing = service.find_by_request_key_for_owner(
            request_key,
            owner,
            description,
            "openai",
        )
        if existing is None:
            marker: Exception = (
                AndroidFeatureSessionBusyError(request_key)
                if attempt.state == "in_progress"
                else AndroidFeatureSessionOutcomeUnknownError(request_key)
            )
            raise _idempotency_error(marker)
        envelope = AndroidGenerationJobEnvelope(
            requestKey=request_key,
            petId=pet_id,
            job=existing,
        )
        _commit_model(
            store,
            owner=owner,
            operation=operation,
            request_key=request_key,
            payload=payload,
            response=envelope,
        )
        return envelope
    reservation = _check_rate_limit_before_downstream(
        store,
        attempt=attempt,
        owner=owner,
        operation=operation,
        request_key=request_key,
        bucket="generation",
    )
    try:
        job = service.submit_for_owner(
            description,
            owner,
            "openai",
            request_key=request_key,
        )
    except GenerationIdempotencyConflictError:
        _abort_pre_dispatch(
            store,
            owner=owner,
            operation=operation,
            request_key=request_key,
            reservation=reservation,
        )
        raise _idempotency_error(AndroidFeatureIdempotencyConflictError(request_key)) from None
    except GenerationOwnerActiveError as exc:
        _abort_pre_dispatch(
            store,
            owner=owner,
            operation=operation,
            request_key=request_key,
            reservation=reservation,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "GENERATION_ALREADY_ACTIVE", "activeJobId": exc.job_id},
            headers={"Retry-After": "15"},
        ) from None
    except GenerationQueueFullError:
        _abort_pre_dispatch(
            store,
            owner=owner,
            operation=operation,
            request_key=request_key,
            reservation=reservation,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "GENERATION_QUEUE_FULL", "message": "Очередь заполнена."},
        ) from None
    except Exception:
        # The submit boundary may have raised after accepting the idempotent job.
        # Reconcile the exact owner/key/payload before declaring ambiguity.
        existing = service.find_by_request_key_for_owner(
            request_key,
            owner,
            description,
            "openai",
        )
        if existing is None:
            raise
        _refund_rate(reservation)
        job = existing
    envelope = AndroidGenerationJobEnvelope(
        requestKey=request_key,
        petId=pet_id,
        job=job,
    )
    _commit_model(
        store,
        owner=owner,
        operation=operation,
        request_key=request_key,
        payload=payload,
        response=envelope,
    )
    return envelope


@router.post(
    "/create/jobs",
    response_model=AndroidGenerationJobEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
def create_job(
    payload: AndroidCreateJobRequest,
    response: Response,
    identity: GoogleIdentity,
    store: FeatureStore,
    service: GenerationService,
) -> AndroidGenerationJobEnvelope:
    _no_store(response)
    return _generation_attempt(
        store=store,
        service=service,
        owner=_owner(identity),
        operation="create",
        request_key=payload.requestKey,
        payload=_request_payload(payload),
        pet_id=payload.petId,
        description=payload.description.strip(),
    )


def _poll_generation(
    job_id: str,
    identity: GoogleUserIdentity,
    service: GenerationJobService,
) -> AndroidGenerationJobEnvelope:
    try:
        job = service.get_for_owner(job_id, _owner(identity))
    except GenerationJobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job not found."},
        ) from None
    return AndroidGenerationJobEnvelope(job=job)


@router.get(
    "/create/jobs/{job_id}",
    response_model=AndroidGenerationJobEnvelope,
    include_in_schema=False,
)
def create_job_status(
    job_id: str,
    response: Response,
    identity: GoogleIdentity,
    service: GenerationService,
) -> AndroidGenerationJobEnvelope:
    _no_store(response)
    return _poll_generation(job_id, identity, service)


@router.post("/chat", response_model=LocalChatResponse, include_in_schema=False)
def chat(
    payload: AndroidChatRequest,
    response: Response,
    identity: GoogleIdentity,
    store: FeatureStore,
) -> LocalChatResponse:
    _no_store(response)
    owner = _owner(identity)
    request_payload = _request_payload(payload)
    attempt = _begin_sync(
        store,
        owner=owner,
        operation="chat",
        request_key=payload.requestKey,
        payload=request_payload,
    )
    if attempt.response_json is not None:
        return LocalChatResponse.model_validate_json(attempt.response_json)
    _check_rate_limit_before_downstream(
        store,
        attempt=attempt,
        owner=owner,
        operation="chat",
        request_key=payload.requestKey,
        bucket="chat",
    )
    chat_payload = LocalChatRequest.model_validate(
        payload.model_dump(exclude={"requestKey"}) | {"includeDebug": False}
    )
    result = chat_with_local_pet(chat_payload)
    _commit_model(
        store,
        owner=owner,
        operation="chat",
        request_key=payload.requestKey,
        payload=request_payload,
        response=result,
    )
    return result


@router.post(
    "/outfit/simplify",
    response_model=OutfitSimplificationResponse,
    include_in_schema=False,
)
def simplify_outfit(
    payload: AndroidOutfitSimplifyRequest,
    response: Response,
    identity: GoogleIdentity,
    store: FeatureStore,
) -> OutfitSimplificationResponse:
    _no_store(response)
    owner = _owner(identity)
    request_payload = _request_payload(payload)
    attempt = _begin_sync(
        store,
        owner=owner,
        operation="outfit-simplify",
        request_key=payload.requestKey,
        payload=request_payload,
    )
    if attempt.response_json is not None:
        return OutfitSimplificationResponse.model_validate_json(attempt.response_json)
    _check_rate_limit_before_downstream(
        store,
        attempt=attempt,
        owner=owner,
        operation="outfit-simplify",
        request_key=payload.requestKey,
        bucket="chat",
    )
    item, display_item, generation_description = simplify_outfit_request(
        payload.request,
        payload.petDescription,
    )
    result = OutfitSimplificationResponse(
        item=item,
        displayItem=display_item,
        generationDescription=generation_description,
    )
    _commit_model(
        store,
        owner=owner,
        operation="outfit-simplify",
        request_key=payload.requestKey,
        payload=request_payload,
        response=result,
    )
    return result


@router.post(
    "/outfit/jobs",
    response_model=AndroidGenerationJobEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
def outfit_job(
    payload: AndroidOutfitJobRequest,
    response: Response,
    identity: GoogleIdentity,
    store: FeatureStore,
    service: GenerationService,
) -> AndroidGenerationJobEnvelope:
    _no_store(response)
    description = encode_outfit_generation_description(
        payload.prompt,
        idle_image_url=payload.idleImageUrl,
        sad_image_url=payload.sadImageUrl,
        happy_image_url=payload.happyImageUrl,
    )
    return _generation_attempt(
        store=store,
        service=service,
        owner=_owner(identity),
        operation="outfit",
        request_key=payload.requestKey,
        payload=_request_payload(payload),
        pet_id=payload.petId,
        description=description,
    )


@router.get(
    "/outfit/jobs/{job_id}",
    response_model=AndroidGenerationJobEnvelope,
    include_in_schema=False,
)
def outfit_job_status(
    job_id: str,
    response: Response,
    identity: GoogleIdentity,
    service: GenerationService,
) -> AndroidGenerationJobEnvelope:
    _no_store(response)
    return _poll_generation(job_id, identity, service)


@router.post(
    "/travel-video/jobs",
    response_model=TravelVideoPrototypeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
def travel_video_job(
    payload: AndroidTravelVideoJobRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    identity: GoogleIdentity,
    store: FeatureStore,
) -> TravelVideoPrototypeResponse:
    _no_store(response)
    owner = _owner(identity)
    request_payload = _request_payload(payload)
    job_id = travel_video_job_id_for_owner(owner, payload.requestKey)
    reservation: RateLimitReservation | None = None
    try:
        attempt = store.begin_request(
            owner=owner,
            operation="travel-video",
            request_key=payload.requestKey,
            payload=request_payload,
            resource_id=job_id,
        )
    except AndroidFeatureIdempotencyConflictError as exc:
        raise _idempotency_error(exc) from None
    if not attempt.created:
        try:
            result = read_travel_video_prototype_for_owner(job_id, owner=owner)
        except TravelVideoPrototypeNotFoundError:
            marker: Exception = (
                AndroidFeatureSessionBusyError(payload.requestKey)
                if attempt.state == "in_progress"
                else AndroidFeatureSessionOutcomeUnknownError(payload.requestKey)
            )
            raise _idempotency_error(marker) from None
    else:
        reservation = _check_rate_limit_before_downstream(
            store,
            attempt=attempt,
            owner=owner,
            operation="travel-video",
            request_key=payload.requestKey,
            bucket="interactive_travel",
        )
        try:
            result = create_travel_video_prototype_for_owner(
                owner=owner,
                prompt=payload.prompt,
                request_key=payload.requestKey,
                pet=payload.pet,
            )
        except TravelVideoPrototypeIdempotencyConflictError:
            _abort_pre_dispatch(
                store,
                owner=owner,
                operation="travel-video",
                request_key=payload.requestKey,
                reservation=reservation,
            )
            raise _idempotency_error(
                AndroidFeatureIdempotencyConflictError(payload.requestKey)
            ) from None
        except Exception as exc:
            try:
                result = read_travel_video_prototype_for_owner(job_id, owner=owner)
            except TravelVideoPrototypeNotFoundError:
                _abort_pre_dispatch(
                    store,
                    owner=owner,
                    operation="travel-video",
                    request_key=payload.requestKey,
                    reservation=reservation,
                )
                raise exc from None
    if should_resume_travel_video_prototype_for_owner(job_id, owner=owner):
        background_tasks.add_task(
            generate_travel_video_prototype_for_owner,
            job_id=job_id,
            owner=owner,
        )
    if attempt.state != "completed":
        _commit_model(
            store,
            owner=owner,
            operation="travel-video",
            request_key=payload.requestKey,
            payload=request_payload,
            response=result,
        )
    return result


@router.get(
    "/travel-video/jobs/{job_id}",
    response_model=TravelVideoPrototypeResponse,
    include_in_schema=False,
)
def travel_video_status(
    job_id: str,
    response: Response,
    background_tasks: BackgroundTasks,
    identity: GoogleIdentity,
) -> TravelVideoPrototypeResponse:
    _no_store(response)
    owner = _owner(identity)
    try:
        result = read_travel_video_prototype_for_owner(job_id, owner=owner)
        if should_resume_travel_video_prototype_for_owner(job_id, owner=owner):
            background_tasks.add_task(
                generate_travel_video_prototype_for_owner,
                job_id=job_id,
                owner=owner,
            )
        return result
    except TravelVideoPrototypeNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "JOB_NOT_FOUND", "message": "Job not found."},
        ) from None


@router.post(
    "/stories/due",
    response_model=AndroidDueStoryResponse,
    include_in_schema=False,
)
def due_scheduled_story(
    payload: AndroidDueStoryRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    identity: GoogleIdentity,
    store: FeatureStore,
) -> AndroidDueStoryResponse:
    _no_store(response)
    settings = get_settings()
    if not getattr(settings, "android_scheduled_story_enabled", True):
        return AndroidDueStoryResponse()
    now = _story_now()
    slot = _android_scheduled_story_slot(settings, now)
    if slot is None:
        return AndroidDueStoryResponse()
    owner = _owner(identity)
    pet_id = str(payload.pet.petId)
    slot_utc = slot.isoformat().replace("+00:00", "Z")
    story_id = _scheduled_story_id(owner, pet_id, slot_utc)
    claim = store.claim_scheduled_story(
        owner=owner,
        pet_id=pet_id,
        slot_utc=slot_utc,
        story_id=story_id,
    )
    if claim.state == "ready":
        return AndroidDueStoryResponse(story=_scheduled_story_model(claim.record))
    if claim.state != "created":
        return AndroidDueStoryResponse()
    background_tasks.add_task(
        _generate_scheduled_story_background,
        store=store,
        owner=owner,
        pet=payload.pet,
        pet_id=pet_id,
        slot_utc=slot_utc,
        story_id=story_id,
        created_at=now.isoformat().replace("+00:00", "Z"),
    )
    return AndroidDueStoryResponse()


@router.post(
    "/stories/{story_id}/choice",
    response_model=AndroidScheduledStory,
    include_in_schema=False,
)
def choose_scheduled_story(
    story_id: StoryId,
    payload: AndroidScheduledStoryChoiceRequest,
    response: Response,
    identity: GoogleIdentity,
    store: FeatureStore,
) -> AndroidScheduledStory:
    _no_store(response)
    owner = _owner(identity)
    try:
        existing = store.read_scheduled_story(owner=owner, story_id=story_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "STORY_NOT_FOUND", "message": "История не найдена."},
        ) from None
    episode = _story_episode(existing)
    choices = episode.get("choices")
    outcomes = episode.get("outcomes")
    if (
        not isinstance(choices, list)
        or payload.choice not in choices
        or not isinstance(outcomes, list)
        or len(outcomes) != len(choices)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "STORY_CHOICE_INVALID", "message": "Вариант недоступен."},
        )
    if existing.selected_choice is not None:
        if existing.selected_choice != payload.choice:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "STORY_ALREADY_CHOSEN", "message": "Выбор уже сохранён."},
            )
        return _scheduled_story_model(existing)
    selected_index = choices.index(payload.choice)
    result = scheduled_interactive_episode_result(
        situation=str(episode.get("storyText") or ""),
        question=str(episode.get("question") or ""),
        outcomes=[str(value) for value in outcomes],
        correct_choice=str(episode.get("correctChoice") or ""),
        selected_choice=payload.choice,
    )
    result.text = str(outcomes[selected_index])
    try:
        selected = store.choose_scheduled_story(
            owner=owner,
            story_id=story_id,
            request_key=payload.requestKey,
            selected_choice=payload.choice,
            result_json=result.model_dump_json(),
        )
    except AndroidFeatureIdempotencyConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "STORY_ALREADY_CHOSEN", "message": "Выбор уже сохранён."},
        ) from None
    return _scheduled_story_model(selected)
