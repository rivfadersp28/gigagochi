from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm import LLMRequest
from app.llm import runtime as llm_runtime
from app.llm.compat import complete_chat
from app.llm.runtime import LLMRuntimeConfigError, RuntimeTaskRouter


def _write_runtime(tmp_path) -> str:
    path = tmp_path / "llm_runtime.json"
    path.write_text(
        json.dumps(
            {
                "activeProfile": "legacy",
                "profiles": {
                    "legacy": {
                        "default": {"provider": "legacy"},
                        "tasks": {},
                    },
                    "gigachat": {
                        "default": {
                            "provider": "gigachat",
                            "model": "$GIGACHAT_MODEL",
                        },
                        "tasks": {
                            "visible_reply": {"model": "GigaChat-3-Pro"},
                            "review": {
                                "provider": "openai",
                                "model": "gpt-review",
                            },
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def test_runtime_profile_override_and_task_routes(tmp_path) -> None:
    router = RuntimeTaskRouter(
        Settings(
            _env_file=None,
            llm_runtime_path=_write_runtime(tmp_path),
            llm_profile="gigachat",
            ai_provider="openrouter",
            gigachat_model="GigaChat-custom",
        )
    )

    default = router.resolve(
        LLMRequest(messages=[{"role": "user", "content": "story"}], task="story")
    )
    visible = router.resolve(
        LLMRequest(
            messages=[{"role": "user", "content": "hello"}],
            task="visible_reply",
        )
    )
    review = router.resolve(
        LLMRequest(messages=[{"role": "user", "content": "check"}], task="review")
    )

    assert router.profile_name == "gigachat"
    assert (default.provider, default.model) == ("gigachat", "GigaChat-custom")
    assert (visible.provider, visible.model) == ("gigachat", "GigaChat-3-Pro")
    assert (review.provider, review.model) == ("openai", "gpt-review")


def test_legacy_profile_keeps_ai_provider_and_feature_model(tmp_path) -> None:
    router = RuntimeTaskRouter(
        Settings(
            _env_file=None,
            llm_runtime_path=_write_runtime(tmp_path),
            llm_profile="legacy",
            ai_provider="openrouter",
        )
    )

    route = router.resolve(
        LLMRequest(
            messages=[{"role": "user", "content": "hello"}],
            task="visible_reply",
            model="~openai/gpt-latest",
        )
    )

    assert route.provider == "openrouter"
    assert route.model is None


def test_explicit_client_override_does_not_resolve_runtime_model(monkeypatch) -> None:
    calls: list[dict] = []
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: (
                    calls.append(kwargs)
                    or {
                        "choices": [
                            {
                                "message": {"content": "ok"},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                )
            )
        )
    )
    monkeypatch.setattr(
        "app.llm.compat.resolve_llm_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not route")),
    )

    response = complete_chat(
        "visible_reply",
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello"}],
        },
        client=client,
    )

    assert response.content == "ok"
    assert calls[0]["model"] == "test-model"


def test_task_provider_override_requires_its_own_model(tmp_path) -> None:
    path = tmp_path / "invalid_llm_runtime.json"
    path.write_text(
        json.dumps(
            {
                "activeProfile": "mixed",
                "profiles": {
                    "mixed": {
                        "default": {"provider": "gigachat", "model": "GigaChat"},
                        "tasks": {"review": {"provider": "openai"}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LLMRuntimeConfigError, match="changes provider without defining model"):
        RuntimeTaskRouter(
            Settings(
                _env_file=None,
                llm_runtime_path=str(path),
                llm_profile="mixed",
            )
        )


def test_runtime_status_detects_missing_gigachat_credentials(monkeypatch, tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        llm_runtime_path=_write_runtime(tmp_path),
        llm_profile="gigachat",
        gigachat_base_url=None,
        gigachat_username=None,
        gigachat_password=None,
    )
    router = RuntimeTaskRouter(settings)
    monkeypatch.setattr(llm_runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_runtime, "get_llm_router", lambda: router)

    status = llm_runtime.llm_runtime_status()

    assert status["status"] == "error"
    assert status["profile"] == "gigachat"
    assert "gigachat_credentials_missing" in status["errors"]
    assert "provider_not_registered:gigachat" in status["errors"]


def test_runtime_status_accepts_complete_gigachat_config(monkeypatch, tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        llm_runtime_path=_write_runtime(tmp_path),
        llm_profile="gigachat",
        gigachat_base_url="https://giga.test",
        gigachat_username="alice",
        gigachat_password="secret",
        openai_api_key="sk-test",
    )
    router = RuntimeTaskRouter(settings)
    monkeypatch.setattr(llm_runtime, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_runtime, "get_llm_router", lambda: router)

    status = llm_runtime.llm_runtime_status()

    assert status == {
        "status": "ok",
        "profile": "gigachat",
        "providers": ["gigachat", "openai"],
        "errors": [],
    }
