from __future__ import annotations

import json
from types import SimpleNamespace

from app.schemas import (
    LiteFactExtractionRequest,
    LocalAmbientRequest,
    LocalChatRequest,
    LocalProactiveRequest,
    MemoryConsolidationRequest,
    MemoryExtractionRequest,
)
from app.services.chat_service import chat_with_local_pet
from app.services.pet_reply_engine.lite_generator import (
    build_ambient_messages,
    build_lite_chat_messages,
    build_memory_extraction_messages,
    build_proactive_messages,
    consolidate_user_memory,
    extract_lite_overlay_patch_from_reply,
    extract_user_memory_operations,
    generate_ambient_pet_message,
    generate_lite_pet_reply,
    generate_proactive_pet_message,
)
from app.services.story_library import search_story_library


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


def test_chat_service_uses_lite_prompt_and_raw_text(monkeypatch) -> None:
    client, completions = fake_lite_client(
        SimpleNamespace(content="Я стою и слушаю. Говори.", tool_calls=None)
    )
    monkeypatch.setattr(
        "app.services.pet_reply_engine.lite_generator.get_openai_client",
        lambda: client,
    )

    response = chat_with_local_pet(lite_payload())

    assert response.reply == "Я стою и слушаю. Говори."
    assert response.debug is not None
    assert len(completions.calls) == 1
    request = completions.calls[0]
    system_message = request["messages"][0]["content"]
    assert system_message.startswith(
        "Отвечай мне как Громм, гигантский земляной великан. "
        "Сейчас ты взрослый, сформировавшийся представитель такого существа. "
        "Ответ максимум 300 символов; можно короче, даже одной фразой."
    )
    assert "вызови update_pet_name" in system_message
    assert "Отвечай владельцу естественно, кратко и своим голосом." in system_message
    assert "Верни только JSON" not in system_message
    assert "response_format" not in request
    assert [tool["function"]["name"] for tool in request["tools"]] == ["update_pet_name"]
    assert "STORY_LIBRARY" not in system_message


def test_lite_prompt_includes_age_role_hint() -> None:
    payload = lite_payload()
    baby = payload.model_copy(update={"pet": payload.pet.model_copy(update={"stage": "baby"})})
    teen = payload.model_copy(update={"pet": payload.pet.model_copy(update={"stage": "teen"})})
    adult = payload.model_copy(update={"pet": payload.pet.model_copy(update={"stage": "adult"})})

    assert "Сейчас ты малыш такого существа." in build_lite_chat_messages(baby)[0]["content"]
    assert "Сейчас ты подросток такого существа." in build_lite_chat_messages(teen)[0]["content"]
    assert (
        "Сейчас ты взрослый, сформировавшийся представитель такого существа."
        in build_lite_chat_messages(adult)[0]["content"]
    )


def test_lite_prompt_uses_request_reply_limit() -> None:
    system_message = build_lite_chat_messages(lite_payload(replyMaxChars=40))[0]["content"]

    assert "Ответ максимум 40 символов" in system_message
    assert "Сгенерируй законченную реплику сразу в этом лимите" in system_message
    assert "не сокращай ее многоточием" in system_message


def test_lite_prompt_includes_state_modifier() -> None:
    payload = lite_payload()
    happy = payload.model_copy(update={"pet": payload.pet.model_copy(update={"mood": "happy"})})
    hungry = payload.model_copy(
        update={
            "pet": payload.pet.model_copy(
                update={
                    "mood": "happy",
                    "stats": payload.pet.stats.model_copy(update={"hunger": 12}),
                }
            )
        }
    )

    assert (
        "Ты сейчас радостный, энергичный, полный сил."
        in build_lite_chat_messages(happy)[0]["content"]
    )
    assert "Ты сейчас голодный." in build_lite_chat_messages(hungry)[0]["content"]


