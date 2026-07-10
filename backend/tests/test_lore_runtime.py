from __future__ import annotations

import json

import pytest

from app.services.character_dossier import build_character_capsule, effective_character_data
from app.services.lore_runtime import (
    DATA_PATH,
    lore_prompt_block,
    validate_lore_runtime_config,
)


def test_lore_runtime_drives_all_fiction_surfaces() -> None:
    premise = json.loads(DATA_PATH.read_text(encoding="utf-8"))["world"]["premise"]

    for surface in ("characterCreation", "backgroundStory", "dialogueLore", "worldSeed"):
        prompt = lore_prompt_block(surface)
        assert premise in prompt
        assert "Канон конкретного персонажа важнее общей палитры мира." in prompt


def test_lore_runtime_validation_rejects_missing_surface() -> None:
    config = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    del config["surfaces"]["dialogueLore"]

    with pytest.raises(ValueError, match="surfaces.dialogueLore"):
        validate_lore_runtime_config(config)


def test_effective_character_dossier_keeps_voice_world_and_durable_facts() -> None:
    pet = {
        "name": "Звон",
        "description": "медный зверёк",
        "characterBible": {
            "identity": {"role": "слухач дождя"},
            "voice": {"rules": ["говорит медленно"]},
            "inner_state": {"core_want": "понять язык дождя"},
            "world": {"home": "ниша под древней дорогой"},
            "extensions": {
                "lite_overlay": {
                    "facts": [
                        {
                            "sphere": "appearance",
                            "text": "На левом роге осталась царапина.",
                            "source": "background_story_aftermath",
                        }
                    ]
                }
            },
        },
    }

    data = effective_character_data(pet)
    capsule = build_character_capsule(pet)
    chat_capsule = build_character_capsule(pet, include_durable_facts=False)

    assert data["voice"]["rules"] == ["говорит медленно"]
    assert data["world"]["home"] == "ниша под древней дорогой"
    assert "На левом роге осталась царапина." in capsule
    assert "На левом роге осталась царапина." not in chat_capsule


def test_effective_character_dossier_reads_legacy_lore_home() -> None:
    data = effective_character_data(
        {
            "name": "Олег",
            "description": "чел с листом вместо лица",
            "characterBible": {
                "lore": {
                    "home": {"place": "лесная поляна под кроной"},
                    "world": {"environment": "старый лес у древней дороги"},
                }
            },
        }
    )

    assert data["world"] == {
        "home": "лесная поляна под кроной",
        "habitat": "старый лес у древней дороги",
    }
