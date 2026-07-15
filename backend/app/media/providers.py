from __future__ import annotations

from app.media.contracts import ImageRequest, MediaCapability, VideoRequest


class OpenAIImageProvider:
    name = "openai"
    capabilities = frozenset({MediaCapability.TEXT_TO_IMAGE, MediaCapability.IMAGE_TO_IMAGE})

    def generate_image(self, request: ImageRequest) -> bytes:
        from app.services.image_service import generate_openai_image_bytes

        return generate_openai_image_bytes(
            request.prompt,
            label=request.task,
            size=request.size,
            input_references=list(request.input_references),
        )


class OpenRouterImageProvider:
    name = "openrouter"
    capabilities = frozenset({MediaCapability.TEXT_TO_IMAGE, MediaCapability.IMAGE_TO_IMAGE})

    def generate_image(self, request: ImageRequest) -> bytes:
        from app.services.image_service import generate_openrouter_image_bytes

        return generate_openrouter_image_bytes(
            request.prompt,
            label=request.task,
            size=request.size,
            input_references=list(request.input_references),
        )


class KandinskyImageProvider:
    name = "kandinsky"
    capabilities = frozenset({MediaCapability.TEXT_TO_IMAGE, MediaCapability.IMAGE_TO_IMAGE})

    def generate_image(self, request: ImageRequest) -> bytes:
        from app.services.image_service import generate_kandinsky_image_bytes

        return generate_kandinsky_image_bytes(
            request.prompt,
            label=request.task,
            size=request.size,
            input_references=list(request.input_references),
        )


class KandinskyVideoProvider:
    name = "kandinsky"
    capabilities = frozenset({MediaCapability.IMAGE_TO_VIDEO})

    def generate_video(self, request: VideoRequest) -> bytes:
        from app.services.image_service import generate_kandinsky_video_from_image_bytes

        if request.source_image is None:
            raise ValueError("Kandinsky video generation requires source_image")
        return generate_kandinsky_video_from_image_bytes(
            request.source_image,
            label=request.task,
            prompt=request.prompt,
        )


class OpenRouterVideoProvider:
    name = "openrouter"
    capabilities = frozenset({MediaCapability.IMAGE_TO_VIDEO, MediaCapability.REFERENCE_TO_VIDEO})

    def generate_video(self, request: VideoRequest) -> bytes:
        from app.services.image_service import generate_openrouter_video_from_image_bytes

        return generate_openrouter_video_from_image_bytes(
            request.source_image,
            label=request.task,
            prompt=request.prompt,
            resolution=request.resolution,
            aspect_ratio=request.aspect_ratio,
            duration=request.duration_seconds,
            input_references=list(request.input_references),
            model=request.model,
        )
