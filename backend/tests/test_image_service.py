from __future__ import annotations

import base64
import json
import socket
import subprocess
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image, ImageDraw

from app.llm import LLMProviderError
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.prompts.pet_image_prompts import (
    build_character_bible_prompt,
    build_pet_single_sprite_prompt,
    build_pet_state_strip_prompt,
)
from app.services.image_service import (
    CHARACTER_BIBLE_SCHEMA,
    IMAGE_RESULT_MAX_BYTES,
    KANDINSKY_PET_SCENE_VIDEO_PROMPT,
    PET_HAPPY_SCENE_IMAGE_PROMPT,
    PET_SAD_SCENE_IMAGE_PROMPT,
    PET_SAD_SCENE_VIDEO_PROMPT,
    PET_SCENE_VIDEO_PROMPT,
    REFERENCE_IMAGE_MAX_BYTES,
    VIDEO_RESULT_MAX_BYTES,
    MediaResultError,
    PetAssetImageSet,
    _character_reasoning_effort_kwargs,
    _compact_kandinsky_prompt,
    _download_openrouter_video_bytes,
    _image_result_bytes,
    _internal_reference_image_url,
    _kandinsky_create_task,
    _kandinsky_download_result,
    _kandinsky_reference_image_b64,
    _local_reference_image_bytes,
    _probe_generated_video,
    _reference_image_bytes,
    _submit_openrouter_video_job,
    _trusted_openrouter_polling_url,
    align_sprite_to_reference_canvas,
    build_pet_asset_set_response,
    character_bible_quality_issues,
    composite_pet_character_region_bytes,
    create_character_bible,
    extract_sprite_cells,
    extract_state_strip_cells,
    foreground_component_bbox,
    generate_image_edit_bytes,
    generate_individual_sprite_paths,
    generate_kandinsky_image_bytes,
    generate_kandinsky_video_from_image_bytes,
    generate_openai_image_bytes,
    generate_openrouter_image_bytes,
    generate_openrouter_video_bytes,
    generate_pet_asset_set,
    generate_pet_happy_scene_path,
    generate_pet_happy_video_for_image_asset_set,
    generate_pet_image_asset_set,
    generate_pet_sad_scene_path,
    generate_pet_sad_video_for_image_asset_set,
    generate_pet_scene_video_bytes,
    generation_error_code,
    normalize_pet_scene_video_frame_bytes,
    pet_character_region_box,
    render_ping_pong_video_bytes,
    strip_generated_video_auxiliary_streams,
)
from app.services.storage_health_service import StorageCapacityError
from app.services.tone_runtime import tone_context_payload


def _reserved(fake):
    @contextmanager
    def reservation(*args, **kwargs):
        yield fake(*args, **kwargs)

    return reservation


def image_contains_color(image: Image.Image, color: tuple[int, int, int, int]) -> bool:
    pixels = image.load()
    width, height = image.size
    return any(pixels[x, y] == color for y in range(height) for x in range(width))


def test_generation_error_code_preserves_storage_capacity_failure() -> None:
    error = StorageCapacityError(media_kind="image", reason="LOW_DISK_SPACE")

    assert generation_error_code(error) == "STORAGE_CAPACITY_LOW"


def test_kandinsky_idle_video_prompt_allows_subtle_body_motion() -> None:
    prompt = KANDINSKY_PET_SCENE_VIDEO_PROMPT

    assert "спокойно дышит" in prompt
    assert "покачивание корпуса" in prompt
    assert "наклон или поворот головы" in prompt
    assert "Ступни остаются на тех же точках опоры" in prompt
    assert "Без ходьбы" in prompt
    assert "Не меняй анатомию, пропорции" in prompt


def test_ping_pong_video_trims_seedance_preroll_before_reversing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffmpeg_commands: list[list[str]] = []
    subprocess_options: list[dict[str, Any]] = []

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        subprocess_options.append(kwargs)
        if command[0] == "ffprobe":
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "streams": [{"avg_frame_rate": "24/1", "width": 720, "height": 1280}],
                        "format": {"duration": "5.0"},
                    }
                )
            )

        ffmpeg_commands.append(command)
        Path(command[-1]).write_bytes(b"ping-pong-video")
        return SimpleNamespace()

    monkeypatch.setattr("app.services.image_service.subprocess.run", fake_run)

    assert (
        render_ping_pong_video_bytes(b"seedance-video", start_offset_seconds=0.2)
        == b"ping-pong-video"
    )

    filter_graph = ffmpeg_commands[0][ffmpeg_commands[0].index("-filter_complex") + 1]
    assert filter_graph.startswith("[0:v]trim=start=0.200000:")
    assert filter_graph.index("trim=start=0.200000") < filter_graph.index("split=2")
    assert filter_graph.index("split=2") < filter_graph.index("reverse")
    assert filter_graph.endswith("concat=n=2:v=1:a=0,fps=24[out]")
    command = ffmpeg_commands[0]
    assert command[command.index("-f") + 1] == "mov"
    assert command[command.index("-protocol_whitelist") + 1] == "file"
    assert command[command.index("-enable_drefs") + 1] == "0"
    assert command[command.index("-use_absolute_path") + 1] == "0"
    assert command[command.index("-level:v") + 1] == "3.1"
    assert command[command.index("-video_track_timescale") + 1] == "12288"
    assert command[command.index("-fs") + 1] == str(100 * 1024 * 1024)
    assert subprocess_options[0]["timeout"] == 30
    assert subprocess_options[1]["timeout"] == 180


def test_strip_generated_video_auxiliary_streams_keeps_only_primary_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        if command[0] == "ffprobe":
            captured["probe_options"] = kwargs
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "streams": [{"avg_frame_rate": "30/1", "width": 1280, "height": 720}],
                        "format": {"duration": "15.0"},
                    }
                )
            )
        captured["command"] = command
        captured["process_options"] = kwargs
        Path(command[-1]).write_bytes(b"main-video-only")
        return SimpleNamespace()

    monkeypatch.setattr("app.services.image_service.subprocess.run", fake_run)

    assert strip_generated_video_auxiliary_streams(b"grok-video") == b"main-video-only"
    command = captured["command"]
    assert command[command.index("-f") + 1] == "mov"
    assert command[command.index("-protocol_whitelist") + 1] == "file"
    assert command[command.index("-enable_drefs") + 1] == "0"
    assert command[command.index("-use_absolute_path") + 1] == "0"
    assert command[command.index("-map") + 1] == "0:v:0"
    assert "-an" in command
    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-movflags") + 1] == "+faststart"
    assert command[command.index("-fs") + 1] == str(100 * 1024 * 1024)
    assert captured["probe_options"]["timeout"] == 30
    assert captured["process_options"]["timeout"] == 180


def test_generated_video_probe_forces_local_mp4_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def fake_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        captured.extend(command)
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "streams": [{"avg_frame_rate": "30/1", "width": 1280, "height": 720}],
                    "format": {"duration": "5.0"},
                }
            )
        )

    monkeypatch.setattr("app.services.image_service.subprocess.run", fake_run)

    _probe_generated_video(Path("synthetic.mp4"))

    assert captured[captured.index("-f") + 1] == "mov"
    assert captured[captured.index("-protocol_whitelist") + 1] == "file"
    assert captured[captured.index("-enable_drefs") + 1] == "0"
    assert captured[captured.index("-use_absolute_path") + 1] == "0"
    assert captured[captured.index("-i") + 1] == "synthetic.mp4"


@pytest.mark.parametrize(
    ("stream", "duration", "expected_code"),
    [
        (
            {"avg_frame_rate": "24/1", "width": 4097, "height": 720},
            "5.0",
            "VIDEO_RESULT_DIMENSIONS_EXCEEDED",
        ),
        (
            {"avg_frame_rate": "24/1", "width": 4096, "height": 2161},
            "5.0",
            "VIDEO_RESULT_DIMENSIONS_EXCEEDED",
        ),
        (
            {"avg_frame_rate": "24/1", "width": 1280, "height": 720},
            "61.0",
            "VIDEO_RESULT_DURATION_EXCEEDED",
        ),
        (
            {"avg_frame_rate": "0/0", "width": 1280, "height": 720},
            "5.0",
            "VIDEO_RESULT_INVALID",
        ),
    ],
)
def test_video_postprocessing_rejects_unsafe_metadata_before_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
    stream: dict[str, object],
    duration: str,
    expected_code: str,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "streams": [stream],
                    "format": {"duration": duration},
                }
            )
        )

    monkeypatch.setattr("app.services.image_service.subprocess.run", fake_run)

    with pytest.raises(MediaResultError) as raised:
        strip_generated_video_auxiliary_streams(b"synthetic-video")

    assert raised.value.code == expected_code
    assert [command[0] for command in commands] == ["ffprobe"]


def test_video_postprocessing_converts_probe_timeout_to_stable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(command, timeout=30)

    monkeypatch.setattr("app.services.image_service.subprocess.run", fake_run)

    with pytest.raises(MediaResultError) as raised:
        strip_generated_video_auxiliary_streams(b"synthetic-video")

    assert raised.value.code == "VIDEO_PROCESS_TIMEOUT"


def test_video_postprocessing_converts_ffmpeg_timeout_to_stable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        if command[0] == "ffprobe":
            return SimpleNamespace(
                stdout=json.dumps(
                    {
                        "streams": [{"avg_frame_rate": "30/1", "width": 1280, "height": 720}],
                        "format": {"duration": "15.0"},
                    }
                )
            )
        raise subprocess.TimeoutExpired(command, timeout=180)

    monkeypatch.setattr("app.services.image_service.subprocess.run", fake_run)

    with pytest.raises(MediaResultError) as raised:
        strip_generated_video_auxiliary_streams(b"synthetic-video")

    assert raised.value.code == "VIDEO_PROCESS_TIMEOUT"


@pytest.mark.parametrize(
    ("resolved_provider", "video_model", "expected_start_offset"),
    [
        ("openrouter", "bytedance/seedance-2.0", 0.2),
        ("openrouter", "x-ai/grok-imagine-video", 0.1),
        ("kandinsky", "x-ai/grok-imagine-video", 0.1),
    ],
)
def test_pet_scene_video_uses_model_specific_preroll_trim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resolved_provider: str,
    video_model: str,
    expected_start_offset: float,
) -> None:
    captured: dict[str, Any] = {}
    scene_path = tmp_path / "scene.png"
    scene_path.write_bytes(b"scene")

    class FakeRouter:
        def resolve_video(self, _request: Any) -> SimpleNamespace:
            return SimpleNamespace(provider=resolved_provider)

    class FakeGateway:
        def generate_video(self, _request: Any) -> bytes:
            return b"provider-video"

    def fake_render(video_bytes: bytes, **kwargs: Any) -> bytes:
        captured.update(video_bytes=video_bytes, **kwargs)
        return b"ping-pong-video"

    monkeypatch.setattr("app.services.image_service.get_media_router", FakeRouter)
    monkeypatch.setattr("app.services.image_service.get_media_gateway", FakeGateway)
    monkeypatch.setattr("app.services.image_service.render_ping_pong_video_bytes", fake_render)
    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(openrouter_video_model=video_model),
    )

    assert generate_pet_scene_video_bytes(scene_path) == b"ping-pong-video"
    assert captured == {
        "video_bytes": b"provider-video",
        "start_offset_seconds": expected_start_offset,
    }


