from __future__ import annotations

import json
from types import SimpleNamespace

from app.schemas import LocalPetChatContext
from app.services import background_story_service


class FakeBackgroundStoryCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


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
                "cleanliness": 80,
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
                    }
                },
            },
        }
    )


def test_generate_background_story_creates_events_story_patch(monkeypatch) -> None:
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
    completions = FakeBackgroundStoryCompletions(content)
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
    brick = result.story_library_patch["bricks"][0]
    assert brick["pool"] == "events"
    assert brick["poolLabel"] == "Фоновые события"
    assert brick["attributes"]["generatedBy"] == "background_story"
    assert brick["attributes"]["fullStory"].startswith("У лесной миски")
    request = completions.calls[0]
    assert request["response_format"]["json_schema"]["name"] == "background_story"
    assert "чел с листом вместо лица" in request["messages"][1]["content"]
    assert (
        "Листики выпускают запахи-сигналы опасности."
        in request["messages"][1]["content"]
    )
