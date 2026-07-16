from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.media.concurrency import FileSlotMediaAdmission
from app.media.contracts import ImageRequest, VideoRequest
from app.media.gateway import MediaGateway, MediaRoute
from app.media.providers import (
    KandinskyImageProvider,
    KandinskyVideoProvider,
    OpenAIImageProvider,
    OpenRouterImageProvider,
    OpenRouterVideoProvider,
)
from app.services.storage_health_service import reserve_media_storage_capacity


class MediaRuntimeConfigError(RuntimeError):
    pass


BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class _Profile:
    image_provider: str
    video_provider: str
    image_tasks: dict[str, str]
    video_tasks: dict[str, str]


def _clean(value: Any) -> str:
    return str(value or "").strip().lower()


def _runtime_path(settings: Settings) -> Path:
    path = Path(settings.media_runtime_path).expanduser()
    if path.is_absolute() or path.exists():
        return path
    return Path(__file__).resolve().parents[2] / path


def _media_concurrency_lock_dir(settings: Settings) -> Path:
    path = Path(settings.media_concurrency_lock_dir).expanduser()
    if path.is_absolute():
        return path
    return BACKEND_ROOT / path


def _provider(value: Any, *, location: str) -> str:
    if isinstance(value, dict):
        value = value.get("provider")
    provider = _clean(value)
    if not provider:
        raise MediaRuntimeConfigError(f"{location} must define provider")
    return provider


def _task_routes(value: Any, *, location: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MediaRuntimeConfigError(f"{location} must be an object")
    routes: dict[str, str] = {}
    for raw_task, raw_route in value.items():
        task = str(raw_task).strip()
        if not task:
            raise MediaRuntimeConfigError(f"{location} contains an empty task")
        routes[task] = _provider(raw_route, location=f"{location}.{task}")
    return routes


def _load_profile(settings: Settings) -> tuple[str, _Profile]:
    path = _runtime_path(settings)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MediaRuntimeConfigError(f"Media runtime config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MediaRuntimeConfigError(f"Invalid media runtime JSON at {path}: {exc}") from exc
    profiles = payload.get("profiles") if isinstance(payload, dict) else None
    if not isinstance(profiles, dict) or not profiles:
        raise MediaRuntimeConfigError("Media runtime must define non-empty profiles")
    profile_name = str(settings.media_profile or payload.get("activeProfile") or "").strip()
    raw_profile = profiles.get(profile_name)
    if not isinstance(raw_profile, dict):
        raise MediaRuntimeConfigError(f"Unknown media profile: {profile_name}")
    image = raw_profile.get("image")
    video = raw_profile.get("video")
    if not isinstance(image, dict) or not isinstance(video, dict):
        raise MediaRuntimeConfigError(
            f"profiles.{profile_name} must define image and video objects"
        )
    return profile_name, _Profile(
        image_provider=_provider(image.get("default"), location="image.default"),
        video_provider=_provider(video.get("default"), location="video.default"),
        image_tasks=_task_routes(image.get("tasks"), location="image.tasks"),
        video_tasks=_task_routes(video.get("tasks"), location="video.tasks"),
    )


class RuntimeMediaRouter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.profile_name, self._profile = _load_profile(settings)

    def _legacy_provider(self, provider: str) -> str:
        return self._settings.ai_provider if provider == "legacy" else provider

    def resolve_image(self, request: ImageRequest) -> MediaRoute:
        if request.provider:
            return MediaRoute(request.provider)
        provider = self._profile.image_tasks.get(request.task, self._profile.image_provider)
        return MediaRoute(self._legacy_provider(provider))

    def resolve_video(self, request: VideoRequest) -> MediaRoute:
        if request.provider:
            return MediaRoute(request.provider)
        provider = self._profile.video_tasks.get(request.task, self._profile.video_provider)
        return MediaRoute(self._legacy_provider(provider))

    def provider_names(self) -> frozenset[str]:
        return self.image_provider_names() | self.video_provider_names()

    def image_provider_names(self) -> frozenset[str]:
        values = {
            self._profile.image_provider,
            *self._profile.image_tasks.values(),
        }
        return frozenset(self._legacy_provider(value) for value in values)

    def video_provider_names(self) -> frozenset[str]:
        values = {
            self._profile.video_provider,
            *self._profile.video_tasks.values(),
        }
        return frozenset(self._legacy_provider(value) for value in values)


@lru_cache
def get_media_router() -> RuntimeMediaRouter:
    return RuntimeMediaRouter(get_settings())


@lru_cache
def get_media_concurrency_admission() -> FileSlotMediaAdmission:
    settings = get_settings()
    return FileSlotMediaAdmission(
        _media_concurrency_lock_dir(settings),
        image_slots=settings.media_image_concurrency,
        video_slots=settings.media_video_concurrency,
        acquire_timeout_seconds=settings.media_admission_timeout_seconds,
    )


@lru_cache
def get_media_gateway() -> MediaGateway:
    return MediaGateway(
        image_providers={
            "openai": OpenAIImageProvider(),
            "openrouter": OpenRouterImageProvider(),
            "kandinsky": KandinskyImageProvider(),
        },
        video_providers={
            "openrouter": OpenRouterVideoProvider(),
            "kandinsky": KandinskyVideoProvider(),
        },
        router=get_media_router(),
        concurrency_admission=get_media_concurrency_admission().acquire,
        storage_admission=reserve_media_storage_capacity,
    )


def clear_media_runtime_caches() -> None:
    get_media_gateway.cache_clear()
    get_media_concurrency_admission.cache_clear()
    get_media_router.cache_clear()


def media_runtime_status() -> dict[str, Any]:
    try:
        settings = get_settings()
        router = get_media_router()
        # Pet creation always uses OpenAI for its primary image lineage even when
        # the general media profile points at another provider.
        providers = router.provider_names() | {"openai"}
        errors: list[str] = []
        unknown_image_providers = router.image_provider_names() - {
            "openai",
            "openrouter",
            "kandinsky",
        }
        for provider in sorted(unknown_image_providers):
            errors.append(f"image_provider_not_registered:{provider}")
        for provider in sorted(router.video_provider_names() - {"openrouter", "kandinsky"}):
            errors.append(f"video_provider_not_registered:{provider}")
        if "openai" in providers and not (settings.openai_api_key or "").strip():
            errors.append("openai_credentials_missing")
        if "openrouter" in providers:
            openrouter_key = (settings.openrouter_api_key or "").strip()
            legacy_key = (settings.openai_api_key or "").strip()
            if not openrouter_key and not legacy_key.startswith("sk-or-"):
                errors.append("openrouter_credentials_missing")
        if "kandinsky" in providers and not (settings.kandinsky_api_key or "").strip():
            errors.append("kandinsky_credentials_missing")
        return {
            "status": "ok" if not errors else "degraded",
            "profile": router.profile_name,
            "providers": sorted(providers),
            "errors": errors,
        }
    except Exception as exc:
        return {"status": "degraded", "errors": [str(exc)]}