def color_bbox(image: Image.Image, color: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    pixels = image.load()
    width, height = image.size
    points = [(x, y) for y in range(height) for x in range(width) if pixels[x, y] == color]
    assert points
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def png_bytes(image: Image.Image) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def png_header_bytes(width: int, height: int) -> bytes:
    """Build a tiny PNG header declaring dimensions without allocating its pixels."""

    import struct
    import zlib

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def test_normalize_pet_scene_video_frame_bytes_crops_to_seedance_frame() -> None:
    image = Image.new("RGB", (1024, 1536), (200, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 0, 944, 1536), fill=(0, 160, 80))

    output = Image.open(BytesIO(normalize_pet_scene_video_frame_bytes(png_bytes(image))))

    assert output.size == (720, 1280)
    assert output.getpixel((0, 0)) == (0, 160, 80)
    assert output.getpixel((719, 1279)) == (0, 160, 80)


def test_pet_character_region_tracks_raised_scene_position() -> None:
    assert pet_character_region_box((720, 1280)) == (120, 100, 600, 820)


def test_composite_pet_character_region_preserves_pixels_outside_fixed_crop(tmp_path) -> None:
    scene_path = tmp_path / "scene.png"
    scene = Image.new("RGB", (720, 1280), (10, 20, 30))
    scene.save(scene_path)
    generated = Image.new("RGB", (1024, 1536), (200, 100, 50))

    result = Image.open(
        BytesIO(composite_pet_character_region_bytes(scene_path, png_bytes(generated)))
    )

    assert result.size == scene.size
    assert result.getpixel((0, 0)) == (10, 20, 30)
    assert result.getpixel((119, 700)) == (10, 20, 30)
    assert result.getpixel((600, 700)) == (10, 20, 30)
    assert result.getpixel((360, 680)) == (200, 100, 50)


def test_extract_sprite_cells_selects_component_and_aligns_bottom_padding() -> None:
    image = Image.new("RGBA", (400, 300), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)
    teen_color = (20, 140, 70, 255)
    adult_color = (30, 70, 190, 255)

    for row in range(3):
        for col in range(4):
            left = col * 100 + 30
            top = row * 100 + 30
            right = col * 100 + 70
            bottom = row * 100 + 70
            color = (170, 90, 40, 255)
            if row == 1 and col == 0:
                color = teen_color
                top = 115
                bottom = 150
            elif row == 2 and col == 0:
                color = adult_color
                top = 185
                bottom = 240
            draw.rectangle((left, top, right, bottom), fill=color)

    cells = extract_sprite_cells(image)
    teen_idle = cells[("teen", "idle")]
    adult_idle = cells[("adult", "idle")]

    assert teen_idle.size == (100, 100)
    assert adult_idle.size == (100, 100)
    assert not image_contains_color(teen_idle, adult_color)
    assert image_contains_color(teen_idle, teen_color)
    assert image_contains_color(adult_idle, adult_color)
    assert color_bbox(teen_idle, teen_color)[3] == color_bbox(adult_idle, adult_color)[3]


def test_extract_sprite_cells_preserves_real_transparency() -> None:
    image = Image.new("RGBA", (400, 300), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    light_character_color = (245, 238, 218, 255)

    for row in range(3):
        for col in range(4):
            left = col * 100 + 30
            top = row * 100 + 30
            right = col * 100 + 70
            bottom = row * 100 + 70
            draw.ellipse((left, top, right, bottom), fill=light_character_color)

    cells = extract_sprite_cells(image)
    baby_idle = cells[("baby", "idle")]

    assert baby_idle.size == (100, 100)
    assert baby_idle.getpixel((0, 0)) == (255, 255, 255, 0)
    assert image_contains_color(baby_idle, light_character_color)
    assert baby_idle.getchannel("A").getextrema() == (0, 255)


def test_extract_sprite_cells_preserves_opaque_background_pixels() -> None:
    image = Image.new("RGB", (400, 300), (245, 245, 245))
    draw = ImageDraw.Draw(image)

    for row in range(3):
        for col in range(4):
            left = col * 100 + 30
            top = row * 100 + 30
            right = col * 100 + 70
            bottom = row * 100 + 70
            draw.rectangle((left, top, right, bottom), fill=(40, 140, 75))

    cells = extract_sprite_cells(image)
    baby_idle = cells[("baby", "idle")]

    assert baby_idle.getpixel((0, 0)) == (245, 245, 245, 255)
    assert baby_idle.getchannel("A").getextrema() == (255, 255)


def test_extract_state_strip_cells_splits_horizontal_three_state_strip() -> None:
    image = Image.new("RGBA", (300, 100), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)
    idle_color = (20, 140, 70, 255)
    happy_color = (220, 180, 40, 255)
    sad_color = (40, 90, 190, 255)
    draw.rectangle((30, 30, 70, 70), fill=idle_color)
    draw.rectangle((130, 30, 170, 70), fill=happy_color)
    draw.rectangle((230, 30, 270, 70), fill=sad_color)

    cells = extract_state_strip_cells(image)

    assert set(cells) == {("teen", "idle"), ("teen", "happy"), ("teen", "sad")}
    assert cells[("teen", "idle")].size == (100, 100)
    assert image_contains_color(cells[("teen", "idle")], idle_color)
    assert image_contains_color(cells[("teen", "happy")], happy_color)
    assert image_contains_color(cells[("teen", "sad")], sad_color)
    assert not image_contains_color(cells[("teen", "idle")], happy_color)


def test_generate_image_bytes_uses_openrouter_image_endpoint(monkeypatch) -> None:
    captured: dict[str, object] = {}
    result_bytes = png_bytes(Image.new("RGB", (2, 2), (10, 20, 30)))

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"b64_json": base64.b64encode(result_bytes).decode()},
                ]
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            ai_provider="openrouter",
            openrouter_api_key="sk-or-test",
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_image_model="bytedance-seed/seedream-4.5",
            openrouter_site_url="https://app.example",
            openrouter_app_title="Test Tamagotchi",
            backend_public_url=None,
            webapp_url=None,
            openai_image_size="1536x1152",
            openai_image_quality="medium",
            openai_image_output_format="png",
            openai_image_timeout_seconds=180,
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)

    result = generate_openrouter_image_bytes("sprite prompt", label="pet_creation/image")

    assert result == result_bytes
    assert captured["url"] == "https://openrouter.ai/api/v1/images"
    assert captured["timeout"] == 180
    assert captured["headers"] == {
        "Authorization": "Bearer sk-or-test",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://app.example",
        "X-OpenRouter-Title": "Test Tamagotchi",
    }
    assert captured["json"] == {
        "model": "bytedance-seed/seedream-4.5",
        "prompt": "sprite prompt",
        "resolution": "4K",
        "aspect_ratio": "4:3",
        "quality": "medium",
        "n": 1,
        "output_format": "png",
    }


def test_generate_image_bytes_passes_openrouter_input_references(monkeypatch) -> None:
    captured: dict[str, object] = {}
    result_bytes = png_bytes(Image.new("RGB", (2, 2), (10, 20, 30)))

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"b64_json": base64.b64encode(result_bytes).decode()},
                ]
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            ai_provider="openrouter",
            openrouter_api_key="sk-or-test",
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_image_model="bytedance-seed/seedream-4.5",
            openrouter_site_url=None,
            openrouter_app_title="Test Tamagotchi",
            backend_public_url=None,
            webapp_url=None,
            openai_image_size="1536x1152",
            openai_image_quality="medium",
            openai_image_output_format="png",
            openai_image_timeout_seconds=180,
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)

    references = [
        {
            "type": "image_url",
            "image_url": {"url": "https://cdn.example.test/assets/baby-happy.png"},
        }
    ]

    result = generate_openrouter_image_bytes(
        "travel prompt",
        label="travel/image",
        input_references=references,
    )

    assert result == result_bytes
    assert captured["json"]["input_references"] == references


def test_generate_image_bytes_uses_openai_edit_for_input_reference(monkeypatch) -> None:
    captured: dict[str, object] = {}
    reference_bytes = png_bytes(Image.new("RGBA", (32, 32), (20, 140, 70, 255)))
    result_bytes = png_bytes(Image.new("RGB", (2, 2), (30, 20, 10)))

    class FakeDownloadResponse:
        headers = {
            "content-length": str(len(reference_bytes)),
            "content-type": "image/png",
        }

        def raise_for_status(self):
            return None

        def iter_bytes(self, **_kwargs):
            yield reference_bytes

    class FakeImages:
        def generate(self, **_kwargs):
            raise AssertionError("reference generation must use images.edit")

        def edit(self, **kwargs):
            captured.update(kwargs)
            image = kwargs["image"]
            captured["reference_name"] = image.name
            captured["reference_bytes"] = image.read()
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(result_bytes).decode())]
            )

    class FakeClient:
        images = FakeImages()

        def with_options(self, **kwargs):
            captured["client_options"] = kwargs
            return self

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            ai_provider="openai",
            openai_image_model="gpt-image-2",
            openai_image_size="1536x1152",
            openai_image_quality="medium",
            openai_image_output_format="png",
            openai_image_timeout_seconds=180,
            backend_public_url="https://cdn.example.test",
            webapp_url=None,
            backend_internal_url=None,
        ),
    )
    monkeypatch.setattr(
        "app.services.image_service.get_openai_platform_client",
        FakeClient,
    )

    @contextmanager
    def fake_stream(_method: str, url: str, **_kwargs):
        if url != "https://cdn.example.test/static/generated/assets/baby-happy.png":
            raise AssertionError(f"unexpected GET {url}")
        yield FakeDownloadResponse()

    monkeypatch.setattr(
        "app.services.image_service.httpx.stream",
        fake_stream,
    )

    result = generate_openai_image_bytes(
        "story prompt",
        label="background_story/image",
        input_references=[
            {
                "type": "image_url",
                "image_url": {
                    "url": ("https://cdn.example.test/static/generated/assets/baby-happy.png")
                },
            }
        ],
    )

    assert result == result_bytes
    assert captured["model"] == "gpt-image-2"
    assert captured["prompt"] == "story prompt"
    assert captured["reference_name"] == "reference-1.png"
    assert captured["reference_bytes"] == reference_bytes
    assert captured["client_options"] == {"max_retries": 0}


def test_internal_reference_url_rewrites_only_own_public_origin(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            backend_internal_url="http://backend:8000",
            backend_public_url="https://gigagochi.serega.works",
            webapp_url="https://gigagochi.serega.works/app",
        ),
    )

    assert (
        _internal_reference_image_url(
            "https://gigagochi.serega.works/static/generated/pet/idle.png?v=42"
        )
        == "http://backend:8000/static/generated/pet/idle.png?v=42"
    )
    assert (
        _internal_reference_image_url("https://cdn.example.test/static/generated/pet/idle.png?v=42")
        == "https://cdn.example.test/static/generated/pet/idle.png?v=42"
    )


