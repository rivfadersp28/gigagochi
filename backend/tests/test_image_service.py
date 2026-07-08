from __future__ import annotations

import base64
import json
import uuid
from types import SimpleNamespace

import httpx
from PIL import Image, ImageDraw

from app.prompts.pet_image_prompts import (
    build_character_bible_prompt,
    build_pet_state_strip_prompt,
)
from app.services.image_service import (
    BACKGROUND_REMOVAL_SCRIPT,
    CHARACTER_BIBLE_SCHEMA,
    _character_reasoning_effort_kwargs,
    character_bible_quality_issues,
    create_character_bible,
    extract_sprite_cells,
    extract_state_strip_cells,
    generate_image_bytes,
    generate_individual_sprite_paths,
    generate_kandinsky_image_bytes,
    generate_openrouter_image_bytes,
    generate_pet_asset_set,
    generate_sprite_sheet_bytes,
    generation_error_code,
)


def test_background_removal_script_uses_supported_model() -> None:
    script = BACKGROUND_REMOVAL_SCRIPT.read_text(encoding="utf-8")

    assert 'model: "medium"' in script
    assert "publicPath:" in script
    assert 'new Blob([image], { type: "image/png" })' in script
    assert "isnet_fp16" not in script


def image_contains_color(image: Image.Image, color: tuple[int, int, int, int]) -> bool:
    pixels = image.load()
    width, height = image.size
    return any(pixels[x, y] == color for y in range(height) for x in range(width))


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


def test_generate_sprite_sheet_omits_unset_background(monkeypatch) -> None:
    captured: dict[str, object] = {}
    background_removal_inputs: list[bytes] = []

    class FakeImages:
        def generate(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(b"image-bytes").decode())]
            )

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            openai_image_model="gpt-image-2",
            openai_image_size="1536x1152",
            openai_image_quality="medium",
            openai_image_output_format="png",
            openai_image_timeout_seconds=180,
        ),
    )
    monkeypatch.setattr(
        "app.services.image_service.get_openai_client",
        lambda: SimpleNamespace(images=FakeImages()),
    )
    monkeypatch.setattr(
        "app.services.image_service.remove_image_background",
        lambda image_bytes: background_removal_inputs.append(image_bytes) or b"foreground-image",
    )

    result = generate_sprite_sheet_bytes("prompt")

    assert result == b"foreground-image"
    assert background_removal_inputs == [b"image-bytes"]
    assert "background" not in captured
    assert captured["timeout"] == 180


def test_generate_image_bytes_uses_openrouter_image_endpoint(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"b64_json": base64.b64encode(b"openrouter-image").decode()},
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

    result = generate_image_bytes("sprite prompt")

    assert result == b"openrouter-image"
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

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"b64_json": base64.b64encode(b"openrouter-image").decode()},
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

    result = generate_image_bytes("travel prompt", input_references=references)

    assert result == b"openrouter-image"
    assert captured["json"]["input_references"] == references


def test_generate_openrouter_image_bytes_uses_openrouter_with_openai_provider(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"b64_json": base64.b64encode(b"story-image").decode()},
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

    assert result == b"story-image"
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

    class FakeResponse:
        content = b"kandinsky-image"
        text = ""
        status_code = 200

        def __init__(self, payload=None, content: bytes | None = None) -> None:
            self.payload = payload or {}
            if content is not None:
                self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(url, **kwargs):
        captured["post_url"] = url
        captured["post"] = kwargs
        return FakeResponse({"task_id": "task-1"})

    def fake_get(url, **kwargs):
        if url.endswith("/tasks/task-1"):
            return FakeResponse({"status": "done"})
        if url.endswith("/tasks/task-1/result"):
            return FakeResponse(content=b"kandinsky-image")
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
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.httpx.get", fake_get)

    result = generate_kandinsky_image_bytes("story prompt", label="background_story/image")

    assert result == b"kandinsky-image"
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


