from __future__ import annotations

import base64
from types import SimpleNamespace

from PIL import Image, ImageDraw

from app.prompts.pet_image_prompts import build_character_bible_prompt
from app.services.image_service import (
    CHARACTER_BIBLE_SCHEMA,
    extract_sprite_cells,
    generate_sprite_sheet_bytes,
    generation_error_code,
)


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


def test_generate_sprite_sheet_omits_unset_background(monkeypatch) -> None:
    captured: dict[str, object] = {}

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

    result = generate_sprite_sheet_bytes("prompt")

    assert result == b"image-bytes"
    assert "background" not in captured
    assert captured["timeout"] == 180


def test_generation_error_code_defaults_to_generic() -> None:
    assert generation_error_code(RuntimeError("unknown")) == "GENERATION_FAILED"


def test_character_bible_schema_requires_lore() -> None:
    assert "signature" in CHARACTER_BIBLE_SCHEMA["required"]
    signature_schema = CHARACTER_BIBLE_SCHEMA["properties"]["signature"]
    assert signature_schema["type"] == "string"

    assert "lore" in CHARACTER_BIBLE_SCHEMA["required"]
    lore_schema = CHARACTER_BIBLE_SCHEMA["properties"]["lore"]

    assert lore_schema["additionalProperties"] is False
    assert set(lore_schema["required"]) == {
        "world",
        "home",
        "origin",
        "relationships",
        "inner_life",
        "voice",
        "growth_arc",
        "story_seeds",
    }
    world_schema = lore_schema["properties"]["world"]
    home_schema = lore_schema["properties"]["home"]
    origin_schema = lore_schema["properties"]["origin"]
    relationships_schema = lore_schema["properties"]["relationships"]
    inner_life_schema = lore_schema["properties"]["inner_life"]
    voice_schema = lore_schema["properties"]["voice"]
    story_seeds_schema = lore_schema["properties"]["story_seeds"]

    assert "story" in world_schema["required"]
    assert "Background foundation paragraph" in world_schema["properties"]["story"][
        "description"
    ]
    assert "not slogans" in world_schema["properties"]["rules"]["description"]
    assert "daily_life" not in world_schema["properties"]
    assert "story" in home_schema["required"]
    assert "Home foundation paragraph" in home_schema["properties"]["story"]["description"]
    assert "daily_routine" not in home_schema["properties"]
    assert "emotional_meaning" not in home_schema["properties"]
    assert "story" in origin_schema["required"]
    assert "Broad formative pressure" in origin_schema["properties"]["formative_event"][
        "description"
    ]
    assert "turning_point" not in origin_schema["properties"]
    assert relationships_schema["additionalProperties"] is False
    assert "story" in relationships_schema["required"]
    assert "Relationship network foundation" in relationships_schema["properties"]["story"][
        "description"
    ]
    friend_schema = relationships_schema["properties"]["friends"]["items"]
    assert "shared_history" not in friend_schema["properties"]
    assert "Recurring shared dynamic" in friend_schema["properties"][
        "relationship_dynamic"
    ]["description"]
    assert {"core_want", "inner_conflict"}.issubset(inner_life_schema["required"])
    assert "background tension" in inner_life_schema["properties"]["likes"]["description"]
    assert "short requests" in inner_life_schema["properties"]["likes"]["description"]
    assert "Physical actions" in inner_life_schema["properties"]["habits"]["description"]
    assert "private_memory" not in inner_life_schema["properties"]
    assert "speech_pattern" in voice_schema["required"]
    assert "Open-ended future reveal hooks" in story_seeds_schema["description"]


def test_character_bible_prompt_requests_species_specific_lore() -> None:
    prompt = build_character_bible_prompt("маленький дракон с мягкими крыльями")

    assert "scaffold-first character bible" in prompt
    assert "Write every user-facing string value in Russian" in prompt
    assert "мягкий дракончик-компаньон" in prompt
    assert "Keep visual support fields compact" in prompt
    assert "signature and personality the center" in prompt
    assert "personality must be 2-4 connected sentences" in prompt
    assert "dragon-like" in prompt
    assert (
        "world, home, origin, relationships, and inner_life feel like one connected background"
        in prompt
    )
    assert "Initial lore is a foundation for future improvisation" in prompt
    assert "Do not write event-log lore" in prompt
    assert "story_seeds must contain 4-6 open hooks" in prompt
    assert "Ростковом квартале большого города растений" in prompt
    assert "Мох\n  слушает шаги" in prompt
    assert "Do not make objects perform human-like actions" in prompt
    assert 'BAD world rule: "Лист показывает правду настроения."' in prompt
    assert "because test" in prompt
    assert "короткие просьбы" in prompt
    assert 'BAD likes: ["теплый утренний туман", "синие лейки", "короткие просьбы"]' in prompt
    assert "story_seeds" in prompt
    assert "larger concrete setting" in prompt