def test_reference_image_reads_own_generated_asset_without_network(
    monkeypatch, tmp_path
) -> None:
    generated_root = tmp_path / "generated"
    image_path = generated_root / "pet" / "idle.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"local-image")
    public_url = "https://gigagochi.serega.works/static/generated/pet/idle.png?v=42"
    monkeypatch.setattr("app.services.image_service.GENERATED_ASSET_ROOT", generated_root)
    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            backend_internal_url="http://backend:8000",
            backend_public_url="https://gigagochi.serega.works",
            webapp_url="https://gigagochi.serega.works",
        ),
    )
    monkeypatch.setattr(
        "app.services.image_service.httpx.stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )

    assert _reference_image_bytes(public_url) == b"local-image"


def test_reference_image_uses_scene_when_isolated_character_asset_is_missing(
    monkeypatch, tmp_path
) -> None:
    generated_root = tmp_path / "generated"
    scene_path = generated_root / "outfit" / "teen-idle.png"
    scene_path.parent.mkdir(parents=True)
    scene_path.write_bytes(b"outfit-scene")
    character_url = (
        "https://gigagochi.serega.works/static/generated/"
        "outfit/teen-idle-character.png?v=42"
    )
    monkeypatch.setattr("app.services.image_service.GENERATED_ASSET_ROOT", generated_root)
    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            backend_internal_url="http://backend:8000",
            backend_public_url="https://gigagochi.serega.works",
            webapp_url="https://gigagochi.serega.works",
        ),
    )
    monkeypatch.setattr(
        "app.services.image_service.httpx.stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )

    assert _reference_image_bytes(character_url) == b"outfit-scene"


def test_local_reference_image_rejects_symlink_escape(monkeypatch, tmp_path) -> None:
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    (generated_root / "escape.png").symlink_to(outside)
    monkeypatch.setattr("app.services.image_service.GENERATED_ASSET_ROOT", generated_root)

    with pytest.raises(RuntimeError, match="REFERENCE_IMAGE_PATH_INVALID"):
        _local_reference_image_bytes(
            "https://gigagochi.serega.works/static/generated/escape.png"
        )


def test_local_reference_image_rejects_oversized_file(monkeypatch, tmp_path) -> None:
    generated_root = tmp_path / "generated"
    generated_root.mkdir()
    image_path = generated_root / "large.png"
    image_path.write_bytes(b"oversized")
    monkeypatch.setattr("app.services.image_service.GENERATED_ASSET_ROOT", generated_root)
    monkeypatch.setattr("app.services.image_service.REFERENCE_IMAGE_MAX_BYTES", 3)

    with pytest.raises(RuntimeError, match="REFERENCE_IMAGE_TOO_LARGE"):
        _local_reference_image_bytes(
            "https://gigagochi.serega.works/static/generated/large.png"
        )


def test_reference_download_falls_back_to_public_url(monkeypatch) -> None:
    public_url = "https://gigagochi.serega.works/static/generated/pet/idle.png"
    calls: list[str] = []

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            backend_internal_url="http://backend:8000",
            backend_public_url="https://gigagochi.serega.works",
            webapp_url="https://gigagochi.serega.works",
        ),
    )

    class PublicResponse:
        headers = {"content-type": "image/png"}

        def raise_for_status(self):
            return None

        def iter_bytes(self, **_kwargs):
            yield b"public-image"

    @contextmanager
    def fake_stream(_method: str, url: str, **_kwargs):
        calls.append(url)
        if url == "http://backend:8000/static/generated/pet/idle.png":
            raise httpx.ConnectError("internal backend unavailable")
        yield PublicResponse()

    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)

    assert _reference_image_bytes(public_url) == b"public-image"
    assert calls == [
        "http://backend:8000/static/generated/pet/idle.png",
        public_url,
    ]


def test_reference_download_rejects_untrusted_url_without_network(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            backend_internal_url="http://backend:8000",
            backend_public_url="https://gigagochi.example",
            webapp_url=None,
        ),
    )
    monkeypatch.setattr(
        "app.services.image_service.httpx.stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )

    with pytest.raises(RuntimeError, match="REFERENCE_IMAGE_URL_UNTRUSTED"):
        _reference_image_bytes("http://169.254.169.254/latest/meta-data.png")


def test_reference_download_rejects_oversized_stream(monkeypatch) -> None:
    public_url = "https://gigagochi.example/static/generated/pet/idle.png"
    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            backend_internal_url=None,
            backend_public_url="https://gigagochi.example",
            webapp_url=None,
        ),
    )

    class OversizedResponse:
        headers = {
            "content-type": "image/png",
            "content-length": str(REFERENCE_IMAGE_MAX_BYTES + 1),
        }

        def raise_for_status(self):
            return None

        def iter_bytes(self, **_kwargs):
            raise AssertionError("oversized response must be rejected before reading")

    @contextmanager
    def fake_stream(_method: str, _url: str, **_kwargs):
        yield OversizedResponse()

    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)

    with pytest.raises(RuntimeError, match="REFERENCE_IMAGE_TOO_LARGE"):
        _reference_image_bytes(public_url)


def test_image_result_base64_rejects_oversize_before_decode(monkeypatch) -> None:
    encoded = base64.b64encode(b"four-bytes")
    monkeypatch.setattr("app.services.image_service.IMAGE_RESULT_MAX_BYTES", 3)
    monkeypatch.setattr(
        "app.services.image_service.base64.b64decode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not decode")),
    )

    with pytest.raises(MediaResultError, match="IMAGE_RESULT_TOO_LARGE") as error:
        _image_result_bytes({"b64_json": encoded})

    assert generation_error_code(error.value) == "IMAGE_RESULT_TOO_LARGE"


def test_image_result_base64_validates_then_can_be_reopened() -> None:
    expected = Image.new("RGB", (2, 3), (12, 34, 56))
    payload = png_bytes(expected)

    result = _image_result_bytes({"b64_json": base64.b64encode(payload).decode()})

    with Image.open(BytesIO(result)) as reopened:
        reopened.load()
        assert reopened.size == (2, 3)
        assert reopened.getpixel((1, 2)) == (12, 34, 56)


def test_image_result_rejects_invalid_image_payload_with_stable_code() -> None:
    encoded = base64.b64encode(b"not-an-image").decode()

    with pytest.raises(MediaResultError, match="IMAGE_RESULT_INVALID") as error:
        _image_result_bytes({"b64_json": encoded})

    assert generation_error_code(error.value) == "IMAGE_RESULT_INVALID"


@pytest.mark.parametrize(
    ("width", "height"),
    [
        (8193, 1),
        (5000, 4000),
        (200_000, 200_000),
    ],
)
def test_image_result_rejects_decompression_bomb_headers_without_pixel_allocation(
    width: int,
    height: int,
) -> None:
    payload = png_header_bytes(width, height)

    with pytest.raises(MediaResultError, match="IMAGE_RESULT_DIMENSIONS_EXCEEDED") as error:
        _image_result_bytes({"b64_json": base64.b64encode(payload).decode()})

    assert generation_error_code(error.value) == "IMAGE_RESULT_DIMENSIONS_EXCEEDED"


def test_image_result_url_rejects_declared_oversize_before_stream(monkeypatch) -> None:
    class OversizedResponse:
        headers = {
            "content-type": "image/png",
            "content-length": str(IMAGE_RESULT_MAX_BYTES + 1),
        }

        def raise_for_status(self):
            return None

        def iter_bytes(self, **_kwargs):
            raise AssertionError("oversized response must not be read")

    @contextmanager
    def fake_stream(_method: str, _url: str, **_kwargs):
        yield OversizedResponse()

    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)
    monkeypatch.setattr(
        "app.services.image_service.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))
        ],
    )

    with pytest.raises(MediaResultError, match="IMAGE_RESULT_TOO_LARGE"):
        _image_result_bytes({"url": "https://provider.example/result.png"})


def test_image_result_url_rejects_actual_stream_overflow_and_wrong_content_type(
    monkeypatch,
) -> None:
    responses = iter(
        [
            SimpleNamespace(
                headers={"content-type": "image/png"},
                raise_for_status=lambda: None,
                iter_bytes=lambda **_kwargs: iter((b"123", b"45")),
            ),
            SimpleNamespace(
                headers={"content-type": "text/html"},
                raise_for_status=lambda: None,
                iter_bytes=lambda **_kwargs: (_ for _ in ()).throw(
                    AssertionError("wrong content type must not be read")
                ),
            ),
        ]
    )

    @contextmanager
    def fake_stream(_method: str, _url: str, **_kwargs):
        yield next(responses)

    monkeypatch.setattr("app.services.image_service.IMAGE_RESULT_MAX_BYTES", 4)
    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)
    monkeypatch.setattr(
        "app.services.image_service.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443))
        ],
    )

    with pytest.raises(MediaResultError, match="IMAGE_RESULT_TOO_LARGE"):
        _image_result_bytes({"url": "https://provider.example/streamed.png"})
    with pytest.raises(MediaResultError, match="IMAGE_RESULT_CONTENT_TYPE_INVALID"):
        _image_result_bytes({"url": "https://provider.example/not-an-image"})


@pytest.mark.parametrize(
    "image_url",
    [
        "http://cdn.example/result.png",
        "https://user:secret@cdn.example/result.png",
        "https://127.0.0.1/result.png",
        "https://8.8.8.8/result.png",
        "https://10.0.0.7/result.png",
        "https://169.254.169.254/latest/meta-data",
        "https://224.0.0.1/result.png",
        "https://192.0.2.1/result.png",
        "https://[::1]/result.png",
        "https://cdn.example:0/result.png",
    ],
)
def test_image_result_url_rejects_unsafe_scheme_credentials_and_ip_literals(
    monkeypatch,
    image_url: str,
) -> None:
    monkeypatch.setattr(
        "app.services.image_service.socket.getaddrinfo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("IP literals and malformed URLs must not resolve DNS")
        ),
    )
    monkeypatch.setattr(
        "app.services.image_service.httpx.stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe provider URL must not reach HTTP")
        ),
    )

    with pytest.raises(MediaResultError, match="IMAGE_RESULT_URL_UNTRUSTED") as error:
        _image_result_bytes({"url": image_url})

    assert generation_error_code(error.value) == "IMAGE_RESULT_URL_UNTRUSTED"


def test_image_result_url_rejects_dns_with_any_non_global_address(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.image_service.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ],
    )
    monkeypatch.setattr(
        "app.services.image_service.httpx.stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("mixed public/private DNS must not reach HTTP")
        ),
    )

    with pytest.raises(MediaResultError, match="IMAGE_RESULT_URL_UNTRUSTED"):
        _image_result_bytes({"url": "https://cdn.example/result.png"})


def test_image_result_url_allows_public_cdn_dns_and_keeps_redirects_disabled(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    result_bytes = png_bytes(Image.new("RGB", (2, 2), (10, 20, 30)))

    class ImageResponse:
        headers = {"content-type": "image/png"}

        def raise_for_status(self):
            return None

        def iter_bytes(self, **_kwargs):
            yield result_bytes

    def fake_getaddrinfo(hostname: str, port: int, **_kwargs):
        captured["dns"] = (hostname, port)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("8.8.8.8", port)),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("2606:4700:4700::1111", port, 0, 0),
            ),
        ]

    @contextmanager
    def fake_stream(method: str, url: str, **kwargs):
        captured.update(method=method, url=url, kwargs=kwargs)
        yield ImageResponse()

    monkeypatch.setattr("app.services.image_service.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)

    result = _image_result_bytes({"url": "https://cdn.example:8443/result.png?token=one"})

    assert result == result_bytes
    assert captured["dns"] == ("cdn.example", 8443)
    assert captured["url"] == "https://cdn.example:8443/result.png?token=one"
    assert captured["kwargs"]["follow_redirects"] is False


def test_openrouter_video_content_rejects_actual_stream_overflow(monkeypatch) -> None:
    class StreamResponse:
        status_code = 200
        headers = {"content-type": "video/mp4"}

        def iter_bytes(self, **_kwargs):
            yield b"123"
            yield b"45"

    @contextmanager
    def fake_stream(_method: str, _url: str, **_kwargs):
        yield StreamResponse()

    monkeypatch.setattr("app.services.image_service.VIDEO_RESULT_MAX_BYTES", 4)
    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)
    settings = SimpleNamespace(
        openrouter_api_key="sk-or-test",
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_site_url=None,
        openrouter_app_title=None,
        backend_public_url=None,
        webapp_url=None,
    )

    with pytest.raises(MediaResultError, match="VIDEO_RESULT_TOO_LARGE") as error:
        _download_openrouter_video_bytes(settings, "video-job")

    assert VIDEO_RESULT_MAX_BYTES == 100 * 1024 * 1024
    assert generation_error_code(error.value) == "VIDEO_RESULT_TOO_LARGE"