def test_lite_prompt_includes_character_voice_control() -> None:
    payload = lite_payload(
        pet={
            "name": "Пончик",
            "description": "кремовый котенок-компаньон",
            "stage": "baby",
            "mood": "idle",
            "stats": {
                "hunger": 80,
                "happiness": 80,
                "energy": 80,
                "cleanliness": 80,
            },
            "characterBible": {
                "voice": {
                    "rules": ["говорит коротко и замечает запахи"],
                    "catchphrases": ["нюх-нюх"],
                    "sample_replies": ["Нюх-нюх... я проверю носом."],
                    "avoid": ["я ассистент"],
                },
                "dialogue_style": {
                    "voice_rules": ["не объясняет свои правила"],
                },
                "lore": {
                    "voice": {
                        "favorite_phrases": ["нос подсказывает"],
                    }
                },
            },
        }
    )

    system_message = build_lite_chat_messages(payload)[0]["content"]

    assert "VOICE_CONTROL" in system_message
    assert "нижний регулятор всех видимых реплик питомца" in system_message
    assert "говорит коротко и замечает запахи" in system_message
    assert "нюх-нюх" in system_message
    assert "Нюх-нюх... я проверю носом." in system_message
    assert "я ассистент" in system_message


def test_lite_prompt_does_not_include_character_seed() -> None:
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

    assert "Основа характера" not in system_message
    assert "Я неторопливый и думаю, как гора." not in system_message
    assert "Основа мира" not in system_message


def test_lite_prompt_includes_memory_context_only_when_present() -> None:
    empty_system_message = build_lite_chat_messages(lite_payload())[0]["content"]
    assert "Ты помнишь о пользователе" not in empty_system_message

    payload = lite_payload(
        memoryContext={
            "summary": "Пользователь любит короткие диалоги.",
            "relevantMemories": [
                {
                    "id": "m1",
                    "kind": "deadline",
                    "text": "У пользователя сегодня экзамен по математике.",
                    "dueAt": "2026-07-07T09:00:00+03:00",
                }
            ],
        }
    )
    system_message = build_lite_chat_messages(payload)[0]["content"]

    assert "Ты помнишь о пользователе" in system_message
    assert "У пользователя сегодня экзамен по математике." in system_message
    assert "Используй это только если уместно." in system_message


def test_lite_clamps_reply_to_300_chars() -> None:
    client, _completions = fake_lite_client(
        SimpleNamespace(content="а" * 420, tool_calls=None)
    )

    response = generate_lite_pet_reply(lite_payload(), client=client, model="gpt-5.5", timeout=10)

    assert len(response.reply) <= 300
    assert response.reply.endswith("…")


def test_lite_strips_hidden_thought_and_face_lines() -> None:
    client, _completions = fake_lite_client(
        SimpleNamespace(
            content="Я рядом, слышу тебя.\nTHOUGHT: тепло внутри\nFACE: content",
            tool_calls=None,
        )
    )

    response = generate_lite_pet_reply(lite_payload(), client=client, model="gpt-5.5", timeout=10)

    assert response.reply == "Я рядом, слышу тебя."
    assert response.innerThought == "тепло внутри"
    assert response.faceHint == "content"


def test_lite_prompt_uses_baby_dataset_phrases_only_for_baby() -> None:
    payload = lite_payload()
    baby = payload.model_copy(update={"pet": payload.pet.model_copy(update={"stage": "baby"})})
    teen = payload.model_copy(update={"pet": payload.pet.model_copy(update={"stage": "teen"})})

    baby_system_message = build_lite_chat_messages(baby)[0]["content"]
    teen_system_message = build_lite_chat_messages(teen)[0]["content"]

    assert "Примеры детской манеры из датасета" in baby_system_message
    assert "Приветик! Ты пришёл!" in baby_system_message
    assert "Примеры детской манеры из датасета" not in teen_system_message
    assert "Приветик! Ты пришёл!" not in teen_system_message


