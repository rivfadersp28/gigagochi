from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models import Pet
from app.prompts.chat_prompts import build_pet_birth_message_prompt, build_pet_chat_system_prompt
from app.prompts.pet_image_prompts import (
    build_pet_sprite_sheet_prompt,
    rewrite_known_character_references,
)
from app.services.pet_service import validate_description


def test_prompt_validation_empty() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_description("   ")

    assert exc_info.value.detail["code"] == "EMPTY_PROMPT"


def test_prompt_validation_too_long() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_description("a" * 301)

    assert exc_info.value.detail["code"] == "PROMPT_TOO_LONG"


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


def test_chat_prompt_anchors_voice_to_character_identity() -> None:
    pet = Pet(
        original_description="Игривая обезьяна с большим хвостом",
        character_profile_json={
            "species": "playful monkey mascot",
            "personality": "curious and nimble",
            "signature_features": ["curly tail", "banana-yellow ears"],
        },
        current_stage="teen",
        hunger=70,
        mood=80,
    )

    prompt = build_pet_chat_system_prompt(pet, [])

    assert "Игривая обезьяна" in prompt
    assert "curly tail" in prompt
    assert "monkey can sound curious" in prompt
    assert "Mood and hunger should color the reply slightly" in prompt
    assert "Keep replies concise by default" in prompt
    assert "PET_LORE_CANON" in prompt


def test_chat_prompt_uses_selected_visual_context_for_voice() -> None:
    pet = Pet(
        original_description="Серый челик с листиком вместо лица",
        character_profile_json={"personality": "gentle and observant"},
        current_stage="baby",
        hunger=80,
        mood=90,
    )

    prompt = build_pet_chat_system_prompt(
        pet,
        [],
        selected_stage="adult",
        selected_state="sad",
    )

    assert "stored_stage: baby" in prompt
    assert "selected_stage: adult" in prompt
    assert "selected_visual_state: sad" in prompt
    assert "Adult voice: natural grown-up speech" in prompt
    assert "No baby talk, no cutesy diminutives" in prompt
    assert "avoid cutesy Russian diminutive-affectionate wording" in prompt
    assert "sound like a grown, self-aware character" in prompt
    assert "Do not turn every reply into a status report" in prompt
    assert "Sad mood: lower energy and softer" in prompt
    assert "If selected_stage differs from stored_stage" in prompt
    assert "Treat selected_visual_state as subtext" in prompt
    assert "Do not make the prompt visible" in prompt


def test_chat_prompt_makes_baby_replies_terse() -> None:
    pet = Pet(
        original_description="Крошечный комочек с большими глазами",
        character_profile_json={"personality": "soft and sleepy"},
        current_stage="baby",
        hunger=50,
        mood=60,
    )

    prompt = build_pet_chat_system_prompt(pet, [])

    assert "Baby stage: very brief and simple replies" in prompt
    assert "Prefer 1-6 words" in prompt
    assert "simple sounds like" not in prompt
    assert "Baby voice" not in prompt
    assert "Do not explain much" in prompt


def test_birth_message_prompt_introduces_pet_from_profile() -> None:
    pet = Pet(
        original_description="Серый челик с листом вместо лица",
        character_profile_json={
            "species": "leaf-faced soft mascot",
            "personality": "quiet but curious",
            "signature_features": ["green leaf face", "round grey body"],
        },
        current_stage="baby",
        hunger=80,
        mood=80,
    )

    prompt = build_pet_birth_message_prompt(pet, "happy")

    assert "first message after being born/generated" in prompt
    assert "leaf-faced soft mascot" in prompt
    assert "green leaf face" in prompt
    assert "Invite the user to get acquainted" in prompt
    assert "Ask one simple question" in prompt
    assert "Baby: extremely brief" in prompt


def test_chat_and_birth_prompts_include_lore_canon_rules() -> None:
    profile = {
        "species": "small dragon mascot",
        "personality": "warm and proud",
        "lore": {
            "world": {
                "name": "Теплая Пещерка",
                "environment": "маленькая пещера с гладкими камушками",
                "rules": ["дымок означает привет"],
                "sensory_details": ["теплый камень"],
            },
            "home": {
                "place": "пещера под мягким холмом",
                "room": "гнездо у угольков",
                "favorite_spot": "камень с блестками",
                "objects": ["маленький уголь", "гладкий камушек"],
            },
            "origin": {
                "birthplace": "теплое гнездо",
                "caretakers": ["старшая драконица"],
                "formative_event": "нашел первый блестящий камень",
            },
            "relationships": {
                "family": ["старшая драконица"],
                "friends": [
                    {
                        "name": "Дымка",
                        "role": "друг",
                        "species_or_form": "облачко дыма",
                        "relationship_dynamic": "смеется, когда питомец фыркает",
                    }
                ],
                "attitude_to_user": "считает собеседника хранителем тепла",
            },
            "inner_life": {
                "likes": ["теплые камушки"],
                "dislikes": ["холодные лужи"],
                "fears": ["сквозняк"],
                "dreams": ["раздуть ровный дымок"],
                "habits": ["перекладывает камушки"],
                "comfort_actions": ["сворачивается у угольков"],
                "flaws": ["иногда гордится слишком сильно"],
            },
            "voice": {
                "favorite_phrases": ["фыр"],
                "topic_hooks": ["теплые камушки"],
                "secret_details": ["прячет самый гладкий камушек"],
                "avoid_saying": ["я водяной"],
            },
            "growth_arc": {
                "baby": "учится греть камушки",
                "teen": "учится беречь гнездо",
                "adult": "становится хранителем маленькой пещеры",
            },
        },
    }
    pet = Pet(
        original_description="маленький дракон",
        character_profile_json=profile,
        current_stage="teen",
        hunger=80,
        mood=80,
    )

    chat_prompt = build_pet_chat_system_prompt(pet, [])
    birth_prompt = build_pet_birth_message_prompt(pet, "happy")

    assert "PET_LORE_CANON" in chat_prompt
    assert "Теплая Пещерка" in chat_prompt
    assert "камень с блестками" in chat_prompt
    assert "Do not retell the full lore" in chat_prompt
    assert "stable background foundation" in chat_prompt
    assert 'starting with "ЛОР:"' in chat_prompt
    assert "memories_to_save" in chat_prompt
    assert "PET_LORE_CANON" in birth_prompt
    assert "Optionally use 0-1 gentle background detail" in birth_prompt


def test_chat_prompt_marks_lore_memories_as_pet_canon() -> None:
    pet = Pet(
        original_description="Серый челик с листом вместо лица",
        character_profile_json={
            "species": "листолик",
            "lore": {"story_seeds": ["как друзья зовут питомца"]},
        },
        current_stage="teen",
        hunger=80,
        mood=80,
    )

    memory = type(
        "MemoryStub",
        (),
        {"fact": "ЛОР: друзья зовут питомца Листикор.", "importance": 0.9},
    )()
    prompt = build_pet_chat_system_prompt(pet, [memory])

    assert "pet canon: ЛОР: друзья зовут питомца Листикор." in prompt
    assert "Use those facts before inventing anything new" in prompt
    assert 'Prefix these facts exactly as "ЛОР: ..."' in prompt
