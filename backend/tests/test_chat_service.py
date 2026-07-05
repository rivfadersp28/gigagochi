from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.models import Pet
from app.schemas import LiteFactExtractionRequest, LocalChatRequest
from app.services.birth_message_service import fallback_birth_message, parse_birth_message_payload
from app.services.chat_service import (
    build_pet_reply_input,
    chat_with_local_pet,
    parse_chat_payload,
    validate_or_fallback_persisted_reply,
)
from app.services.pet_memory.models import MemoryCandidate
from app.services.pet_reply_engine.lite_generator import (
    build_lite_chat_messages,
    extract_lite_overlay_patch_from_reply,
    generate_lite_pet_reply,
)
from app.services.pet_reply_engine.lore import home_fragment
from app.services.pet_reply_engine.models import PetReplyResult
from app.services.pet_reply_engine.prompt_builder import build_pet_reply_messages


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


class FakeLiteCompletions:
    def __init__(self, messages):
        self._messages = list(messages)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = self._messages.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def fake_lite_client(*messages):
    completions = FakeLiteCompletions(messages)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def lite_payload(**overrides) -> LocalChatRequest:
    data = {
        "message": "привет",
        "replyMode": "lite",
        "pet": {
            "name": "Громм",
            "description": "гигантский земляной великан",
            "stage": "adult",
            "mood": "idle",
            "stats": {
                "hunger": 80,
                "happiness": 80,
                "energy": 80,
                "cleanliness": 80,
            },
            "characterBible": {"lore": {"home": {"story": "каменная балка"}}},
        },
        "history": [],
    }
    data.update(overrides)
    return LocalChatRequest.model_validate(data)


def test_lite_mode_uses_minimal_prompt_and_raw_text(monkeypatch) -> None:
    client, completions = fake_lite_client(
        SimpleNamespace(content="Я стою и слушаю. Говори.", tool_calls=None)
    )
    monkeypatch.setattr(
        "app.services.pet_reply_engine.lite_generator.get_openai_client",
        lambda: client,
    )

    def fail_full_engine(_reply_input):
        raise AssertionError("full reply engine must not be called in Lite mode")

    monkeypatch.setattr("app.services.chat_service.generate_pet_reply", fail_full_engine)

    response = chat_with_local_pet(lite_payload())

    assert response.reply == "Я стою и слушаю. Говори."
    assert response.debug is not None
    assert response.debug.replyMode == "lite"
    assert response.debug.usedFallback is False
    assert len(completions.calls) == 1
    request = completions.calls[0]
    system_message = request["messages"][0]["content"]
    assert (
        system_message
        == "Отвечай мне как Громм, гигантский земляной великан. "
        "Сейчас ты взрослый, сформировавшийся представитель такого существа. "
        "Ответ максимум 300 символов; можно короче, даже одной фразой."
    )
    assert "Верни только JSON" not in system_message
    assert "Примеры фраз" not in system_message
    assert "response_format" not in request
    assert "tools" not in request


@pytest.mark.parametrize(
    ("stage", "hint"),
    (
        ("baby", "Сейчас ты малыш такого существа."),
        ("teen", "Сейчас ты подросток такого существа."),
        ("adult", "Сейчас ты взрослый, сформировавшийся представитель такого существа."),
    ),
)
def test_lite_mode_prompt_includes_age_role_hint(stage, hint) -> None:
    payload = lite_payload()
    payload = payload.model_copy(
        update={"pet": payload.pet.model_copy(update={"stage": stage})}
    )

    system_message = build_lite_chat_messages(payload)[0]["content"]

    assert system_message.startswith("Отвечай мне как Громм, гигантский земляной великан.")
    assert hint in system_message


def test_lite_mode_prompt_includes_state_modifier() -> None:
    payload = lite_payload()
    payload = payload.model_copy(
        update={"pet": payload.pet.model_copy(update={"mood": "happy"})}
    )

    system_message = build_lite_chat_messages(payload)[0]["content"]

    assert system_message.startswith(
        "Отвечай мне как Громм, гигантский земляной великан. "
        "Сейчас ты взрослый, сформировавшийся представитель такого существа. "
        "Ты сейчас радостный, энергичный, полный сил."
    )