def test_kandinsky_result_rejects_declared_oversize_without_buffering(monkeypatch) -> None:
    class OversizedResponse:
        status_code = 200
        headers = {
            "content-type": "application/octet-stream",
            "content-length": str(IMAGE_RESULT_MAX_BYTES + 1),
        }

        def raise_for_status(self):
            return None

        def iter_bytes(self, **_kwargs):
            raise AssertionError("oversized response must not be read")

    @contextmanager
    def fake_stream(_method: str, _url: str, **_kwargs):
        yield OversizedResponse()

    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)
    settings = SimpleNamespace(
        kandinsky_api_key="kandinsky-token",
        kandinsky_base_url="https://studio.kandinskylab.ai/api",
        openai_image_timeout_seconds=180,
    )

    with pytest.raises(MediaResultError, match="IMAGE_RESULT_TOO_LARGE"):
        _kandinsky_download_result(
            settings,
            task_id="task-oversized",
            label="test/image",
            result_kind="image",
        )


def test_kandinsky_empty_video_result_has_video_specific_error(monkeypatch) -> None:
    class EmptyVideoResponse:
        status_code = 200
        headers = {"content-type": "video/mp4"}

        def raise_for_status(self):
            return None

        def iter_bytes(self, **_kwargs):
            return iter(())

    @contextmanager
    def fake_stream(_method: str, _url: str, **_kwargs):
        yield EmptyVideoResponse()

    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)
    settings = SimpleNamespace(
        kandinsky_api_key="kandinsky-token",
        kandinsky_base_url="https://studio.kandinskylab.ai/api",
        openai_image_timeout_seconds=180,
    )

    with pytest.raises(RuntimeError, match="KANDINSKY_VIDEO_RESPONSE_EMPTY"):
        _kandinsky_download_result(
            settings,
            task_id="task-empty-video",
            label="test/video",
            result_kind="video",
        )


def test_generate_openrouter_image_bytes_uses_openrouter_with_openai_provider(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    result_bytes = png_bytes(Image.new("RGB", (2, 2), (10, 20, 30)))

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"b64_json": base64.b64encode(result_bytes).decode()},
                ]
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            ai_provider="openai",
            openrouter_api_key="sk-or-test",
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_image_model="bytedance-seed/seedream-4.5",
            openrouter_site_url="https://app.example",
            openrouter_app_title="Test Tamagotchi",
            backend_public_url=None,
            webapp_url=None,
            openai_image_model="gpt-image-2",
            openai_image_size="1536x1152",
            openai_image_quality="medium",
            openai_image_output_format="png",
            openai_image_timeout_seconds=180,
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)

    result = generate_openrouter_image_bytes("story prompt", label="background_story/image")

    assert result == result_bytes
    assert captured["url"] == "https://openrouter.ai/api/v1/images"
    assert captured["headers"]["Authorization"] == "Bearer sk-or-test"
    assert captured["json"] == {
        "model": "bytedance-seed/seedream-4.5",
        "prompt": "story prompt",
        "resolution": "4K",
        "aspect_ratio": "4:3",
        "quality": "medium",
        "n": 1,
        "output_format": "png",
    }


def test_generate_kandinsky_image_bytes_uses_t2i_without_references(monkeypatch) -> None:
    captured: dict[str, object] = {}
    result_bytes = png_bytes(Image.new("RGB", (2, 2), (10, 20, 30)))

    class FakeResponse:
        content = b"kandinsky-image"
        text = ""
        status_code = 200
        headers = {"content-type": "image/png"}

        def __init__(self, payload=None, content: bytes | None = None) -> None:
            self.payload = payload or {}
            if content is not None:
                self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

        def iter_bytes(self, **_kwargs):
            yield self.content

    def fake_post(url, **kwargs):
        captured["post_url"] = url
        captured["post"] = kwargs
        return FakeResponse({"task_id": "task-1"})

    def fake_get(url, **kwargs):
        if url.endswith("/tasks/task-1"):
            return FakeResponse({"status": "done"})
        raise AssertionError(f"unexpected GET {url}")

    @contextmanager
    def fake_stream(_method: str, url: str, **_kwargs):
        if not url.endswith("/tasks/task-1/result"):
            raise AssertionError(f"unexpected stream GET {url}")
        yield FakeResponse(content=result_bytes)

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            kandinsky_api_key="kandinsky-token",
            kandinsky_base_url="https://studio.kandinskylab.ai/api",
            kandinsky_t2i_task_type="k6-image-t2i",
            kandinsky_i2i_task_type="k6-i2i",
            kandinsky_image_resolution="1280x768",
            kandinsky_poll_interval_seconds=1,
            openai_image_timeout_seconds=180,
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.httpx.get", fake_get)
    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)

    result = generate_kandinsky_image_bytes("story prompt", label="smoke/image")

    assert result == result_bytes
    assert captured["post_url"] == "https://studio.kandinskylab.ai/api/tasks/k6-image-t2i"
    assert captured["post"]["headers"] == {
        "Authorization": "Bearer kandinsky-token",
        "Content-Type": "application/json",
    }
    assert captured["post"]["json"] == {
        "params": {
            "query": "story prompt",
            "resolution": "1280x768",
        }
    }


def test_generate_kandinsky_video_uses_k5_i2v_hd(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = ""
        headers = {"content-type": "video/mp4"}

        def __init__(self, payload=None, content: bytes = b"") -> None:
            self.payload = payload or {}
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

        def iter_bytes(self, **_kwargs):
            yield self.content

    def fake_post(url, **kwargs):
        captured.update(url=url, request=kwargs)
        return FakeResponse({"task_id": "video-task-1"})

    def fake_get(url, **_kwargs):
        if url.endswith("/tasks/video-task-1"):
            return FakeResponse({"status": "done"})
        raise AssertionError(f"unexpected GET {url}")

    @contextmanager
    def fake_stream(_method: str, url: str, **_kwargs):
        if not url.endswith("/tasks/video-task-1/result"):
            raise AssertionError(f"unexpected stream GET {url}")
        yield FakeResponse(content=b"kandinsky-video")

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            kandinsky_api_key="kandinsky-token",
            kandinsky_base_url="https://studio.kandinskylab.ai/api",
            kandinsky_i2v_task_type="k5-i2v-hd",
            kandinsky_video_timeout_seconds=900,
            kandinsky_poll_interval_seconds=1,
            openai_image_timeout_seconds=180,
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.httpx.get", fake_get)
    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)

    result = generate_kandinsky_video_from_image_bytes(
        b"source-image",
        label="pet_creation/scene_video",
        prompt="Только естественное моргание, камера неподвижна.",
    )

    assert result == b"kandinsky-video"
    assert captured["url"] == "https://studio.kandinskylab.ai/api/tasks/k5-i2v-hd"
    assert captured["request"]["json"] == {
        "params": {
            "query": "Только естественное моргание, камера неподвижна.",
            "image": base64.b64encode(b"source-image").decode("utf-8"),
            "beautificator": "disabled",
        }
    }


def test_compact_kandinsky_prompt_preserves_subject_and_output_constraints() -> None:
    prompt = "SUBJECT: copper dragon\n" + ("middle style detail\n" * 300) + "NO TEXT OR LOGO"

    compacted = _compact_kandinsky_prompt(prompt)

    assert len(compacted) <= 2048
    assert compacted.startswith("SUBJECT: copper dragon")
    assert compacted.endswith("NO TEXT OR LOGO")


def test_compact_kandinsky_prompt_applies_collectible_style_frame() -> None:
    prompt = build_pet_single_sprite_prompt(
        "маленький паровой дракончик с фонарём",
        {},
        stage="teen",
        state="idle",
    )

    compacted = _compact_kandinsky_prompt(prompt, task="pet_creation/image")

    assert len(compacted) <= 2048
    assert "маленький паровой дракончик с фонарём" in compacted
    assert "коллекционную дизайнерскую арт-игрушку" in compacted
    assert "полный рост без обрезки" in compacted
    assert compacted.find("полный рост без обрезки") < 500
    assert "точно сохрани вид существа" in compacted
    assert "увеличенная округлая голова" in compacted.casefold()
    assert "матовая окрашенная смола" in compacted
    assert "минимум три слоя ручной одежды" in compacted
    assert "один крупный носимый предмет" in compacted
    assert "макрореализм фактур при полном росте" in compacted
    assert "без яркой насыщенности" in compacted.casefold()
    assert "normal/idle" not in compacted
    assert "CHARACTER_COLOR_SCRIPT" not in compacted


def test_generate_kandinsky_image_bytes_uses_i2i_with_reference(monkeypatch) -> None:
    captured: dict[str, object] = {}
    result_bytes = png_bytes(Image.new("RGB", (2, 2), (10, 20, 30)))

    class FakeResponse:
        text = ""
        status_code = 200
        headers = {"content-type": "image/png"}

        def __init__(self, payload=None, content: bytes = b"") -> None:
            self.payload = payload or {}
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

        def iter_bytes(self, **_kwargs):
            yield self.content

    def fake_post(url, **kwargs):
        captured["post_url"] = url
        captured["post"] = kwargs
        return FakeResponse({"task_id": "task-2"})

    def fake_get(url, **kwargs):
        if url.endswith("/tasks/task-2"):
            return FakeResponse({"status": "done"})
        raise AssertionError(f"unexpected GET {url}")

    @contextmanager
    def fake_stream(_method: str, url: str, **_kwargs):
        if url == "https://cdn.example.test/static/generated/pet/idle.png":
            yield FakeResponse(content=b"sprite-image")
            return
        if url.endswith("/tasks/task-2/result"):
            yield FakeResponse(content=result_bytes)
            return
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            kandinsky_api_key="kandinsky-token",
            kandinsky_base_url="https://studio.kandinskylab.ai/api",
            kandinsky_t2i_task_type="k6-image-t2i",
            kandinsky_i2i_task_type="k6-i2i",
            kandinsky_image_resolution="1280x768",
            kandinsky_poll_interval_seconds=1,
            openai_image_timeout_seconds=180,
            backend_public_url="https://cdn.example.test",
            webapp_url=None,
            backend_internal_url=None,
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.httpx.get", fake_get)
    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)

    result = generate_kandinsky_image_bytes(
        "story prompt",
        label="smoke/image",
        input_references=[
            {
                "type": "image_url",
                "image_url": {"url": "https://cdn.example.test/static/generated/pet/idle.png"},
            }
        ],
    )

    assert result == result_bytes
    assert captured["post_url"] == "https://studio.kandinskylab.ai/api/tasks/k6-i2i"
    assert captured["post"]["json"] == {
        "params": {
            "image": [base64.b64encode(b"sprite-image").decode("utf-8")],
            "query": "story prompt",
        }
    }


