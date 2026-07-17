from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.services.rate_limit_service import (
    DEFAULT_RATE_LIMIT_STORE_PATH,
    RateLimitExceeded,
    get_rate_limiter,
)

BACKGROUND_STORY_PAID_MEDIA_BUDGET_BUCKET = "background-story-paid-media"
BACKGROUND_STORY_PAID_MEDIA_BUDGET_USER_ID = 0
BACKGROUND_STORY_PAID_MEDIA_BUDGET_WINDOW = timedelta(days=1)
BACKGROUND_STORY_PAID_MEDIA_BUDGET_DISABLED = "BACKGROUND_STORY_PAID_MEDIA_BUDGET_DISABLED"
BACKGROUND_STORY_PAID_MEDIA_BUDGET_EXHAUSTED = "BACKGROUND_STORY_PAID_MEDIA_BUDGET_EXHAUSTED"


class BackgroundStoryPaidMediaBudgetError(RuntimeError):
    def __init__(
        self,
        *,
        stage: str,
        status: str,
        code: str,
        message: str,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.status = status
        self.code = code
        self.message = message
        self.retry_after_seconds = retry_after_seconds


def consume_background_story_paid_media_budget(settings: Any, *, stage: str) -> None:
    limit = int(getattr(settings, "scheduled_background_story_paid_media_daily_cap", 0) or 0)
    if limit <= 0:
        raise BackgroundStoryPaidMediaBudgetError(
            stage=stage,
            status="disabled",
            code=BACKGROUND_STORY_PAID_MEDIA_BUDGET_DISABLED,
            message="Платные медиа фоновых историй отключены суточным лимитом.",
        )

    store_path = getattr(settings, "rate_limit_store_path", DEFAULT_RATE_LIMIT_STORE_PATH)
    try:
        get_rate_limiter(store_path).check_fixed_window(
            BACKGROUND_STORY_PAID_MEDIA_BUDGET_BUCKET,
            BACKGROUND_STORY_PAID_MEDIA_BUDGET_USER_ID,
            limit,
            BACKGROUND_STORY_PAID_MEDIA_BUDGET_WINDOW,
        )
    except RateLimitExceeded as exc:
        raise BackgroundStoryPaidMediaBudgetError(
            stage=stage,
            status="exhausted",
            code=BACKGROUND_STORY_PAID_MEDIA_BUDGET_EXHAUSTED,
            message="Суточный лимит платных медиа фоновых историй исчерпан.",
            retry_after_seconds=exc.retry_after_seconds,
        ) from exc
