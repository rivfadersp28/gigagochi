from __future__ import annotations

import json
from types import SimpleNamespace

from app.media.contracts import ImageRequest, MediaCapability, VideoRequest
from app.media.gateway import MediaGateway, MediaRoute
from app.media.runtime import RuntimeMediaRouter, media_runtime_status


def _runtime_file(tmp_path):
    path = tmp_path / "media_runtime.json"
    path.write_text(
        json.dumps(
            {
                "activeProfile": "legacy",
                "profiles": {
                    "legacy": {
                        "image": {
                            "default": {"provider": "legacy"},
                            "tasks": {"background_story/image": {"provider": "kandinsky"}},
                        },
                        "video": {
                            "default": {"provider": "openrouter"},
                            "tasks": {},
                        },
                    },
                    "kandinsky": {
                        "image": {
                            "default": {"provider": "kandinsky"},
                            "tasks": {},
                        },
                        "video": {
                            "default": {"provider": "kandinsky"},
                            "tasks": {},
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_media_runtime_routes_kinds_independently_and_supports_task_override(tmp_path) -> None:
    router = RuntimeMediaRouter(
        SimpleNamespace(
            media_runtime_path=str(_runtime_file(tmp_path)),
            media_profile="legacy",
            ai_provider="openai",
        )
    )

    default_image = router.resolve_image(ImageRequest(prompt="pet", task="pet_creation/image"))
    story_image = router.resolve_image(ImageRequest(prompt="story", task="background_story/image"))
    video = router.resolve_video(
        VideoRequest(prompt="blink", source_image=b"png", task="pet_creation/video")
    )

    assert default_image.provider == "openai"
    assert story_image.provider == "kandinsky"
    assert video.provider == "openrouter"


def test_kandinsky_profile_routes_video_to_kandinsky(tmp_path) -> None:
    router = RuntimeMediaRouter(
        SimpleNamespace(
            media_runtime_path=str(_runtime_file(tmp_path)),
            media_profile="kandinsky",
            ai_provider="openai",
        )
    )

    assert router.resolve_image(ImageRequest(prompt="pet")).provider == "kandinsky"
    assert (
        router.resolve_video(VideoRequest(prompt="blink", source_image=b"png")).provider
        == "kandinsky"
    )


def test_image_request_provider_overrides_runtime_profile(tmp_path) -> None:
    router = RuntimeMediaRouter(
        SimpleNamespace(
            media_runtime_path=str(_runtime_file(tmp_path)),
            media_profile="legacy",
            ai_provider="openai",
        )
    )

    route = router.resolve_image(
        ImageRequest(
            prompt="pet",
            task="pet_creation/image",
            provider="KANDINSKY",
        )
    )

    assert route.provider == "kandinsky"


def test_video_request_provider_overrides_runtime_profile(tmp_path) -> None:
    router = RuntimeMediaRouter(
        SimpleNamespace(
            media_runtime_path=str(_runtime_file(tmp_path)),
            media_profile="legacy",
            ai_provider="openai",
        )
    )

    route = router.resolve_video(
        VideoRequest(
            prompt="blink",
            source_image=b"png",
            provider="KANDINSKY",
        )
    )

    assert route.provider == "kandinsky"


def test_media_gateway_selects_i2i_capability() -> None:
    captured: dict[str, object] = {}

    class Router:
        def resolve_image(self, _request):
            return MediaRoute("test")

        def resolve_video(self, _request):
            return MediaRoute("test-video")

    class ImageProvider:
        name = "test"
        capabilities = frozenset({MediaCapability.TEXT_TO_IMAGE, MediaCapability.IMAGE_TO_IMAGE})

        def generate_image(self, request):
            captured["capability"] = request.required_capability
            return b"image"

    gateway = MediaGateway(
        image_providers={"test": ImageProvider()},
        video_providers={},
        router=Router(),
    )
    result = gateway.generate_image(
        ImageRequest(
            prompt="restyle",
            input_references=({"image_url": {"url": "data:image/png;base64,eA=="}},),
        )
    )

    assert result == b"image"
    assert captured["capability"] is MediaCapability.IMAGE_TO_IMAGE


def test_media_runtime_status_requires_kandinsky_credentials(monkeypatch, tmp_path) -> None:
    settings = SimpleNamespace(
        media_runtime_path=str(_runtime_file(tmp_path)),
        media_profile="kandinsky",
        ai_provider="openai",
        openai_api_key="openai-key",
        openrouter_api_key=None,
        kandinsky_api_key=None,
    )
    monkeypatch.setattr("app.media.runtime.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.media.runtime.get_media_router",
        lambda: RuntimeMediaRouter(settings),
    )

    status = media_runtime_status()

    assert status["status"] == "degraded"
    assert status["profile"] == "kandinsky"
    assert status["providers"] == ["kandinsky", "openai"]
    assert status["errors"] == ["kandinsky_credentials_missing"]


def test_media_runtime_status_requires_primary_openai_credentials(
    monkeypatch, tmp_path
) -> None:
    settings = SimpleNamespace(
        media_runtime_path=str(_runtime_file(tmp_path)),
        media_profile="legacy",
        ai_provider="openrouter",
        openai_api_key=None,
        openrouter_api_key="openrouter-key",
        kandinsky_api_key="kandinsky-key",
    )
    monkeypatch.setattr("app.media.runtime.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.media.runtime.get_media_router",
        lambda: RuntimeMediaRouter(settings),
    )

    status = media_runtime_status()

    assert status["status"] == "degraded"
    assert status["providers"] == ["kandinsky", "openai", "openrouter"]
    assert status["errors"] == ["openai_credentials_missing"]