def test_kandinsky_reference_is_resized_and_compressed_before_base64(monkeypatch) -> None:
    source = Image.new("RGBA", (2400, 1600), (40, 90, 140, 180))
    source_bytes = png_bytes(source)

    monkeypatch.setattr(
        "app.services.image_service._reference_image_bytes",
        lambda _url: source_bytes,
    )
    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            kandinsky_reference_max_side=1024,
            kandinsky_reference_jpeg_quality=80,
        ),
    )

    encoded = _kandinsky_reference_image_b64("https://example.test/source.png")
    compressed = base64.b64decode(encoded)

    assert compressed.startswith(b"\xff\xd8")
    assert len(compressed) < len(source_bytes)
    with Image.open(BytesIO(compressed)) as image:
        assert image.format == "JPEG"
        assert max(image.size) == 1024


def test_generate_kandinsky_image_bytes_does_not_retry_ambiguous_create_timeout(
    monkeypatch,
) -> None:
    post_calls: list[dict[str, object]] = []

    class FakeResponse:
        text = ""
        status_code = 200
        headers = {"content-type": "image/png"}

        def __init__(self, payload=None, content: bytes = b"") -> None:
            self.payload = payload or {}
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

        def iter_bytes(self, **_kwargs):
            yield self.content

    def fake_post(url, **kwargs):
        post_calls.append({"url": url, **kwargs})
        raise httpx.ReadTimeout("slow kandinsky create")

    def fake_get(url, **kwargs):
        if url.endswith("/tasks/task-retry"):
            return FakeResponse({"status": "done"})
        raise AssertionError(f"unexpected GET {url}")

    @contextmanager
    def fake_stream(_method: str, url: str, **_kwargs):
        if url == "https://cdn.example.test/static/generated/pet/idle.png":
            yield FakeResponse(content=b"sprite-image")
            return
        if url.endswith("/tasks/task-retry/result"):
            yield FakeResponse(content=b"kandinsky-retry-image")
            return
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            kandinsky_api_key="kandinsky-token",
            kandinsky_base_url="https://studio.kandinskylab.ai/api",
            kandinsky_t2i_task_type="k6-image-t2i",
            kandinsky_i2i_task_type="k6-i2i",
            kandinsky_image_resolution="1280x768",
            kandinsky_poll_interval_seconds=1,
            openai_image_timeout_seconds=180,
            backend_public_url="https://cdn.example.test",
            webapp_url=None,
            backend_internal_url=None,
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.httpx.get", fake_get)
    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)
    monkeypatch.setattr("app.services.image_service.time.sleep", lambda _seconds: None)

    with pytest.raises(httpx.ReadTimeout):
        generate_kandinsky_image_bytes(
            "story prompt",
            label="background_story/image",
            input_references=[
                {
                    "type": "image_url",
                    "image_url": {"url": "https://cdn.example.test/static/generated/pet/idle.png"},
                }
            ],
        )

    assert len(post_calls) == 1
    assert post_calls[0]["timeout"] == 180


def test_kandinsky_create_retries_pre_send_connect_failure(monkeypatch) -> None:
    post_calls = 0
    retry_delays: list[float] = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"task_id": "task-after-connect-retry"}

    def fake_post(*_args, **_kwargs):
        nonlocal post_calls
        post_calls += 1
        if post_calls == 1:
            raise httpx.ConnectError("connection was not established")
        return FakeResponse()

    settings = SimpleNamespace(
        kandinsky_api_key="kandinsky-token",
        kandinsky_base_url="https://studio.kandinskylab.ai/api",
        openai_image_timeout_seconds=180,
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.time.sleep", retry_delays.append)

    task_id = _kandinsky_create_task(
        settings,
        task_type="k6-image-t2i",
        params={"query": "synthetic prompt"},
        label="test/image",
    )

    assert task_id == "task-after-connect-retry"
    assert post_calls == 2
    assert retry_delays == [3.0]


def test_generate_kandinsky_image_bytes_retries_transient_censor_result(monkeypatch) -> None:
    result_calls = 0
    retry_delays: list[float] = []
    result_bytes = png_bytes(Image.new("RGB", (2, 2), (10, 20, 30)))

    class FakeResponse:
        status_code = 200
        text = ""
        headers = {"content-type": "image/png"}

        def __init__(self, payload=None, content: bytes = b"") -> None:
            self.payload = payload or {}
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

        def iter_bytes(self, **_kwargs):
            yield self.content

    class CensorUnavailableResponse(FakeResponse):
        status_code = 422
        text = '{"detail":"output censor service unavailable: GigaChat returned no response"}'

        def __init__(self) -> None:
            super().__init__(content=self.text.encode())

        def raise_for_status(self):
            request = httpx.Request("GET", "https://studio.test/tasks/task-3/result")
            response = httpx.Response(422, request=request, text=self.text)
            raise httpx.HTTPStatusError("censor unavailable", request=request, response=response)

    def fake_get(url, **_kwargs):
        if url.endswith("/tasks/task-3"):
            return FakeResponse({"status": "done"})
        raise AssertionError(f"unexpected GET {url}")

    @contextmanager
    def fake_stream(_method: str, url: str, **_kwargs):
        nonlocal result_calls
        if not url.endswith("/tasks/task-3/result"):
            raise AssertionError(f"unexpected stream GET {url}")
        result_calls += 1
        if result_calls == 1:
            yield CensorUnavailableResponse()
            return
        yield FakeResponse(content=result_bytes)

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            kandinsky_api_key="kandinsky-token",
            kandinsky_base_url="https://studio.kandinskylab.ai/api",
            kandinsky_t2i_task_type="k6-image-t2i",
            kandinsky_i2i_task_type="k6-i2i",
            kandinsky_image_resolution="1280x768",
            kandinsky_poll_interval_seconds=1,
            openai_image_timeout_seconds=180,
        ),
    )
    monkeypatch.setattr(
        "app.services.image_service.httpx.post",
        lambda _url, **_kwargs: FakeResponse({"task_id": "task-3"}),
    )
    monkeypatch.setattr("app.services.image_service.httpx.get", fake_get)
    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)
    monkeypatch.setattr("app.services.image_service.time.sleep", retry_delays.append)

    result = generate_kandinsky_image_bytes("short prompt", label="smoke/image")

    assert result == result_bytes
    assert result_calls == 2
    assert retry_delays == [3.0]


def test_generate_image_edit_bytes_routes_reference_through_media_gateway(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, object] = {}
    source_path = tmp_path / "teen-idle.png"
    source_path.write_bytes(b"source-image")

    class FakeGateway:
        def generate_image(self, request):
            captured["request"] = request
            return b"edited-image"

    monkeypatch.setattr("app.services.image_service.get_media_gateway", FakeGateway)

    result = generate_image_edit_bytes(
        "Закрой ему глаза",
        source_path,
        label="pet_creation/edit",
    )

    assert result == b"edited-image"
    request = captured["request"]
    assert request.prompt == "Закрой ему глаза"
    assert request.task == "pet_creation/edit"
    assert request.required_capability.value == "image_to_image"
    assert request.input_references[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_generate_openrouter_video_bytes_uses_fixed_aspect_ratio(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    source_path = tmp_path / "teen-idle.png"
    source_path.write_bytes(png_bytes(Image.new("RGB", (720, 1280), (20, 140, 70))))

    class FakePostResponse:
        status_code = 200

        def json(self):
            return {"id": "video-job-1"}

    class FakePollResponse:
        status_code = 200
        content = b""

        def json(self):
            return {"status": "completed"}

    class FakeContentResponse:
        status_code = 200
        content = b"video-bytes"
        headers = {"content-type": "video/mp4"}

        def json(self):
            return {}

        def iter_bytes(self, **_kwargs):
            yield self.content

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakePostResponse()

    def fake_get(url, *, headers, timeout):
        captured.setdefault("get_urls", []).append(url)
        return FakePollResponse()

    @contextmanager
    def fake_stream(_method: str, url: str, **_kwargs):
        captured.setdefault("get_urls", []).append(url)
        if not url.endswith("/content"):
            raise AssertionError(f"unexpected stream GET {url}")
        yield FakeContentResponse()

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            openrouter_api_key="sk-or-test",
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_video_model="bytedance/seedance-2.0",
            openrouter_video_timeout_seconds=10,
            openrouter_video_poll_interval_seconds=1,
            openrouter_site_url="https://app.example",
            openrouter_app_title="Test Tamagotchi",
            backend_public_url=None,
            webapp_url=None,
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.httpx.get", fake_get)
    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)

    result = generate_openrouter_video_bytes(source_path, label="pet_creation/scene_video")

    assert result == b"video-bytes"
    assert captured["url"] == "https://openrouter.ai/api/v1/videos"
    assert captured["timeout"] == 60
    assert captured["json"]["resolution"] == "480p"
    assert captured["json"]["aspect_ratio"] == "9:16"
    assert "size" not in captured["json"]
    assert captured["json"]["frame_images"][0]["frame_type"] == "first_frame"
    assert captured["json"]["frame_images"][0]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


def test_generate_openrouter_video_bytes_does_not_retry_ambiguous_server_error(
    monkeypatch, tmp_path
) -> None:
    source_path = tmp_path / "teen-idle.png"
    source_path.write_bytes(png_bytes(Image.new("RGB", (720, 1280), (20, 140, 70))))
    post_statuses = [500, 200]
    retry_delays: list[float] = []

    class FakeResponse:
        content = b""
        headers = {"content-type": "video/mp4"}

        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self.payload = payload

        def json(self):
            return self.payload

        def iter_bytes(self, **_kwargs):
            yield self.content

    def fake_post(*_args, **_kwargs):
        status_code = post_statuses.pop(0)
        payload = {"id": "video-job-1"} if status_code == 200 else {"error": "temporary"}
        return FakeResponse(status_code, payload)

    def fake_get(url, **_kwargs):
        return FakeResponse(200, {"status": "completed"})

    @contextmanager
    def fake_stream(_method: str, url: str, **_kwargs):
        if not url.endswith("/content"):
            raise AssertionError(f"unexpected stream GET {url}")
        response = FakeResponse(200, {})
        response.content = b"video-bytes"
        yield response

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            openrouter_api_key="sk-or-test",
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_video_model="bytedance/seedance-2.0",
            openrouter_video_timeout_seconds=10,
            openrouter_video_poll_interval_seconds=1,
            openrouter_site_url=None,
            openrouter_app_title="Test Tamagotchi",
            backend_public_url=None,
            webapp_url=None,
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.httpx.get", fake_get)
    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)
    monkeypatch.setattr("app.services.image_service.time.sleep", retry_delays.append)

    with pytest.raises(RuntimeError, match="status=500"):
        generate_openrouter_video_bytes(source_path, label="pet_creation/scene_video")

    assert post_statuses == [200]
    assert retry_delays == []


