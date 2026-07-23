from __future__ import annotations

import ipaddress
import time
from datetime import timedelta
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import get_settings
from app.dependencies import get_google_account_identity
from app.services.android_analytics_service import (
    MAX_EVENT_AGE_SECONDS,
    MAX_FUTURE_SKEW_SECONDS,
    AnalyticsNotConfiguredError,
    AndroidAnalyticsForwarder,
)
from app.services.google_auth_session_store import GoogleUserIdentity
from app.services.rate_limit_service import RateLimitExceeded, get_rate_limiter

router = APIRouter(prefix="/api/android/analytics", tags=["android-analytics"])
UUID4_PATTERN = r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"
SAFE_PROPERTY_VALUE = r"^[A-Za-z0-9._:-]{1,64}$"
ALLOWED_EVENT_NAMES = frozenset(
    {
        "app_opened",
        "session_started",
        "create_started",
        "create_step_completed",
        "generation_requested",
        "generation_completed",
        "generation_failed",
        "pet_ready",
        "onboarding_completed",
        "chat_completed",
        "feed_completed",
        "story_completed",
        "travel_shared",
        "notification_permission_result",
        "notification_opened",
        "operation_failed",
        "webview_renderer_gone",
        "app_crash",
    }
)
ALLOWED_PROPERTY_NAMES = frozenset(
    {
        "kind",
        "step",
        "result",
        "error_code",
        "duration_bucket",
        "retry_count",
        "permission",
        "route",
        "source",
    }
)


class AndroidAnalyticsEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    eventId: str = Field(min_length=36, max_length=36, pattern=UUID4_PATTERN)
    sessionId: str = Field(min_length=36, max_length=36, pattern=UUID4_PATTERN)
    name: str = Field(min_length=1, max_length=64)
    occurredAtEpochMillis: int
    appVersion: str = Field(min_length=1, max_length=32)
    buildNumber: int = Field(ge=0, le=2_147_483_647)
    environment: Literal["production"]
    channel: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9-]+$")
    properties: dict[str, str] = Field(default_factory=dict, max_length=8)

    @field_validator("name")
    @classmethod
    def require_allowed_event_name(cls, value: str) -> str:
        if value not in ALLOWED_EVENT_NAMES:
            raise ValueError("unsupported analytics event")
        return value

    @field_validator("properties")
    @classmethod
    def require_safe_properties(cls, value: dict[str, str]) -> dict[str, str]:
        import re

        if not set(value).issubset(ALLOWED_PROPERTY_NAMES):
            raise ValueError("unsupported analytics property")
        if any(re.fullmatch(SAFE_PROPERTY_VALUE, item) is None for item in value.values()):
            raise ValueError("unsafe analytics property value")
        return dict(sorted(value.items()))


class AndroidAnalyticsBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schemaVersion: Literal[1]
    events: list[AndroidAnalyticsEvent] = Field(min_length=1, max_length=50)


@lru_cache(maxsize=1)
def get_android_analytics_forwarder() -> AndroidAnalyticsForwarder:
    return AndroidAnalyticsForwarder(get_settings())


AnalyticsForwarder = Annotated[
    AndroidAnalyticsForwarder,
    Depends(get_android_analytics_forwarder),
]
GoogleIdentity = Annotated[GoogleUserIdentity, Depends(get_google_account_identity)]


def _request_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    candidate = forwarded or (request.client.host if request.client else "")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return "unknown"


def _check_rate_limits(request: Request, actor_id: str) -> None:
    settings = get_settings()
    limiter = get_rate_limiter(settings.rate_limit_store_path)
    try:
        limiter.check_fixed_window(
            "android-analytics-minute",
            actor_id,
            settings.android_analytics_batches_per_minute,
            timedelta(minutes=1),
        )
        limiter.check_fixed_window(
            "android-analytics-burst",
            actor_id,
            settings.android_analytics_burst_per_10_seconds,
            timedelta(seconds=10),
        )
        limiter.check_fixed_window(
            "android-analytics-ip-minute",
            _request_ip(request),
            settings.android_analytics_batches_per_minute * 20,
            timedelta(minutes=1),
        )
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMITED", "message": "Слишком много запросов."},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None


@router.post("/events", status_code=status.HTTP_202_ACCEPTED, include_in_schema=False)
def ingest_android_analytics(
    payload: AndroidAnalyticsBatch,
    request: Request,
    response: Response,
    identity: GoogleIdentity,
    forwarder: AnalyticsForwarder,
) -> dict[str, Any]:
    now_ms = int(time.time() * 1_000)
    oldest = now_ms - MAX_EVENT_AGE_SECONDS * 1_000
    newest = now_ms + MAX_FUTURE_SKEW_SECONDS * 1_000
    if any(
        event.occurredAtEpochMillis < oldest or event.occurredAtEpochMillis > newest
        for event in payload.events
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INVALID_EVENT_TIME", "message": "Некорректное время события."},
        )
    try:
        actor_id = forwarder.actor_id(identity.account_id)
    except AnalyticsNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "ANALYTICS_UNAVAILABLE", "message": "Сервис временно недоступен."},
            headers={"Retry-After": "60"},
        ) from None
    _check_rate_limits(request, actor_id)
    forwarder.accept(
        identity.account_id,
        [event.model_dump(mode="json") for event in payload.events],
    )
    response.headers["Cache-Control"] = "no-store"
    return {"accepted": len(payload.events)}
