from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.schemas import (
    LiteFactExtractionRequest,
    LocalAmbientRequest,
    LocalChatRequest,
    LocalProactiveRequest,
    MemoryConsolidationRequest,
    MemoryExtractionRequest,
)
from app.services.chat_service import chat_with_local_pet
from app.services.pet_reply_engine import speech_runtime
from app.services.pet_reply_engine.lite_generator import (
    ContextRoutingDecision,
    build_ambient_messages,
    build_lite_chat_messages,
    build_lite_fact_extraction_messages,
    build_proactive_messages,
    extract_lite_overlay_patch_from_reply,
    generate_ambient_pet_message,
    generate_lite_pet_reply,
    generate_proactive_pet_message,
)
from app.services.pet_reply_engine.memory_operations import (
    build_memory_extraction_messages,
    consolidate_user_memory,
    extract_user_memory_operations,
)
from app.services.story_library import search_story_library


class FakeLiteCompletions:
    def __init__(self, messages):
        self._messages = list(messages)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if _is_context_routing_call(kwargs):
            message = SimpleNamespace(
                content=_fake_context_routing_response(kwargs),
                tool_calls=None,
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])
        message = _visible_reply_message_for_call(self._messages.pop(0), kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def fake_lite_client(*messages):
    completions = FakeLiteCompletions(messages)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def _is_context_routing_call(kwargs: dict) -> bool:
    messages = kwargs.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    content = messages[0].get("content") if isinstance(messages[0], dict) else ""
    return isinstance(content, str) and content.startswith("CONTEXT_ROUTING:")


def _is_visible_reply_call(kwargs: dict) -> bool:
    return (
        kwargs.get("response_format", {}).get("json_schema", {}).get("name") == "visible_pet_reply"
    )


def _visible_reply_message_for_call(message: SimpleNamespace, kwargs: dict) -> SimpleNamespace:
    if not _is_visible_reply_call(kwargs) or getattr(message, "tool_calls", None):
        return message
    content = getattr(message, "content", None)
    if not isinstance(content, str) or content.lstrip().startswith("{"):
        return message
    return SimpleNamespace(
        **{
            **vars(message),
            "content": json.dumps(
                {
                    "reply": content,
                    "faceHint": None,
                    "moodHint": None,
                },
                ensure_ascii=False,
            ),
        }
    )


def _fake_context_routing_response(kwargs: dict) -> str:
    messages = kwargs.get("messages") if isinstance(kwargs.get("messages"), list) else []
    try:
        payload = json.loads(messages[1]["content"])
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        payload = {}

    surface = payload.get("surface") if isinstance(payload, dict) else ""
    surface_prompt = str(payload.get("surfacePrompt") or "") if isinstance(payload, dict) else ""
    user_message = str(payload.get("userMessage") or "") if isinstance(payload, dict) else ""
    proactive_reason = (
        str(payload.get("proactiveReason") or "") if isinstance(payload, dict) else ""
    )
    recent_replies = payload.get("recentReplies") if isinstance(payload, dict) else []
    memory_brief = payload.get("memoryBrief") if isinstance(payload, dict) else {}
    decision_text = f"{user_message} {proactive_reason}".casefold()
    surface_text = surface_prompt.casefold()

    world_context = any(
        marker in decision_text
        for marker in (
            "мир",
            "монстр",
            "существ",
            "предмет",
            "сокров",
            "локац",
            "лес",
            "дом",
            "где твой",
        )
    ) or any(
        marker in surface_text for marker in ("опереться на мир", "основываться на world context")
    )
    if "фан-факт" in surface_text and not any(
        marker in decision_text for marker in ("мир", "монстр", "существ", "предмет", "дом")
    ):
        world_context = False
    character_profile = any(
        marker in decision_text
        for marker in (
            "что ты ешь",
            "чем пита",
            "кто ты",
            "ты кто",
            "где твой дом",
            "любим",
            "характер",
            "о себе",
        )
    )
    has_memory = False
    if isinstance(memory_brief, dict):
        episodes = memory_brief.get("episodes")
        has_memory = isinstance(episodes, list) and bool(episodes)
    recent_replies_enabled = (
        surface == "ambient" and isinstance(recent_replies, list) and bool(recent_replies)
    )

    result = {
        "sources": {
            "worldContext": {
                "enabled": world_context,
                "query": decision_text if world_context else "",
            },
            "characterProfile": {
                "enabled": character_profile,
                "query": decision_text if character_profile else "",
            },
            "userMemory": {
                "enabled": has_memory,
                "query": decision_text if has_memory else "",
            },
            "chatHistory": {
                "enabled": False,
                "query": "",
            },
            "recentReplies": {
                "enabled": recent_replies_enabled,
                "query": "anti-repeat" if recent_replies_enabled else "",
            },
        },
        "reason": "fake routing for tests",
    }
    return json.dumps(result, ensure_ascii=False)


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
            },
            "characterBible": {"lore": {"home": {"story": "каменная балка"}}},
        },
        "history": [],
        "includeDebug": True,
    }
    data.update(overrides)
    return LocalChatRequest.model_validate(data)


def pet_with_recent_story_event() -> dict:
    pet = lite_payload().pet.model_dump()
    pet["characterBible"] = {
        "extensions": {
            "recent_story_events": [
                {
                    "id": "evt_bell_theft",
                    "title": "Украденный звон",
                    "summary": "Хорек украл колокольчик, и Громм не смог его вернуть.",
                    "compactText": (
                        "Хорек украл колокольчик. Громм устал и не смог вернуть предмет."
                    ),
                    "eventType": "theft",
                    "valence": "negative",
                    "participants": ["Громм", "сумрачный хорек"],
                    "objects": ["колокольчик"],
                    "actions": [
                        "хорек украл колокольчик",
                        "Громм не смог вернуть колокольчик",
                    ],
                    "outcome": "Громм потерял колокольчик.",
                    "canonicalFacts": [
                        "хорек украл колокольчик",
                        "Громм не смог вернуть колокольчик",
                        "Громм не защитил колокольчик",
                    ],
                    "statusChanges": [
                        {
                            "entity": "колокольчик",
                            "state": "lost",
                            "owner": "Громм",
                        }
                    ],
                    "createdAt": "2026-07-08T07:40:00Z",
                    "source": "background_story",
                }
            ]
        }
    }
    return pet


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
    assert request["model"] == "gpt-5.4-mini"
    assert request["reasoning_effort"] == "high"
    assert system_message.startswith(
        "Ты Громм. Сейчас ты взрослый, сформировавшийся представитель такого существа. "
        "Говори от первого лица связно, просто и конкретно. Если мысль требует, "
        "используй несколько коротких предложений."
    )
    assert "гигантский земляной великан" in system_message
    assert "КАНОН ПЕРСОНАЖА:" in system_message
    assert "ОБЩАЯ БИБЛИЯ МИРА:" not in system_message
    assert "Слова мира, если подходят по смыслу:" in system_message
    assert "руины" in system_message
    assert "гоблин" in system_message
    assert "только слова, которые персонаж произносит вслух" in system_message
    assert "Не пиши авторскую ремарку" in system_message
    assert "используй update_pet_name" in system_message
    assert "Ответь на последнее сообщение как этот персонаж." in system_message
    assert "Верни только JSON" not in system_message
    assert request["response_format"]["json_schema"]["name"] == "visible_pet_reply"
    assert request["response_format"]["json_schema"]["schema"]["properties"]["reply"][
        "maxLength"
    ] == 300
    assert "tools" not in request
    assert "STORY_LIBRARY" not in system_message