def test_openrouter_video_submit_does_not_retry_ambiguous_read_timeout(monkeypatch) -> None:
    post_calls = 0
    retry_delays: list[float] = []

    def fake_post(*_args, **_kwargs):
        nonlocal post_calls
        post_calls += 1
        raise httpx.ReadTimeout("response timed out after request write")

    settings = SimpleNamespace(
        openrouter_api_key="sk-or-test",
        openai_api_key=None,
        openrouter_base_url="https://openrouter.ai/api/v1",
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.time.sleep", retry_delays.append)

    with pytest.raises(httpx.ReadTimeout):
        _submit_openrouter_video_job(
            settings,
            {"model": "test/video", "prompt": "synthetic prompt"},
            label="test/video",
        )

    assert post_calls == 1
    assert retry_delays == []


def test_openrouter_video_submit_retries_pre_send_connect_failure(monkeypatch) -> None:
    post_calls = 0
    retry_delays: list[float] = []
    success = SimpleNamespace(status_code=200)

    def fake_post(*_args, **_kwargs):
        nonlocal post_calls
        post_calls += 1
        if post_calls == 1:
            raise httpx.ConnectError("connection was not established")
        return success

    settings = SimpleNamespace(
        openrouter_api_key="sk-or-test",
        openai_api_key=None,
        openrouter_base_url="https://openrouter.ai/api/v1",
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.time.sleep", retry_delays.append)

    response = _submit_openrouter_video_job(
        settings,
        {"model": "test/video", "prompt": "synthetic prompt"},
        label="test/video",
    )

    assert response is success
    assert post_calls == 2
    assert retry_delays == [1.0]


@pytest.mark.parametrize("status_code", (502, 503, 504))
def test_openrouter_video_submit_retries_explicit_upstream_connect_failure(
    monkeypatch,
    status_code: int,
) -> None:
    post_calls = 0
    retry_delays: list[float] = []
    success = httpx.Response(200, request=httpx.Request("POST", "https://openrouter.test"))

    def fake_post(*_args, **_kwargs):
        nonlocal post_calls
        post_calls += 1
        if post_calls == 1:
            return httpx.Response(
                status_code,
                request=httpx.Request("POST", "https://openrouter.test"),
                json={
                    "error": {
                        "message": (
                            "upstream connect error or disconnect/reset before headers; "
                            "remote connection failure: Connection refused"
                        )
                    }
                },
            )
        return success

    settings = SimpleNamespace(
        openrouter_api_key="sk-or-test",
        openai_api_key=None,
        openrouter_base_url="https://openrouter.ai/api/v1",
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.time.sleep", retry_delays.append)

    response = _submit_openrouter_video_job(
        settings,
        {"model": "test/video", "prompt": "synthetic prompt"},
        label="test/video",
    )

    assert response is success
    assert post_calls == 2
    assert retry_delays == [1.0]


def test_openrouter_video_submit_does_not_retry_ambiguous_503(monkeypatch) -> None:
    post_calls = 0
    retry_delays: list[float] = []

    def fake_post(*_args, **_kwargs):
        nonlocal post_calls
        post_calls += 1
        return httpx.Response(
            503,
            request=httpx.Request("POST", "https://openrouter.test"),
            json={"error": {"message": "Service temporarily unavailable"}},
        )

    settings = SimpleNamespace(
        openrouter_api_key="sk-or-test",
        openai_api_key=None,
        openrouter_base_url="https://openrouter.ai/api/v1",
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.time.sleep", retry_delays.append)

    response = _submit_openrouter_video_job(
        settings,
        {"model": "test/video", "prompt": "synthetic prompt"},
        label="test/video",
    )

    assert response.status_code == 503
    assert post_calls == 1
    assert retry_delays == []


def test_generate_openrouter_video_bytes_retries_poll_errors(monkeypatch, tmp_path) -> None:
    source_path = tmp_path / "teen-idle.png"
    source_path.write_bytes(png_bytes(Image.new("RGB", (720, 1280), (20, 140, 70))))
    poll_statuses = [500, 429, 200]
    retry_delays: list[float] = []
    requested_urls: list[str] = []

    class FakeResponse:
        content = b""
        headers = {"content-type": "video/mp4"}

        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self.payload = payload

        def json(self):
            return self.payload

        def iter_bytes(self, **_kwargs):
            yield self.content

    def fake_post(*_args, **_kwargs):
        return FakeResponse(
            200,
            {
                "id": "video-job-1",
                "polling_url": "/api/v1/videos/video-job-1",
                "status": "pending",
            },
        )

    def fake_get(url, **_kwargs):
        requested_urls.append(url)
        status_code = poll_statuses.pop(0)
        payload = {"status": "completed"} if status_code == 200 else {"error": "temporary"}
        return FakeResponse(status_code, payload)

    @contextmanager
    def fake_stream(_method: str, url: str, **_kwargs):
        requested_urls.append(url)
        if not url.endswith("/content"):
            raise AssertionError(f"unexpected stream GET {url}")
        response = FakeResponse(200, {})
        response.content = b"video-bytes"
        yield response

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            openrouter_api_key="sk-or-test",
            openrouter_base_url="https://openrouter.ai/api/v1",
            openrouter_video_model="bytedance/seedance-2.0",
            openrouter_video_timeout_seconds=10,
            openrouter_video_poll_interval_seconds=1,
            openrouter_site_url=None,
            openrouter_app_title="Test Tamagotchi",
            backend_public_url=None,
            webapp_url=None,
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.httpx.get", fake_get)
    monkeypatch.setattr("app.services.image_service.httpx.stream", fake_stream)
    monkeypatch.setattr("app.services.image_service.time.sleep", retry_delays.append)

    result = generate_openrouter_video_bytes(source_path, label="pet_creation/scene_video")

    assert result == b"video-bytes"
    assert poll_statuses == []
    assert retry_delays == [1.0, 2.0]
    assert requested_urls[:3] == ["https://openrouter.ai/api/v1/videos/video-job-1"] * 3


def test_openrouter_polling_url_must_keep_provider_origin() -> None:
    settings = SimpleNamespace(
        openrouter_base_url="https://openrouter.ai/api/v1",
    )

    assert (
        _trusted_openrouter_polling_url(
            settings,
            "/api/v1/videos/video-job-1",
        )
        == "https://openrouter.ai/api/v1/videos/video-job-1"
    )
    with pytest.raises(RuntimeError, match="OPENROUTER_VIDEO_POLL_URL_UNTRUSTED"):
        _trusted_openrouter_polling_url(settings, "https://attacker.example/steal-token")


def test_align_sprite_to_reference_canvas_matches_reference_bbox(tmp_path) -> None:
    reference = Image.new("RGBA", (100, 100), (255, 255, 255, 0))
    reference_draw = ImageDraw.Draw(reference)
    reference_draw.rectangle((30, 10, 70, 90), fill=(20, 140, 70, 255))
    reference_path = tmp_path / "teen-idle.png"
    reference.save(reference_path, format="PNG")

    shifted = Image.new("RGBA", (200, 200), (255, 255, 255, 0))
    shifted_draw = ImageDraw.Draw(shifted)
    shifted_draw.rectangle((20, 100, 180, 190), fill=(40, 90, 190, 255))

    output = Image.open(
        BytesIO(align_sprite_to_reference_canvas(png_bytes(shifted), reference_path))
    ).convert("RGBA")

    assert output.size == reference.size
    assert foreground_component_bbox(output, (0, 0, output.width, output.height)) == color_bbox(
        reference,
        (20, 140, 70, 255),
    )


def test_image_asset_set_can_reuse_bible(
    monkeypatch,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}
    bible = {"species": "dragon"}
    idle_path = tmp_path / "teen-idle.png"

    monkeypatch.setattr(
        "app.services.image_service.generated_dir_for",
        lambda _asset_id: tmp_path,
    )
    monkeypatch.setattr(
        "app.services.image_service.create_character_bible",
        lambda _description: pytest.fail("comparison must reuse the primary bible"),
    )

    def fake_generate_paths(_asset_id, _description, received_bible, **kwargs):
        captured.update(bible=received_bible, kwargs=kwargs)
        return {("teen", "idle"): (idle_path, "prompt")}

    monkeypatch.setattr(
        "app.services.image_service.generate_individual_sprite_image_paths",
        fake_generate_paths,
    )

    result = generate_pet_image_asset_set(
        "дракон",
        image_provider="kandinsky",
        character_bible=bible,
    )

    assert result.character_bible == bible
    assert captured == {
        "bible": bible,
        "kwargs": {
            "image_provider": "kandinsky",
        },
    }


def test_generate_pet_asset_set_generates_idle_scene(monkeypatch, tmp_path) -> None:
    generated_prompts: list[str] = []
    prompt_bibles: list[dict[str, Any]] = []
    scene_sources: list[str] = []
    video_sources: list[str] = []
    character_bible = {
        "identity": {"name": "Гроза"},
        "openings": {
            "first_message": "Я Гроза. А ты расскажешь немного о себе?",
            "alternate_greetings": [],
        },
    }

    monkeypatch.setattr(
        "app.services.image_service.generated_dir_for",
        lambda asset_id: tmp_path / str(asset_id),
    )

    monkeypatch.setattr(
        "app.services.image_service.create_character_bible",
        lambda _description: character_bible,
    )

    def fake_prompt(_description, received_bible, *, stage, state):
        prompt_bibles.append(received_bible)
        return f"single:{stage}:{state}"

    def fake_image_bytes(prompt, **_kwargs):
        generated_prompts.append(prompt)
        image = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 30, 70, 70), fill=(20, 140, 70, 255))
        return png_bytes(image)

    def fake_scene_bytes(source_path, **_kwargs):
        scene_sources.append(source_path.name)
        image = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 30, 70, 70), fill=(40, 90, 190, 255))
        return png_bytes(image)

    monkeypatch.setattr("app.services.image_service.build_pet_single_sprite_prompt", fake_prompt)
    monkeypatch.setattr(
        "app.services.image_service.reserve_single_sprite_image_bytes",
        _reserved(fake_image_bytes),
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_pet_scene_image_bytes",
        _reserved(fake_scene_bytes),
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_pet_scene_video_bytes",
        _reserved(lambda source_path: video_sources.append(source_path.name) or b"video-bytes"),
    )
    result = generate_pet_asset_set("электрический дракон")

    assert generated_prompts == ["single:teen:idle"]
    assert scene_sources == ["teen-idle-character.png"]
    assert video_sources == ["teen-idle.png"]
    assert sorted(path.name for path in next(tmp_path.iterdir()).iterdir()) == [
        "teen-idle-character.png",
        "teen-idle-foreground.png",
        "teen-idle.mp4",
        "teen-idle.png",
    ]
    output_dir = next(tmp_path.iterdir())
    assert Image.open(output_dir / "teen-idle.png").size == (720, 1280)

    images = result["images"]
    assert prompt_bibles == [character_bible]
    assert result["characterBible"] == character_bible
    assert result["characterBible"]["identity"]["name"] == "Гроза"
    assert result["characterBible"]["openings"]["first_message"].startswith("Я Гроза")
    assert "/teen-idle.mp4" in result["videoUrl"]
    assert result["blinkImageUrl"] is None
    for stage in ("baby", "teen", "adult"):
        assert set(images[stage]) == {"idle", "happy", "hungry", "sad"}
        assert images[stage]["idle"] == images[stage]["happy"]
        assert images[stage]["idle"] == images[stage]["sad"]
        assert images[stage]["idle"] == images[stage]["hungry"]
        assert "/teen-idle.png" in images[stage]["idle"]


