from __future__ import annotations

from app.prompts.pet_image_prompts import (
    build_pet_single_sprite_prompt,
    build_pet_single_sprite_safety_retry_prompt,
    build_pet_sprite_sheet_prompt,
    build_pet_state_strip_prompt,
    build_pet_state_strip_safety_retry_prompt,
    rewrite_known_character_references,
)


def test_known_character_rewrite() -> None:
    rewritten = rewrite_known_character_references("Пикачу с крыльями")

    assert "Пикачу" not in rewritten
    assert "фантазийное" in rewritten


def test_human_character_rewrite() -> None:
    rewritten = rewrite_known_character_references(
        "Я хочу милую аниме-чиби девочку, но с таинственным вайбом"
    )

    assert "девочку" not in rewritten
    assert "фантазийное существо" in rewritten


def test_sprite_sheet_prompt_requests_white_background_without_checkerboard() -> None:
    prompt = build_pet_sprite_sheet_prompt("серый челик с листом вместо лица", "{}")

    assert "square app viewport composition" in prompt
    assert "without cropping any body part" in prompt
    assert "Flat pure white background" in prompt
    assert "Do not use transparency" in prompt
    assert "checkerboard pattern" in prompt
    assert "must not cast any shadow outside its body" in prompt
    assert "contact shadow" in prompt
    assert "shadow-free" in prompt


def test_sprite_sheet_prompt_uses_visual_bible_slice() -> None:
    prompt = build_pet_sprite_sheet_prompt(
        "серый челик с листом вместо лица",
        {
            "species": "листолицое семечко",
            "signature": "лист вместо лица раскрывается, когда питомец доверяет собеседнику",
            "signature_features": ["крупный лист вместо лица"],
            "main_colors": ["зеленый", "кремовый"],
            "materials": ["гладкий винил"],
            "lore": {
                "world": {"story": "эта длинная история мира не должна попадать в image prompt"}
            },
        },
    )

    assert "крупный лист вместо лица" in prompt
    assert "эта длинная история мира" not in prompt


def test_single_sprite_prompt_uses_raw_description_and_shared_style_frame() -> None:
    prompt = build_pet_single_sprite_prompt(
        "серый челик с листом вместо лица",
        {"species": "листолицое семечко", "signature_features": ["крупный лист вместо лица"]},
        stage="baby",
        state="happy",
    )
    normalized = " ".join(prompt.split())

    assert prompt.startswith("серый челик с листом вместо лица\n\n")
    assert "collectible designer art toy" in prompt
    assert "SPRITE_PRESENTATION:" in prompt
    assert "pure white seamless background" in normalized
    assert "листолицое семечко" not in prompt
    assert "VARIANT:" not in prompt
    assert "CHARACTER_COLOR_SCRIPT:" in prompt
    assert "Do not collapse the character into beige/brown or blue/gray monochrome" in prompt


def test_single_sprite_safety_retry_keeps_minimal_prompt_contract() -> None:
    prompt = build_pet_single_sprite_safety_retry_prompt(
        "электрический дракон",
        {"signature_features": ["мягкие рога", "светящийся хвост"]},
        stage="teen",
        state="sad",
    )

    assert prompt.startswith("электрический дракон\n\n")
    assert "collectible designer art toy" in prompt
    assert "SPRITE_PRESENTATION:" in prompt
    assert "светящийся хвост" not in prompt
    assert "VARIANT:" not in prompt


def test_single_sprite_prompt_does_not_inject_growth_metadata() -> None:
    prompt = build_pet_single_sprite_prompt(
        "электрический дракон",
        {
            "species": "искровой дракон",
            "baby_design": "малыш с коротким хвостом",
            "teen_design": "чуть выше, с яркими рогами",
            "adult_design": "взрослая форма с широкими крыльями",
        },
        stage="teen",
        state="idle",
    )

    assert "active_growth_form_design" not in prompt
    assert "чуть выше, с яркими рогами" not in prompt
    assert "teen_design" not in prompt
    assert "small_growth_form_design" not in prompt
    assert "middle_growth_form_design" not in prompt
    assert "mature_growth_form_design" not in prompt
    assert "малыш с коротким хвостом" not in prompt
    assert "взрослая форма с широкими крыльями" not in prompt
    assert "VARIANT:" not in prompt


def test_state_strip_prompt_uses_one_middle_growth_form_with_three_states() -> None:
    prompt = build_pet_state_strip_prompt(
        "электрический дракон",
        {
            "species": "искровой дракон",
            "baby_design": "малыш с коротким хвостом",
            "teen_design": "чуть выше, с яркими рогами",
            "adult_design": "взрослая форма с широкими крыльями",
        },
        stage="teen",
    )

    assert "horizontal 3-column" in prompt
    assert "Exactly one row and three equal columns" in prompt
    assert "square app viewport composition" in prompt
    assert "without cropping any body part" in prompt
    assert "Columns from left to right: Idle, Happy, Sad" in prompt
    assert "Middle growth form" in prompt
    assert "active_growth_form_design" in prompt
    assert "чуть выше, с яркими мягкими антеннами" in prompt
    assert "малыш с коротким хвостом" not in prompt
    assert "взрослая форма с широкими крыльями" not in prompt
    assert "No text, no labels" in prompt


def test_state_strip_prompt_sanitizes_image_safety_trigger_words() -> None:
    prompt = build_pet_state_strip_prompt(
        "baby электрический дракон",
        {
            "species": "искровой дракон",
            "signature_features": ["игрушечное оружие"],
            "teen_design": "Подросток вытягивается в боб и держит дугу выше.",
            "do_not_change": [
                "не добавлять одежду, оружие, броню, профессию или социальную организацию"
            ],
        },
        stage="teen",
    )

    lowered = prompt.lower()

    assert "Tamagotchi" not in prompt
    assert "baby" not in lowered
    assert "teen" not in lowered
    assert "подрост" not in lowered
    assert "оруж" not in lowered
    assert "брон" not in lowered
    assert "weapon" not in lowered
    assert "armor" not in lowered
    assert "средняя форма вытягивается" in prompt
    assert "лишние предметы" in prompt
    assert "профессию" not in prompt


def test_state_strip_safety_retry_keeps_visual_style_frame() -> None:
    prompt = build_pet_state_strip_safety_retry_prompt(
        "электрический дракон",
        {"signature_features": ["мягкие рога", "светящийся хвост"]},
        stage="teen",
    )
    normalized = " ".join(prompt.split())

    assert "STYLE_FRAME:" in prompt
    assert "collectible designer art toy" in prompt
    assert "unexpected handcrafted wearable elements" in prompt
    assert "bold instantly recognizable silhouette" in normalized
    assert "SPRITE_PRESENTATION:" in prompt
