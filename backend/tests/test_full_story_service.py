from __future__ import annotations

import json
from types import SimpleNamespace

from app.schemas import LocalPetChatContext
from app.services import full_story_service


def _part(number: int, *, valence: str, impacts: list[dict]) -> dict:
    return {
        "partNumber": number,
        "title": f"Часть {number}",
        "summary": f"Событие части {number}.",
        "storyParagraphs": [
            f"В части {number} начинается конкретное происшествие.",
            "Герои действуют вместе и меняют ситуацию.",
            "Результат прямо ведёт к следующей части.",
        ],
        "valence": valence,
        "statImpacts": impacts,
    }


def test_full_story_generates_four_linked_parts_with_impacts(monkeypatch) -> None:
    payload = {
        "overallTitle": "Лекарство до снегопада",
        "arcPlan": {
            "goal": "Доставить лекарства.",
            "stakes": "Перевал закроется ночью.",
            "escalation": "Телега ломается, река поднимается, груз теряется.",
            "finale": "Лекарства доставлены в посёлок.",
        },
        "parts": [
            _part(
                1,
                valence="mixed",
                impacts=[
                    {"stat": "energy", "amount": -8, "reason": "Переносила груз."},
                    {"stat": "happiness", "amount": 5, "reason": "Ей доверились."},
                ],
            ),
            _part(
                2,
                valence="negative",
                impacts=[{"stat": "hunger", "amount": -7, "reason": "Пропустила обед."}],
            ),
            _part(
                3,
                valence="mixed",
                impacts=[
                    {"stat": "energy", "amount": -11, "reason": "Поднялась по склону."},
                    {"stat": "happiness", "amount": 8, "reason": "Спасла груз."},
                ],
            ),
            _part(
                4,
                valence="positive",
                impacts=[{"stat": "hunger", "amount": 15, "reason": "Получила ужин."}],
            ),
        ],
    }
    calls: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    monkeypatch.setattr(
        full_story_service,
        "get_settings",
        lambda: SimpleNamespace(
            openai_chat_timeout_seconds=30,
            openai_chat_reasoning_effort=None,
        ),
    )
    pet = LocalPetChatContext.model_validate(
        {
            "name": "Мяу",
            "description": "кошка-волшебница",
            "stage": "teen",
            "mood": "idle",
            "stats": {"hunger": 60, "happiness": 50, "energy": 70},
            "characterBible": {
                "identity": {"name": "Мяу", "species": "кошка-волшебница"},
                "genesis": {
                    "character_trait": "смелая",
                    "story_engine": "ритуалы с маленькими предметами",
                },
            },
        }
    )

    result = full_story_service.generate_full_story(
        pet=pet,
        client=client,
        model="test-model",
        timeout=30,
    )

    assert result.overall_title == "Лекарство до снегопада"
    assert len(result.parts) == 4
    assert result.story_direction["plotMode"]
    assert result.parts[0].stat_impacts[0]["amount"] == -8
    assert result.parts[3].stat_impacts[0]["amount"] == 15
    request = calls[0]
    assert request["response_format"]["json_schema"]["name"] == "full_story"
    prompt = request["messages"][1]["content"]
    assert '"name": "Мяу"' in prompt
    assert "ритуалы с маленькими предметами" not in prompt
    assert '"hunger": 60' in prompt
    assert "STORY_DIRECTION" in prompt
    assert "ANTI_REPEAT" in prompt


def test_full_story_prompt_forbids_reusing_previous_arc(monkeypatch) -> None:
    captured: list[dict] = []
    payload = {
        "overallTitle": "Новый спор",
        "arcPlan": {
            "goal": "Уладить спор.",
            "stakes": "Стороны разойдутся.",
            "escalation": "Переговоры заходят в тупик.",
            "finale": "Стороны договариваются.",
        },
        "parts": [
            _part(
                number,
                valence="positive",
                impacts=[{"stat": "happiness", "amount": 1, "reason": "Есть прогресс."}],
            )
            for number in range(1, 5)
        ],
    }

    class Completions:
        def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
            )

    monkeypatch.setattr(
        full_story_service,
        "get_settings",
        lambda: SimpleNamespace(openai_chat_timeout_seconds=30),
    )
    pet = LocalPetChatContext.model_validate(
        {
            "name": "Мяу",
            "description": "кошка",
            "stage": "teen",
            "mood": "idle",
            "stats": {"hunger": 60, "happiness": 50, "energy": 70},
        }
    )

    full_story_service.generate_full_story(
        pet=pet,
        recent_full_stories=[
            {
                "overallTitle": "Один день холодящего мёда",
                "goal": "Доставить лекарство до заката.",
                "plotMode": "rescue_or_help",
                "incidentClass": "rescue_or_aid",
                "settingClass": "road_or_crossing",
                "resolutionMode": "journey_or_relocation",
            }
        ],
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        model="test-model",
        timeout=30,
    )

    prompt = captured[0]["messages"][1]["content"]
    assert "Один день холодящего мёда" in prompt
    assert "Доставить лекарство до заката" in prompt
    assert "только как запрет на повтор" in prompt
    assert "valenceTarget задаёт общий эмоциональный итог всей арки" in prompt