def test_generate_sad_assets_use_single_full_scene_edit(monkeypatch, tmp_path) -> None:
    asset_id = uuid.uuid4()
    output_dir = tmp_path / str(asset_id)
    output_dir.mkdir()
    idle_scene_path = output_dir / "teen-idle.png"
    idle_scene_path.write_bytes(png_bytes(Image.new("RGB", (720, 1280), (30, 40, 50))))
    image_set = PetAssetImageSet(
        asset_set_id=asset_id,
        generated_paths={("teen", "idle"): (idle_scene_path, "scene")},
        scene_path=idle_scene_path,
        character_bible={},
        version=1,
        generated_at=datetime.now(UTC),
    )
    image_calls: list[tuple[str, str, str]] = []
    video_calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        "app.services.image_service.generated_dir_for",
        lambda _asset_id: output_dir,
    )

    def fake_image_edit(prompt, source_path, *, label, **_kwargs):
        image_calls.append((prompt, source_path.name, label))
        return png_bytes(Image.new("RGB", (1024, 1536), (60, 70, 80)))

    def fake_video_bytes(scene_path, *, prompt, label):
        video_calls.append((scene_path.name, prompt, label))
        return b"sad-video"

    monkeypatch.setattr(
        "app.services.image_service.reserve_image_edit_bytes",
        _reserved(fake_image_edit),
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_pet_scene_video_bytes",
        _reserved(fake_video_bytes),
    )

    sad_scene_path = generate_pet_sad_scene_path(image_set)
    sad_video_path = generate_pet_sad_video_for_image_asset_set(image_set, sad_scene_path)

    assert image_calls == [
        (
            PET_SAD_SCENE_IMAGE_PROMPT,
            "teen-idle.png",
            "pet_creation/sad_scene",
        )
    ]
    assert video_calls == [
        ("teen-sad.png", PET_SAD_SCENE_VIDEO_PROMPT, "pet_creation/sad_scene_video")
    ]
    assert Image.open(sad_scene_path).size == (720, 1280)
    assert sad_video_path.read_bytes() == b"sad-video"


def test_generate_happy_assets_use_single_full_scene_edit_and_normal_blink_prompt(
    monkeypatch,
    tmp_path,
) -> None:
    asset_id = uuid.uuid4()
    output_dir = tmp_path / str(asset_id)
    output_dir.mkdir()
    idle_scene_path = output_dir / "teen-idle.png"
    idle_scene_path.write_bytes(png_bytes(Image.new("RGB", (720, 1280), (30, 40, 50))))
    image_set = PetAssetImageSet(
        asset_set_id=asset_id,
        generated_paths={("teen", "idle"): (idle_scene_path, "scene")},
        scene_path=idle_scene_path,
        character_bible={},
        version=1,
        generated_at=datetime.now(UTC),
    )
    image_calls: list[tuple[str, str, str]] = []
    video_calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        "app.services.image_service.generated_dir_for",
        lambda _asset_id: output_dir,
    )

    def fake_image_edit(prompt, source_path, *, label, **_kwargs):
        image_calls.append((prompt, source_path.name, label))
        return png_bytes(Image.new("RGB", (1024, 1536), (60, 70, 80)))

    def fake_video_bytes(scene_path, *, prompt, label):
        video_calls.append((scene_path.name, prompt, label))
        return b"happy-video"

    monkeypatch.setattr(
        "app.services.image_service.reserve_image_edit_bytes",
        _reserved(fake_image_edit),
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_pet_scene_video_bytes",
        _reserved(fake_video_bytes),
    )

    happy_scene_path = generate_pet_happy_scene_path(image_set)
    happy_video_path = generate_pet_happy_video_for_image_asset_set(
        image_set,
        happy_scene_path,
    )

    assert image_calls == [
        (
            PET_HAPPY_SCENE_IMAGE_PROMPT,
            "teen-idle.png",
            "pet_creation/happy_scene",
        )
    ]
    assert video_calls == [
        ("teen-happy.png", PET_SCENE_VIDEO_PROMPT, "pet_creation/happy_scene_video")
    ]
    assert Image.open(happy_scene_path).size == (720, 1280)
    assert happy_video_path.read_bytes() == b"happy-video"


def test_asset_response_switches_sad_urls_only_when_both_sad_assets_are_ready(tmp_path) -> None:
    asset_id = uuid.uuid4()
    idle_path = tmp_path / "teen-idle.png"
    idle_video_path = tmp_path / "teen-idle.mp4"
    sad_path = tmp_path / "teen-sad.png"
    sad_video_path = tmp_path / "teen-sad.mp4"
    image_set = PetAssetImageSet(
        asset_set_id=asset_id,
        generated_paths={("teen", "idle"): (idle_path, "scene")},
        scene_path=idle_path,
        character_bible={},
        version=7,
        generated_at=datetime.now(UTC),
    )

    pending = build_pet_asset_set_response(image_set, idle_video_path, sad_path, None)
    ready = build_pet_asset_set_response(
        image_set,
        idle_video_path,
        sad_path,
        sad_video_path,
    )

    assert pending["sadVideoUrl"] is None
    assert pending["images"]["teen"]["sad"] == pending["images"]["teen"]["idle"]
    assert ready["sadVideoUrl"].endswith("teen-sad.mp4?v=7")
    assert ready["images"]["teen"]["sad"].endswith("teen-sad.png?v=7")


def test_asset_response_switches_happy_urls_only_when_both_happy_assets_are_ready(
    tmp_path,
) -> None:
    asset_id = uuid.uuid4()
    idle_path = tmp_path / "teen-idle.png"
    idle_video_path = tmp_path / "teen-idle.mp4"
    happy_path = tmp_path / "teen-happy.png"
    happy_video_path = tmp_path / "teen-happy.mp4"
    image_set = PetAssetImageSet(
        asset_set_id=asset_id,
        generated_paths={("teen", "idle"): (idle_path, "scene")},
        scene_path=idle_path,
        character_bible={},
        version=7,
        generated_at=datetime.now(UTC),
    )

    pending = build_pet_asset_set_response(
        image_set,
        idle_video_path,
        None,
        None,
        happy_path,
        None,
    )
    ready = build_pet_asset_set_response(
        image_set,
        idle_video_path,
        None,
        None,
        happy_path,
        happy_video_path,
    )

    assert pending["happyVideoUrl"] is None
    assert pending["images"]["teen"]["happy"] == pending["images"]["teen"]["idle"]
    assert ready["happyVideoUrl"].endswith("teen-happy.mp4?v=7")
    assert ready["images"]["teen"]["happy"].endswith("teen-happy.png?v=7")


def test_state_strip_prompt_omits_lore_only_do_not_change() -> None:
    prompt = build_pet_state_strip_prompt(
        "электрический дракон",
        {
            "species": "электрический дракончик",
            "main_colors": ["синий", "жёлтый"],
            "signature_features": ["рога-молнии", "хвост-вилка"],
            "teen_design": "рога держат заряд ровнее",
            "do_not_change": [
                "Дом связан с сухим грозовым уступом.",
                "Вода опасна, потому что быстро уводит заряд.",
                "Голос дружелюбный и немного язвительный.",
            ],
        },
        stage="teen",
    )

    assert "do_not_change" not in prompt
    assert "Вода опасна" not in prompt
    assert "Голос дружелюбный" not in prompt
    assert "мягкие зигзагообразные антенны" in prompt
    assert "хвост с округлым раздвоенным кончиком" in prompt


