from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.openai_service import (
    MissingOpenAIAPIKey,
    get_ai_api_key,
    get_chat_model,
    get_image_model,
    get_openai_client,
    get_openrouter_headers,
    get_openrouter_image_url,
)


def openrouter_settings(**overrides):
    data = {
        "ai_provider": "openrouter",
        "openrouter_api_key": "sk-or-test",
        "openrouter_base_url": "https://openrouter.ai/api/v1/",
        "openrouter_chat_model": "~openai/gpt-latest",
        "openrouter_image_model": "bytedance-seed/seedream-4.5",
        "openrouter_site_url": "https://app.example",
        "openrouter_app_title": "Test Tamagotchi",
        "openai_api_key": None,
        "openai_chat_model": "gpt-5.5",
        "openai_image_model": "gpt-image-2",
        "openai_max_retries": 2,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_openrouter_models_and_headers() -> None:
    settings = openrouter_settings()

    assert get_chat_model(settings) == "~openai/gpt-latest"
    assert get_image_model(settings) == "bytedance-seed/seedream-4.5"
    assert get_openrouter_image_url(settings) == "https://openrouter.ai/api/v1/images"
    assert get_openrouter_headers(settings) == {
        "HTTP-Referer": "https://app.example",
        "X-OpenRouter-Title": "Test Tamagotchi",
    }


def test_openrouter_accepts_legacy_openai_env_only_for_openrouter_keys() -> None:
    settings = openrouter_settings(openrouter_api_key=None, openai_api_key="sk-or-legacy")

    assert get_ai_api_key(settings) == "sk-or-legacy"


def test_openrouter_rejects_missing_key() -> None:
    settings = openrouter_settings(openrouter_api_key=None, openai_api_key="sk-not-openrouter")

    with pytest.raises(MissingOpenAIAPIKey):
        get_ai_api_key(settings)


def test_openai_client_configures_openrouter_base_url_and_headers(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("app.services.openai_service.OpenAI", FakeOpenAI)
    monkeypatch.setattr("app.services.openai_service.get_settings", openrouter_settings)
    get_openai_client.cache_clear()

    try:
        client = get_openai_client()
    finally:
        get_openai_client.cache_clear()

    assert isinstance(client, FakeOpenAI)
    assert captured["api_key"] == "sk-or-test"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"
    assert captured["max_retries"] == 2
    assert captured["default_headers"] == {
        "HTTP-Referer": "https://app.example",
        "X-OpenRouter-Title": "Test Tamagotchi",
    }
