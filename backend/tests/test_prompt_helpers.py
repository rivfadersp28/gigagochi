from __future__ import annotations

from app.prompts.pet_image_prompts import (
    build_pet_single_sprite_prompt,
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
                "world": {
                    "story": "эта длинная история мира не должна попадать в image prompt"
                }
            },
        },
    )

    assert "крупный лист вместо лица" in prompt
    assert "эта длинная история мира" not in prompt


def test_single_sprite_prompt_forbids_grid_and_multiple_characters() -> None:
    prompt = build_pet_single_sprite_prompt(
        "серый челик с листом вместо лица",
        {"species": "листолицое семечко", "signature_features": ["крупный лист вместо лица"]},
        stage="baby",
        state="happy",
    )

    assert "Create one standalone character sprite" in prompt
    assert "No sprite sheet, no grid, no panels, no multiple characters" in prompt
    assert "Square app viewport composition" in prompt
    assert "without cropping any body part" in prompt
    assert "Stage: Small growth form" in prompt
    assert "State: Happy" in prompt
    assert "крупный лист вместо лица" in prompt


def test_single_sprite_prompt_avoids_minor_age_words_for_middle_growth_form() -> None:
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

    assert "Middle growth form" in prompt
    assert "Teen" not in prompt
    assert "active_growth_form_design" in prompt
    assert "чуть выше, с яркими мягкими антеннами" in prompt
    assert "teen_design" not in prompt
    assert "small_growth_form_design" not in prompt
    assert "middle_growth_form_design" not in prompt
    assert "mature_growth_form_design" not in prompt
    assert "малыш с коротким хвостом" not in prompt
    assert "взрослая форма с широкими крыльями" not in prompt
    assert "Only growth form" in prompt


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

    assert "STYLE_FRAME:" in prompt
    assert "one bold, memorable visual idea" in prompt
    assert "sphere, cube, drop, crystal, bean, star, cloud" in prompt
    assert "Prioritize iconic silhouette over anatomy" in prompt
