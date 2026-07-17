from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generation_rate_limit_per_day", -1),
        ("interactive_travel_rate_limit_per_day", -1),
        ("chat_rate_limit_per_hour", -1),
        ("telegram_init_data_max_age_seconds", 0),
        ("telegram_daily_push_interval_seconds", 59),
        ("background_story_interval_seconds", 59),
        ("admin_publish_command_timeout_seconds", 0),
        ("openrouter_video_timeout_seconds", 0),
        ("openrouter_video_poll_interval_seconds", 0),
        ("kandinsky_video_timeout_seconds", 0),
        ("kandinsky_poll_interval_seconds", 0),
        ("openai_character_timeout_seconds", 0),
        ("openai_chat_timeout_seconds", 0),
        ("openai_image_timeout_seconds", 0),
        ("gigachat_token_timeout_seconds", 0),
        ("gigachat_chat_timeout_seconds", 0),
        ("provider_task_receipt_store_path", "not-a-sqlite-path"),
        ("provider_task_receipt_store_max_records", 0),
        ("auth_session_store_path", "not-a-sqlite-path"),
        ("auth_access_token_ttl_seconds", 59),
        ("auth_refresh_token_ttl_seconds", 3599),
        ("openrouter_account_namespace", "sk-secret-must-not-be-stored"),
    ],
)
def test_settings_reject_runtime_values_that_cannot_work(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("telegram_daily_push_hours", []),
        ("telegram_daily_push_hours", [9, 15, 21, 23]),
        ("telegram_daily_push_hours", [24]),
        ("telegram_daily_push_hours", [9, 9]),
        ("background_story_hours", [9, 13, 17]),
        ("background_story_hours", [9, 13, 17, 24]),
        ("background_story_hours", [9, 9, 17, 21]),
    ],
)
def test_settings_reject_schedules_that_would_be_silently_ignored(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_zero_rate_limit_remains_an_explicit_disable_switch() -> None:
    settings = Settings(
        _env_file=None,
        generation_rate_limit_per_day=0,
        interactive_travel_rate_limit_per_day=0,
        chat_rate_limit_per_hour=0,
        lite_facts_rate_limit_per_hour=0,
        memory_rate_limit_per_hour=0,
    )

    assert settings.generation_rate_limit_per_day == 0
    assert settings.interactive_travel_rate_limit_per_day == 0
    assert settings.chat_rate_limit_per_hour == 0


def test_google_auth_is_unconfigured_by_default_and_refresh_outlives_access() -> None:
    settings = Settings(_env_file=None)

    assert settings.google_auth_web_client_id is None
    assert settings.auth_session_store_path.endswith("google_auth_sessions.sqlite3")
    assert settings.auth_refresh_token_ttl_seconds > settings.auth_access_token_ttl_seconds

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            auth_access_token_ttl_seconds=3_600,
            auth_refresh_token_ttl_seconds=3_600,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("openrouter_video_timeout_seconds", 901),
        ("kandinsky_video_timeout_seconds", 901),
        ("openai_character_timeout_seconds", 301),
        ("openai_chat_timeout_seconds", 301),
        ("openai_image_timeout_seconds", 301),
        ("gigachat_chat_timeout_seconds", 301),
    ],
)
def test_provider_timeouts_fit_within_shutdown_grace(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
