from __future__ import annotations

import json

from app.prompts.pet_image_prompts import build_pet_sprite_sheet_prompt
from app.services.character_templates import create_character_bible_from_template
from app.services.pet_reply_engine.effective_bible import build_effective_character_bible
from app.services.pet_reply_engine.models import PetPromptLayers, PetStats
from app.services.pokemon_template_presets import (
    load_pokemon_description_presets,
    pokemon_source_forbidden_terms,
    select_pokemon_description_preset,
)


def _visible_text(character_bible: dict) -> str:
    visible = {
        key: value
        for key, value in character_bible.items()
        if key not in {"extensions", "provenance"}
    }
    return json.dumps(visible, ensure_ascii=False).casefold()


def test_pokemon_description_markdown_loads_current_100_records() -> None:
    records = load_pokemon_description_presets()

    assert len(records) == 100
    assert records[0].display_name == "Bulbasaur"
    assert records[0].source_id == "1"
    assert records[0].genus == "Seed Pokémon"
    assert records[0].evolution_path == ("bulbasaur", "ivysaur", "venusaur")
    assert len(records[0].descriptions) >= 9
    assert records[-1].display_name == "Voltorb"


def test_pokemon_preset_selector_matches_core_prompt_traits() -> None:
    records = load_pokemon_description_presets()

    fire = select_pokemon_description_preset("огненный ящер", records=records)
    electric = select_pokemon_description_preset("электрическая мышь", records=records)
    turtle = select_pokemon_description_preset("черепаха с панцирем", records=records)

    assert fire.record.display_name == "Charmander"
    assert fire.confidence == "high"
    assert {"fire", "lizard"} <= set(fire.matched_terms)
    assert electric.record.display_name == "Pikachu"
    assert {"electric", "mouse"} <= set(electric.matched_terms)
    assert turtle.record.display_name == "Squirtle"
    assert {"turtle", "shell"} <= set(turtle.matched_terms)


def test_template_preset_mode_uses_pokemon_engine_without_old_template_loader(monkeypatch) -> None:
    def fail_old_loader(*_args, **_kwargs):
        raise AssertionError("old Character Card template directory should not be loaded")

    monkeypatch.setattr(
        "app.services.character_templates.load_character_templates",
        fail_old_loader,
    )

    character_bible = create_character_bible_from_template("огненный ящер")

    assert character_bible["provenance"]["source"] == "description_preset"
    assert character_bible["species"] == "огненный ящер"
    assert character_bible["identity"]["name"] == "Ящер"
    assert character_bible["extensions"]["preset_source_internal"]["source_id"] == "4"


def test_pokemon_preset_visible_bible_and_sprite_prompt_do_not_leak_source_identity() -> None:
    records = load_pokemon_description_presets()
    selection = select_pokemon_description_preset("огненный ящер", records=records)
    character_bible = create_character_bible_from_template("огненный ящер")
    visible_text = _visible_text(character_bible)
    full_text = json.dumps(character_bible, ensure_ascii=False).casefold()
    sprite_prompt = build_pet_sprite_sheet_prompt("огненный ящер", character_bible).casefold()

    forbidden_terms = {
        *(term.casefold() for term in pokemon_source_forbidden_terms(selection.record)),
        "pokemon",
        "pokémon",
        "pokeapi",
        "pokéapi",
        "poké ball",
        "trainer",
    }
    for term in forbidden_terms:
        assert term not in visible_text
        assert term not in full_text
        assert term not in sprite_prompt

    assert "visual_constraints" in sprite_prompt
    assert '"target_form": "огненный ящер"' in sprite_prompt
    assert "known franchise character identity" in sprite_prompt


def test_pokemon_preset_keeps_near_raw_source_description_facts_for_bellsprout_case() -> None:
    character_bible = create_character_bible_from_template(
        "серый чел дракон с листом вместо лица"
    )
    text = _visible_text(character_bible)

    assert character_bible["extensions"]["preset_source_internal"]["source_id"] == "69"
    assert "traps and eats bugs" in text
    assert "root feet" in text
    assert "hot and humid" in text
    assert "vines" in text
    assert "thin and flexible body" in text
    assert "corrosive fluid" in text
    assert "melts even iron" in text
    assert "legendary mandrake plant" in text
    assert "leaf-like face looks like a human face" in text

    assert "bellsprout" not in text
    assert "pokemon" not in text
    assert "pokémon" not in text
    assert "бережет внутреннее тепло" not in text
    assert "внутренний ритм" not in text
    assert "главный внутренний мотив" not in text


def test_pokemon_source_facts_do_not_pollute_sprite_prompt() -> None:
    character_bible = create_character_bible_from_template("электрический дракон")
    bible_text = json.dumps(character_bible, ensure_ascii=False).casefold()
    sprite_prompt = build_pet_sprite_sheet_prompt(
        "электрический дракон",
        character_bible,
    ).casefold()

    assert character_bible["extensions"]["preset_source_internal"]["source_id"] == "82"
    assert "radio signals" in bible_text
    assert "magnetic storm" in bible_text
    assert "fatal to electronics" in bible_text

    assert "radio signals" not in sprite_prompt
    assert "magnetic storm" not in sprite_prompt
    assert "fatal" not in sprite_prompt
    assert "high voltage" not in sprite_prompt
    assert "source_descriptions" not in sprite_prompt
    assert '"target_form": "электрический дракон"' in sprite_prompt


def test_pokemon_preset_growth_arc_and_runtime_age_override_are_separate() -> None:
    character_bible = create_character_bible_from_template("электрическая мышь")

    assert set(character_bible["growth_arc"]) == {"baby", "teen", "adult"}

    effective = build_effective_character_bible(
        character_bible,
        raw_description="электрическая мышь",
        age_stage="teen",
        mood="idle",
        stats=PetStats(hunger=80, happiness=70, energy=65, cleanliness=90),
        prompt_layers=PetPromptLayers(),
    )

    assert effective["identity"]["runtime_age_stage"] == "teen"
    assert effective["identity"]["runtime_age_label"] == "подросток"
    assert "age_behavior_profile" in effective["extensions"]["runtime_bible"]
    assert "подросток" in effective["extensions"]["runtime_bible"]["age_behavior_profile"]