def test_generate_kandinsky_image_bytes_uses_i2i_with_reference(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        text = ""
        status_code = 200

        def __init__(self, payload=None, content: bytes = b"") -> None:
            self.payload = payload or {}
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(url, **kwargs):
        captured["post_url"] = url
        captured["post"] = kwargs
        return FakeResponse({"task_id": "task-2"})

    def fake_get(url, **kwargs):
        if url == "https://cdn.example.test/pet.png":
            return FakeResponse(content=b"sprite-image")
        if url.endswith("/tasks/task-2"):
            return FakeResponse({"status": "done"})
        if url.endswith("/tasks/task-2/result"):
            return FakeResponse(content=b"kandinsky-i2i-image")
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
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.httpx.get", fake_get)

    result = generate_kandinsky_image_bytes(
        "story prompt",
        label="background_story/image",
        input_references=[
            {
                "type": "image_url",
                "image_url": {"url": "https://cdn.example.test/pet.png"},
            }
        ],
    )

    assert result == b"kandinsky-i2i-image"
    assert captured["post_url"] == "https://studio.kandinskylab.ai/api/tasks/k6-i2i"
    assert captured["post"]["json"] == {
        "params": {
            "image": [base64.b64encode(b"sprite-image").decode("utf-8")],
            "query": "story prompt",
        }
    }


def test_generate_kandinsky_image_bytes_retries_create_timeout(monkeypatch) -> None:
    post_calls: list[dict[str, object]] = []

    class FakeResponse:
        text = ""
        status_code = 200

        def __init__(self, payload=None, content: bytes = b"") -> None:
            self.payload = payload or {}
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(url, **kwargs):
        post_calls.append({"url": url, **kwargs})
        if len(post_calls) == 1:
            raise httpx.ReadTimeout("slow kandinsky create")
        return FakeResponse({"task_id": "task-retry"})

    def fake_get(url, **kwargs):
        if url == "https://cdn.example.test/pet.png":
            return FakeResponse(content=b"sprite-image")
        if url.endswith("/tasks/task-retry"):
            return FakeResponse({"status": "done"})
        if url.endswith("/tasks/task-retry/result"):
            return FakeResponse(content=b"kandinsky-retry-image")
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
        ),
    )
    monkeypatch.setattr("app.services.image_service.httpx.post", fake_post)
    monkeypatch.setattr("app.services.image_service.httpx.get", fake_get)
    monkeypatch.setattr("app.services.image_service.time.sleep", lambda _seconds: None)

    result = generate_kandinsky_image_bytes(
        "story prompt",
        label="background_story/image",
        input_references=[
            {
                "type": "image_url",
                "image_url": {"url": "https://cdn.example.test/pet.png"},
            }
        ],
    )

    assert result == b"kandinsky-retry-image"
    assert len(post_calls) == 2
    assert post_calls[0]["timeout"] == 180
    assert post_calls[1]["timeout"] == 180


