from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.media.contracts import (
    ImageProvider,
    ImageRequest,
    MediaCapability,
    MediaError,
    VideoProvider,
    VideoRequest,
)


@dataclass(frozen=True, slots=True)
class MediaRoute:
    provider: str


class MediaRouter(Protocol):
    def resolve_image(self, request: ImageRequest) -> MediaRoute: ...

    def resolve_video(self, request: VideoRequest) -> MediaRoute: ...


class MediaGateway:
    def __init__(
        self,
        *,
        image_providers: dict[str, ImageProvider],
        video_providers: dict[str, VideoProvider],
        router: MediaRouter,
    ) -> None:
        self._image_providers = {name.lower(): value for name, value in image_providers.items()}
        self._video_providers = {name.lower(): value for name, value in video_providers.items()}
        self._router = router

    def generate_image(self, request: ImageRequest) -> bytes:
        route = self._router.resolve_image(request)
        provider = self._image_providers.get(route.provider.lower())
        if provider is None:
            raise MediaError(f"Image provider is not registered: {route.provider}")
        if request.required_capability not in provider.capabilities:
            raise MediaError(
                f"Image provider {provider.name!r} does not support "
                f"{request.required_capability.value}"
            )
        return provider.generate_image(request)

    def generate_video(self, request: VideoRequest) -> bytes:
        route = self._router.resolve_video(request)
        provider = self._video_providers.get(route.provider.lower())
        if provider is None:
            raise MediaError(f"Video provider is not registered: {route.provider}")
        if MediaCapability.IMAGE_TO_VIDEO not in provider.capabilities:
            raise MediaError(f"Video provider {provider.name!r} does not support image_to_video")
        return provider.generate_video(request)