def test_lite_prompt_includes_preselected_world_context_for_story_query() -> None:
    system_message = build_lite_chat_messages(
        lite_payload(message="есть ли в твоем мире монстры?")
    )[0]["content"]

    assert "WORLD_CONTEXT" in system_message
    assert "Опасности и монстры" in system_message
    assert "STORY_LIBRARY" not in system_message
    assert "search_story_library" not in system_message


def test_proactive_prompt_includes_character_voice_control() -> None:
    payload = LocalProactiveRequest.model_validate(
        {
            "pet": {
                "name": "Пончик",
                "description": "кремовый котенок-компаньон",
                "stage": "baby",
                "mood": "happy",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                    "cleanliness": 80,
                },
                "characterBible": {
                    "voice": {
                        "rules": ["говорит через маленькие бытовые детали"],
                        "catchphrases": ["нос подсказывает"],
                    }
                },
            },
            "memoryContext": {
                "summary": "Пользователь любит короткие диалоги.",
                "proactiveCandidate": {
                    "memoryIds": ["m1"],
                    "reason": "пользователь обещал вернуться вечером",
                },
            },
        }
    )

    system_message = build_proactive_messages(payload)[0]["content"]

    assert "VOICE_CONTROL" in system_message
    assert "говорит через маленькие бытовые детали" in system_message
    assert "нос подсказывает" in system_message


def test_proactive_prompt_skips_world_context_without_story_signal() -> None:
    payload = LocalProactiveRequest.model_validate(
        {
            "pet": {
                "name": "Пончик",
                "description": "кремовый котенок-компаньон",
                "stage": "baby",
                "mood": "happy",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                    "cleanliness": 80,
                },
                "characterBible": {},
            },
            "memoryContext": {
                "summary": "Пользователь любит короткие диалоги.",
                "proactiveCandidate": {
                    "memoryIds": ["m1"],
                    "reason": "пользователь обещал вернуться вечером",
                },
            },
        }
    )

    system_message = build_proactive_messages(payload)[0]["content"]

    assert "WORLD_CONTEXT" not in system_message
    assert "STORY_LIBRARY" not in system_message


def test_proactive_prompt_uses_preselected_world_context_when_needed() -> None:
    payload = LocalProactiveRequest.model_validate(
        {
            "pet": {
                "name": "Пончик",
                "description": "кремовый котенок-компаньон",
                "stage": "baby",
                "mood": "happy",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                    "cleanliness": 80,
                },
                "characterBible": {},
            },
            "memoryContext": {
                "summary": "Пользователь спрашивал, есть ли в мире монстры.",
                "proactiveCandidate": {
                    "memoryIds": ["m1"],
                    "reason": "пользователь интересовался монстрами в мире питомца",
                },
            },
        }
    )

    system_message = build_proactive_messages(payload)[0]["content"]

    assert "WORLD_CONTEXT" in system_message
    assert "STORY_LIBRARY" not in system_message


def test_ambient_prompt_uses_same_phrase_engine_without_forced_world_context() -> None:
    payload = LocalAmbientRequest.model_validate(
        {
            "pet": {
                "name": "Листик",
                "description": "серый челик с листом вместо лица",
                "stage": "baby",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                    "cleanliness": 80,
                },
                "characterBible": {
                    "voice": {
                        "rules": ["говорит коротко и тихо"],
                        "catchphrases": ["лист шепчет"],
                    }
                },
            },
            "history": [],
            "recentAmbientReplies": [
                "Привет, я Листик. Я просто рядом.",
                "В школе ты был бы отличником или тем, кто рисует на полях?",
            ],
            "replyMaxChars": 120,
        }
    )

    messages = build_ambient_messages(payload)
    system_message = messages[0]["content"]

    assert "idle-фразу на главном экране" in system_message
    assert "IDLE_DIALOGUE_ENGINE" in system_message
    assert "Расскажи про свой мир так" in system_message
    assert "Привет, я Листик. Я просто рядом." in system_message
    assert "я просто рядом" in system_message
    assert "ask_school_or_work_role" in system_message
    assert "VOICE_CONTROL" in system_message
    assert "WORLD_CONTEXT" not in system_message
    assert "лист шепчет" not in system_message
    assert "заинтересоваться его миром" in system_message
    assert "STORY_LIBRARY" not in system_message
    assert messages[-1]["content"] != "Скажи одну короткую idle-фразу сейчас."
    assert "выбранному диалоговому ходу" in messages[-1]["content"]