def test_generate_pet_asset_set_generates_three_separate_teen_skins(monkeypatch, tmp_path) -> None:
    generated_prompts: list[str] = []

    monkeypatch.setattr(
        "app.services.image_service.generated_dir_for",
        lambda asset_id: tmp_path / str(asset_id),
    )
    monkeypatch.setattr(
        "app.services.image_service.create_character_bible",
        lambda _description: {"species": "дракончик"},
    )

    def fake_prompt(_description, _character_bible, *, stage, state):
        return f"single:{stage}:{state}"

    def fake_image_bytes(prompt):
        generated_prompts.append(prompt)
        image = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
        draw = ImageDraw.Draw(image)
        color = {
            "single:teen:idle": (20, 140, 70, 255),
            "single:teen:happy": (220, 180, 40, 255),
            "single:teen:sad": (40, 90, 190, 255),
        }[prompt]
        draw.rectangle((30, 30, 70, 70), fill=color)
        return png_bytes(image)

    monkeypatch.setattr("app.services.image_service.build_pet_single_sprite_prompt", fake_prompt)
    monkeypatch.setattr(
        "app.services.image_service.generate_image_bytes",
        fake_image_bytes,
    )
    monkeypatch.setattr(
        "app.services.image_service.remove_image_background",
        lambda image_bytes: image_bytes,
    )

    result = generate_pet_asset_set("электрический дракон")

    assert generated_prompts == [
        "single:teen:idle",
        "single:teen:happy",
        "single:teen:sad",
    ]
    assert sorted(path.name for path in next(tmp_path.iterdir()).iterdir()) == [
        "teen-happy.png",
        "teen-idle.png",
        "teen-sad.png",
    ]

    images = result["images"]
    for stage in ("baby", "teen", "adult"):
        assert set(images[stage]) == {"idle", "happy", "hungry", "sad"}
        assert "/teen-idle.png" in images[stage]["idle"]
        assert "/teen-happy.png" in images[stage]["happy"]
        assert "/teen-sad.png" in images[stage]["sad"]
        assert "/teen-sad.png" in images[stage]["hungry"]


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

    def fake_image_bytes(prompt):
        calls.append(prompt)
        if prompt == "standard:teen:happy":
            raise RuntimeError("blocked")
        image = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 30, 70, 70), fill=(20, 140, 70, 255))
        return png_bytes(image)

    monkeypatch.setattr("app.services.image_service.generate_image_bytes", fake_image_bytes)
    monkeypatch.setattr(
        "app.services.image_service.remove_image_background",
        lambda image_bytes: image_bytes,
    )

    result = generate_individual_sprite_paths(
        uuid.uuid4(),
        "электрический дракон",
        {"species": "дракончик"},
    )

    assert calls == [
        "standard:teen:idle",
        "standard:teen:happy",
        "safe-retry:teen:happy",
        "standard:teen:sad",
    ]
    assert sorted(path.name for path, _prompt in result.values()) == [
        "teen-happy.png",
        "teen-idle.png",
        "teen-sad.png",
    ]


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
        "app.services.image_service.get_openai_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
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
    assert "Dark fantasy" not in prompt
    assert "WORLD_DESCRIPTION_ANCHORS" not in prompt
    assert "source_text_do_not_copy" not in prompt
    assert "LORE_VARIATION_SEED" not in prompt


def test_generation_error_code_defaults_to_generic() -> None:
    assert generation_error_code(RuntimeError("unknown")) == "GENERATION_FAILED"


def test_generation_error_code_classifies_background_removal_failures() -> None:
    assert (
        generation_error_code(RuntimeError("Background removal failed: model unavailable"))
        == "IMAGE_POSTPROCESS_FAILED"
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
    assert (
        "never_say"
        not in CHARACTER_BIBLE_SCHEMA["properties"]["roleplay_contract"]["required"]
    )
    assert "growth_forms" in CHARACTER_BIBLE_SCHEMA["properties"]["visual"]["required"]
    assert "sample_replies" in CHARACTER_BIBLE_SCHEMA["properties"]["voice"]["required"]
    assert "drives" not in CHARACTER_BIBLE_SCHEMA["properties"]["inner_state"]["required"]
    assert "lore" not in CHARACTER_BIBLE_SCHEMA["required"]
    assert "dialogue_moves" not in CHARACTER_BIBLE_SCHEMA["required"]
    assert "provenance" not in CHARACTER_BIBLE_SCHEMA["required"]


def test_character_bible_prompt_requests_species_specific_lore() -> None:
    prompt = build_character_bible_prompt("маленький дракон с мягкими крыльями")

    assert "compact character profile" in prompt
    assert "tiny persona-file shape" in prompt
    assert "describe it" in prompt
    assert "what does it like" in prompt
    assert "what does it usually do" in prompt
    assert "roleplay_contract" in prompt
    assert "TONE_PROFILE" not in prompt
    assert "safe fictional behavior pattern" not in prompt
    assert "forbidden_random_additions" not in prompt
    assert "never_say" not in prompt
    assert "digital companion" not in prompt
    assert "visibly blend at least 4 different source fragments" not in prompt
    assert "Write every user-facing string value in natural Russian" in prompt
    assert "high-quality character card" not in prompt
    assert "voice.sample_replies: 5-8 short Russian replies" in prompt
    assert "lorebook_entries: 3-5 triggerable facts" in prompt
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
        "app.services.image_service.get_openai_client",
        lambda: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
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
