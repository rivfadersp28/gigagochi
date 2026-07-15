from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AIProvider = Literal["openai", "openrouter"]
OpenAIReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
ScheduleHour = Annotated[int, Field(ge=0, le=23)]


class Settings(BaseSettings):
    bot_token: str | None = None
    webapp_url: str | None = None
    backend_public_url: str | None = None
    backend_internal_url: str | None = None
    allow_dev_tma_auth: bool = False
    enable_in_memory_rate_limit: bool = True
    rate_limit_store_path: str = "data/push/rate_limits.sqlite3"
    generation_rate_limit_per_day: int = Field(default=3, ge=0, le=100_000)
    interactive_travel_rate_limit_per_day: int = Field(default=30, ge=0, le=100_000)
    chat_rate_limit_per_hour: int = Field(default=120, ge=0, le=100_000)
    lite_facts_rate_limit_per_hour: int = Field(default=120, ge=0, le=100_000)
    memory_rate_limit_per_hour: int = Field(default=120, ge=0, le=100_000)
    push_snapshot_rate_limit_per_hour: int = Field(default=60, ge=1, le=1_000)
    push_snapshot_attempt_rate_limit_per_hour: int = Field(default=120, ge=1, le=10_000)
    push_snapshot_delete_rate_limit_per_hour: int = Field(default=10, ge=1, le=1_000)
    http_request_max_body_bytes: int = Field(default=1_048_576, ge=1_024, le=67_108_864)
    http_llm_global_concurrency: int = Field(default=16, ge=1, le=32)
    http_llm_per_user_concurrency: int = Field(default=2, ge=1, le=8)
    http_media_global_concurrency: int = Field(default=4, ge=1, le=16)
    http_media_per_user_concurrency: int = Field(default=1, ge=1, le=4)
    http_admission_retry_after_seconds: int = Field(default=5, ge=1, le=300)
    ai_log_max_bytes: int = Field(default=10_485_760, ge=65_536, le=1_073_741_824)
    ai_log_backup_count: int = Field(default=3, ge=0, le=20)
    generated_media_cleanup_enabled: bool = True
    storage_health_generated_assets_path: str = "static/generated"
    storage_health_push_data_path: str = "data/push"
    storage_health_logs_path: str = "logs"
    storage_health_min_free_bytes: int = Field(
        default=1_073_741_824,
        ge=0,
        le=10_000_000_000_000,
    )
    storage_health_min_free_percent: float = Field(default=5.0, ge=0, le=100)
    storage_health_probe_cache_seconds: float = Field(default=10, ge=0, le=300)
    storage_admission_image_reserve_bytes: int = Field(
        default=33_554_432,
        # Must cover IMAGE_RESULT_MAX_BYTES while the atomic writer creates
        # the replacement file on the generated-assets volume.
        ge=25 * 1_048_576,
        le=1_073_741_824,
    )
    storage_admission_video_reserve_bytes: int = Field(
        default=268_435_456,
        # Video post-processing temporarily stores a bounded source and output
        # on the generated-assets volume before the final atomic replace.
        ge=2 * 100 * 1_048_576,
        le=2_147_483_648,
    )
    bot_story_workers: int = Field(default=2, ge=1, le=4)
    bot_command_max_queued: int = Field(default=8, ge=0, le=100)
    bot_update_offset_path: str = "data/push/bot_update_offset.json"
    bot_command_inbox_path: str = "data/push/bot_command_inbox.sqlite3"
    bot_command_inbox_max_pending: int = Field(default=500, ge=1, le=10_000)
    bot_command_inbox_max_pending_per_chat: int = Field(default=8, ge=1, le=100)
    bot_command_inbox_max_completed: int = Field(default=10_000, ge=1, le=1_000_000)
    bot_command_inbox_completed_retention_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        ge=60,
        le=90 * 24 * 60 * 60,
    )
    bot_command_inbox_lease_seconds: int = Field(default=120, ge=30, le=3_600)
    generation_image_workers: int = Field(default=4, ge=1, le=32)
    generation_video_workers: int = Field(default=2, ge=1, le=32)
    generation_max_queued_jobs: int = Field(default=40, ge=0, le=500)
    generation_job_store_path: str = "data/push/generation_jobs.sqlite3"
    provider_task_receipt_store_path: str = Field(
        default="data/push/provider_task_receipts.sqlite3",
        min_length=1,
        max_length=4_096,
    )
    provider_task_receipt_store_max_records: int = Field(
        default=100_000,
        ge=100,
        le=1_000_000,
    )
    generation_job_stuck_seconds: int = Field(default=1800, ge=300, le=7200)
    generation_job_lease_grace_seconds: int = Field(default=300, ge=60, le=3600)
    interactive_travel_owner_store_path: str = (
        "static/generated/.private/interactive_travel_owners.sqlite3"
    )
    interactive_travel_owner_retention_seconds: int = Field(
        default=180 * 24 * 60 * 60,
        ge=24 * 60 * 60,
        le=5 * 365 * 24 * 60 * 60,
    )
    interactive_travel_owner_max_records: int = Field(
        default=100_000,
        ge=100,
        le=1_000_000,
    )
    diagnostic_telegram_ids: set[int] = Field(default_factory=set)
    interactive_travel_pilot_telegram_ids: set[int] = Field(default_factory=set)
    telegram_init_data_max_age_seconds: int = Field(
        default=60 * 60 * 24,
        ge=60,
        le=30 * 24 * 60 * 60,
    )
    telegram_daily_push_enabled: bool = False
    telegram_daily_push_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    telegram_daily_push_hours: list[ScheduleHour] = Field(
        default_factory=lambda: [9, 15, 21],
        min_length=1,
        max_length=3,
    )
    telegram_daily_push_window_minutes: int = Field(default=120, ge=5, le=180)
    telegram_daily_push_default_timezone: str = "Europe/Moscow"
    background_story_enabled: bool = False
    background_story_interval_seconds: int = Field(default=300, ge=60, le=86_400)
    background_story_hours: list[ScheduleHour] = Field(
        default_factory=lambda: [9, 13, 17, 21],
        min_length=4,
        max_length=4,
    )
    background_story_window_minutes: int = Field(default=120, ge=5, le=180)
    scheduled_short_story_enabled: bool = False
    scheduled_short_story_interval_seconds: int = Field(default=600, ge=60, le=86_400)
    scheduled_short_story_telegram_ids: set[int] = Field(default_factory=set)
    scheduled_background_story_paid_media_daily_cap: int = Field(
        default=0,
        ge=0,
        le=10_000,
    )
    telegram_push_store_path: str = "data/push/telegram_push_state.sqlite3"
    telegram_push_store_backend: Literal["auto", "json", "sqlite"] = "auto"
    telegram_push_legacy_json_path: str | None = "data/push/telegram_push_state.json"
    telegram_push_legacy_json_required: bool = True
    telegram_push_record_max_bytes: int = Field(
        default=1_048_576,
        ge=65_536,
        le=16_777_216,
    )
    telegram_push_store_max_bytes: int = Field(
        default=134_217_728,
        ge=1_048_576,
        le=2_147_483_648,
    )
    telegram_push_store_max_records: int = Field(default=10_000, ge=100, le=1_000_000)
    telegram_push_unreachable_retention_seconds: int = Field(
        default=90 * 24 * 60 * 60,
        ge=24 * 60 * 60,
        le=2 * 365 * 24 * 60 * 60,
    )
    admin_publish_enabled: bool = False
    admin_publish_git_remote: str = "origin"
    admin_publish_git_branch: str = "main"
    admin_publish_ssh_target: str | None = None
    admin_publish_ssh_key_path: str | None = None
    admin_publish_remote_path: str = "/opt/gigagochi"
    admin_publish_health_url: str = "https://gigagochi.serega.works/health"
    admin_publish_command_timeout_seconds: float = Field(default=1200, ge=1, le=7_200)
    admin_sync_from_server_enabled: bool = False
    llm_profile: str | None = None
    llm_runtime_path: str = "data/llm_runtime.json"
    media_profile: str | None = None
    media_runtime_path: str = "data/media_runtime.json"
    media_concurrency_lock_dir: str = "data/push/media_provider_slots"
    media_image_concurrency: int = Field(default=4, ge=1, le=32)
    media_video_concurrency: int = Field(default=2, ge=1, le=32)
    ai_provider: AIProvider = "openrouter"
    openrouter_api_key: str | None = None
    openrouter_account_namespace: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_chat_model: str = "~openai/gpt-latest"
    openrouter_character_model: str | None = None
    openrouter_image_model: str = "bytedance-seed/seedream-4.5"
    openrouter_video_model: str = "x-ai/grok-imagine-video"
    # Keep one resumable provider stage below Docker's 20 minute stop grace.
    openrouter_video_timeout_seconds: float = Field(default=900, ge=1, le=900)
    openrouter_video_poll_interval_seconds: float = Field(default=5, ge=0.1, le=300)
    openrouter_site_url: str | None = None
    openrouter_app_title: str = "AI Tamagotchi Telegram Mini App"
    openai_api_key: str | None = None
    openai_chat_model: str = "gpt-5.5"
    full_story_model: str | None = None
    full_story_review_model: str | None = None
    full_story_max_plan_attempts: int = Field(default=2, ge=1, le=3)
    openai_character_model: str | None = None
    openai_image_model: str = "gpt-image-2"
    openai_image_quality: str = "medium"
    openai_image_size: str = "1536x1152"
    openai_image_output_format: str = "png"
    pet_comparison_enabled: bool = False
    kandinsky_api_key: str | None = None
    kandinsky_account_namespace: str | None = None
    kandinsky_base_url: str = "https://studio.kandinskylab.ai/api"
    kandinsky_t2i_task_type: str = "k6-image-t2i"
    kandinsky_i2i_task_type: str = "k6-i2i"
    kandinsky_i2v_task_type: str = "k5-i2v-hd"
    kandinsky_video_timeout_seconds: float = Field(default=900, ge=1, le=900)
    kandinsky_image_resolution: str = "1280x768"
    kandinsky_pet_image_resolution: str = "768x1280"
    kandinsky_reference_max_side: int = Field(default=1280, ge=256, le=4096)
    kandinsky_reference_jpeg_quality: int = Field(default=85, ge=60, le=95)
    kandinsky_poll_interval_seconds: float = Field(default=5, ge=0.1, le=300)
    image_aspect_ratio: str = "322:540"
    openai_character_reasoning_effort: OpenAIReasoningEffort | None = "minimal"
    openai_chat_reasoning_effort: OpenAIReasoningEffort | None = "low"
    # OpenAI-compatible clients may retry a request up to three times. Five minutes
    # per attempt leaves shutdown headroom even at the configured retry maximum.
    openai_character_timeout_seconds: float = Field(default=180, ge=1, le=300)
    openai_chat_timeout_seconds: float = Field(default=90, ge=1, le=300)
    openai_image_timeout_seconds: float = Field(default=180, ge=1, le=300)
    openai_max_retries: int = Field(default=2, ge=0, le=5)
    gigachat_base_url: str | None = None
    gigachat_username: str | None = None
    gigachat_password: str | None = None
    gigachat_model: str = "GigaChat-3-Ultra"
    gigachat_ssl_verify: bool = True
    gigachat_ca_bundle: str | None = None
    gigachat_token_timeout_seconds: float = Field(default=30, ge=1, le=600)
    gigachat_chat_timeout_seconds: float = Field(default=120, ge=1, le=300)
    gigachat_token_ttl_seconds: int = Field(default=1500, ge=60, le=3600)
    ops_alerts_enabled: bool = False
    ops_alert_telegram_ids: set[int] = Field(default_factory=set)
    ops_alert_dedup_seconds: int = Field(default=300, ge=30, le=3600)
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    @field_validator("telegram_daily_push_hours", "background_story_hours")
    @classmethod
    def require_unique_schedule_hours(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("schedule hours must be unique")
        return value

    @field_validator("provider_task_receipt_store_path")
    @classmethod
    def require_durable_provider_receipt_store_path(cls, value: str) -> str:
        cleaned = value.strip()
        if "\x00" in cleaned or Path(cleaned).suffix.lower() not in {
            ".db",
            ".sqlite",
            ".sqlite3",
        }:
            raise ValueError("provider task receipt store must be a SQLite file path")
        return cleaned

    @field_validator(
        "openrouter_account_namespace",
        "kandinsky_account_namespace",
        mode="before",
    )
    @classmethod
    def normalize_provider_account_namespace(cls, value: object) -> str | None:
        if value is None or not str(value).strip():
            return None
        cleaned = str(value).strip()
        if (
            len(cleaned) > 128
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", cleaned)
            or cleaned.casefold().startswith(("sk-", "bearer", "token"))
        ):
            raise ValueError("provider account namespace must be a short non-secret identifier")
        return cleaned

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
