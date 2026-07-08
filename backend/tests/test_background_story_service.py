from __future__ import annotations

import json
from types import SimpleNamespace

from app.schemas import LocalChatHistoryItem, LocalPetChatContext, LocalPetMemoryContext
from app.services import background_story_service


class FakeBackgroundStoryCompletions:
    def __init__(self, content: str | list[str]) -> None:
        self.contents = [content] if isinstance(content, str) else content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        content = self.contents[min(len(self.calls), len(self.contents) - 1)]
        self.calls.append(kwargs)
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _call_by_schema(completions: FakeBackgroundStoryCompletions, schema_name: str) -> dict:
    for call in completions.calls:
        if call.get("response_format", {}).get("json_schema", {}).get("name") == schema_name:
            return call
    raise AssertionError(f"schema call not found: {schema_name}")


def _pet() -> LocalPetChatContext:
    return LocalPetChatContext.model_validate(
        {
            "name": "Олег",
            "description": "чел с листом вместо лица",
            "stage": "baby",
            "mood": "happy",
            "stats": {
                "hunger": 96,
                "happiness": 100,
                "energy": 71,
            },
            "characterBible": {
                "identity": {
                    "name": "Листик",
                    "species": "Чел с листом вместо лица",
                    "one_liner": (
                        "Лист на лице стук, сердце в растениях"
                    ),
                },
                "inner_state": {
                    "core_want": (
                        "ощущать тепло и заботу через листовую чешую"
                    ),
                    "fears": ["ветер слишком сильный"],
                },
                "lore": {
                    "home": {"place": "лесная поляна под кроной"},
                    "story_seeds": ["древний дуб отвечает шепотом"],
                },
                "extensions": {
                    "lite_overlay": {
                        "facts": [
                            {
                                "sphere": "appearance",
                                "kind": "appearance_fact",
                                "text": (
                                    "Листики выпускают запахи-сигналы опасности."
                                ),
                            }
                        ]
                    },
                    "story_library_overlay": {
                        "bricks": [
                            {
                                "name": "Каменная тропа",
                                "text": "На тропе живет стеклянный шорох.",
                            }
                        ]
                    }
                },
            },
        }
    )


