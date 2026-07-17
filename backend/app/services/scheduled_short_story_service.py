from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


@dataclass(frozen=True, slots=True)
class ScheduledShortStoryEpisode:
    story_id: str
    plan: dict[str, Any]
    situation_image_url: str
    situation_video_url: str
    outcome_image_urls: tuple[str, ...]
    outcome_video_urls: tuple[str, ...]
    outcome_files: tuple[str, ...]


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
    run_provider_job(
        "situation:image",
        lambda: generate_interactive_travel_part_image(
            pet=pet,
            travel_id=story_id,
            destination=plan["destination"],
            part_number=1,
            title=plan["title"],
            story_text=plan["storyText"],
        ),
    )
    run_provider_job(
        "situation:video",
        lambda: generate_interactive_travel_part_video(
            travel_id=story_id,
            part_number=1,
        ),
    )
    outcome_files: list[str] = []
    for index, outcome in enumerate(plan["outcomes"]):
        variant = f"outcome-{index}"
        run_provider_job(
            f"{variant}:image",
            lambda outcome=outcome, variant=variant: generate_interactive_travel_part_image(
                pet=pet,
                travel_id=story_id,
                destination=plan["destination"],
                part_number=1,
                title=f"{plan['title']}: исход",
                story_text=outcome,
                variant=variant,
            ),
        )
        run_provider_job(
            f"{variant}:video",
            lambda variant=variant: generate_interactive_travel_part_video(
                travel_id=story_id,
                part_number=1,
                variant=variant,
            ),
        )
        outcome_files.append(f"interactive-travel-part-01-{variant}.mp4")
    root = f"/static/generated/{story_id}"
    return ScheduledShortStoryEpisode(
        story_id=story_id,
        plan=plan,
        situation_image_url=f"{root}/interactive-travel-part-01.png",
        situation_video_url=f"{root}/interactive-travel-part-01.mp4",
        outcome_image_urls=tuple(
            f"{root}/interactive-travel-part-01-outcome-{index}.png"
            for index in range(len(plan["outcomes"]))
        ),
        outcome_video_urls=tuple(f"{root}/{name}" for name in outcome_files),
        outcome_files=tuple(outcome_files),
    )
