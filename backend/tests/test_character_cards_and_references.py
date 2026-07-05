from __future__ import annotations

import json

from app.prompts.pet_image_prompts import build_pet_sprite_sheet_prompt
from app.services.character_cards import (
    import_character_card,
    normalize_character_profile_v2,
    upgrade_character_bible_v2,
)
from app.services.character_templates import (
    adapt_character_template_card,
    create_character_bible_from_template,
    load_character_templates,
    select_character_template,
)
from app.services.reference_cards import load_reference_cards, select_reference_cards


def test_reference_cards_load_and_select_by_intent() -> None:
    cards = load_reference_cards()
    assert len(cards) >= 38
    assert all(card.source_url.startswith("https://") for card in cards)
    assert all("external" in card.source_family for card in cards)
    assert all("internal://" not in card.source_url for card in cards)
    assert all(
        any(marker in card.license_note.casefold() for marker in ("copied", "synthetic"))
        for card in cards
    )

    selected = select_reference_cards(
        user_text="что ты любишь?",
        intent="answer_preference",
        character_profile={"identity": {"species": "ключик"}, "world": {"objects": ["ключ"]}},
        limit=5,
        cards=cards,
    )

    assert 3 <= len(selected) <= 5
    assert any(card.type == "dialogue_act" for card in selected)
    assert any("answer_preference" in card.trigger_intents for card in selected)
    assert any(card.type == "negative_pattern" for card in selected)


def test_upgrade_character_bible_v2_preserves_existing_voice_and_fills_missing() -> None:
    old_bible = {
        "species": "ключик-компаньон",
        "personality": "робкий ключик, который любит нижний ящик",
        "dialogue_style": {
            "voice_rules": ["говорит тихо"],
            "sample_replies": ["я звякну совсем тихо."],
            "avoid_patterns": ["не говорить как ассистент"],
        },
        "lorebook_entries": [
            {"keys": ["ящик"], "content": "нижний ящик - его дом."},
        ],
        "lore": {
            "home": {"story": "Он живет в нижнем ящике.", "objects": ["бирка"]},
            "inner_life": {
                "core_want": "хочет открывать нужные дверцы",
                "inner_conflict": "боится потеряться",
                "fears": ["громкие замки"],
                "comfort_actions": ["держится за бирку"],
            },
        },
    }

    upgraded = upgrade_character_bible_v2(old_bible, raw_description="маленький ключик")
    profile = normalize_character_profile_v2(upgraded)

    assert upgraded["schema_version"] == 2
    assert profile["identity"]["species"] == "ключик-компаньон"
    assert profile["identity"]["role"] == "персонаж-компаньон из собственного мира"
    assert "говорит тихо" in profile["voice"]["voice_rules"]
    assert "я звякну совсем тихо." in profile["voice"]["sample_replies"]
    assert profile["inner_state"]["core_want"] == "хочет открывать нужные дверцы"
    assert profile["world"]["lorebook_entries"][0]["selective"] is True
    assert any(move["intent"] == "answer_preference" for move in profile["dialogue_moves"])


def test_import_character_card_maps_v2_fields() -> None:
    imported = import_character_card(
        {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": "Тум",
                "description": "маленький хранитель туманных пуговиц",
                "personality": "говорит коротко и прячет пуговицу, когда волнуется",
                "scenario": "живет в бюро находок",
                "first_mes": "я нашел твою пуговицу. держать ее рядом?",
                "alternate_greetings": ["я тихо звякнул биркой."],
                "mes_example": (
                    "{{user}}: что любишь?\n"
                    "{{char}}: люблю пуговицу с якорем, она не укатывается."
                ),
                "character_book": {
                    "entries": [
                        {
                            "keys": ["пуговица", "якорь"],
                            "content": "пуговица с якорем помогает Туму не теряться.",
                            "priority": 2,
                            "constant": False,
                            "selective": True,
                        }
                    ]
                },
                "system_prompt": "не звучать как помощник",
                "post_history_instructions": "держать короткий тон",
            },
        },
        source_url="https://example.com/card.json",
    )

    assert imported["schema_version"] == 2
    assert imported["identity"]["name"] == "Тум"
    assert imported["identity"]["role"] == "импортированный персонаж-компаньон"
    assert imported["openings"]["first_message"].startswith("я нашел")
    assert imported["world"]["lorebook_entries"][0]["keys"] == ["пуговица", "якорь"]
    assert imported["voice"]["voice_rules"] == [
        "говорит коротко и прячет пуговицу, когда волнуется"
    ]
    assert "example messages" in imported["voice"]["sentence_rhythm"]
    assert imported["voice"]["addressing_user"].startswith("обращается к пользователю")
    assert imported["provenance"]["source"] == "imported"
    assert imported["extensions"]["imported_instructions"]["system_prompt"] == (
        "не звучать как помощник"
    )
    assert imported["extensions"]["imported_instructions"]["post_history_instructions"]


