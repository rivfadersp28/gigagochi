from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class MediaError(RuntimeError):
    pass


class MediaProviderError(MediaError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MediaCapability(StrEnum):
    TEXT_TO_IMAGE = "text_to_image"
    IMAGE_TO_IMAGE = "image_to_image"
    IMAGE_TO_VIDEO = "image_to_video"


@dataclass(frozen=True, slots=True)
class ImageRequest:
    prompt: str
    task: str = "default"
    size: str | None = None
    input_references: tuple[dict[str, object], ...] = ()
    provider: str | None = None

    def __post_init__(self) -> None:
        prompt = self.prompt.strip()
        task = self.task.strip()
        if not prompt:
            raise ValueError("prompt must not be empty")
        if not task:
            raise ValueError("task must not be empty")
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(self, "task", task)
        object.__setattr__(self, "provider", (self.provider or "").strip().lower() or None)
        object.__setattr__(
            self,
            "input_references",
            tuple(dict(reference) for reference in self.input_references),
        )

    @property
    def required_capability(self) -> MediaCapability:
        if self.input_references:
            return MediaCapability.IMAGE_TO_IMAGE
        return MediaCapability.TEXT_TO_IMAGE


@dataclass(frozen=True, slots=True)
class VideoRequest:
    prompt: str
    source_image: bytes
    task: str = "default"
    resolution: str = "720p"
    aspect_ratio: str = "9:16"
    duration_seconds: int = 4
    generate_audio: bool = False
    provider: str | None = None

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if not self.source_image:
            raise ValueError("source_image must not be empty")
        if not self.task.strip():
            raise ValueError("task must not be empty")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be greater than zero")
        object.__setattr__(self, "provider", (self.provider or "").strip().lower() or None)


@runtime_checkable
class ImageProvider(Protocol):
    name: str
    capabilities: frozenset[MediaCapability]

    def generate_image(self, request: ImageRequest) -> bytes: ...


@runtime_checkable
class VideoProvider(Protocol):
    name: str
    capabilities: frozenset[MediaCapability]

    def generate_video(self, request: VideoRequest) -> bytes: ...