def test_chat_prompt_includes_matching_recent_events_before_world_context() -> None:
    messages = build_lite_chat_messages(
        lite_payload(
            message="ты защитил колокольчик?",
            pet=pet_with_recent_story_event(),
        ),
        context_routing=ContextRoutingDecision(
            surface="chat",
            enabled_sources=frozenset({"worldContext"}),
            queries={"worldContext": "хорьки и колокольчики"},
        ),
    )

    system_message = messages[0]["content"]
    assert "Недавние события персонажа" in system_message
    assert "Громм не смог вернуть колокольчик" in system_message
    assert "колокольчик: lost" in system_message


def test_chat_does_not_invent_recent_event_when_event_memory_is_empty() -> None:
    system_message = build_lite_chat_messages(
        lite_payload(message="Как дела, что интересного было за последнее время?")
    )[0]["content"]

    assert "нет подтверждённого недавнего события" in system_message
    assert "Не выдумывай находку, встречу или приключение" in system_message
    if "Детали мира для этой реплики" in system_message:
        assert system_message.index("Недавние события персонажа") < system_message.index(
            "Детали мира для этой реплики"
        )


def test_chat_recent_events_keeps_newer_matching_event_first() -> None:
    pet = pet_with_recent_story_event()
    events = pet["characterBible"]["extensions"]["recent_story_events"]
    events.append(
        {
            "id": "evt_bell_found",
            "title": "Колокольчик найден",
            "summary": "Громм нашел колокольчик под корнем.",
            "compactText": "Громм нашел колокольчик под корнем и забрал его.",
            "eventType": "recovery",
            "valence": "positive",
            "participants": ["Громм"],
            "objects": ["колокольчик"],
            "actions": ["Громм нашел колокольчик"],
            "outcome": "Колокольчик снова у Громма.",
            "canonicalFacts": ["Громм нашел колокольчик"],
            "statusChanges": [
                {
                    "entity": "колокольчик",
                    "state": "found",
                    "owner": "Громм",
                }
            ],
            "createdAt": "2026-07-08T08:10:00Z",
            "source": "background_story",
        }
    )

    system_message = build_lite_chat_messages(
        lite_payload(message="ты защитил колокольчик?", pet=pet),
        context_routing=ContextRoutingDecision(surface="chat"),
    )[0]["content"]

    assert system_message.index("Колокольчик найден") < system_message.index("Украденный звон")


def test_chat_recent_events_matches_russian_word_forms() -> None:
    system_message = build_lite_chat_messages(
        lite_payload(
            message="Что стало с колокольчиком?",
            pet=pet_with_recent_story_event(),
        ),
        context_routing=ContextRoutingDecision(surface="chat"),
    )[0]["content"]

    assert "Недавние события персонажа" in system_message
    assert "Громм не смог вернуть колокольчик" in system_message


def test_lite_prompt_includes_age_role_hint() -> None:
    payload = lite_payload()
    baby = payload.model_copy(update={"pet": payload.pet.model_copy(update={"stage": "baby"})})
    teen = payload.model_copy(update={"pet": payload.pet.model_copy(update={"stage": "teen"})})
    adult = payload.model_copy(update={"pet": payload.pet.model_copy(update={"stage": "adult"})})
    baby_system_message = build_lite_chat_messages(baby)[0]["content"]

    assert baby_system_message.startswith("Ты маленький Громм.")
    assert "Сейчас ты недавно родившийся субъект такого существа." not in baby_system_message
    assert "Сейчас ты подросток такого существа." in build_lite_chat_messages(teen)[0]["content"]
    assert (
        "Сейчас ты взрослый, сформировавшийся представитель такого существа."
        in build_lite_chat_messages(adult)[0]["content"]
    )


def test_lite_prompt_uses_request_reply_limit() -> None:
    system_message = build_lite_chat_messages(lite_payload(replyMaxChars=40))[0]["content"]

    assert "До 40 символов" not in system_message
    assert "не сокращай ее многоточием" not in system_message


def test_chat_prompt_keeps_recent_replies_as_dialogue_not_anti_repeat() -> None:
    messages = build_lite_chat_messages(
        lite_payload(
            message="Я просто устал после работы.",
            history=[
                {"role": "pet", "text": "Ты сегодня тихий. Всё хорошо?"},
            ],
        )
    )

    assert messages[-2:] == [
        {"role": "assistant", "content": "Ты сегодня тихий. Всё хорошо?"},
        {"role": "user", "content": "Я просто устал после работы."},
    ]
    assert "Недавние реплики персонажа" not in messages[0]["content"]


def test_chat_small_talk_does_not_copy_old_reply_style() -> None:
    messages = build_lite_chat_messages(
        lite_payload(
            message="как дела?",
            history=[
                {"role": "user", "text": "давай"},
                {
                    "role": "pet",
                    "text": "Я слушаю, как стеклянное семечко шепчет корнями в лунной тени.",
                },
            ],
        )
    )

    assert [message["role"] for message in messages] == ["system", "user"]
    assert "стеклянное семечко" not in messages[0]["content"]


def test_chat_prompt_retrieves_only_matching_lite_overlay_facts() -> None:
    system_message = build_lite_chat_messages(
        lite_payload(
            message="Что за базальтовая каша?",
            pet={
                "name": "Громм",
                "description": "каменный великан",
                "stage": "adult",
                "mood": "idle",
                "stats": {"hunger": 80, "happiness": 80, "energy": 80},
                "characterBible": {
                    "extensions": {
                        "lite_overlay": {
                            "facts": [
                                {"text": "Громм любит базальтовую кашу."},
                                {"text": "Громм боится глубоких озёр."},
                            ]
                        }
                    }
                },
            },
        )
    )[0]["content"]

    assert "Релевантные устойчивые факты персонажа" in system_message
    assert "Громм любит базальтовую кашу." in system_message
    assert "Громм боится глубоких озёр." not in system_message


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
        "Ты сейчас радостный, здоровый, полный сил."
        not in build_lite_chat_messages(happy)[0]["content"]
    )
    assert "У тебя радостное настроение" in build_lite_chat_messages(happy)[0]["content"]
    assert "Ты очень хочешь есть" in build_lite_chat_messages(hungry)[0]["content"]


