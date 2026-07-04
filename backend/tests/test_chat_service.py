from __future__ import annotations

import pytest

from app.models import Pet
from app.schemas import LocalChatRequest
from app.services.birth_message_service import fallback_birth_message, parse_birth_message_payload
from app.services.chat_service import (
    chat_with_local_pet,
    parse_chat_payload,
    validate_or_fallback_persisted_reply,
)
from app.services.pet_reply_engine.models import PetReplyResult


def test_memory_extraction_parsing() -> None:
    reply, memories = parse_chat_payload(
        """
        {
          "reply": "Я запомню!",
          "memories_to_save": [
            {"fact": "У пользователя завтра экзамен", "importance": 0.8}
          ]
        }
        """
    )

    assert reply == "Я запомню!"
    assert memories == [{"fact": "У пользователя завтра экзамен", "importance": 0.8}]


def test_memory_extraction_requires_reply() -> None:
    with pytest.raises(ValueError):
        parse_chat_payload("""{"reply": "", "memories_to_save": []}""")


def test_birth_message_parsing() -> None:
    reply = parse_birth_message_payload("""{"reply": "Я появился. Как тебя зовут?"}""")

    assert reply == "Я появился. Как тебя зовут?"


def test_birth_message_parsing_requires_reply() -> None:
    with pytest.raises(ValueError):
        parse_birth_message_payload("""{"reply": ""}""")


def test_birth_message_fallback_respects_baby_stage() -> None:
    pet = Pet(
        original_description="маленький комочек",
        character_profile_json={"species": "soft tiny mascot"},
        current_stage="baby",
    )

    assert fallback_birth_message(pet) == "Я тут... Привет. Как тебя звать?"


def test_persisted_chat_replaces_template_preference_reply() -> None:
    pet = Pet(
        original_description="серый челик с листом вместо лица",
        character_profile_json={
            "species": "листолик",
            "signature_features": ["лист вместо лица"],
            "lore": {
                "home": {
                    "favorite_spot": "моховая полка",
                    "story": "На моховой полке Кап спрятал его после кошки и оставил каплю.",
                },
                "origin": {"formative_event": "Кап спрятал его после кошки"},
                "inner_life": {
                    "likes": [
                        "теплый утренний туман",
                        "синие лейки",
                        "короткие просьбы",
                    ]
                },
            },
        },
        current_stage="teen",
        hunger=80,
        mood=80,
    )

    reply, used_fallback = validate_or_fallback_persisted_reply(
        "я люблю теплый утренний туман и синие лейки. короткие просьбы тоже.",
        pet,
        "что ты любишь?",
        [],
    )

    assert used_fallback
    assert "синие лейки" not in reply
    assert "короткие просьбы" not in reply
    assert "моховой полке" in reply


def test_local_chat_response_returns_lore_memories(monkeypatch) -> None:
    def fake_generate(_reply_input):
        return PetReplyResult(
            reply="друзья зовут меня Листикор.",
            mood_hint="idle",
            lore_memories_to_save=("ЛОР: друзья зовут питомца Листикор.",),
        )

    monkeypatch.setattr("app.services.chat_service.generate_pet_reply", fake_generate)

    response = chat_with_local_pet(
        LocalChatRequest.model_validate(
            {
                "message": "как тебя друзья зовут?",
                "pet": {
                    "description": "челик с листом вместо лица",
                    "stage": "teen",
                    "mood": "idle",
                    "stats": {
                        "hunger": 80,
                        "happiness": 80,
                        "energy": 80,
                        "cleanliness": 80,
                    },
                    "characterBible": {"lore": {"story_seeds": ["прозвище друзей"]}},
                    "loreMemories": ["ЛОР: питомец живет на нижней полке."],
                },
                "history": [],
            }
        )
    )

    assert response.reply == "друзья зовут меня Листикор."
    assert response.loreMemoriesToSave == ["ЛОР: друзья зовут питомца Листикор."]