def test_character_templates_load_and_select_fantasy_for_dragon() -> None:
    templates = load_character_templates()
    selection = select_character_template("я хочу сделать дракона", templates=templates)

    assert len(templates) >= 9
    assert selection.template.name in {
        "Aereth Family",
        "Reincarnated in otome game as a MOB!",
        "Taji",
    }
    assert selection.score > 0
    assert {"fantasy", "dragon"} & set(selection.matched_terms)


def test_template_preset_aligns_visuals_with_knight_prompt(tmp_path) -> None:
    card_path = tmp_path / "taji-card.json"
    card_path.write_text(
        """
        {
          "spec": "chara_card_v2",
          "spec_version": "2.0",
          "data": {
            "name": "Taji",
            "description": "dragon demihuman; black horns; scaled forearms; long tail.",
            "personality": "Taji's dragon-line makes his tail sway when he is tense.",
            "scenario": "Demihumans in this kingdom have animal parts and old dragon body customs.",
            "first_mes": "black horns caught the light; Taji's tail knocked the chair.",
            "mes_example": "{{user}}: что?\\n{{char}}: scaled forearms stiff; tail keeps moving.",
            "tags": ["Fantasy", "Historical", "Royalty"],
            "character_book": {
              "entries": [
                {
                  "keys": ["dragon-line", "tail"],
                  "content": "dragon demihuman lineage shows horns, scales, and tail."
                }
              ]
            }
          }
        }
        """,
        encoding="utf-8",
    )
    templates = load_character_templates(data_dir=tmp_path)

    character_bible = create_character_bible_from_template(
        "я хочу сделать рыцаря",
        templates=templates,
    )

    assert character_bible["species"] == "рыцарь"
    assert character_bible["identity"]["name"] == "Рыцарь"
    visual_constraints = character_bible["visual_constraints"]
    assert visual_constraints["target_form"] == "рыцарь"
    assert set(visual_constraints["forbidden_traits"]) == {
        "tail",
        "horns",
        "scales",
        "animal_traits",
        "dragon_body",
    }

    visible_bible = {
        key: value
        for key, value in character_bible.items()
        if key not in {"extensions", "provenance", "visual_constraints"}
    }
    visible_text = json.dumps(visible_bible, ensure_ascii=False).casefold()
    assert "dragon demihuman" not in visible_text
    assert "black horns" not in visible_text
    assert "scaled forearms" not in visible_text
    assert "tail sw" not in visible_text
    assert "dragon-line" not in visible_text

    prompt = build_pet_sprite_sheet_prompt("я хочу сделать рыцаря", character_bible)
    assert "visual_constraints" in prompt
    assert '"target_form": "рыцарь"' in prompt
    assert "tail / хвост" in prompt
    assert "USER_CHARACTER_DESCRIPTION and CHARACTER_BIBLE.visual_constraints" in prompt
    assert "dragon demihuman" not in prompt.casefold()
    assert "black horns" not in prompt.casefold()
    assert "scaled forearms" not in prompt.casefold()


def test_template_preset_keeps_dragon_anatomy_allowed(tmp_path) -> None:
    card_path = tmp_path / "dragon-card.json"
    card_path.write_text(
        """
        {
          "spec": "chara_card_v2",
          "spec_version": "2.0",
          "data": {
            "name": "Taji",
            "description": "dragon demihuman; black horns; scaled forearms; long tail.",
            "personality": "calm dragon-line guardian",
            "scenario": "dragon household",
            "tags": ["Fantasy", "Dragon"]
          }
        }
        """,
        encoding="utf-8",
    )
    templates = load_character_templates(data_dir=tmp_path)

    character_bible = create_character_bible_from_template(
        "я хочу сделать дракона",
        templates=templates,
    )

    assert character_bible["species"] == "дракон"
    forbidden_traits = set(character_bible["visual_constraints"]["forbidden_traits"])
    assert {"tail", "horns", "scales", "dragon_body"}.isdisjoint(forbidden_traits)