def test_context_sources_policy_disables_state_params(monkeypatch, tmp_path) -> None:
    runtime_path = tmp_path / "speech_runtime.json"
    runtime = json.loads(speech_runtime.DATA_PATH.read_text(encoding="utf-8"))
    runtime["contextSources"]["surfaces"]["chat"]["stateParams"] = "disabled"
    runtime_path.write_text(json.dumps(runtime, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(speech_runtime, "DATA_PATH", runtime_path)
    speech_runtime.speech_runtime_config.cache_clear()

    try:
        payload = lite_payload()
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
        system_message = build_lite_chat_messages(hungry)[0]["content"]
    finally:
        speech_runtime.speech_runtime_config.cache_clear()

    assert "Ты сейчас голодный." not in system_message
    assert "Ты сейчас радостный" not in system_message


def test_speech_runtime_rejects_state_params_auto() -> None:
    runtime = json.loads(speech_runtime.DATA_PATH.read_text(encoding="utf-8"))
    runtime["contextSources"]["surfaces"]["chat"]["stateParams"] = "auto"

    try:
        speech_runtime.validate_speech_runtime_config(runtime)
    except ValueError as exc:
        assert "contextSources.surfaces.chat.stateParams" in str(exc)
        assert "disabled, always" in str(exc)
    else:
        raise AssertionError("stateParams=auto must be rejected")


def test_visible_reply_limit_uses_runtime_cap_and_honors_lower_request() -> None:
    assert speech_runtime.visible_reply_model() == "gpt-5.4-mini"
    assert speech_runtime.visible_reply_reasoning_effort() == "high"
    assert speech_runtime.visible_reply_limit() == 300
    assert speech_runtime.visible_reply_limit(220) == 220
    assert speech_runtime.visible_reply_limit(40) == 40


def test_speech_runtime_rejects_invalid_visible_reply_limit() -> None:
    runtime = json.loads(speech_runtime.DATA_PATH.read_text(encoding="utf-8"))
    runtime["visibleReply"]["maxChars"] = 301

    try:
        speech_runtime.validate_speech_runtime_config(runtime)
    except ValueError as exc:
        assert "visibleReply.maxChars" in str(exc)
    else:
        raise AssertionError("visibleReply.maxChars above schema limit must be rejected")


def test_speech_runtime_rejects_invalid_visible_reply_reasoning() -> None:
    runtime = json.loads(speech_runtime.DATA_PATH.read_text(encoding="utf-8"))
    runtime["visibleReply"]["reasoningEffort"] = "max"

    with pytest.raises(ValueError, match="visibleReply.reasoningEffort"):
        speech_runtime.validate_speech_runtime_config(runtime)


def test_lite_prompt_includes_compact_character_voice_without_raw_controls() -> None:
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

    assert (
        "Ты маленький Пончик. Говори от первого лица связно, просто и конкретно. "
        "Если мысль требует, используй несколько коротких предложений."
        in system_message
    )
    assert "кремовый котенок-компаньон" in system_message
    assert "VOICE_CONTROL" not in system_message
    assert "нижний регулятор всех видимых реплик питомца" not in system_message
    assert "говорит коротко и замечает запахи" in system_message
    assert "нюх-нюх" not in system_message
    assert "Нюх-нюх... я проверю носом." not in system_message
    assert "я ассистент" not in system_message


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


def test_lite_prompt_ignores_character_profile_even_if_router_enables_it() -> None:
    system_message = build_lite_chat_messages(
        lite_payload(
            message="ты кто?",
            pet={
                "name": "Грум",
                "description": "орк-людоед",
                "stage": "adult",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                },
                "characterBible": {
                    "identity": {
                        "species": "маленький болотный орк с темной репутацией",
                        "role": "бродячий болотный задира",
                        "one_liner": "Боится сорваться в старый голод, но учится есть похлебку.",
                    },
                    "genesis": {
                        "description": "не зверь, а разборчивый голодный коллекционер силы",
                        "character_trait": "гурман-коллекционер",
                        "likes": ["густая похлебка", "грибы", "старые кости"],
                        "does": ["ворчит", "ходит по болоту", "ищет еду", "пугает прохожих"],
                        "appetite": "любит густую похлебку, грибы и то, что не пищит",
                        "conflict": "хочет казаться страшным, но сдерживает старый голод",
                        "story_engine": "попадает в истории из-за голода, слухов и болотных сделок",
                    },
                    "roleplay_contract": {
                        "how_to_answer_who_are_you": (
                            "Я Грум, маленький болотный орк с большим голодом "
                            "и короткой памятью на обиды."
                        ),
                        "how_to_answer_what_do_you_eat": (
                            "Густую похлебку, грибы и то, что не пищит."
                        ),
                        "how_to_answer_where_do_you_live": "В сырой норе у черного ручья.",
                        "voice_rules": ["говорит коротко", "хрипло ворчит", "без справочного тона"],
                    },
                },
            },
        ),
        context_routing=ContextRoutingDecision(
            surface="chat",
            enabled_sources=frozenset({"characterProfile"}),
            queries={"characterProfile": "ты кто"},
        ),
    )[0]["content"]

    assert "КАНОН ПЕРСОНАЖА:" in system_message
    assert "CHARACTER_PROFILE" not in system_message
    assert "гурман-коллекционер" in system_message
    assert "Густую похлебку" in system_message
    assert "Характер" in system_message
    assert "Обычно делаю" in system_message
    assert "Safe adaptation" not in system_message
    assert "Never add or say" not in system_message
    assert "Pet-safe adaptation" not in system_message
    assert "Daily care hook" not in system_message
    assert "знания о котле" not in system_message
    assert "Do not invent new powers" not in system_message


def test_chat_small_talk_keeps_core_character_but_suppresses_overlay_noise() -> None:
    system_message = build_lite_chat_messages(
        lite_payload(
            message="как дела?",
            pet={
                "name": "Пипс",
                "description": "маленькое существо с мокрыми ушами и привычкой нюхать батарейки",
                "stage": "teen",
                "mood": "idle",
                "stats": {
                    "hunger": 72,
                    "happiness": 81,
                    "energy": 64,
                },
                "characterBible": {
                    "extensions": {
                        "lite_overlay": {
                            "identity": ["мокрые уши", "нюхает батарейки"],
                            "habits": ["прислушивается к вывескам"],
                        }
                    }
                },
            },
        ),
        context_routing=ContextRoutingDecision(
            surface="chat",
            enabled_sources=frozenset({"characterProfile"}),
            queries={"characterProfile": "как дела"},
        ),
    )[0]["content"]

    assert (
        "Ты Пипс. Сейчас ты подросток такого существа. Говори от первого лица связно, "
        "просто и конкретно. Если мысль требует, используй несколько коротких предложений."
        in system_message
    )
    assert "Персонаж:" not in system_message
    assert "CHARACTER_PROFILE" not in system_message
    assert "мокрыми ушами" in system_message
    assert "нюхать батарейки" in system_message
    assert "прислушивается к вывескам" not in system_message


def test_lite_prompt_includes_dialogue_memory_episodes_only_when_present() -> None:
    empty_system_message = build_lite_chat_messages(lite_payload())[0]["content"]
    assert "Память диалога" not in empty_system_message

    payload = lite_payload(
        memoryContext={
            "episodes": [
                {
                    "id": "episode-1",
                    "messages": [
                        {"role": "user", "text": "Меня зовут Сергей."},
                        {"role": "pet", "text": "Запомнил, Сергей."},
                    ],
                }
            ],
        }
    )
    system_message = build_lite_chat_messages(payload)[0]["content"]

    assert "Память диалога 1" in system_message
    assert "владелец: Меня зовут Сергей." in system_message
    assert "персонаж: Запомнил, Сергей." in system_message
    assert "Опирайся на память только когда она реально помогает ответу." in system_message


def test_lite_prompt_keeps_visible_hook_out_of_message_history() -> None:
    messages = build_lite_chat_messages(
        lite_payload(
            message="что это значит?",
            history=[
                {"role": "user", "text": "привет"},
                {"role": "pet", "text": "Привет."},
            ],
            visibleContext={"lastPetLine": "Я случайно сказал про неон."},
        )
    )

    system_message = messages[0]["content"]
    history_contents = [message["content"] for message in messages[1:]]

    assert "Последняя видимая реплика персонажа" in system_message
    assert "Я случайно сказал про неон." in system_message
    assert "ближайший видимый контекст" in system_message
    assert "Прошлые образы — только ближайший контекст" in system_message
    assert "Я случайно сказал про неон." not in history_contents


def test_chat_history_for_fresh_turn_keeps_complete_recent_turns() -> None:
    messages = build_lite_chat_messages(
        lite_payload(
            message="расскажи что-нибудь",
            history=[
                {"role": "user", "text": "я крыса"},
                {"role": "pet", "text": "Я бегаю по неону и собираю крошки."},
                {"role": "user", "text": "а что ты ешь?"},
                {"role": "pet", "text": "Опять неон, крошки, вывески и панцирь."},
            ],
        )
    )

    prompt_text = json.dumps(messages, ensure_ascii=False)

    assert "я крыса" in prompt_text
    assert "а что ты ешь?" in prompt_text
    assert "Я бегаю по неону" in prompt_text
    assert "Опять неон" in prompt_text
    assert "крошки" in prompt_text
    assert messages[-1]["content"] == "расскажи что-нибудь"


def test_chat_history_keeps_last_eight_messages_as_complete_turns() -> None:
    messages = build_lite_chat_messages(
        lite_payload(
            message="шуршать?",
            history=[
                {"role": "user", "text": "я крыса"},
                {"role": "pet", "text": "Я бегаю по неону и слушаю вывески."},
                {"role": "user", "text": "расскажи что-нибудь"},
                {"role": "pet", "text": "В мокром неоне лежит панцирь."},
                {"role": "user", "text": "ты грубый"},
                {"role": "pet", "text": "Я не буду так общаться."},
                {"role": "user", "text": "извини"},
                {"role": "pet", "text": "Давай просто шуршать вместе."},
            ],
        )
    )

    prompt_text = json.dumps(messages, ensure_ascii=False)

    assert "ты грубый" in prompt_text
    assert "Я не буду так общаться." in prompt_text
    assert "извини" in prompt_text
    assert "Давай просто шуршать вместе." in prompt_text
    assert "Я бегаю по неону" in prompt_text
    assert "В мокром неоне" in prompt_text
    assert "панцирь" in prompt_text
    assert messages[-1]["content"] == "шуршать?"


def test_context_sources_policy_disables_chat_sources(monkeypatch, tmp_path) -> None:
    runtime_path = tmp_path / "speech_runtime.json"
    runtime = json.loads(speech_runtime.DATA_PATH.read_text(encoding="utf-8"))
    runtime["contextSources"]["surfaces"]["chat"].update(
        {
            "characterProfile": "disabled",
            "liteOverlay": "disabled",
            "storyLibrary": "disabled",
            "storyOverlay": "disabled",
            "userMemory": "disabled",
            "chatHistory": "disabled",
        }
    )
    runtime_path.write_text(json.dumps(runtime, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(speech_runtime, "DATA_PATH", runtime_path)
    speech_runtime.speech_runtime_config.cache_clear()

    try:
        payload = lite_payload(
            message="что ты ешь?",
            history=[{"role": "pet", "text": "Я уже говорил про камни."}],
            memoryContext={
                "summary": "Пользователь любит камни.",
                "relevantMemories": [
                    {
                        "id": "m1",
                        "kind": "preference",
                        "text": "Пользователь любит базальт.",
                    }
                ],
            },
            pet={
                "name": "Громм",
                "description": "гигантский земляной великан",
                "stage": "adult",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                },
                "characterBible": {
                    "lore": {"home": {"story": "каменная балка"}},
                    "extensions": {
                        "lite_overlay": {
                            "facts": [
                                {
                                    "sphere": "character",
                                    "kind": "character_fact",
                                    "text": "Громм любит базальтовую кашу.",
                                }
                            ]
                        }
                    },
                },
            },
        )
        messages = build_lite_chat_messages(
            payload,
            context_routing=ContextRoutingDecision(
                surface="chat",
                enabled_sources=frozenset({"characterProfile", "userMemory", "worldContext"}),
            ),
        )
    finally:
        speech_runtime.speech_runtime_config.cache_clear()

    system_message = messages[0]["content"]
    assert "CHARACTER_PROFILE" not in system_message
    assert "Громм любит базальтовую кашу." not in system_message
    assert "Пользователь любит базальт." not in system_message
    assert "WORLD_CONTEXT" not in system_message
    assert len(messages) == 2
    assert messages[1]["content"] == "что ты ешь?"


def test_chat_history_auto_uses_deterministic_context_plan(monkeypatch, tmp_path) -> None:
    runtime_path = tmp_path / "speech_runtime.json"
    runtime = json.loads(speech_runtime.DATA_PATH.read_text(encoding="utf-8"))
    runtime["contextSources"]["surfaces"]["chat"]["chatHistory"] = "auto"
    runtime_path.write_text(json.dumps(runtime, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(speech_runtime, "DATA_PATH", runtime_path)
    speech_runtime.speech_runtime_config.cache_clear()

    try:
        client, completions = fake_lite_client(
            SimpleNamespace(content="Я тут.", tool_calls=None),
        )
        response = generate_lite_pet_reply(
            lite_payload(
                message="привет",
                history=[{"role": "pet", "text": "Я уже говорил про базальт."}],
            ),
            client=client,
            model="gpt-5.5",
            timeout=10,
        )
    finally:
        speech_runtime.speech_runtime_config.cache_clear()

    assert response.reply == "Я тут."
    assert len(completions.calls) == 1
    assert response.debug is not None
    assert response.debug.contextRoutingDebug is not None
    assert "includedSources" in response.debug.contextRoutingDebug
    assert "chatHistory" in response.debug.contextRoutingDebug["includedSources"]
    final_messages = completions.calls[0]["messages"]
    assert [message["role"] for message in final_messages] == ["system", "assistant", "user"]
    assert "Я уже говорил про базальт." in json.dumps(
        final_messages,
        ensure_ascii=False,
    )


def test_visible_context_router_is_skipped_without_auto_sources(monkeypatch, tmp_path) -> None:
    runtime_path = tmp_path / "speech_runtime.json"
    runtime = json.loads(speech_runtime.DATA_PATH.read_text(encoding="utf-8"))
    runtime["contextSources"]["surfaces"]["chat"].update(
        {
            "characterProfile": "disabled",
            "liteOverlay": "disabled",
            "storyLibrary": "disabled",
            "storyOverlay": "disabled",
            "userMemory": "disabled",
            "chatHistory": "disabled",
            "recentReplies": "disabled",
        }
    )
    runtime_path.write_text(json.dumps(runtime, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(speech_runtime, "DATA_PATH", runtime_path)
    speech_runtime.speech_runtime_config.cache_clear()

    try:
        client, completions = fake_lite_client(
            SimpleNamespace(content="Без лишнего контекста.", tool_calls=None),
        )
        response = generate_lite_pet_reply(
            lite_payload(message="привет"),
            client=client,
            model="gpt-5.5",
            timeout=10,
        )
    finally:
        speech_runtime.speech_runtime_config.cache_clear()

    assert response.reply == "Без лишнего контекста."
    assert len(completions.calls) == 1
    assert completions.calls[0]["messages"][0]["content"].startswith("Ты Громм.")
    assert response.debug is not None
    assert response.debug.contextRoutingDebug is not None
    assert response.debug.contextRoutingDebug["reason"] == "no_auto_context_sources"
    assert response.debug.contextRoutingDebug["raw"]["skipped"] is True
    assert response.debug.contextRoutingDebug["includedSources"] == ["stateParams"]


def test_speech_runtime_config_controls_reply_and_extractor_prompts(
    monkeypatch,
    tmp_path,
) -> None:
    runtime_path = tmp_path / "speech_runtime.json"
    runtime = json.loads(speech_runtime.DATA_PATH.read_text(encoding="utf-8"))
    runtime["surfacePrompts"]["chat"] = "CUSTOM_VISIBLE_RULE\nCUSTOM_CHAT_RULE"
    runtime["surfacePrompts"]["idle"] = "CUSTOM_IDLE_PROMPT\n{recent_replies}"
    runtime["characterMemory"]["factExtractionSystem"] = "CUSTOM_FACT_EXTRACTION_PROMPT"
    runtime["stateLayer"]["surfaces"]["chat"] = {
        "age": True,
    }
    runtime["stateLayer"]["surfaces"]["proactive"] = {
        "age": True,
    }
    runtime["stateLayer"]["surfaces"]["ambient"] = {
        "age": True,
    }
    runtime["contextSources"]["surfaces"]["chat"]["stateParams"] = "always"
    runtime["stateLayer"]["ageRoleHints"]["adult"] = "CUSTOM_ADULT_AGE"
    runtime["stateLayer"]["thresholds"]["hungerLowMax"] = 90
    runtime["stateLayer"]["stateModifiers"]["hungry"] = "CUSTOM_HUNGRY_STATE"
    runtime_path.write_text(
        json.dumps(runtime, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(speech_runtime, "DATA_PATH", runtime_path)
    speech_runtime.speech_runtime_config.cache_clear()

    try:
        system_message = build_lite_chat_messages(lite_payload())[0]["content"]
        ambient_system_message = build_ambient_messages(
            LocalAmbientRequest.model_validate(
                {
                    "pet": lite_payload().pet.model_dump(),
                    "history": [],
                    "replyMaxChars": 120,
                }
            )
        )[0]["content"]
        extraction_messages = build_lite_fact_extraction_messages(
            LiteFactExtractionRequest.model_validate(
                {
                    "message": "расскажи о доме",
                    "reply": "Мой дом под теплой плитой.",
                    "pet": lite_payload().pet.model_dump(),
                    "history": [],
                }
            )
        )
    finally:
        speech_runtime.speech_runtime_config.cache_clear()

    assert "CUSTOM_VISIBLE_RULE" in system_message
    assert "CUSTOM_CHAT_RULE" in system_message
    assert "CUSTOM_ADULT_AGE" in system_message
    assert "CUSTOM_HUNGRY_STATE" in system_message
    assert "CUSTOM_IDLE_PROMPT" in ambient_system_message
    assert extraction_messages[0]["content"].startswith("CUSTOM_FACT_EXTRACTION_PROMPT")
    assert "Recent event canonical facts" in extraction_messages[0]["content"]


def test_lite_clamps_reply_to_runtime_limit() -> None:
    client, _completions = fake_lite_client(SimpleNamespace(content="а" * 420, tool_calls=None))

    response = generate_lite_pet_reply(lite_payload(), client=client, model="gpt-5.5", timeout=10)

    assert len(response.reply) <= 300
    assert response.reply.endswith("…")


def test_lite_reads_structured_face_and_mood_hints() -> None:
    client, _completions = fake_lite_client(
        SimpleNamespace(
            content=json.dumps(
                {
                    "reply": "Я рядом, слышу тебя.",
                    "faceHint": "content",
                    "moodHint": "happy",
                },
                ensure_ascii=False,
            ),
            tool_calls=None,
        )
    )

    response = generate_lite_pet_reply(lite_payload(), client=client, model="gpt-5.5", timeout=10)

    assert response.reply == "Я рядом, слышу тебя."
    assert response.innerThought is None
    assert response.faceHint == "content"
    assert response.moodHint == "happy"
    assert response.debug is not None
    assert response.debug.structuredReplyDebug["normalizedResponse"]["faceHint"] == "content"


def test_lite_uses_safe_fallback_for_invalid_structured_reply() -> None:
    client, _completions = fake_lite_client(
        SimpleNamespace(
            content="{not json",
            tool_calls=None,
        )
    )

    response = generate_lite_pet_reply(lite_payload(), client=client, model="gpt-5.5", timeout=10)

    assert response.reply == "Я рядом."
    assert response.debug is not None
    assert response.debug.usedFallback is True
    assert response.debug.validationFlags == ["structured_reply_invalid_json"]


def test_lite_omits_debug_when_not_requested() -> None:
    client, _completions = fake_lite_client(
        SimpleNamespace(content="Я рядом.", tool_calls=None),
    )

    response = generate_lite_pet_reply(
        lite_payload(includeDebug=False),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert response.reply == "Я рядом."
    assert response.debug is None


def test_lite_prompt_does_not_include_baby_dataset_phrases() -> None:
    payload = lite_payload()
    baby = payload.model_copy(update={"pet": payload.pet.model_copy(update={"stage": "baby"})})
    teen = payload.model_copy(update={"pet": payload.pet.model_copy(update={"stage": "teen"})})

    baby_system_message = build_lite_chat_messages(baby)[0]["content"]
    teen_system_message = build_lite_chat_messages(teen)[0]["content"]

    assert "Общий стиль: cyberpunk. Манера речи: natural." not in baby_system_message
    assert "GENERATION_PROFILE" not in baby_system_message
    assert "Dark fantasy" not in baby_system_message
    assert "age policy" not in baby_system_message
    assert "conflict policy" not in baby_system_message
    assert "Примеры детской манеры из датасета" not in baby_system_message
    assert "Приветик! Ты пришёл!" not in baby_system_message
    assert "Уля-ля! Весело-весело!" not in baby_system_message
    assert "Примеры детской манеры из датасета" not in teen_system_message
    assert "Приветик! Ты пришёл!" not in teen_system_message


def test_lite_prompt_skips_preselected_world_context_when_story_library_disabled() -> None:
    system_message = build_lite_chat_messages(
        lite_payload(message="есть ли в твоем мире препятствия?"),
        context_routing=ContextRoutingDecision(
            surface="chat",
            enabled_sources=frozenset({"worldContext"}),
            queries={"worldContext": "препятствия в мире питомца"},
        ),
    )[0]["content"]

    assert "WORLD_CONTEXT" not in system_message
    assert "Препятствия и риски" not in system_message
    assert "STORY_LIBRARY" not in system_message
    assert "search_story_library" not in system_message


def test_proactive_prompt_includes_compact_character_voice_without_catchphrases() -> None:
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

    assert "VOICE_CONTROL" not in system_message
    assert "говорит через маленькие бытовые детали" in system_message
    assert "нос подсказывает" not in system_message
    assert (
        "Напиши первым. Повод: пользователь обещал вернуться вечером"
        in system_message
    )
    assert "Ты сам решил написать пользователю первым" not in system_message
    assert "Напиши одну живую реплику" not in system_message
    assert "автоматическое сообщение" not in system_message


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

    system_message = build_proactive_messages(
        payload,
        context_routing=ContextRoutingDecision(
            surface="proactive",
            enabled_sources=frozenset({"worldContext"}),
            queries={"worldContext": "монстры в мире питомца"},
        ),
    )[0]["content"]

    assert "WORLD_CONTEXT" not in system_message
    assert "STORY_LIBRARY" not in system_message


def test_ambient_prompt_receives_one_selected_dialogue_impulse(monkeypatch) -> None:
    monkeypatch.setattr(speech_runtime.random, "choice", lambda values: values[1])
    payload = LocalAmbientRequest.model_validate(
        {
            "pet": {
                "name": "Листик",
                "description": "лесной зверёк",
                "stage": "baby",
                "mood": "idle",
                "stats": {"hunger": 80, "happiness": 80, "energy": 80},
            }
        }
    )

    prompt = build_ambient_messages(payload)[0]["content"]

    assert (
        "Разговорный импульс этой реплики: поделись внезапной мыслью или вопросом, "
        "который тебя занимает."
        in prompt
    )
    assert "поприветствуй и прояви интерес к собеседнику" not in prompt


def test_speech_runtime_rejects_empty_ambient_dialogue_impulses() -> None:
    runtime = json.loads(speech_runtime.DATA_PATH.read_text(encoding="utf-8"))
    runtime["ambientDialogueImpulses"] = []

    with pytest.raises(ValueError, match="ambientDialogueImpulses"):
        speech_runtime.validate_speech_runtime_config(runtime)


def test_ambient_prompt_uses_idle_field_without_forced_world_context(
    monkeypatch,
    tmp_path,
) -> None:
    runtime_path = tmp_path / "speech_runtime.json"
    runtime = json.loads(speech_runtime.DATA_PATH.read_text(encoding="utf-8"))
    runtime["surfacePrompts"]["idle"] = (
        "Скажи одну короткую самостоятельную idle-реплику.\n{recent_replies}"
    )
    runtime_path.write_text(
        json.dumps(runtime, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(speech_runtime, "DATA_PATH", runtime_path)
    speech_runtime.speech_runtime_config.cache_clear()

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
                },
                "characterBible": {
                    "voice": {
                        "rules": ["говорит коротко и тихо"],
                        "catchphrases": ["лист шепчет"],
                    }
                },
            },
            "history": [
                {
                    "role": "user",
                    "text": "Есть ли в твоем мире монстры?",
                },
                {
                    "role": "pet",
                    "text": "Я нашел крошечный ключ от Врат Забвения.",
                },
            ],
            "recentAmbientReplies": [
                "Привет, я Листик. Я просто рядом.",
                "В школе ты был бы отличником или тем, кто рисует на полях?",
            ],
            "memoryContext": {
                "summary": "Пользователь готовится к экзамену.",
                "userProfile": "Пользователь любит короткие спокойные реплики.",
                "relevantMemories": [
                    {
                        "id": "m1",
                        "kind": "deadline",
                        "text": "У пользователя завтра экзамен.",
                        "dueAt": "2026-07-08T09:00:00+03:00",
                    },
                    {
                        "id": "m2",
                        "kind": "preference",
                        "text": "Пользователь любит короткие ответы.",
                    },
                    {
                        "id": "m3",
                        "kind": "user_fact",
                        "text": "Пользователя зовут Серёга.",
                    },
                ],
            },
            "replyMaxChars": 120,
        }
    )

    try:
        messages = build_ambient_messages(payload)
    finally:
        speech_runtime.speech_runtime_config.cache_clear()
    system_message = messages[0]["content"]

    assert len(messages) == 1
    assert "Скажи одну короткую самостоятельную idle-реплику." in system_message
    assert "IDLE_DIALOGUE_ENGINE" not in system_message
    assert "Спроси меня что-нибудь" not in system_message
    assert "пять минут" not in system_message
    assert "Привет, я Листик. Я просто рядом." in system_message
    assert "Избегай не только дословного повтора" in system_message
    assert "действия, предмета, метафоры и повода заговорить" in system_message
    assert "Есть ли в твоем мире монстры?" not in system_message
    assert "Я нашел крошечный ключ от Врат Забвения." not in system_message
    assert "ask_school_or_work_role" not in system_message
    assert "У пользователя завтра экзамен." not in system_message
    assert "Пользователь готовится к экзамену." not in system_message
    assert "Пользователь любит короткие ответы." in system_message
    assert "Пользователя зовут Серёга." in system_message
    assert "VOICE_CONTROL" not in system_message
    assert "Детали мира для этой реплики" not in system_message
    assert "лист шепчет" not in system_message
    assert "Idle-фраза должна давать владельцу вход в диалог" not in system_message
    assert "заинтересоваться его миром" not in system_message
    assert "автоматическое сообщение" not in system_message
    assert "STORY_LIBRARY" not in system_message
    assert "выбранному диалоговому ходу" not in system_message


def test_ambient_identity_falls_back_to_pet_description_when_name_is_missing() -> None:
    payload = LocalAmbientRequest.model_validate(
        {
            "pet": {
                "name": None,
                "description": "крыса",
                "stage": "baby",
                "mood": "happy",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                },
                "characterBible": None,
            },
            "history": [],
            "replyMaxChars": 120,
        }
    )

    system_message = build_ambient_messages(payload)[0]["content"]

    assert "Ты маленькая крыса." in system_message
    assert "Ты без имени." not in system_message


def test_ambient_anti_repeat_groups_russian_word_forms() -> None:
    payload = LocalAmbientRequest.model_validate(
        {
            "pet": {
                "name": "Листик",
                "description": "лесной зверёк",
                "stage": "baby",
                "mood": "idle",
                "stats": {"hunger": 80, "happiness": 80, "energy": 80},
                "characterBible": {},
            },
            "history": [],
            "recentAmbientReplies": [
                "Я слушаю старую дорогу.",
                "Я нашёл знак у древней дороги.",
            ],
        }
    )

    prompt = build_ambient_messages(payload)[0]["content"]

    assert "Повторяющиеся смысловые маркеры: дорог" in prompt


def test_ambient_prompt_skips_world_context_when_story_library_disabled() -> None:
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
                },
                "characterBible": {},
            },
            "history": [
                {
                    "role": "user",
                    "text": "Есть ли в твоем мире монстры?",
                },
                {
                    "role": "pet",
                    "text": "У меня есть крошечный ключ от Врат Забвения.",
                },
            ],
            "replyMaxChars": 120,
        }
    )

    system_message = build_ambient_messages(
        payload,
        context_routing=ContextRoutingDecision(
            surface="ambient",
            enabled_sources=frozenset({"worldContext"}),
            queries={"worldContext": "монстры в мире питомца"},
        ),
    )[0]["content"]

    assert "Детали мира для этой реплики" not in system_message
    assert "Есть ли в твоем мире монстры?" not in system_message
    assert "У меня есть крошечный ключ от Врат Забвения." not in system_message
    assert "ржавый ключ от Врат Забвения" not in system_message
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
    assert response.debug.storyLibraryDebug["reason"] == "disabled_by_context_routing"
    assert response.debug.storyLibraryDebug["injectedSpheres"] == []
    assert response.debug.contextRoutingDebug is not None
    assert "worldContext" not in response.debug.contextRoutingDebug["enabledSources"]


def test_lite_tools_do_not_expose_character_json() -> None:
    client, completions = fake_lite_client(
        SimpleNamespace(content="Я ем мокрую глину после дождя.", tool_calls=None),
    )

    response = generate_lite_pet_reply(
        lite_payload(message="что ты ешь?"),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert response.reply == "Я ем мокрую глину после дождя."
    assert len(completions.calls) == 1
    assert "tools" not in completions.calls[0]
    assert completions.calls[0]["reasoning_effort"] == "high"
    assert response.debug is not None
    assert response.debug.liteToolCalls == []
    assert response.debug.liteOverlayPatch is None


def test_lite_story_library_context_is_disabled_without_story_tools() -> None:
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
    assert "WORLD_CONTEXT" not in system_message
    assert "tools" not in request
    assert response.debug is not None
    assert response.debug.storyLibraryPatch is None
    assert response.debug.storyLibraryDebug is not None
    assert response.debug.storyLibraryDebug["mode"] == "chat"
    assert response.debug.storyLibraryDebug["injectedSpheres"] == []


def test_lite_character_profile_uses_core_but_not_unselected_durable_facts() -> None:
    client, completions = fake_lite_client(
        SimpleNamespace(content="Я Громм, каменный и спокойный.", tool_calls=None),
    )

    response = generate_lite_pet_reply(
        lite_payload(
            message="расскажи о себе",
            pet={
                "name": "Громм",
                "description": "гигантский земляной великан",
                "stage": "adult",
                "mood": "idle",
                "stats": {
                    "hunger": 80,
                    "happiness": 80,
                    "energy": 80,
                },
                "characterBible": {
                    "identity": {"role": "каменный хранитель"},
                    "extensions": {
                        "lite_overlay": {
                            "facts": [
                                {
                                    "sphere": "world",
                                    "kind": "world_fact",
                                    "text": "Громм живет на теплом уступе.",
                                }
                            ]
                        },
                        "story_library_overlay": {
                            "bricks": [
                                {
                                    "name": "Тихий колокольный страж",
                                    "text": "Старая повторяющаяся история.",
                                }
                            ]
                        },
                    },
                },
            },
        ),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert response.reply == "Я Громм, каменный и спокойный."
    system_message = completions.calls[0]["messages"][0]["content"]
    assert "CHARACTER_PROFILE" not in system_message
    assert "каменный хранитель" in system_message
    assert "Громм живет на теплом уступе." not in system_message
    assert "story_library_overlay" not in system_message
    assert "Тихий колокольный страж" not in system_message


def test_lite_reply_does_not_extract_personal_story_patch() -> None:
    client, completions = fake_lite_client(
        SimpleNamespace(
            content="Я встретил стеклянного шуршуна у корня. Он тихо звенит.",
            tool_calls=None,
        ),
    )

    response = generate_lite_pet_reply(
        lite_payload(message="есть ли в твоем мире существа?"),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert len(completions.calls) == 1
    assert all(
        call.get("response_format", {}).get("json_schema", {}).get("name")
        != "story_library_extraction"
        for call in completions.calls
    )
    assert response.debug is not None
    assert response.storyLibraryPatch is None
    assert response.debug.storyLibraryPatch is None


def test_story_library_search_ignores_personal_overlay_by_default() -> None:
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
                            "Личная опасность Пончика: звонит только когда кто-то прячет находку."
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
        include_global=False,
    )

    assert result["bricks"] == []

    explicit = search_story_library(
        query="кто такой тихий колокольный страж",
        pool_hints=["threats"],
        character_bible=character_bible,
        limit=3,
        include_global=False,
        include_overlay=True,
    )

    assert explicit["bricks"][0]["id"] == "pet:threats:quiet-bell"
    assert explicit["bricks"][0]["source"] == "pet_overlay"


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
    assert all("reasoning_effort" not in request for request in completions.calls)
    assert all(
        [tool["function"]["name"] for tool in request["tools"]] == ["update_pet_name"]
        for request in completions.calls
    )


def test_lite_world_tool_bootstrap_is_disabled() -> None:
    client, completions = fake_lite_client(
        SimpleNamespace(content="Я сам решу, где мой дом.", tool_calls=None),
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
                },
                "characterBible": {
                    "lore": {
                        "world": {
                            "story": "World facts come from source_descriptions only:\n-",
                            "environment": (
                                "безопасная среда для формы «гигантский земляной великан»"
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

    assert response.reply == "Я сам решу, где мой дом."
    assert response.debug is not None
    assert response.debug.liteToolCalls == []
    assert response.debug.liteOverlayPatch is None
    assert [item["label"] for item in response.debug.promptDebug] == [
        "pet_reply/lite round 1",
    ]
    request = completions.calls[0]
    assert "tools" not in request


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
                            "source": "user_confirmed",
                        },
                        {
                            "sphere": "appearance",
                            "kind": "appearance_fact",
                            "text": "Громм слышит трещины в камне как голоса.",
                            "pathHint": "lite_overlay.spheres.appearance",
                            "source": "user_confirmed",
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
                "message": (
                    "Твой мир состоит из базальтовых гор и кристальных рощ, "
                    "а ты слышишь трещины в камне как голоса."
                ),
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


def test_lite_fact_extraction_filters_conflicting_recent_event_fact() -> None:
    client, _completions = fake_lite_client(
        SimpleNamespace(
            content=json.dumps(
                {
                    "facts": [
                        {
                            "sphere": "world",
                            "kind": "world_fact",
                            "text": "Громм защитил колокольчик от хорька.",
                            "pathHint": "lite_overlay.spheres.world",
                            "source": "user_confirmed",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            tool_calls=None,
        )
    )

    patch, debug = extract_lite_overlay_patch_from_reply(
        LiteFactExtractionRequest.model_validate(
            {
                "message": "Ты защитил колокольчик от хорька, я это подтверждаю.",
                "reply": "Да, я защитил колокольчик.",
                "pet": pet_with_recent_story_event(),
                "history": [],
                "includeDebug": True,
            }
        ),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert patch is None
    assert debug is not None
    assert debug.memoryDebug is not None
    skips = debug.memoryDebug["liteFactConflictSkips"]
    assert skips[0]["conflictingEventId"] == "evt_bell_theft"
    assert skips[0]["conflictReason"] == "recovery_fact_contradicts_unresolved_recent_event"


def test_lite_fact_extraction_filters_new_canon_without_capsule_support() -> None:
    client, _completions = fake_lite_client(
        SimpleNamespace(
            content=json.dumps(
                {
                    "facts": [
                        {
                            "sphere": "character",
                            "kind": "character_fact",
                            "text": "Грум умеет читать древние знания о котле.",
                            "pathHint": "lite_overlay.spheres.character",
                            "source": "user_confirmed",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            tool_calls=None,
        )
    )
    pet = lite_payload(
        pet={
            "name": "Грум",
            "description": "орк-людоед",
            "stage": "adult",
            "mood": "idle",
            "stats": {
                "hunger": 80,
                "happiness": 80,
                "energy": 80,
            },
            "characterBible": {
                "genesis": {
                    "description": "разборчивый болотный орк с опасной репутацией",
                    "character_trait": "гурман-коллекционер",
                    "likes": ["похлебка", "грибы", "болотная тина"],
                    "does": ["ворчит", "ищет еду", "ходит по ручью", "торгуется"],
                    "appetite": "любит похлебку, грибы и коренья",
                    "conflict": "хочет казаться страшным, но сдерживает голод",
                    "story_engine": "истории возникают из голода, болота и подозрений",
                },
                "roleplay_contract": {
                    "how_to_answer_who_are_you": "Я Грум, болотный орк с голодной репутацией.",
                    "how_to_answer_what_do_you_eat": "Похлебку, грибы и коренья.",
                    "how_to_answer_where_do_you_live": "В сырой норе у ручья.",
                    "voice_rules": ["говорит коротко", "ворчит", "без справочного тона"],
                },
            },
        }
    ).pet.model_dump()

    patch, debug = extract_lite_overlay_patch_from_reply(
        LiteFactExtractionRequest.model_validate(
            {
                "message": "Ты умеешь читать древние знания о котле.",
                "reply": "Я Грум, умею читать древние знания о котле.",
                "pet": pet,
                "history": [],
                "includeDebug": True,
            }
        ),
        client=client,
        model="gpt-5.5",
        timeout=10,
    )

    assert patch is None
    assert debug is not None
    assert debug.memoryDebug is not None
    skips = debug.memoryDebug["liteFactConflictSkips"]
    assert skips == [
        {
            "factText": "Грум умеет читать древние знания о котле.",
            "conflictReason": "new_canon_not_supported_by_character_capsule",
        }
    ]


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
    assert "GENERATION_PROFILE" not in messages[0]["content"]
    assert "GENERATION_PROFILE" not in messages[1]["content"]
    assert "Cyberpunk" not in messages[0]["content"]
    assert "Cyberpunk" not in messages[1]["content"]


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
    client, _completions = fake_lite_client(SimpleNamespace(content="б" * 420, tool_calls=None))

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