def test_lite_mode_prompt_prioritizes_hungry_state_modifier() -> None:
    payload = lite_payload()
    payload = payload.model_copy(
        update={
            "pet": payload.pet.model_copy(
                update={
                    "mood": "happy",
                    "stats": payload.pet.stats.model_copy(update={"hunger": 12}),
                }
            )
        }
    )

    system_message = build_lite_chat_messages(payload)[0]["content"]

    assert system_message.startswith(
        "Отвечай мне как Громм, гигантский земляной великан. "
        "Сейчас ты взрослый, сформировавшийся представитель такого существа. "
        "Ты сейчас голодный."
    )


def test_lite_mode_prompt_includes_initial_character_seed() -> None:
    system_message = build_lite_chat_messages(
        lite_payload(
            pet={
                "name": "Громм",
                "description": "гигантский земляной великан",
                "stage": "adult",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                    "cleanliness": 80,
                },
                "characterBible": {
                    "extensions": {
                        "lite_overlay": {
                            "spheres": {
                                "character": {
                                    "facts": [
                                        {
                                            "sphere": "character",
                                            "kind": "character_fact",
                                            "text": "Я неторопливый и думаю, как гора.",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                },
            },
        )
    )[0]["content"]

    assert "Основа характера: Я неторопливый и думаю, как гора." in system_message
    assert "Основа мира" not in system_message


def test_lite_mode_clamps_reply_to_300_chars() -> None:
    long_reply = "а" * 420
    client, _completions = fake_lite_client(
        SimpleNamespace(content=long_reply, tool_calls=None)
    )

    response = generate_lite_pet_reply(lite_payload(), client=client, model="gpt-5.5", timeout=10)

    assert len(response.reply) <= 300
    assert response.reply.endswith("…")


def test_lite_mode_prompt_uses_baby_dataset_phrases_only_for_baby() -> None:
    baby_payload = lite_payload()
    baby_payload = baby_payload.model_copy(
        update={"pet": baby_payload.pet.model_copy(update={"stage": "baby"})}
    )
    teen_payload = lite_payload()
    teen_payload = teen_payload.model_copy(
        update={"pet": teen_payload.pet.model_copy(update={"stage": "teen"})}
    )

    baby_system_message = build_lite_chat_messages(baby_payload)[0]["content"]
    teen_system_message = build_lite_chat_messages(teen_payload)[0]["content"]

    assert "Примеры детской манеры из датасета" in baby_system_message
    assert "Приветик! Ты пришёл!" in baby_system_message
    assert "Примеры детской манеры из датасета" not in teen_system_message
    assert "Приветик! Ты пришёл!" not in teen_system_message


def test_lite_mode_tools_read_and_append_overlay_fact() -> None:
    read_call = SimpleNamespace(
        id="call_read",
        function=SimpleNamespace(
            name="read_character_json",
            arguments=json.dumps({"sections": ["characterBible", "liteOverlay"]}),
        ),
    )
    update_call = SimpleNamespace(
        id="call_update",
        function=SimpleNamespace(
            name="update_character_json",
            arguments=json.dumps(
                {
                    "kind": "preference",
                    "text": "Громм ест мокрую глину после дождя.",
                    "pathHint": "lore.inner_life.likes",
                    "source": "invented_in_lite_chat",
                }
            ),
        ),
    )
    client, completions = fake_lite_client(
        SimpleNamespace(content="", tool_calls=[read_call, update_call]),
        SimpleNamespace(content="Я ем мокрую глину после дождя.", tool_calls=None),
    )

    response = generate_lite_pet_reply(
        lite_payload(message="что ты ешь?"),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert response.reply == "Я ем мокрую глину после дождя."
    assert len(completions.calls) == 2
    assert "tools" in completions.calls[0]
    assert "reasoning_effort" not in completions.calls[0]
    assert "response_format" not in completions.calls[0]
    assert response.debug is not None
    assert [call["name"] for call in response.debug.liteToolCalls] == [
        "read_character_json",
        "update_character_json",
    ]
    read_result = response.debug.liteToolCalls[0]["result"]
    assert read_result["characterBible"]["lore"]["home"]["story"] == "каменная балка"
    assert response.debug.liteOverlayPatch is not None
    assert response.debug.liteOverlayPatch["facts"][0]["kind"] == "preference"
    assert (
        response.debug.liteOverlayPatch["facts"][0]["text"]
        == "Громм ест мокрую глину после дождя."
    )


def test_lite_mode_world_tool_bootstraps_missing_world_from_chatgpt() -> None:
    read_call = SimpleNamespace(
        id="call_read",
        function=SimpleNamespace(
            name="read_character_json",
            arguments=json.dumps({"sections": ["characterBible", "liteOverlay"]}),
        ),
    )
    client, completions = fake_lite_client(
        SimpleNamespace(content="", tool_calls=[read_call]),
        SimpleNamespace(
            content=json.dumps(
                {
                    "worldText": (
                        "Громм живет на старом горном уступе, где теплые камни "
                        "держат дневное солнце даже ночью."
                    )
                },
                ensure_ascii=False,
            ),
            tool_calls=None,
        ),
        SimpleNamespace(content="Я живу среди тёплых камней и горного ветра.", tool_calls=None),
    )

    payload = lite_payload(
        message="где твой дом?",
        pet={
            "name": "Громм",
            "description": "гигантский земляной великан",
            "stage": "adult",
            "mood": "idle",
            "stats": {
                "hunger": 80,
                "happiness": 80,
                "energy": 80,
                "cleanliness": 80,
            },
            "characterBible": {
                "lore": {
                    "world": {
                        "story": "World facts come from source_descriptions only:\n-",
                        "environment": "безопасная среда для формы «гигантский земляной великан»",
                    },
                    "home": {
                        "story": (
                            "Home/habitat details must be inferred only "
                            "from source_descriptions:\n-"
                        ),
                    },
                }
            },
        },
    )

    response = generate_lite_pet_reply(payload, client=client, model="gpt-5.5", timeout=10)

    assert response.reply == "Я живу среди тёплых камней и горного ветра."
    assert response.debug is not None
    read_result = response.debug.liteToolCalls[0]["result"]
    assert "Home/habitat details must be inferred" not in json.dumps(
        read_result,
        ensure_ascii=False,
    )
    assert read_result["worldInfo"]["createdByChatGPT"] is True
    assert response.debug.liteOverlayPatch is not None
    world_facts = response.debug.liteOverlayPatch["spheres"]["world"]["facts"]
    assert world_facts[0]["sphere"] == "world"
    assert world_facts[0]["kind"] == "world_fact"
    assert "Громм" in world_facts[0]["text"]
    assert world_facts[0]["source"] == "chatgpt_world_seed"
    assert response.debug.liteOverlayPatch["worldSeed"]["source"] == "chatgpt"
    assert [item["label"] for item in response.debug.promptDebug] == [
        "pet_reply/lite round 1",
        "pet_reply/lite_world_seed",
        "pet_reply/lite round 2",
    ]

    tool_response = completions.calls[2]["messages"][-1]["content"]
    assert "Home/habitat details must be inferred" not in tool_response
    assert "chatgpt_world_seed" in tool_response


def test_lite_mode_world_tool_does_not_reseed_existing_world_overlay() -> None:
    read_call = SimpleNamespace(
        id="call_read",
        function=SimpleNamespace(
            name="read_character_json",
            arguments=json.dumps({"sections": ["characterBible", "liteOverlay"]}),
        ),
    )
    client, _completions = fake_lite_client(
        SimpleNamespace(content="", tool_calls=[read_call]),
        SimpleNamespace(content="Я уже помню свой каменный двор.", tool_calls=None),
    )

    response = generate_lite_pet_reply(
        lite_payload(
            message="расскажи о своем мире",
            pet={
                "name": "Громм",
                "description": "гигантский земляной великан",
                "stage": "adult",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                    "cleanliness": 80,
                },
                "characterBible": {
                    "extensions": {
                        "lite_overlay": {
                            "spheres": {
                                "world": {
                                    "facts": [
                                        {
                                            "sphere": "world",
                                            "kind": "world_fact",
                                            "text": "Громм живет во дворе каменных плит.",
                                            "createdAt": "2026-07-05T00:00:00Z",
                                        }
                                    ]
                                }
                            }
                        }
                    }
                },
            },
        ),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert response.debug is not None
    assert response.debug.liteOverlayPatch is None
    assert "worldInfo" not in response.debug.liteToolCalls[0]["result"]


def test_home_fragment_ignores_technical_source_description_placeholders() -> None:
    assert (
        home_fragment(
            {
                "world": {
                    "story": "World facts come from source_descriptions only:\n-",
                    "environment": "безопасная среда для формы «камень»",
                },
                "home": {
                    "story": (
                        "Home/habitat details must be inferred only "
                        "from source_descriptions:\n-"
                    ),
                },
            }
        )
        is None
    )


def test_lite_fact_extraction_groups_facts_by_sphere() -> None:
    client, completions = fake_lite_client(
        SimpleNamespace(
            content=json.dumps(
                {
                    "facts": [
                        {
                            "sphere": "world",
                            "kind": "world_fact",
                            "text": "Мир Громма состоит из базальтовых гор и кристальных рощ.",
                            "pathHint": "lite_overlay.spheres.world",
                            "source": "lite_post_reply_extractor",
                        },
                        {
                            "sphere": "appearance",
                            "kind": "appearance_fact",
                            "text": "Громм слышит трещины в камне как голоса.",
                            "pathHint": "lite_overlay.spheres.appearance",
                            "source": "lite_post_reply_extractor",
                        },
                    ]
                }
            ),
            tool_calls=None,
        )
    )

    patch, debug = extract_lite_overlay_patch_from_reply(
        LiteFactExtractionRequest.model_validate(
            {
                "message": "расскажи о своем мире",
                "reply": "Мой мир состоит из базальтовых гор и кристальных рощ.",
                "pet": lite_payload().pet.model_dump(),
                "history": [],
                "includeDebug": True,
            }
        ),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert len(completions.calls) == 1
    request = completions.calls[0]
    assert request["response_format"]["json_schema"]["name"] == "lite_fact_extraction"
    assert patch is not None
    assert [fact["sphere"] for fact in patch["facts"]] == ["world", "appearance"]
    assert patch["spheres"]["world"]["facts"][0]["kind"] == "world_fact"
    assert patch["spheres"]["appearance"]["facts"][0]["kind"] == "appearance_fact"
    assert debug is not None
    assert debug.liteOverlayPatch == patch


def test_lite_fact_extraction_returns_none_without_new_facts() -> None:
    client, _completions = fake_lite_client(
        SimpleNamespace(content=json.dumps({"facts": []}), tool_calls=None)
    )

    patch, debug = extract_lite_overlay_patch_from_reply(
        LiteFactExtractionRequest.model_validate(
            {
                "message": "привет",
                "reply": "Привет.",
                "pet": lite_payload().pet.model_dump(),
                "history": [],
            }
        ),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert patch is None
    assert debug is not None
    assert [item["label"] for item in debug.promptDebug] == ["pet_reply/lite_fact_extraction"]


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

    assert fallback_birth_message(pet) == "ой... я проснулся! ты кто?"


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
    assert response.debug is None


def test_local_chat_builds_effective_bible_over_template_age() -> None:
    source_bible = {
        "identity": {
            "name": "Лука",
            "species": "дракон",
            "one_liner": "Luca is a 26-year-old guardian.",
        },
        "species": "дракон",
        "personality": "Age: 35. Appears mid-thirties, calm and tired.",
        "voice": {
            "voice_rules": ["sometimes says he is 35 years old"],
            "avoid_patterns": [],
        },
        "dialogue_style": {
            "voice_rules": ["speaks like a tired adult"],
            "avoid_patterns": [],
        },
        "lore": {"home": {"story": "живет в маленькой башне"}},
    }
    payload = LocalChatRequest.model_validate(
        {
            "message": "сколько тебе лет?",
            "pet": {
                "description": "маленький дракон",
                "stage": "baby",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                    "cleanliness": 80,
                },
                "characterBible": source_bible,
            },
            "history": [],
        }
    )

    reply_input = build_pet_reply_input(payload)
    prompt = build_pet_reply_messages(reply_input)[0]["content"]
    effective = reply_input.pet.effective_character_bible or {}

    assert source_bible["personality"].startswith("Age: 35")
    assert effective["extensions"]["runtime_bible"]["selected_age_stage"] == "baby"
    assert "Effective Character Bible runtime overrides" not in prompt
    assert "Примеры фраз по возрасту" in prompt
    assert "selected_stage: baby" in prompt
    assert "Age: 35" not in prompt
    assert "35 years old" not in prompt
    assert "26-year-old" not in prompt
    assert "Appears mid-thirties" not in prompt
    assert "текущая возрастная стадия задается приложением" not in prompt


def test_local_chat_debug_is_opt_in(monkeypatch) -> None:
    def fake_generate(_reply_input):
        return PetReplyResult(
            reply="я у нижней полки.",
            mood_hint="idle",
            detected_intent="answer_lore",
            reference_card_ids=("dialogue_answer_lore_ru_012",),
            speech_anchor_ids=("turn:answer_lore:teen:001",),
            speech_anchor_debug=(
                {
                    "id": "turn:answer_lore:teen:001",
                    "intent": "answer_lore",
                    "adaptationMode": "rhythm_only",
                },
            ),
            rejected_speech_anchor_debug=({"id": "turn:adult:001", "reason": "stage_mismatch"},),
            included_layers=("characterCore",),
            excluded_layers=("memory", "proactivity"),
        )

    monkeypatch.setattr("app.services.chat_service.generate_pet_reply", fake_generate)

    response = chat_with_local_pet(
        LocalChatRequest.model_validate(
            {
                "message": "где ты живешь?",
                "includeDebug": True,
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
                    "characterBible": {"lore": {"home": {"story": "нижняя полка"}}},
                },
                "history": [],
            }
        )
    )

    assert response.debug is not None
    assert response.debug.detectedIntent == "answer_lore"
    assert response.debug.selectedReferenceCardIds == ["dialogue_answer_lore_ru_012"]
    assert response.debug.selectedSpeechAnchorIds == ["turn:answer_lore:teen:001"]
    assert response.debug.speechAnchors[0]["adaptationMode"] == "rhythm_only"
    assert response.debug.rejectedSpeechAnchors[0]["reason"] == "stage_mismatch"
    assert response.debug.includedLayers == ["characterCore"]
    assert response.debug.excludedLayers == ["memory", "proactivity"]


def test_local_chat_debug_includes_generated_fact_decisions(monkeypatch) -> None:
    def fake_generate(_reply_input):
        return PetReplyResult(
            reply="я храню тихую пуговицу у чашки.",
            mood_hint="idle",
            memory_candidates=(
                MemoryCandidate(
                    type="pet_generated_fact",
                    text="Питомец хранит тихую пуговицу у чашки.",
                    importance=0.55,
                    confidence=0.6,
                    sourceSpan="я храню тихую пуговицу у чашки",
                ),
            ),
        )

    monkeypatch.setattr("app.services.chat_service.generate_pet_reply", fake_generate)

    response = chat_with_local_pet(
        LocalChatRequest.model_validate(
            {
                "message": "что нового?",
                "includeDebug": True,
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
                    "characterBible": {"lore": {"home": {"story": "нижняя полка"}}},
                },
                "history": [],
            }
        )
    )

    assert response.memoryPatch
    assert response.memoryPatch.generatedFactUpserts
    assert response.debug is not None
    assert response.debug.generatedFacts[0]["status"] == "draft"
    assert response.debug.generatedFacts[0]["text"] == "Питомец хранит тихую пуговицу у чашки."
