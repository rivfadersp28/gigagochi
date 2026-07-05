from __future__ import annotations

from app.services.character_cards import (
    import_character_card,
    normalize_character_profile_v2,
    upgrade_character_bible_v2,
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
    assert imported["openings"]["first_message"].startswith("я нашел")
    assert imported["world"]["lorebook_entries"][0]["keys"] == ["пуговица", "якорь"]
    assert imported["provenance"]["source"] == "imported"
    assert imported["extensions"]["imported_instructions"]["post_history_instructions"]
