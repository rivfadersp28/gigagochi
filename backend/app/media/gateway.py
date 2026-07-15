from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from dataclasses import dataclass
from typing import Protocol

from app.media.contracts import (
    ImageProvider,
    ImageRequest,
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
        concurrency_admission: Callable[[str], AbstractContextManager[None]] | None = None,
        storage_admission: Callable[[str], AbstractContextManager[None]] | None = None,
    ) -> None:
        self._image_providers = {name.lower(): value for name, value in image_providers.items()}
        self._video_providers = {name.lower(): value for name, value in video_providers.items()}
        self._router = router
        self._concurrency_admission = concurrency_admission or (lambda _kind: nullcontext())
        self._storage_admission = storage_admission or (lambda _kind: nullcontext())

    def generate_image(self, request: ImageRequest) -> bytes:
        with self.generate_image_reserved(request) as payload:
            return payload

    @contextmanager
    def generate_image_reserved(self, request: ImageRequest) -> Iterator[bytes]:
        route = self._router.resolve_image(request)
        provider = self._image_providers.get(route.provider.lower())
        if provider is None:
            raise MediaError(f"Image provider is not registered: {route.provider}")
        if request.required_capability not in provider.capabilities:
            raise MediaError(
                f"Image provider {provider.name!r} does not support "
                f"{request.required_capability.value}"
            )
        storage_stack = ExitStack()
        try:
            with self._concurrency_admission("image"):
                storage_stack.enter_context(self._storage_admission("image"))
                payload = provider.generate_image(request)
            # Provider capacity is free here; storage capacity remains reserved
            # while the caller validates/transforms and atomically commits bytes.
            yield payload
        finally:
            storage_stack.close()

    def generate_video(self, request: VideoRequest) -> bytes:
        with self.generate_video_reserved(request) as payload:
            return payload

    @contextmanager
    def generate_video_reserved(self, request: VideoRequest) -> Iterator[bytes]:
        route = self._router.resolve_video(request)
        provider = self._video_providers.get(route.provider.lower())
        if provider is None:
            raise MediaError(f"Video provider is not registered: {route.provider}")
        if request.required_capability not in provider.capabilities:
            raise MediaError(
                f"Video provider {provider.name!r} does not support "
                f"{request.required_capability.value}"
            )
        storage_stack = ExitStack()
        try:
            with self._concurrency_admission("video"):
                storage_stack.enter_context(self._storage_admission("video"))
                payload = provider.generate_video(request)
            yield payload
        finally:
            storage_stack.close()
