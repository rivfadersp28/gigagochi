from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.background_story_paid_media_budget import (
    BackgroundStoryPaidMediaBudgetError,
)
from app.services.image_service import generation_error_code
from app.services.interactive_travel_media_service import (
    generate_interactive_travel_part_image,
    generate_interactive_travel_part_video,
)
from app.services.interactive_travel_service import (
    generate_scheduled_interactive_episode_plan,
)

DEFAULT_SCHEDULED_SHORT_STORY_HOURS = tuple(range(10, 22))
DEFAULT_SCHEDULED_SHORT_STORY_TIMEZONE = "Europe/Moscow"
SCHEDULED_SHORT_STORY_PROVIDER_MAX_ATTEMPTS = 3
SCHEDULED_SHORT_STORY_PROVIDER_RETRY_DELAYS_SECONDS = (15.0, 45.0)
logger = logging.getLogger(__name__)
ANDROID_SCHEDULED_STORY_ID_PATTERN = re.compile(r"android-story-[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class ScheduledShortStoryEpisode:
    story_id: str
    plan: dict[str, Any]
    situation_image_url: str | None
    situation_video_url: str | None
    outcome_image_urls: tuple[str | None, ...]
    outcome_video_urls: tuple[str | None, ...]
    outcome_files: tuple[str | None, ...]


def scheduled_short_story_provider_error_is_retryable(exc: Exception) -> bool:
    code = generation_error_code(exc)
    if code in {
        "OPENAI_TIMEOUT",
        "OPENAI_RATE_LIMIT",
        "OPENAI_CONNECTION_FAILED",
        "LLM_TIMEOUT",
        "LLM_RATE_LIMIT",
        "LLM_CONNECTION_FAILED",
        "LLM_FAILED",
        "KANDINSKY_TASK_FAILED",
    }:
        return True
    if code.startswith(("OPENAI_STATUS_5", "LLM_STATUS_5")):
        return True
    compact_error = str(exc).casefold()
    return "timed out" in compact_error or "timeout" in compact_error


def run_scheduled_short_story_provider_job(
    label: str,
    operation: Callable[[], Any],
) -> Any:
    for attempt in range(1, SCHEDULED_SHORT_STORY_PROVIDER_MAX_ATTEMPTS + 1):
        try:
            return operation()
        except Exception as exc:
            retryable = scheduled_short_story_provider_error_is_retryable(exc)
            if not retryable or attempt >= SCHEDULED_SHORT_STORY_PROVIDER_MAX_ATTEMPTS:
                raise
            delay = SCHEDULED_SHORT_STORY_PROVIDER_RETRY_DELAYS_SECONDS[attempt - 1]
            logger.warning(
                "scheduled_short_story_provider_job_retry label=%s attempt=%s "
                "maxAttempts=%s retryDelaySeconds=%s code=%s errorType=%s",
                label,
                attempt,
                SCHEDULED_SHORT_STORY_PROVIDER_MAX_ATTEMPTS,
                delay,
                generation_error_code(exc),
                type(exc).__name__,
            )
            time.sleep(delay)
    raise RuntimeError("unreachable scheduled short story provider retry state")


def scheduled_short_story_hours(settings: Any) -> tuple[int, ...]:
    raw_hours = getattr(
        settings,
        "scheduled_short_story_hours",
        DEFAULT_SCHEDULED_SHORT_STORY_HOURS,
    )
    if not isinstance(raw_hours, (list, tuple, set)):
        return DEFAULT_SCHEDULED_SHORT_STORY_HOURS
    hours: list[int] = []
    for raw_hour in raw_hours:
        try:
            hour = int(raw_hour)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 23 and hour not in hours:
            hours.append(hour)
    return tuple(sorted(hours)) or DEFAULT_SCHEDULED_SHORT_STORY_HOURS


def scheduled_short_story_timezone(settings: Any) -> ZoneInfo:
    timezone_name = str(
        getattr(
            settings,
            "scheduled_short_story_timezone",
            DEFAULT_SCHEDULED_SHORT_STORY_TIMEZONE,
        )
    )
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_SCHEDULED_SHORT_STORY_TIMEZONE)


def scheduled_short_story_slot(settings: Any, now: datetime) -> datetime | None:
    local_now = now.astimezone(scheduled_short_story_timezone(settings))
    if local_now.hour not in scheduled_short_story_hours(settings):
        return None
    return local_now.replace(minute=0, second=0, microsecond=0).astimezone(UTC)


def generate_scheduled_short_story_episode(
    *,
    pet: Any,
    story_id: str,
    run_provider_job: Callable[[str, Callable[[], Any]], Any],
) -> ScheduledShortStoryEpisode:
    plan = generate_scheduled_interactive_episode_plan()
    media_id = (
        f"interactive-travel-{story_id}"
        if ANDROID_SCHEDULED_STORY_ID_PATTERN.fullmatch(story_id)
        else story_id
    )
    media_available = True

    def run_media(label: str, operation: Callable[[], Any]) -> bool:
        nonlocal media_available
        if not media_available:
            return False
        try:
            run_provider_job(label, operation)
        except BackgroundStoryPaidMediaBudgetError:
            media_available = False
            return False
        return True

    situation_image_ready = run_media(
        "situation:image",
        lambda: generate_interactive_travel_part_image(
            pet=pet,
            travel_id=media_id,
            destination=plan["destination"],
            part_number=1,
            title=plan["title"],
            story_text=plan["storyText"],
        ),
    )
    situation_video_ready = run_media(
        "situation:video",
        lambda: generate_interactive_travel_part_video(
            travel_id=media_id,
            part_number=1,
        ),
    )
    outcome_image_urls: list[str | None] = []
    outcome_video_urls: list[str | None] = []
    outcome_files: list[str | None] = []
    root = f"/static/generated/{media_id}"
    for index, outcome in enumerate(plan["outcomes"]):
        variant = f"outcome-{index}"
        image_ready = run_media(
            f"{variant}:image",
            lambda outcome=outcome, variant=variant: generate_interactive_travel_part_image(
                pet=pet,
                travel_id=media_id,
                destination=plan["destination"],
                part_number=1,
                title=f"{plan['title']}: исход",
                story_text=outcome,
                variant=variant,
            ),
        )
        video_ready = run_media(
            f"{variant}:video",
            lambda variant=variant: generate_interactive_travel_part_video(
                travel_id=media_id,
                part_number=1,
                variant=variant,
            ),
        )
        filename = f"interactive-travel-part-01-{variant}.mp4"
        outcome_image_urls.append(
            f"{root}/interactive-travel-part-01-{variant}.png" if image_ready else None
        )
        outcome_video_urls.append(f"{root}/{filename}" if video_ready else None)
        outcome_files.append(filename if video_ready else None)
    return ScheduledShortStoryEpisode(
        story_id=story_id,
        plan=plan,
        situation_image_url=(
            f"{root}/interactive-travel-part-01.png" if situation_image_ready else None
        ),
        situation_video_url=(
            f"{root}/interactive-travel-part-01.mp4" if situation_video_ready else None
        ),
        outcome_image_urls=tuple(outcome_image_urls),
        outcome_video_urls=tuple(outcome_video_urls),
        outcome_files=tuple(outcome_files),
    )
