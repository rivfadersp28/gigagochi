from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.llm.contracts import LLMRequest
from app.llm.gateway import LLMGateway, LLMRoute
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.llm.registry import ProviderRegistry
from app.services.openai_service import (
    get_openai_platform_client,
    get_openrouter_client,
)


class LLMRuntimeConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _RouteConfig:
    provider: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class _ProfileConfig:
    default: _RouteConfig
    tasks: dict[str, _RouteConfig]


def _clean_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _route_config(value: Any, *, location: str, settings: Settings) -> _RouteConfig:
    if not isinstance(value, dict):
        raise LLMRuntimeConfigError(f"{location} must be an object")
    provider_value = _clean_optional(value.get("provider"))
    provider = provider_value.lower() if provider_value is not None else None
    model = _clean_optional(value.get("model"))
    if model and model.startswith("$"):
        setting_name = model[1:].strip().lower()
        if not setting_name.endswith("_model") or not hasattr(settings, setting_name):
            raise LLMRuntimeConfigError(f"{location}.model references unknown setting {model!r}")
        model = _clean_optional(getattr(settings, setting_name))
        if model is None:
            raise LLMRuntimeConfigError(
                f"{location}.model references empty setting {setting_name!r}"
            )
    if provider is None and model is None:
        raise LLMRuntimeConfigError(f"{location} must define provider or model")
    return _RouteConfig(provider=provider, model=model)


def _runtime_path(settings: Settings) -> Path:
    path = Path(settings.llm_runtime_path).expanduser()
    if path.is_absolute() or path.exists():
        return path
    return Path(__file__).resolve().parents[2] / path