def test_generate_background_story_extracts_aftermath_lite_patch(monkeypatch) -> None:
    routing_content = json.dumps(
        {
            "sources": {
                "worldContext": {"enabled": False, "query": ""},
                "characterProfile": {"enabled": True, "query": "описание персонажа"},
                "userMemory": {"enabled": False, "query": ""},
                "chatHistory": {"enabled": False, "query": ""},
                "recentReplies": {"enabled": False, "query": ""},
            },
            "reason": "Нужен профиль персонажа.",
        },
        ensure_ascii=False,
    )
    content = json.dumps(
        {
            "title": "Налет стеклянных улиток",
            "summary": (
                "На Олега напали стеклянные улитки у лесной миски."
            ),
            "storyText": (
                "У лесной миски Олег услышал хруст: стеклянные "
                "улитки поползли к его листу."
            ),
            "eventType": "attack",
            "valence": "negative",
            "tags": ["лес", "улитки"],
            "ragText": (
                "На Олега у лесной миски напали стеклянные улитки, "
                "охотившиеся за запахами-сигналами листа."
            ),
        },
        ensure_ascii=False,
    )
    aftermath_content = json.dumps(
        {
            "facts": [
                {
                    "sphere": "world",
                    "kind": "world_fact",
                    "text": (
                        "У лесной миски Олега водятся стеклянные улитки, "
                        "которые охотятся за запахами-сигналами листа."
                    ),
                    "pathHint": "lite_overlay.spheres.world",
                    "source": "background_story_aftermath",
                    "confidence": 0.91,
                }
            ],
            "recentEvent": {
                "summary": "Стеклянные улитки поползли к листу Олега у лесной миски.",
                "eventType": "attack",
                "participants": ["стеклянные улитки", "Олег"],
                "actions": ["нападение"],
                "objects": ["лист"],
                "location": "лесная миска",
                "outcome": "Олег пережил налет.",
            },
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions(
        [routing_content, content, aftermath_content]
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )

    result = background_story_service.generate_background_story(
        pet=_pet(),
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    assert result.title == "Налет стеклянных улиток"
    assert result.event_type == "attack"
    assert result.story_library_patch is None
    assert result.lite_overlay_patch is not None
    assert result.recent_story_event is not None
    assert result.recent_story_event["summary"] == (
        "Стеклянные улитки поползли к листу Олега у лесной миски."
    )
    assert result.recent_story_event["participants"] == ["стеклянные улитки", "Олег"]
    fact = result.lite_overlay_patch["facts"][0]
    assert fact["sphere"] == "world"
    assert fact["source"] == "background_story_aftermath"
    assert "стеклянные улитки" in fact["text"]
    request = _call_by_schema(completions, "background_story")
    assert request["response_format"]["json_schema"]["name"] == "background_story"
    prompt = request["messages"][1]["content"]
    assert "наевшийся" in prompt
    assert "счастливый" in prompt
    assert "энергичный" in prompt
    assert '"stats"' not in prompt
    assert '"голод"' in prompt
    assert (
        "Листики выпускают запахи-сигналы опасности."
        in prompt
    )
    aftermath_request = _call_by_schema(
        completions,
        "background_story_aftermath_extraction",
    )
    aftermath_prompt = aftermath_request["messages"][1]["content"]
    assert "Сгенерированная история JSON" in aftermath_prompt
    assert "Налет стеклянных улиток" in aftermath_prompt


def test_background_story_profile_toggle_controls_description() -> None:
    source_flags = {
        "characterProfile": False,
        "stateParams": False,
        "liteOverlay": False,
        "storyOverlay": False,
        "userMemory": False,
        "chatHistory": False,
        "recentReplies": False,
    }

    without_profile = background_story_service.character_dossier_for_background_story(
        pet=_pet(),
        source_flags=source_flags,
        include_story_library=False,
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
    )
    with_profile = background_story_service.character_dossier_for_background_story(
        pet=_pet(),
        source_flags={**source_flags, "characterProfile": True},
        include_story_library=False,
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
    )

    assert "чел с листом вместо лица" not in without_profile
    assert '"description": "чел с листом вместо лица"' in with_profile


def test_background_story_context_sources_policy_controls_dossier(monkeypatch) -> None:
    content = json.dumps(
        {
            "title": "Тихий налет",
            "summary": "На Олега напали.",
            "storyText": "На Олега напали у миски.",
            "eventType": "attack",
            "valence": "negative",
            "tags": [],
            "ragText": "На Олега напали у миски.",
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions(content)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    memory_context = LocalPetMemoryContext.model_validate(
        {
            "summary": "Сергей любит стеклянный шорох.",
            "userProfile": "Сергей собирает лунные листья.",
            "relevantMemories": [
                {
                    "id": "m1",
                    "kind": "user_fact",
                    "text": "Сергей принес листовой амулет.",
                }
            ],
        }
    )
    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(
        background_story_service,
        "context_source_enabled",
        lambda surface, source, *, router_enabled=None, auto_default=False: False,
    )

    background_story_service.generate_background_story(
        pet=_pet(),
        memory_context=memory_context,
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    prompt = _call_by_schema(completions, "background_story")["messages"][1]["content"]
    assert "чел с листом вместо лица" not in prompt
    assert "params" not in prompt
    assert "наевшийся" not in prompt
    assert "Лист на лице стук" not in prompt
    assert "Листики выпускают запахи-сигналы опасности." not in prompt
    assert "стеклянный шорох" not in prompt
    assert "Сергей принес листовой амулет" not in prompt
    assert "Каменная тропа" not in prompt


def test_background_story_auto_sources_use_context_router(monkeypatch) -> None:
    routing_content = json.dumps(
        {
            "sources": {
                "worldContext": {"enabled": True, "query": "лор мира"},
                "characterProfile": {"enabled": False, "query": ""},
                "userMemory": {"enabled": False, "query": ""},
                "chatHistory": {"enabled": False, "query": ""},
                "recentReplies": {"enabled": False, "query": ""},
            },
            "reason": "Нужен только лор мира.",
        },
        ensure_ascii=False,
    )
    story_content = json.dumps(
        {
            "title": "Световая капля",
            "summary": "На Олега напала световая капля.",
            "storyText": "На Олега напала световая капля у тропы.",
            "eventType": "attack",
            "valence": "negative",
            "tags": ["свет"],
            "ragText": "На Олега напала световая капля.",
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions([routing_content, story_content])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    memory_context = LocalPetMemoryContext.model_validate(
        {
            "summary": "Сергей любит стеклянный шорох.",
            "userProfile": "Сергей собирает лунные листья.",
            "relevantMemories": [
                {
                    "id": "m1",
                    "kind": "user_fact",
                    "text": "Сергей принес листовой амулет.",
                }
            ],
        }
    )

    def fake_mode(surface, source):
        if surface == "backgroundStory" and source in {
            "characterProfile",
            "liteOverlay",
            "storyLibrary",
            "storyOverlay",
            "userMemory",
        }:
            return "auto"
        return "disabled"

    def fake_enabled(surface, source, *, router_enabled=None, auto_default=False):
        mode = fake_mode(surface, source)
        if mode == "disabled":
            return False
        if mode == "always":
            return True
        return router_enabled if router_enabled is not None else auto_default

    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(background_story_service, "context_source_mode", fake_mode)
    monkeypatch.setattr(background_story_service, "context_source_enabled", fake_enabled)
    captured_story_queries: list[str | None] = []

    def fake_global_story_briefs(*, pet, query=None):
        captured_story_queries.append(query)
        return [
            {
                "name": "Кристаллическая капля",
                "text": "Капля удерживает чужой свет на тропе.",
            }
        ]

    monkeypatch.setattr(
        background_story_service,
        "_global_story_briefs",
        fake_global_story_briefs,
    )

    result = background_story_service.generate_background_story(
        pet=_pet(),
        memory_context=memory_context,
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    assert result.title == "Световая капля"
    assert len(completions.calls) == 3
    assert completions.calls[0]["response_format"]["json_schema"]["name"] == (
        "background_story_context_routing"
    )
    assert completions.calls[1]["response_format"]["json_schema"]["name"] == "background_story"
    assert completions.calls[2]["response_format"]["json_schema"]["name"] == (
        "background_story_aftermath_extraction"
    )
    prompt = _call_by_schema(completions, "background_story")["messages"][1]["content"]
    assert captured_story_queries == ["лор мира"]
    assert "Кристаллическая капля" in prompt
    assert "Каменная тропа" not in prompt
    assert "Лист на лице стук" not in prompt
    assert "Листики выпускают запахи-сигналы опасности." not in prompt
    assert "Сергей принес листовой амулет" not in prompt


def test_background_story_never_uses_previous_generated_stories(monkeypatch) -> None:
    content = json.dumps(
        {
            "title": "Новая история",
            "summary": "На Олега напали у миски.",
            "storyText": "На Олега напали у миски.",
            "eventType": "attack",
            "valence": "negative",
            "tags": [],
            "ragText": "На Олега напали у миски.",
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions(content)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    def fake_enabled(surface, source, *, router_enabled=None, auto_default=False):
        if surface == "backgroundStory" and source == "storyOverlay":
            return True
        if surface == "backgroundStory" and source == "storyLibrary":
            return False
        return auto_default

    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(background_story_service, "context_source_enabled", fake_enabled)

    background_story_service.generate_background_story(
        pet=_pet(),
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    prompt = _call_by_schema(completions, "background_story")["messages"][1]["content"]
    assert "recentStoryBricks" not in prompt
    assert "Каменная тропа" not in prompt


def test_background_story_uses_recent_events_only_as_anti_repeat(monkeypatch) -> None:
    content = json.dumps(
        {
            "title": "Новая случайность",
            "summary": "Олег споткнулся у миски.",
            "storyText": "Олег споткнулся у миски и поднялся.",
            "eventType": "accident",
            "valence": "mixed",
            "tags": ["случайность"],
            "ragText": "Олег споткнулся у миски.",
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions(content)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(
        background_story_service,
        "context_source_mode",
        lambda surface, source: "disabled",
    )

    background_story_service.generate_background_story(
        pet=_pet(),
        recent_story_events=[
            {
                "summary": "Олег уже споткнулся о мягкий камень у миски.",
                "eventType": "accident",
                "participants": ["Олег"],
                "actions": ["споткнулся"],
                "objects": ["мягкий камень"],
                "location": "миска",
                "outcome": "поднялся сам",
            }
        ],
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    prompt = _call_by_schema(completions, "background_story")["messages"][1]["content"]
    assert "ANTI_REPEAT" in prompt
    assert "Используй список только как запрет на повтор" in prompt
    assert "Олег уже споткнулся о мягкий камень" in prompt


def test_background_story_aftermath_ignores_ephemeral_events(monkeypatch) -> None:
    story_content = json.dumps(
        {
            "title": "Меловая тень",
            "summary": "На Олега напала меловая тень.",
            "storyText": "На Олега напала меловая тень и исчезла.",
            "eventType": "attack",
            "valence": "negative",
            "tags": ["тень"],
            "ragText": "На Олега напала меловая тень.",
        },
        ensure_ascii=False,
    )
    aftermath_content = json.dumps(
        {
            "facts": [
                {
                    "sphere": "world",
                    "kind": "world_fact",
                    "text": "На Олега однажды напала меловая тень.",
                    "pathHint": "lite_overlay.spheres.world",
                    "source": "background_story_aftermath",
                    "confidence": 0.4,
                }
            ],
            "recentEvent": {
                "summary": "На Олега напала меловая тень и исчезла.",
                "eventType": "attack",
                "participants": ["меловая тень", "Олег"],
                "actions": ["нападение"],
                "objects": [],
                "location": "",
                "outcome": "тень исчезла",
            },
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions([story_content, aftermath_content])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(
        background_story_service,
        "context_source_mode",
        lambda surface, source: "disabled",
    )

    result = background_story_service.generate_background_story(
        pet=_pet(),
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    assert result.story_library_patch is None
    assert result.lite_overlay_patch is None
    assert result.recent_story_event is not None
    assert result.recent_story_event["summary"] == "На Олега напала меловая тень и исчезла."


def test_background_story_uses_snapshot_history_when_story_toggles_allow(
    monkeypatch,
) -> None:
    content = json.dumps(
        {
            "title": "Налет из прошлой темы",
            "summary": "На Олега напали после разговора о тропе.",
            "storyText": "На Олега напали у тропы после старого разговора.",
            "eventType": "attack",
            "valence": "negative",
            "tags": ["тропа"],
            "ragText": "На Олега напали у тропы.",
        },
        ensure_ascii=False,
    )
    completions = FakeBackgroundStoryCompletions(content)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    history = [
        LocalChatHistoryItem(role="user", text="Помнишь стеклянную тропу?"),
        LocalChatHistoryItem(role="pet", text="Я уже слышал ее шорох."),
    ]

    def fake_enabled(surface, source, *, router_enabled=None, auto_default=False):
        if surface == "backgroundStory" and source in {"chatHistory", "recentReplies"}:
            return True
        if surface == "backgroundStory" and source in {
            "characterProfile",
            "stateParams",
            "liteOverlay",
            "storyLibrary",
            "storyOverlay",
            "userMemory",
        }:
            return False
        return auto_default

    monkeypatch.setattr(
        background_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=10, openai_chat_reasoning_effort=None),
    )
    monkeypatch.setattr(background_story_service, "context_source_enabled", fake_enabled)

    background_story_service.generate_background_story(
        pet=_pet(),
        history=history,
        recent_replies=["Не буду снова говорить про светляков."],
        now_iso="2026-07-08T07:40:00Z",
        timezone="Europe/Moscow",
        client=client,
        model="test-model",
        timeout=10,
    )

    prompt = _call_by_schema(completions, "background_story")["messages"][1]["content"]
    assert "recentChatHistory" in prompt
    assert "Помнишь стеклянную тропу?" in prompt
    assert "recentReplies" in prompt
    assert "Не буду снова говорить про светляков." in prompt