def test_ambient_prompt_uses_world_context_when_history_needs_it() -> None:
    payload = LocalAmbientRequest.model_validate(
        {
            "pet": {
                "name": "Листик",
                "description": "серый челик с листом вместо лица",
                "stage": "baby",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                    "cleanliness": 80,
                },
                "characterBible": {},
            },
            "history": [
                {
                    "role": "user",
                    "text": "Есть ли в твоем мире монстры?",
                }
            ],
            "replyMaxChars": 120,
        }
    )

    system_message = build_ambient_messages(payload)[0]["content"]

    assert "WORLD_CONTEXT" in system_message
    assert "STORY_LIBRARY" not in system_message


def test_ambient_generation_returns_story_context_debug() -> None:
    client, completions = fake_lite_client(
        SimpleNamespace(content="Лист шепчет: крошка сегодня светится.", tool_calls=None),
    )
    payload = LocalAmbientRequest.model_validate(
        {
            "pet": {
                "name": "Листик",
                "description": "серый челик с листом вместо лица",
                "stage": "baby",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                    "cleanliness": 80,
                },
                "characterBible": {},
            },
            "includeDebug": True,
            "replyMaxChars": 120,
        }
    )

    response = generate_ambient_pet_message(
        payload,
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert len(completions.calls) == 1
    assert response.reply == "Лист шепчет: крошка сегодня светится."
    assert response.debug is not None
    assert response.debug.storyLibraryDebug is not None
    assert response.debug.storyLibraryDebug["mode"] == "ambient"
    assert response.debug.storyLibraryDebug["injectedSpheres"] == []


def test_lite_tools_read_character_json_without_direct_mutation() -> None:
    read_call = SimpleNamespace(
        id="call_read",
        function=SimpleNamespace(
            name="read_character_json",
            arguments=json.dumps({"sections": ["characterBible", "liteOverlay"]}),
        ),
    )
    client, completions = fake_lite_client(
        SimpleNamespace(content="", tool_calls=[read_call]),
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
    assert response.debug is not None
    assert [tool["function"]["name"] for tool in completions.calls[0]["tools"]] == [
        "update_pet_name",
        "read_character_json",
    ]
    assert [call["name"] for call in response.debug.liteToolCalls] == ["read_character_json"]
    read_result = response.debug.liteToolCalls[0]["result"]
    assert read_result["characterBible"]["lore"]["home"]["story"] == "каменная балка"
    assert response.debug.liteOverlayPatch is None


def test_lite_story_library_context_is_preselected_without_story_tools() -> None:
    client, completions = fake_lite_client(
        SimpleNamespace(content="Да, но они чаще странные, чем злые.", tool_calls=None),
    )

    response = generate_lite_pet_reply(
        lite_payload(message="есть ли в твоем мире монстры?"),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert len(completions.calls) == 1
    assert response.reply == "Да, но они чаще странные, чем злые."
    request = completions.calls[0]
    system_message = request["messages"][0]["content"]
    assert "WORLD_CONTEXT" in system_message
    assert "search_story_library" not in [
        tool["function"]["name"] for tool in request["tools"]
    ]
    assert response.debug is not None
    assert response.debug.storyLibraryPatch is None
    assert response.debug.storyLibraryDebug is not None
    assert response.debug.storyLibraryDebug["mode"] == "chat"
    assert response.debug.storyLibraryDebug["injectedSpheres"]


def test_lite_story_library_extraction_returns_personal_patch() -> None:
    client, completions = fake_lite_client(
        SimpleNamespace(
            content="Я встретил стеклянного шуршуна у корня. Он тихо звенит.",
            tool_calls=None,
        ),
        SimpleNamespace(
            content=json.dumps(
                {
                    "bricks": [
                        {
                            "pool": "creatures",
                            "name": "стеклянный шуршун",
                            "description": (
                                "Личное существо питомца: маленький стеклянный "
                                "шуршун, который тихо звенит у корней."
                            ),
                            "basedOnBrickIds": ["global:creatures:000"],
                            "reason": "питомец ввел новое устойчивое существо",
                            "confidence": 0.92,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            tool_calls=None,
        ),
    )

    response = generate_lite_pet_reply(
        lite_payload(message="есть ли в твоем мире существа?"),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert len(completions.calls) == 2
    assert completions.calls[1]["response_format"]["json_schema"]["name"] == (
        "story_library_extraction"
    )
    assert response.debug is not None
    assert response.debug.storyLibraryPatch is not None
    brick = response.debug.storyLibraryPatch["bricks"][0]
    assert brick["source"] == "pet_overlay"
    assert brick["pool"] == "creatures"
    assert brick["name"] == "стеклянный шуршун"


def test_story_library_search_uses_personal_overlay_first() -> None:
    character_bible = {
        "extensions": {
            "story_library_overlay": {
                "version": 1,
                "bricks": [
                    {
                        "id": "pet:threats:quiet-bell",
                        "source": "pet_overlay",
                        "pool": "threats",
                        "name": "Тихий колокольный страж",
                        "text": (
                            "Личная опасность Пончика: звонит только когда "
                            "кто-то прячет находку."
                        ),
                    }
                ],
            }
        }
    }

    result = search_story_library(
        query="кто такой тихий колокольный страж",
        pool_hints=["threats"],
        character_bible=character_bible,
        limit=3,
    )

    assert result["bricks"][0]["id"] == "pet:threats:quiet-bell"
    assert result["bricks"][0]["source"] == "pet_overlay"


def test_lite_tool_updates_pet_name() -> None:
    rename_call = SimpleNamespace(
        id="call_rename",
        function=SimpleNamespace(
            name="update_pet_name",
            arguments=json.dumps({"name": "Дружок"}),
        ),
    )
    client, completions = fake_lite_client(
        SimpleNamespace(content="", tool_calls=[rename_call]),
        SimpleNamespace(content="Дружок звучит тепло.", tool_calls=None),
    )

    response = generate_lite_pet_reply(
        lite_payload(message="буду звать тебя как-нибудь по-домашнему: Дружок"),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert len(completions.calls) == 2
    assert response.reply == "Дружок звучит тепло."
    assert response.petPatch is not None
    assert response.petPatch.name == "Дружок"
    assert response.debug is not None
    assert response.debug.liteToolCalls[0]["name"] == "update_pet_name"
    assert response.debug.liteToolCalls[0]["result"] == {
        "saved": True,
        "petPatch": {"name": "Дружок"},
    }


def test_lite_world_tool_bootstraps_missing_world_from_chatgpt() -> None:
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

    response = generate_lite_pet_reply(
        lite_payload(
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
                            "environment": (
                                "безопасная среда для формы "
                                "«гигантский земляной великан»"
                            ),
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
        ),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

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
    assert world_facts[0]["source"] == "chatgpt_world_seed"
    assert [item["label"] for item in response.debug.promptDebug] == [
        "pet_reply/lite round 1",
        "pet_reply/lite_world_seed",
        "pet_reply/lite round 2",
    ]
    tool_response = completions.calls[2]["messages"][-1]["content"]
    assert "Home/habitat details must be inferred" not in tool_response
    assert "chatgpt_world_seed" in tool_response


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
    assert debug is not None
    assert debug.liteOverlayPatch == patch


def test_memory_extraction_prompt_uses_user_message_as_source() -> None:
    payload = MemoryExtractionRequest.model_validate(
        {
            "message": "У меня завтра экзамен",
            "reply": "Я буду рядом камнем удачи.",
            "pet": lite_payload().pet.model_dump(),
            "history": [],
            "nowIso": "2026-07-06T12:00:00+03:00",
            "timezone": "Europe/Moscow",
            "existingMemoryBrief": "",
        }
    )

    messages = build_memory_extraction_messages(payload)

    assert "Извлекай только факты, которые сказал" in messages[0]["content"]
    assert "У меня завтра экзамен" in messages[1]["content"]
    assert "2026-07-06T12:00:00+03:00" in messages[1]["content"]


def test_memory_extraction_returns_structured_operations() -> None:
    client, completions = fake_lite_client(
        SimpleNamespace(
            content=json.dumps(
                {
                    "operations": [
                        {
                            "type": "remember_user_fact",
                            "observation": None,
                            "patternKey": None,
                            "kind": "deadline",
                            "text": "У пользователя завтра экзамен.",
                            "normalizedKey": "exam-2026-07-07",
                            "confidence": 0.9,
                            "importance": 0.9,
                            "dueAt": "2026-07-07T09:00:00+03:00",
                            "expiresAt": "2026-07-08T00:00:00+03:00",
                            "tags": ["exam"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            tool_calls=None,
        )
    )

    response = extract_user_memory_operations(
        MemoryExtractionRequest.model_validate(
            {
                "message": "У меня завтра экзамен",
                "reply": "Я буду рядом камнем удачи.",
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
    assert completions.calls[0]["response_format"]["json_schema"]["name"] == (
        "user_memory_extraction"
    )
    assert response.operations[0]["type"] == "remember_user_fact"
    assert response.operations[0]["kind"] == "deadline"
    assert response.debug is not None
    assert response.debug.memoryDebug["extractionOperations"] == response.operations


def test_memory_consolidation_promotes_learning() -> None:
    client, _completions = fake_lite_client(
        SimpleNamespace(
            content=json.dumps(
                {
                    "operations": [
                        {
                            "type": "promote_learning",
                            "learningId": "l1",
                            "reason": None,
                            "content": None,
                            "memory": {
                                "kind": "preference",
                                "text": "Пользователь любит короткие ответы.",
                                "normalizedKey": "pref-short-replies",
                                "confidence": 0.8,
                                "importance": 0.7,
                                "dueAt": None,
                                "expiresAt": None,
                                "tags": ["style"],
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            tool_calls=None,
        )
    )

    response = consolidate_user_memory(
        MemoryConsolidationRequest.model_validate(
            {
                "pendingLearnings": [
                    {
                        "id": "l1",
                        "observation": "Пользователь любит короткие ответы.",
                    }
                ],
                "existingMemories": [],
                "includeDebug": True,
            }
        ),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert response.operations[0]["type"] == "promote_learning"
    assert response.operations[0]["memory"]["kind"] == "preference"
    assert response.debug is not None
    assert response.debug.memoryDebug["consolidationOperations"] == response.operations


def test_proactive_reply_is_clamped() -> None:
    client, _completions = fake_lite_client(
        SimpleNamespace(content="б" * 420, tool_calls=None)
    )

    response = generate_proactive_pet_message(
        LocalProactiveRequest.model_validate(
            {
                "pet": lite_payload().pet.model_dump(),
                "memoryContext": {
                    "relevantMemories": [
                        {
                            "id": "m1",
                            "kind": "deadline",
                            "text": "У пользователя сегодня экзамен.",
                        }
                    ],
                    "proactiveCandidate": {
                        "memoryIds": ["m1"],
                        "reason": "у пользователя сегодня экзамен",
                    },
                },
                "includeDebug": True,
            }
        ),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert len(response.reply) <= 300
    assert response.reply.endswith("…")
    assert response.debug is not None
    assert response.debug.memoryDebug["selectedMemoryIds"] == ["m1"]