def test_generate_individual_sprite_paths_retries_safety_prompt_on_rejection(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        "app.services.image_service.generated_dir_for",
        lambda asset_id: tmp_path / str(asset_id),
    )
    monkeypatch.setattr(
        "app.services.image_service.build_pet_single_sprite_prompt",
        lambda _description, _character_bible, *, stage, state: f"standard:{stage}:{state}",
    )
    monkeypatch.setattr(
        "app.services.image_service.build_pet_single_sprite_safety_retry_prompt",
        lambda _description, _character_bible, *, stage, state: f"safe-retry:{stage}:{state}",
    )
    monkeypatch.setattr(
        "app.services.image_service.generation_error_code",
        lambda exc: "IMAGE_PROMPT_REJECTED" if str(exc) == "blocked" else "GENERATION_FAILED",
    )

    def fake_image_bytes(prompt, **_kwargs):
        calls.append(prompt)
        if prompt == "standard:teen:idle":
            raise RuntimeError("blocked")
        image = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 30, 70, 70), fill=(20, 140, 70, 255))
        return png_bytes(image)

    monkeypatch.setattr(
        "app.services.image_service.reserve_single_sprite_image_bytes",
        _reserved(fake_image_bytes),
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_pet_scene_image_bytes",
        _reserved(
            lambda _source_path, **_kwargs: png_bytes(
                Image.new("RGBA", (100, 100), (255, 255, 255, 0))
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_pet_scene_video_bytes",
        _reserved(lambda _source_path: b"video-bytes"),
    )
    result, video_path = generate_individual_sprite_paths(
        uuid.uuid4(),
        "электрический дракон",
        {"species": "дракончик"},
    )

    assert calls == [
        "standard:teen:idle",
        "safe-retry:teen:idle",
    ]
    assert sorted(path.name for path, _prompt in result.values()) == [
        "teen-idle.png",
    ]
    assert video_path is not None
    assert video_path.name == "teen-idle.mp4"


def test_generate_individual_sprite_paths_requires_video(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "app.services.image_service.generated_dir_for",
        lambda asset_id: tmp_path / str(asset_id),
    )
    monkeypatch.setattr(
        "app.services.image_service.build_pet_single_sprite_prompt",
        lambda *_args, **_kwargs: "single:teen:idle",
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_single_sprite_image_bytes",
        _reserved(
            lambda _prompt, **_kwargs: png_bytes(Image.new("RGBA", (100, 100), (20, 140, 70, 255)))
        ),
    )
    monkeypatch.setattr(
        "app.services.image_service.reserve_pet_scene_image_bytes",
        _reserved(
            lambda _source_path, **_kwargs: png_bytes(
                Image.new("RGBA", (100, 100), (40, 90, 190, 255))
            )
        ),
    )

    def fail_video(_source_path):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        "app.services.image_service.reserve_pet_scene_video_bytes",
        _reserved(fail_video),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        generate_individual_sprite_paths(
            uuid.uuid4(),
            "электрический дракон",
            {},
        )

    assert not list(tmp_path.rglob("*.mp4"))


def test_create_character_bible_uses_character_timeout(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            last_message = kwargs["messages"][-1]["content"]
            if "характере" in last_message:
                content = "Я упрямый, теплый и люблю говорить коротко."
            elif "мире" in last_message:
                content = "Мой мир держится на горячих камнях и узких горных тропах."
            else:
                content = json.dumps({"species": "дракончик"})
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            openai_chat_model="test-model",
            openai_character_model="gpt-5-mini",
            openai_character_reasoning_effort="minimal",
            openai_chat_timeout_seconds=1,
            openai_character_timeout_seconds=180,
        ),
    )
    monkeypatch.setattr(
        "app.llm.compat.get_llm_gateway",
        lambda: OpenAICompatibleProvider(
            name="test",
            client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
        ),
    )
    monkeypatch.setattr(
        "app.services.image_service.resolve_llm_model",
        lambda _task, fallback: fallback,
    )
    monkeypatch.setattr(
        "app.services.image_service.character_bible_quality_issues",
        lambda description, character_bible: (),
    )

    result = create_character_bible("маленький дракон")

    assert result["schema_version"] == 2
    assert result["species"] == "дракончик"
    assert "world_description_anchors_used" not in result["extensions"]
    assert all(call["timeout"] == 180 for call in calls)
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-5-mini"
    assert calls[0]["reasoning_effort"] == "minimal"
    assert "WORLD_DESCRIPTION_ANCHORS" not in calls[0]["messages"][1]["content"]
    assert "LORE_VARIATION_SEED" not in calls[0]["messages"][1]["content"]


def test_character_reasoning_effort_only_for_supported_models() -> None:
    settings = SimpleNamespace(openai_character_reasoning_effort="minimal")

    assert _character_reasoning_effort_kwargs(settings, "gpt-5-mini") == {
        "reasoning_effort": "minimal"
    }
    assert _character_reasoning_effort_kwargs(settings, "openai/gpt-5-mini") == {
        "reasoning_effort": "minimal"
    }
    assert _character_reasoning_effort_kwargs(settings, "gpt-4.1-mini") == {}


def test_character_bible_prompt_omits_curated_generation_context() -> None:
    prompt = build_character_bible_prompt("водяной зверек с ракушкой")

    assert "TONE_PROFILE" not in prompt
    assert "GENERATION_PROFILE" not in prompt
    assert "SETTING_HINT" in prompt
    character_profile = tone_context_payload("characterBible")
    assert f"setting: {character_profile['setting']}" in prompt
    assert "tone: natural" in prompt
    assert "Dark fantasy" not in prompt
    assert "WORLD_DESCRIPTION_ANCHORS" not in prompt
    assert "source_text_do_not_copy" not in prompt
    assert "LORE_VARIATION_SEED" not in prompt


def test_generation_error_code_defaults_to_generic() -> None:
    assert generation_error_code(RuntimeError("unknown")) == "GENERATION_FAILED"


def test_generation_error_code_classifies_neutral_llm_provider_errors() -> None:
    assert (
        generation_error_code(LLMProviderError("rate limited", status_code=429)) == "LLM_RATE_LIMIT"
    )

    try:
        raise httpx.ReadTimeout("timed out")
    except httpx.ReadTimeout as cause:
        wrapped = LLMProviderError("GigaChat request failed")
        wrapped.__cause__ = cause

    assert generation_error_code(wrapped) == "LLM_TIMEOUT"


def test_generation_error_code_keeps_video_runtime_failures_generic() -> None:
    assert (
        generation_error_code(RuntimeError("OpenRouter video generation failed: status=400"))
        == "GENERATION_FAILED"
    )


def test_character_bible_schema_is_compact() -> None:
    assert CHARACTER_BIBLE_SCHEMA["required"] == [
        "schema_version",
        "genesis",
        "roleplay_contract",
        "identity",
        "visual",
        "voice",
        "inner_state",
        "world",
        "openings",
        "lorebook_entries",
    ]
    assert CHARACTER_BIBLE_SCHEMA["properties"]["schema_version"]["enum"] == [2]
    genesis_required = CHARACTER_BIBLE_SCHEMA["properties"]["genesis"]["required"]
    assert "description" in genesis_required
    assert "character_trait" in genesis_required
    assert "likes" in genesis_required
    assert "does" in genesis_required
    assert "appetite" in genesis_required
    assert "safe_adaptation" not in genesis_required
    assert "forbidden_random_additions" not in genesis_required
    assert (
        "how_to_answer_who_are_you"
        in CHARACTER_BIBLE_SCHEMA["properties"]["roleplay_contract"]["required"]
    )
    assert "never_say" not in CHARACTER_BIBLE_SCHEMA["properties"]["roleplay_contract"]["required"]
    assert "growth_forms" in CHARACTER_BIBLE_SCHEMA["properties"]["visual"]["required"]
    assert "sample_replies" in CHARACTER_BIBLE_SCHEMA["properties"]["voice"]["required"]
    assert "drives" not in CHARACTER_BIBLE_SCHEMA["properties"]["inner_state"]["required"]
    assert "lore" not in CHARACTER_BIBLE_SCHEMA["required"]
    assert "dialogue_moves" not in CHARACTER_BIBLE_SCHEMA["required"]
    assert "provenance" not in CHARACTER_BIBLE_SCHEMA["required"]
    name_schema = CHARACTER_BIBLE_SCHEMA["properties"]["identity"]["properties"]["name"]
    assert name_schema["minLength"] == 1
    assert name_schema["maxLength"] == 32
    assert "Preserve a name explicitly supplied by the user" in name_schema["description"]
    assert "calm, neutral, pronounceable name" in name_schema["description"]
    assert "The user may rename it later" in name_schema["description"]


def test_character_bible_prompt_requests_species_specific_lore() -> None:
    prompt = build_character_bible_prompt("маленький дракон с мягкими крыльями")

    assert "compact character profile" in prompt
    assert "tiny persona-file shape" in prompt
    assert "describe it" in prompt
    assert "what does it like" in prompt
    assert "what does it usually do" in prompt
    assert "roleplay_contract" in prompt
    assert "TONE_PROFILE" not in prompt
    assert "GENERATION_PROFILE" not in prompt
    assert "SETTING_HINT" in prompt
    assert "safe fictional behavior pattern" not in prompt
    assert "forbidden_random_additions" not in prompt
    assert "never_say" not in prompt
    assert "digital companion" not in prompt
    assert "visibly blend at least 4 different source fragments" not in prompt
    assert "Write every user-facing string value in natural Russian" in prompt
    assert "high-quality character card" not in prompt
    assert "voice.sample_replies: 5-8 short Russian replies" in prompt
    assert "lorebook_entries: 3-5 triggerable facts" in prompt
    assert "If the user explicitly gave the creature a name, preserve that name" in prompt
    assert "Otherwise invent a calm, neutral, pronounceable display name" in prompt
    assert "The name is an initial suggestion that the user may later change" in prompt
    assert "Never use a fixed default name or a generic placeholder" in prompt
    assert "identity.name must always contain the creature's initial display name" in prompt
    assert "openings.first_message is the creature's first meeting with the user" in prompt
    assert "it says its identity.name and invites the user to introduce themselves" in prompt
    assert "Do not apply voice, personality mannerisms, catchphrases" in prompt
    assert "Do not default to the same" not in prompt
    assert "storybook logic" not in prompt
    assert "короткие просьбы" not in prompt
    assert "larger concrete setting" not in prompt
    assert "бюро забытых вещей" not in prompt


def test_character_bible_quality_flags_overused_defaults_and_bad_physics() -> None:
    character_bible = {
        "species": "паровой дракончик",
        "lore": {
            "world": {
                "story": (
                    "Он живет на теплой полке у мха и выпускает мягкий пар, "
                    "стараясь не делать его слишком громким."
                )
            },
            "inner_life": {"likes": ["короткие просьбы"]},
        },
    }

    issues = character_bible_quality_issues("маленький паровой дракончик", character_bible)
    plant_issues = character_bible_quality_issues("листик с лицом", character_bible)

    assert "non_plant_pet_uses_greenhouse_shelf_moss_dew_or_warm_lamp_defaults" in issues
    assert "incoherent_physical_or_sensory_logic" in issues
    assert "generic_life_lesson_or_user_behavior_preference" in issues
    assert "non_plant_pet_uses_greenhouse_shelf_moss_dew_or_warm_lamp_defaults" not in plant_issues
    assert "incoherent_physical_or_sensory_logic" in plant_issues


def test_character_bible_quality_requires_concrete_home_location() -> None:
    invalid = {"world": {"home": "потемневшая меди"}}
    valid = {"world": {"home": "в сухой нише под древней каменной дорогой"}}

    assert "home_is_not_a_concrete_location" in character_bible_quality_issues(
        "медный зверёк",
        invalid,
    )
    assert "home_is_not_a_concrete_location" not in character_bible_quality_issues(
        "медный зверёк",
        valid,
    )


def test_create_character_bible_does_not_run_repair_or_initial_overlay(monkeypatch) -> None:
    compact_bible = {
        "schema_version": 2,
        "genesis": {
            "description": "маленький паровой зверек с осторожным теплом",
            "character_trait": "бережный хранитель пара",
            "likes": ["теплые камни", "густая похлебка", "тихий склон"],
            "does": [
                "проверяет клапан",
                "садится на теплый камень",
                "шипит, когда волнуется",
                "ищет ровное тепло",
            ],
            "appetite": "любит густую похлебку и тепло камней",
            "conflict": "боится расплескать жар, но хочет греться рядом",
            "story_engine": "истории строятся вокруг клапана, тепла и каменного склона",
        },
        "roleplay_contract": {
            "self_intro": "Я Пых, маленький паровой дракончик с упрямым клапаном.",
            "how_to_answer_who_are_you": "Я Пых. Клапан шипит, когда я волнуюсь.",
            "how_to_answer_what_do_you_eat": "Люблю густую похлебку и тепло камней.",
            "how_to_answer_where_do_you_live": "Живу в теплой каменной нише.",
            "voice_rules": ["говорит коротко", "сначала телесная реакция", "без справочного тона"],
        },
        "identity": {
            "name": "Пых",
            "species": "паровой дракончик",
            "role": "житель теплой каменной ниши",
            "one_liner": "Паровой дракончик с клапаном на спине.",
        },
        "visual": {
            "colors": ["красный", "кремовый"],
            "features": ["клапан на спине"],
            "materials": ["матовый винил"],
            "proportions": "округлое тело, короткие лапы",
            "growth_forms": {
                "baby": "маленький и круглый",
                "teen": "чуть выше, клапан заметнее",
                "adult": "устойчивый силуэт",
            },
            "anchors": ["паровой дракончик", "клапан на спине"],
        },
        "voice": {
            "rules": ["говорит коротко"],
            "rhythm": "короткие фразы",
            "catchphrases": ["пшш, спокойно"],
            "sample_replies": ["Я Пых. Сначала сяду на теплый камень."],
        },
        "inner_state": {
            "core_want": "держать тепло ровно",
            "inner_conflict": "волнуется и шипит клапаном",
            "fears": ["холодная вода"],
            "comfort_actions": ["садится на теплый камень"],
        },
        "world": {
            "home": "теплая каменная ниша",
            "habitat": "каменный склон с теплым паром",
            "objects": ["теплый камень"],
            "routines": ["проверяет клапан"],
            "relationships": ["привыкает к спокойному собеседнику"],
            "story_seeds": ["почему клапан меняет звук"],
        },
        "openings": {
            "first_message": "Я Пых. Тут теплый камень, садись рядом.",
            "alternate_greetings": ["Пшш, ты пришел."],
        },
        "lorebook_entries": [
            {"keys": ["клапан", "пар"], "content": "Клапан тихо шипит, когда Пых волнуется."}
        ],
    }
    calls: list[list[dict[str, str]]] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs["messages"])
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=json.dumps(compact_bible)))
                ]
            )

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            openai_chat_model="test-model",
            openai_chat_timeout_seconds=1,
        ),
    )
    monkeypatch.setattr(
        "app.llm.compat.get_llm_gateway",
        lambda: OpenAICompatibleProvider(
            name="test",
            client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
        ),
    )

    result = create_character_bible("маленький паровой дракончик")

    assert result["schema_version"] == 2
    assert result["species"] == "паровой дракончик"
    assert result["main_colors"] == ["красный", "кремовый"]
    assert result["lore"]["world"]["story"] == "каменный склон с теплым паром"
    assert result["genesis"]["character_trait"] == "бережный хранитель пара"
    assert result["genesis"]["appetite"] == "любит густую похлебку и тепло камней"
    assert "safe_adaptation" not in result["genesis"]
    assert "forbidden_random_additions" not in result["genesis"]
    assert result["roleplay_contract"]["how_to_answer_who_are_you"].startswith("Я Пых")
    assert result["extensions"]["generation"]["pipeline"] == "direct_creature_profile_v4"
    assert "identity" in result
    assert "dialogue_moves" in result
    assert "lite_overlay" not in result["extensions"]
    assert len(calls) == 1
    assert "LORE_VARIATION_SEED" not in calls[0][1]["content"]
    assert "WORLD_DESCRIPTION_ANCHORS" not in calls[0][1]["content"]
    assert "Repair this character bible" not in calls[0][1]["content"]
