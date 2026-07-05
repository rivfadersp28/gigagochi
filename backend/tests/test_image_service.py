from __future__ import annotations

import base64
import json
from types import SimpleNamespace

from PIL import Image, ImageDraw

from app.prompts.pet_image_prompts import build_character_bible_prompt, create_lore_seed
from app.services.external_character_sources import (
    external_fragments_prompt_block,
    select_external_character_fragments,
)
from app.services.image_service import (
    CHARACTER_BIBLE_SCHEMA,
    character_bible_quality_issues,
    create_character_bible,
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
    assert {
        "schema_version",
        "identity",
        "voice",
        "inner_state",
        "world",
        "dialogue_moves",
        "openings",
        "provenance",
        "extensions",
    }.issubset(CHARACTER_BIBLE_SCHEMA["required"])
    assert CHARACTER_BIBLE_SCHEMA["properties"]["schema_version"]["enum"] == [2]
    assert "sample_replies" in CHARACTER_BIBLE_SCHEMA["properties"]["voice"]["required"]
    assert "drives" in CHARACTER_BIBLE_SCHEMA["properties"]["inner_state"]["required"]
    assert "lorebook_entries" in CHARACTER_BIBLE_SCHEMA["properties"]["world"]["required"]
    assert CHARACTER_BIBLE_SCHEMA["properties"]["dialogue_moves"]["items"]["required"] == [
        "intent",
        "pattern",
        "good_example",
        "bad_example",
    ]

    assert "signature" in CHARACTER_BIBLE_SCHEMA["required"]
    signature_schema = CHARACTER_BIBLE_SCHEMA["properties"]["signature"]
    assert signature_schema["type"] == "string"
    assert {"dialogue_style", "opening_scenes", "lorebook_entries"}.issubset(
        CHARACTER_BIBLE_SCHEMA["required"]
    )
    dialogue_schema = CHARACTER_BIBLE_SCHEMA["properties"]["dialogue_style"]
    opening_scenes_schema = CHARACTER_BIBLE_SCHEMA["properties"]["opening_scenes"]
    lorebook_entries_schema = CHARACTER_BIBLE_SCHEMA["properties"]["lorebook_entries"]
    assert set(dialogue_schema["required"]) == {
        "voice_rules",
        "emotional_reactions",
        "initiative_style",
        "sample_replies",
        "avoid_patterns",
    }
    assert "chat replies" in dialogue_schema["properties"]["sample_replies"]["description"]
    assert "first-message style" in opening_scenes_schema["description"]
    assert "keys" in lorebook_entries_schema["items"]["required"]
    assert "content" in lorebook_entries_schema["items"]["required"]

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
    assert "Background foundation paragraph" in world_schema["properties"]["story"]["description"]
    assert "not slogans" in world_schema["properties"]["rules"]["description"]
    assert "daily_life" not in world_schema["properties"]
    assert "story" in home_schema["required"]
    assert "Home foundation paragraph" in home_schema["properties"]["story"]["description"]
    assert "daily_routine" not in home_schema["properties"]
    assert "emotional_meaning" not in home_schema["properties"]
    assert "story" in origin_schema["required"]
    assert (
        "Broad formative pressure" in origin_schema["properties"]["formative_event"]["description"]
    )
    assert "turning_point" not in origin_schema["properties"]
    assert relationships_schema["additionalProperties"] is False
    assert "story" in relationships_schema["required"]
    assert (
        "Relationship network foundation"
        in relationships_schema["properties"]["story"]["description"]
    )
    friend_schema = relationships_schema["properties"]["friends"]["items"]
    assert "shared_history" not in friend_schema["properties"]
    assert (
        "Recurring shared dynamic"
        in friend_schema["properties"]["relationship_dynamic"]["description"]
    )
    assert {"core_want", "inner_conflict"}.issubset(inner_life_schema["required"])
    assert "background tension" in inner_life_schema["properties"]["likes"]["description"]
    assert "short requests" in inner_life_schema["properties"]["likes"]["description"]
    assert "Physical actions" in inner_life_schema["properties"]["habits"]["description"]
    assert "private_memory" not in inner_life_schema["properties"]
    assert "speech_pattern" in voice_schema["required"]
    assert "Open-ended future reveal hooks" in story_seeds_schema["description"]


def test_character_bible_prompt_requests_species_specific_lore() -> None:
    prompt = build_character_bible_prompt(
        "маленький дракон с мягкими крыльями",
        external_source_fragments="- test_source [external; seed_reply; en]: concrete line",
    )

    assert "scaffold-first character bible" in prompt
    assert "EXTERNAL_SOURCE_FRAGMENT_MIX" in prompt
    assert "test_source" in prompt
    assert "Use EXTERNAL_SOURCE_FRAGMENT_MIX as raw test corpus material" in prompt
    assert "visibly blend at least 4 different source fragments" in prompt
    assert "Write every user-facing string value in Russian" in prompt
    assert "мягкий дракончик-компаньон" in prompt
    assert "Keep visual support fields compact" in prompt
    assert "signature and personality the center" in prompt
    assert "personality must be 2-4 connected sentences" in prompt
    assert "high-quality character card" in prompt
    assert "dialogue_style must be a compact behavior simulator" in prompt
    assert "voice.sample_replies must contain 8-12 short Russian replies" in prompt
    assert "dialogue_style.sample_replies may mirror the best 4-6" in prompt
    assert "opening_scenes must contain 2-3 first-message style scenes" in prompt
    assert "lorebook_entries must contain 5-8 compact triggerable facts" in prompt
    assert "dialogue_moves must contain 3-5 entries" in prompt
    assert "Forbidden generic reply patterns" in prompt
    assert "короткие просьбы" in prompt
    assert "dragon-like" in prompt
    assert (
        "world, home, origin, relationships, and inner_life feel like one connected background"
        in prompt
    )
    assert "Initial lore is a foundation for future improvisation" in prompt
    assert "Do not write event-log lore" in prompt
    assert "story_seeds must contain 4-6 open hooks" in prompt
    assert "Do not default to the same" in prompt
    assert "avoid greenhouse, shelf, moss, dew, warm lamp" in prompt
    assert "storybook logic" in prompt
    assert "steam itself is not loud" in prompt
    assert "Do not make objects perform human-like actions" in prompt
    assert 'BAD world rule: "Лист показывает правду настроения."' in prompt
    assert 'BAD physical logic: "Я выпускаю мягкий пар' in prompt
    assert "клапан на спине тихо шипит" in prompt
    assert "because test" in prompt
    assert "короткие просьбы" in prompt
    assert 'BAD likes: ["теплый утренний туман", "синие лейки", "короткие просьбы"]' in prompt
    assert "story_seeds" in prompt
    assert "larger concrete setting" in prompt
    assert "GOOD world story" in prompt
    assert "бюро забытых вещей" in prompt


def test_character_bible_prompt_accepts_private_lore_seed() -> None:
    lore_seed = {
        "setting_tone": "ящик путешественника с вещами из разных мест",
        "social_shape": "есть один потенциальный друг и строгий наставник",
        "background_tension": "питомец хочет доказать самостоятельность",
        "future_reveal": "позже можно раскрыть прозвище друга",
    }

    prompt = build_character_bible_prompt(
        "сонное облако с маленьким ключом",
        lore_seed=lore_seed,
    )
    plain_prompt = build_character_bible_prompt("сонное облако с маленьким ключом")

    assert "LORE_VARIATION_SEED" in prompt
    assert "ящик путешественника" in prompt
    assert "shape the setting, social roles, background tension" in prompt
    assert "LORE_VARIATION_SEED" not in plain_prompt


def test_create_lore_seed_uses_curated_dimensions() -> None:
    class FirstChoice:
        def choice(self, values):
            return values[0]

    seed = create_lore_seed(FirstChoice())

    assert set(seed) == {
        "setting_tone",
        "social_shape",
        "background_tension",
        "future_reveal",
    }
    assert seed["setting_tone"] == "маленькое ремесленное место"


def test_external_source_fragments_are_available_for_character_generation() -> None:
    fragments = select_external_character_fragments(
        user_description="робот с чашкой кофе",
        count=6,
    )
    block = external_fragments_prompt_block(fragments)

    assert len(fragments) >= 4
    assert "external_a16z_companion_app" in block
    assert all(fragment.source_url.startswith("https://") for fragment in fragments)


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


def test_create_character_bible_repairs_quality_issues_once(monkeypatch) -> None:
    bad_bible = {
        "species": "паровой дракончик",
        "lore": {
            "world": {
                "story": (
                    "Он живет на теплой полке у мха и выпускает мягкий пар, "
                    "стараясь не делать его слишком громким."
                )
            }
        },
    }
    repaired_bible = {
        "species": "паровой дракончик",
        "lore": {
            "world": {
                "story": (
                    "Он живет в маленькой котельной при ночной булочной. "
                    "Когда волнуется, клапан на спине тихо шипит."
                )
            }
        },
    }
    calls: list[list[dict[str, str]]] = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs["messages"])
            payload = bad_bible if len(calls) == 1 else repaired_bible
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload))),
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

    result = create_character_bible(
        "маленький паровой дракончик",
        lore_seed={
            "setting_tone": "ночная пекарня с дежурными полками",
            "social_shape": "есть один потенциальный друг и строгий наставник",
            "background_tension": "питомец хочет быть полезным, но боится ошибиться",
            "future_reveal": "позже можно раскрыть местную традицию",
        },
    )

    assert result["schema_version"] == 2
    assert result["species"] == repaired_bible["species"]
    assert result["lore"]["world"]["story"] == repaired_bible["lore"]["world"]["story"]
    assert "identity" in result
    assert "dialogue_moves" in result
    assert len(calls) == 2
    assert "LORE_VARIATION_SEED" in calls[0][1]["content"]
    assert "ночная пекарня" in calls[0][1]["content"]
    assert "Repair this character bible" in calls[1][1]["content"]
    assert "LORE_VARIATION_SEED_USED" in calls[1][1]["content"]