def _load_runtime(settings: Settings) -> tuple[str, dict[str, _ProfileConfig]]:
    path = _runtime_path(settings)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LLMRuntimeConfigError(f"LLM runtime config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise LLMRuntimeConfigError(f"Invalid LLM runtime JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LLMRuntimeConfigError("LLM runtime root must be an object")

    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise LLMRuntimeConfigError("LLM runtime must define non-empty profiles")
    normalized_profiles: dict[str, dict[str, Any]] = {}
    for raw_name, raw_profile in raw_profiles.items():
        name = str(raw_name).strip()
        if not name or not isinstance(raw_profile, dict):
            raise LLMRuntimeConfigError("LLM profile names must map to objects")
        if name in normalized_profiles:
            raise LLMRuntimeConfigError(f"Duplicate LLM profile name: {name}")
        normalized_profiles[name] = raw_profile

    active_profile = _clean_optional(settings.llm_profile) or _clean_optional(
        payload.get("activeProfile")
    )
    if active_profile is None:
        raise LLMRuntimeConfigError("LLM runtime must define activeProfile or LLM_PROFILE")
    if active_profile not in normalized_profiles:
        raise LLMRuntimeConfigError(f"Unknown LLM profile: {active_profile}")

    raw_profile = normalized_profiles[active_profile]
    default = _route_config(
        raw_profile.get("default"),
        location=f"profiles.{active_profile}.default",
        settings=settings,
    )
    if default.provider is None:
        raise LLMRuntimeConfigError(f"profiles.{active_profile}.default must define provider")
    raw_tasks = raw_profile.get("tasks", {})
    if not isinstance(raw_tasks, dict):
        raise LLMRuntimeConfigError(f"profiles.{active_profile}.tasks must be an object")
    tasks = {
        str(task).strip(): _route_config(
            route,
            location=f"profiles.{active_profile}.tasks.{task}",
            settings=settings,
        )
        for task, route in raw_tasks.items()
    }
    if any(not task for task in tasks):
        raise LLMRuntimeConfigError(f"profiles.{active_profile}.tasks contains an empty task")
    for task, route in tasks.items():
        if (
            route.provider is not None
            and route.provider != default.provider
            and route.model is None
        ):
            raise LLMRuntimeConfigError(
                f"profiles.{active_profile}.tasks.{task} changes provider without defining model"
            )
    profile = _ProfileConfig(default=default, tasks=tasks)
    return active_profile, {active_profile: profile}


class RuntimeTaskRouter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.profile_name, profiles = _load_runtime(settings)
        self._profile = profiles[self.profile_name]

    def resolve(self, request: LLMRequest) -> LLMRoute:
        task_route = self._profile.tasks.get(request.task)
        provider = (task_route.provider if task_route else None) or self._profile.default.provider
        model = (task_route.model if task_route else None) or self._profile.default.model
        if provider == "legacy":
            provider = self._settings.ai_provider
        if provider is None:
            raise LLMRuntimeConfigError(
                f"LLM profile {self.profile_name!r} has no provider for task {request.task!r}"
            )
        return LLMRoute(provider=provider, model=model)

    def provider_names(self) -> frozenset[str]:
        routes = (self._profile.default, *self._profile.tasks.values())
        default_provider = self._profile.default.provider
        providers: set[str] = set()
        for route in routes:
            provider = route.provider or default_provider
            if provider == "legacy":
                provider = self._settings.ai_provider
            if provider is not None:
                providers.add(provider.strip().lower())
        return frozenset(providers)


@lru_cache
def get_llm_router() -> RuntimeTaskRouter:
    return RuntimeTaskRouter(get_settings())


def resolve_llm_model(task: str, fallback: str | None) -> str | None:
    probe = LLMRequest(messages=({"role": "user", "content": "model resolution"},), task=task)
    return get_llm_router().resolve(probe).model or fallback


def resolve_llm_provider(task: str = "default") -> str:
    probe = LLMRequest(messages=({"role": "user", "content": "provider resolution"},), task=task)
    return get_llm_router().resolve(probe).provider


def _provider_registry(settings: Settings) -> ProviderRegistry:
    registry = ProviderRegistry(
        [
            OpenAICompatibleProvider(
                name="openai",
                client_factory=get_openai_platform_client,
                default_model=settings.openai_chat_model,
            ),
            OpenAICompatibleProvider(
                name="openrouter",
                client_factory=get_openrouter_client,
                default_model=settings.openrouter_chat_model,
                max_tokens_parameter="max_tokens",
            ),
        ]
    )

    from app.llm.providers.gigachat import GigaChatProvider

    if settings.gigachat_base_url and settings.gigachat_username and settings.gigachat_password:
        registry.register(
            GigaChatProvider(
                base_url=settings.gigachat_base_url,
                username=settings.gigachat_username,
                password=settings.gigachat_password,
                default_model=settings.gigachat_model,
                verify=settings.gigachat_ca_bundle or settings.gigachat_ssl_verify,
                token_timeout_seconds=settings.gigachat_token_timeout_seconds,
                chat_timeout_seconds=settings.gigachat_chat_timeout_seconds,
                default_token_ttl_seconds=settings.gigachat_token_ttl_seconds,
            )
        )

    try:
        from app.llm.providers.litellm_provider import LiteLLMProvider
    except ImportError:
        pass
    else:
        registry.register(LiteLLMProvider())
    return registry


@lru_cache
def get_llm_gateway() -> LLMGateway:
    settings = get_settings()
    return LLMGateway(_provider_registry(settings), get_llm_router())


def clear_llm_runtime_caches() -> None:
    get_llm_gateway.cache_clear()
    get_llm_router.cache_clear()


def llm_runtime_status() -> dict[str, Any]:
    try:
        settings = get_settings()
        router = get_llm_router()
        providers = router.provider_names()
        errors: list[str] = []

        if "openai" in providers and not (settings.openai_api_key or "").strip():
            errors.append("openai_credentials_missing")
        if "openrouter" in providers:
            openrouter_key = (settings.openrouter_api_key or "").strip()
            legacy_key = (settings.openai_api_key or "").strip()
            if not openrouter_key and not legacy_key.startswith("sk-or-"):
                errors.append("openrouter_credentials_missing")
        if "gigachat" in providers and not all(
            (
                (settings.gigachat_base_url or "").strip(),
                (settings.gigachat_username or "").strip(),
                (settings.gigachat_password or "").strip(),
            )
        ):
            errors.append("gigachat_credentials_missing")
        if (
            "gigachat" in providers
            and settings.gigachat_ca_bundle
            and not Path(settings.gigachat_ca_bundle).expanduser().is_file()
        ):
            errors.append("gigachat_ca_bundle_missing")
        if "litellm" in providers:
            try:
                available = find_spec("litellm") is not None
            except (ImportError, ValueError):
                available = False
            if not available:
                errors.append("litellm_dependency_missing")

        registered = frozenset(_provider_registry(settings).names())
        for provider in sorted(providers - registered):
            errors.append(f"provider_not_registered:{provider}")

        return {
            "status": "ok" if not errors else "error",
            "profile": router.profile_name,
            "providers": sorted(providers),
            "errors": errors,
        }
    except Exception as exc:
        return {
            "status": "error",
            "profile": None,
            "providers": [],
            "errors": [f"runtime_config_invalid:{type(exc).__name__}"],
        }
