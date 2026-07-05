from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from app.prompts.pet_image_prompts import build_character_bible_prompt, create_lore_seed
from app.prompts.world_description_anchors import (
    format_world_description_anchors_for_prompt,
    select_world_description_anchors,
)
from app.services.external_character_sources import (
    external_fragments_prompt_block,
    select_external_character_fragments,
)
from app.services.image_service import (
    CHARACTER_BIBLE_SCHEMA,
    character_bible_quality_issues,
    create_character_bible,
    extract_sprite_cells,
    generate_pet_asset_set,
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
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=content)
                    )
                ]
            )

    monkeypatch.setattr(
        "app.services.image_service.get_settings",
        lambda: SimpleNamespace(
            openai_chat_model="test-model",
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
    assert result["extensions"]["world_description_anchors_used"]
    assert result["extensions"]["lite_overlay"]["spheres"]["character"]["facts"][0]["text"]
    assert result["extensions"]["lite_overlay"]["spheres"]["world"]["facts"][0]["text"]
    assert all(call["timeout"] == 180 for call in calls)
    assert "WORLD_DESCRIPTION_ANCHORS" in calls[0]["messages"][1]["content"]


def test_world_description_anchors_select_habitat_from_description() -> None:
    anchors = select_world_description_anchors("маленький огненный дракон с угольками", count=3)

    assert anchors
    assert anchors[0].habitat == "volcanic"
    assert anchors[0].id.startswith("world:volcanic:")
    assert anchors[0].source_text


def test_character_bible_prompt_uses_world_description_anchors() -> None:
    anchors = select_world_description_anchors("водяной зверек с ракушкой", count=2)
    block = format_world_description_anchors_for_prompt(anchors)
    prompt = build_character_bible_prompt(
        "водяной зверек с ракушкой",
        lore_seed=create_lore_seed(),
        world_description_anchors=block,
    )

    assert "WORLD_DESCRIPTION_ANCHORS" in prompt
    assert "source_text_do_not_copy" in prompt
    assert "Do not copy source_text_do_not_copy" in prompt
    assert "world:waters-edge:" in prompt


def test_generate_pet_asset_set_can_use_template_presets(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {"prompt_calls": []}

    def fail_old_character_bible(_description):
        pytest.fail("create_character_bible should not run in template preset mode")

    def fake_build_single_sprite_prompt(description, character_bible, *, stage, state):
        captured["prompt_calls"].append((description, character_bible, stage, state))
        return f"sprite prompt {stage} {state}"

    monkeypatch.setattr(
        "app.services.image_service.generated_dir_for",
        lambda asset_id: tmp_path / str(asset_id),
    )
    monkeypatch.setattr(
        "app.services.image_service.create_character_bible",
        fail_old_character_bible,
    )
    monkeypatch.setattr(
        "app.services.image_service.create_character_bible_from_template",
        lambda description: {"schema_version": 2, "species": "дракон"},
    )
    monkeypatch.setattr(
        "app.services.image_service.attach_lite_initial_overlay",
        lambda character_bible, description: character_bible,
    )
    monkeypatch.setattr(
        "app.services.image_service.build_pet_single_sprite_prompt",
        fake_build_single_sprite_prompt,
    )
    monkeypatch.setattr(
        "app.services.image_service.generate_single_sprite_image_bytes",
        lambda _prompt: b"png",
    )
    monkeypatch.setattr(
        "app.services.image_service.crop_sprite_sheet",
        lambda *_args: pytest.fail("sprite sheet crop should not run"),
    )

    result = generate_pet_asset_set(
        "я хочу сделать дракона",
        use_template_presets=True,
    )

    assert result["characterBible"]["species"] == "дракон"
    assert result["spriteSheetUrl"] is None
    assert len(captured["prompt_calls"]) == 12
    assert captured["prompt_calls"][0] == (
        "я хочу сделать дракона",
        {"schema_version": 2, "species": "дракон"},
        "baby",
        "idle",
    )
    for stage in ("baby", "teen", "adult"):
        assert set(result["images"][stage]) == {"idle", "happy", "hungry", "sad"}


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

    assert "clean original creature bible" in prompt
    assert "CHARACTER_BIBLE_STYLE_DIRECTION" in prompt
    assert "CREATURE_DESCRIPTION_STYLE_GUIDE" in prompt
    assert "species entry" in prompt
    assert "physical anchor" in prompt
    assert "mechanism" in prompt
    assert "behavior trigger" in prompt
    assert "habitat" in prompt
    assert "EXTERNAL_SOURCE_FRAGMENT_MIX" in prompt
    assert "test_source" in prompt
    assert "weak dialogue-rhythm references" in prompt
    assert "must not supply the pet's" in prompt
    assert "must never describe itself as digital" in prompt
    assert "Do not invent a random social world" in prompt
    assert "identity.role must be a lived role" in prompt
    assert "digital companion" not in prompt
    assert "visibly blend at least 4 different source fragments" not in prompt
    assert "Write every user-facing string value in Russian" in prompt
    assert "мягкий дракончик-компаньон" in prompt
    assert "Keep visual support fields compact" in prompt
    assert "signature must be 2-3 compact sentences" in prompt
    assert "personality must be 2-4 connected sentences" in prompt
    assert "high-quality character card" not in prompt
    assert "dialogue_style must be a compact behavior simulator" in prompt
    assert "voice.sample_replies must contain 8-12 short Russian replies" in prompt
    assert "dialogue_style.sample_replies may mirror the best 4-6" in prompt
    assert "opening_scenes must contain 2-3 first-message style scenes" in prompt
    assert "lorebook_entries must contain 5-8 compact triggerable facts" in prompt
    assert "dialogue_moves must contain 3-5 entries" in prompt
    assert "Forbidden generic reply patterns" in prompt
    assert "короткие просьбы" in prompt
    assert "dragon-like" in prompt
    assert "world.home must be habitat" in prompt
    assert "Initial lore is a foundation for future improvisation" in prompt
    assert "Do not write event-log lore" in prompt
    assert "story_seeds must contain 4-6 open hooks" in prompt
    assert "Do not default to the same" not in prompt
    assert "storybook logic" not in prompt
    assert "Every cause must make literal or storybook sense" in prompt
    assert "Do not make objects perform human-like actions" not in prompt
    assert 'BAD world rule: "Лед помогает мне быть полезным' in prompt
    assert 'BAD physical logic: "Я выпускаю мягкий пар' not in prompt
    assert "слишком холодным" in prompt
    assert "because test" in prompt
    assert "короткие просьбы" in prompt
    assert 'BAD likes: ["короткие просьбы", "добрые слова", "быть нужным"]' in prompt
    assert "story_seeds" in prompt
    assert "larger concrete setting" not in prompt
    assert "GOOD world story" in prompt
    assert "снежной нише" in prompt
    assert "бюро забытых вещей" not in prompt


def test_character_bible_prompt_accepts_private_lore_seed() -> None:
    lore_seed = {
        "body_mechanism": "заметная часть тела хранит энергию и меняется от состояния",
        "behavior_trigger": "при радости признак становится ярче или активнее",
        "habitat_pressure": "домом служит простое место, где удобно поддерживать главный элемент",
        "growth_clue": "каждая стадия добавляет одну простую способность",
    }

    prompt = build_character_bible_prompt(
        "сонное облако с маленьким ключом",
        lore_seed=lore_seed,
    )
    plain_prompt = build_character_bible_prompt("сонное облако с маленьким ключом")

    assert "LORE_VARIATION_SEED" in prompt
    assert "body_mechanism" in prompt
    assert "behavior triggers, habitat pressure, and growth clues" in prompt
    assert "LORE_VARIATION_SEED" not in plain_prompt


def test_create_lore_seed_uses_curated_dimensions() -> None:
    class FirstChoice:
        def choice(self, values):
            return values[0]

    seed = create_lore_seed(FirstChoice())

    assert set(seed) == {
        "body_mechanism",
        "behavior_trigger",
        "habitat_pressure",
        "growth_clue",
    }
    assert seed["body_mechanism"] == "заметная часть тела хранит энергию и меняется от состояния"


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
            "body_mechanism": "заметная часть тела хранит энергию и меняется от состояния",
            "behavior_trigger": "при усталости элемент тускнеет, остывает или затихает",
            "habitat_pressure": (
                "домом служит простое место, где удобно поддерживать главный элемент"
            ),
            "growth_clue": "каждая стадия добавляет одну простую способность",
        },
    )

    assert result["schema_version"] == 2
    assert result["species"] == repaired_bible["species"]
    assert result["lore"]["world"]["story"] == repaired_bible["lore"]["world"]["story"]
    assert result["extensions"]["lite_overlay"]["spheres"]["character"]["facts"]
    assert result["extensions"]["lite_overlay"]["spheres"]["world"]["facts"]
    assert "identity" in result
    assert "dialogue_moves" in result
    assert len(calls) == 4
    assert "LORE_VARIATION_SEED" in calls[0][1]["content"]
    assert "body_mechanism" in calls[0][1]["content"]
    assert "Repair this character bible" in calls[1][1]["content"]
    assert "CHARACTER_BIBLE_STYLE_DIRECTION" in calls[1][1]["content"]
    assert "LORE_VARIATION_SEED_USED" in calls[1][1]["content"]