def test_template_preset_adapts_card_without_dropping_story_fields(tmp_path) -> None:
    card_path = tmp_path / "tum-card.json"
    card_path.write_text(
        """
        {
          "spec": "chara_card_v2",
          "spec_version": "2.0",
          "data": {
            "name": "Тум",
            "description": "маленький хранитель туманных пуговиц",
            "personality": "Тум говорит коротко и прячет пуговицу, когда волнуется",
            "scenario": "Тум живет в бюро находок",
            "first_mes": "я нашел твою пуговицу. держать ее рядом?",
            "alternate_greetings": ["Тум тихо звякнул биркой."],
            "mes_example": "{{user}}: что любишь?\\n{{char}}: Тум любит пуговицу с якорем.",
            "tags": ["Fantasy"],
            "character_book": {
              "entries": [
                {
                  "keys": ["пуговица", "якорь"],
                  "content": "пуговица с якорем помогает Туму не теряться.",
                  "priority": 2
                }
              ]
            }
          }
        }
        """,
        encoding="utf-8",
    )
    template = load_character_templates(data_dir=tmp_path)[0]

    adapted = adapt_character_template_card("я хочу сделать дракона", template)
    data = adapted["data"]

    assert data["name"] == "Дракон"
    assert "Главный персонаж этой истории - дракон" in data["description"]
    assert "Характер применяется" not in data["personality"]
    assert "template" not in data.get("system_prompt", "").casefold()
    assert "reskin" not in data.get("system_prompt", "").casefold()
    assert "Дракон живет в бюро находок" in data["scenario"]
    assert "Дракон любит пуговицу с якорем" in data["mes_example"]
    assert data["character_book"]["entries"][0]["constant"] is True
    assert (
        "пуговица с якорем помогает Дракону не теряться"
        in data["character_book"]["entries"][1]["content"]
    )


def test_template_preset_treats_source_age_as_runtime_metadata(tmp_path) -> None:
    card_path = tmp_path / "age-card.json"
    card_path.write_text(
        """
        {
          "spec": "chara_card_v2",
          "spec_version": "2.0",
          "data": {
            "name": "Лука",
            "description": "Age: 35. Luca appears mid-thirties and looks 35 years old.",
            "personality": "A 26-year-old guardian who sounds older than he is.",
            "scenario": "Luca watches a little tower.",
            "mes_example": "{{user}}: сколько тебе лет?\\n{{char}}: I am 35 years old.",
            "tags": ["Fantasy"]
          }
        }
        """,
        encoding="utf-8",
    )
    templates = load_character_templates(data_dir=tmp_path)

    adapted = adapt_character_template_card("я хочу сделать дракона", templates[0])
    adapted_text = json.dumps(adapted, ensure_ascii=False)
    character_bible = create_character_bible_from_template(
        "я хочу сделать дракона",
        templates=templates,
    )
    visible_text = json.dumps(
        {
            key: value
            for key, value in character_bible.items()
            if key not in {"extensions", "provenance"}
        },
        ensure_ascii=False,
    )

    assert "Age: 35" not in adapted_text
    assert "35 years old" not in adapted_text
    assert "26-year-old" not in adapted_text
    assert "mid-thirties" not in adapted_text
    assert "текущая возрастная стадия задается приложением" in adapted_text
    assert "Age: 35" not in visible_text
    assert "35 years old" not in visible_text
    assert "26-year-old" not in visible_text
    assert character_bible["extensions"]["template_preset"]["age_override_rule"]


def test_create_character_bible_from_template_marks_provenance(tmp_path) -> None:
    card_path = tmp_path / "tum-card.json"
    card_path.write_text(
        """
        {
          "spec": "chara_card_v2",
          "spec_version": "2.0",
          "data": {
            "name": "Тум",
            "description": "маленький хранитель туманных пуговиц",
            "personality": "говорит коротко и прячет пуговицу, когда волнуется",
            "scenario": "живет в бюро находок",
            "first_mes": "я нашел твою пуговицу. держать ее рядом?",
            "mes_example": "{{user}}: что любишь?\\n{{char}}: люблю пуговицу с якорем.",
            "character_book": {
              "entries": [
                {
                  "keys": ["пуговица", "якорь"],
                  "content": "пуговица с якорем помогает Туму не теряться."
                }
              ]
            }
          }
        }
        """,
        encoding="utf-8",
    )
    templates = load_character_templates(data_dir=tmp_path)

    character_bible = create_character_bible_from_template(
        "я хочу сделать дракона",
        templates=templates,
    )

    assert character_bible["schema_version"] == 2
    assert character_bible["species"] == "дракон"
    assert character_bible["identity"]["name"] == "Дракон"
    assert character_bible["identity"]["species"] == "дракон"
    assert character_bible["provenance"]["source"] == "template_preset"
    assert character_bible["provenance"]["source_urls"] == [
        "internal://character_templates/tum-card.json"
    ]
    assert character_bible["extensions"]["template_preset"]["original_name"] == "Тум"
    assert "template" in character_bible["extensions"]["template_preset"]["adaptation_instructions"]
    assert character_bible["world"]["lorebook_entries"][0]["keys"] == ["Дракон", "дракон"]
    visible_voice = "\n".join(character_bible["voice"]["voice_rules"])
    assert "template" not in visible_voice.casefold()
    assert "prompt" not in visible_voice.casefold()
    assert "reskin" not in visible_voice.casefold()
