from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

OpenAIReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://tamagotchi:tamagotchi@localhost:5432/tamagotchi"
    bot_token: str | None = None
    webapp_url: str | None = None
    backend_public_url: str | None = None
    allow_dev_tma_auth: bool = False
    enable_admin_generation_lab: bool = False
    admin_generation_lab_token: str | None = None
    enable_in_memory_rate_limit: bool = True
    generated_assets_storage: str = "local"
    telegram_init_data_max_age_seconds: int = 60 * 60 * 24
    openai_api_key: str | None = None
    openai_chat_model: str = "gpt-5.5"
    openai_image_model: str = "gpt-image-2"
    openai_image_quality: str = "medium"
    openai_image_size: str = "1536x1152"
    openai_image_output_format: str = "png"
    openai_chat_reasoning_effort: OpenAIReasoningEffort | None = "low"
    openai_chat_timeout_seconds: float = 90
    openai_image_timeout_seconds: float = 180
    openai_max_retries: int = 0
    hunger_decay_per_min: float = 0.25
    mood_decay_per_min: float = 0.15
    baby_duration_hours: float = 24
    teen_duration_hours: float = 72
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
