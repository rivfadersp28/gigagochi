from __future__ import annotations

from functools import lru_cache
from typing import Any

from openai import OpenAI

from app.config import get_settings


class MissingOpenAIAPIKey(RuntimeError):
    pass


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def is_openrouter_provider(settings: Any) -> bool:
    provider = _clean_string(getattr(settings, "ai_provider", None))
    if provider:
        return provider.lower() == "openrouter"
    return any(
        _clean_string(getattr(settings, attr, None))
        for attr in ("openrouter_api_key", "openrouter_chat_model", "openrouter_image_model")
    )


def get_chat_model(settings: Any) -> str:
    if is_openrouter_provider(settings):
        return (
            _clean_string(getattr(settings, "openrouter_chat_model", None))
            or _clean_string(getattr(settings, "openai_chat_model", None))
            or "~openai/gpt-latest"
        )
    return _clean_string(getattr(settings, "openai_chat_model", None)) or "gpt-5.5"


def get_character_model(settings: Any) -> str:
    if is_openrouter_provider(settings):
        return (
            _clean_string(getattr(settings, "openrouter_character_model", None))
            or _clean_string(getattr(settings, "openrouter_chat_model", None))
            or _clean_string(getattr(settings, "openai_character_model", None))
            or _clean_string(getattr(settings, "openai_chat_model", None))
            or "~openai/gpt-latest"
        )
    return (
        _clean_string(getattr(settings, "openai_character_model", None))
        or _clean_string(getattr(settings, "openai_chat_model", None))
        or "gpt-5.5"
    )


def get_image_model(settings: Any) -> str:
    if is_openrouter_provider(settings):
        return (
            _clean_string(getattr(settings, "openrouter_image_model", None))
            or _clean_string(getattr(settings, "openai_image_model", None))
            or "bytedance-seed/seedream-4.5"
        )
    return _clean_string(getattr(settings, "openai_image_model", None)) or "gpt-image-2"


def get_openrouter_image_model(settings: Any) -> str:
    return (
        _clean_string(getattr(settings, "openrouter_image_model", None))
        or "bytedance-seed/seedream-4.5"
    )


def get_openrouter_video_model(settings: Any) -> str:
    return (
        _clean_string(getattr(settings, "openrouter_video_model", None))
        or "x-ai/grok-imagine-video"
    )


def get_openrouter_api_key(settings: Any) -> str:
    api_key = _clean_string(getattr(settings, "openrouter_api_key", None))
    legacy_key = _clean_string(getattr(settings, "openai_api_key", None))
    if not api_key and legacy_key and legacy_key.startswith("sk-or-"):
        api_key = legacy_key
    if not api_key:
        raise MissingOpenAIAPIKey
    return api_key


def get_ai_api_key(settings: Any) -> str:
    if is_openrouter_provider(settings):
        api_key = get_openrouter_api_key(settings)
    else:
        api_key = _clean_string(getattr(settings, "openai_api_key", None))
    if not api_key:
        raise MissingOpenAIAPIKey
    return api_key


def get_openrouter_base_url(settings: Any) -> str:
    base_url = _clean_string(getattr(settings, "openrouter_base_url", None))
    return (base_url or "https://openrouter.ai/api/v1").rstrip("/")


def get_openrouter_headers(settings: Any) -> dict[str, str]:
    site_url = (
        _clean_string(getattr(settings, "openrouter_site_url", None))
        or _clean_string(getattr(settings, "backend_public_url", None))
        or _clean_string(getattr(settings, "webapp_url", None))
    )
    app_title = _clean_string(getattr(settings, "openrouter_app_title", None))
    headers: dict[str, str] = {}
    if site_url:
        headers["HTTP-Referer"] = site_url
    if app_title:
        headers["X-OpenRouter-Title"] = app_title
    return headers


def get_openrouter_image_url(settings: Any) -> str:
    return f"{get_openrouter_base_url(settings)}/images"


def get_openrouter_video_url(settings: Any) -> str:
    return f"{get_openrouter_base_url(settings)}/videos"


def chat_reasoning_effort_kwargs(reasoning_effort: str | None) -> dict[str, str]:
    effort = (reasoning_effort or "").strip()
    if effort == "none":
        return {}
    return {"reasoning_effort": effort} if effort else {}


def _build_openai_platform_client(settings: Any) -> OpenAI:
    api_key = _clean_string(getattr(settings, "openai_api_key", None))
    if not api_key:
        raise MissingOpenAIAPIKey
    return OpenAI(
        api_key=api_key,
        max_retries=settings.openai_max_retries,
    )


def _build_openrouter_client(settings: Any) -> OpenAI:
    client_kwargs: dict[str, Any] = {
        "api_key": get_openrouter_api_key(settings),
        "max_retries": settings.openai_max_retries,
        "base_url": get_openrouter_base_url(settings),
    }
    default_headers = get_openrouter_headers(settings)
    if default_headers:
        client_kwargs["default_headers"] = default_headers
    return OpenAI(**client_kwargs)


@lru_cache
def get_openai_platform_client() -> OpenAI:
    return _build_openai_platform_client(get_settings())


@lru_cache
def get_openrouter_client() -> OpenAI:
    return _build_openrouter_client(get_settings())


@lru_cache
def get_openai_client() -> OpenAI:
    settings = get_settings()
    if is_openrouter_provider(settings):
        return _build_openrouter_client(settings)
    return _build_openai_platform_client(settings)
