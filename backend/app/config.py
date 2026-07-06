from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AIProvider = Literal["openai", "openrouter"]
OpenAIReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    bot_token: str | None = None
    webapp_url: str | None = None
    backend_public_url: str | None = None
    allow_dev_tma_auth: bool = False
    enable_in_memory_rate_limit: bool = True
    generation_rate_limit_per_day: int = 0
    chat_rate_limit_per_hour: int = 0
    lite_facts_rate_limit_per_hour: int = 0
    memory_rate_limit_per_hour: int = 0
    telegram_init_data_max_age_seconds: int = 60 * 60 * 24
    ai_provider: AIProvider = "openrouter"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_chat_model: str = "~openai/gpt-latest"
    openrouter_character_model: str | None = None
    openrouter_image_model: str = "bytedance-seed/seedream-4.5"
    openrouter_site_url: str | None = None
    openrouter_app_title: str = "AI Tamagotchi Telegram Mini App"
    openai_api_key: str | None = None
    openai_chat_model: str = "gpt-5.5"
    openai_character_model: str | None = None
    openai_image_model: str = "gpt-image-2"
    openai_image_quality: str = "medium"
    openai_image_size: str = "1536x1152"
    openai_image_output_format: str = "png"
    image_aspect_ratio: str = "322:540"
    openai_character_reasoning_effort: OpenAIReasoningEffort | None = "minimal"
    openai_chat_reasoning_effort: OpenAIReasoningEffort | None = "low"
    openai_character_timeout_seconds: float = 180
    openai_chat_timeout_seconds: float = 90
    openai_image_timeout_seconds: float = 180
    background_removal_timeout_seconds: float = 180
    openai_max_retries: int = 0
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
