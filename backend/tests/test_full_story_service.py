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
                "voice": {
                    "voice_rules": ["вставляет загадочные рукодельные метафоры"],
                    "sentence_rhythm": "короткие фразы",
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
    assert sum(len(part.stat_impacts) for part in result.parts) == 3
    assert result.parts[3].stat_impacts == ()
    request = calls[0]
    assert request["response_format"]["json_schema"]["name"] == "full_story"
    prompt = request["messages"][1]["content"]
    assert '"name": "Мяу"' in prompt
    assert "ритуалы с маленькими предметами" not in prompt
    assert '"rhythm": "короткие фразы"' in prompt
    assert "загадочные рукодельные метафоры" not in prompt
    assert (
        "весь видимый текст storyParagraphs рассказывает сам питомец от первого лица"
        in request["messages"][0]["content"]
    )
    assert "Центральное действие содержит не больше двух причинных шагов" in request[
        "messages"
    ][0]["content"]
    assert '"hunger": 60' in prompt
    assert "STORY_DIRECTION" in prompt
    assert "ANTI_REPEAT" in prompt


def test_full_story_allows_empty_impacts_and_does_not_bind_sign_to_valence() -> None:
    payload = {
        "overallTitle": "Тихий день",
        "arcPlan": {
            "goal": "Переждать дождь.",
            "stakes": "Тропа размыта.",
            "escalation": "Вода подбирается к укрытию.",
            "finale": "Дождь стихает.",
        },
        "parts": [
            _part(number, valence="positive", impacts=[])
            for number in range(1, 4)
        ]
        + [
            _part(
                4,
                valence="positive",
                impacts=[{"stat": "energy", "amount": -3, "reason": "Я долго гребла."}],
            )
        ],
    }

    _, _, parts = full_story_service._normalize_payload(payload)

    assert parts[0].stat_impacts == ()
    assert parts[3].stat_impacts[0]["amount"] == -3
    stat_schema = full_story_service.FULL_STORY_SCHEMA["properties"]["parts"]["items"][
        "properties"
    ]["statImpacts"]
    assert stat_schema["minItems"] == 0
    assert stat_schema["maxItems"] == 1


def test_full_story_retries_once_after_style_rejection(monkeypatch) -> None:
    first = {
        "overallTitle": "Испытание",
        "arcPlan": {
            "goal": "Пройти мост.",
            "stakes": "Другого выхода нет.",
            "escalation": "Начинается проверка.",
            "finale": "Герой побеждает.",
        },
        "parts": [_part(number, valence="positive", impacts=[]) for number in range(1, 5)],
    }
    repaired = {
        **first,
        "overallTitle": "Мост после дождя",
        "parts": [
            {
                **_part(number, valence="positive", impacts=[]),
                "storyParagraphs": [
                    "Я упёрлась лапами в мокрую доску.",
                    "Я сдвинула её к целой балке и прижала камнем.",
                    "Доска перестала качаться, и я перешла ручей.",
                ],
            }
            for number in range(1, 5)
        ],
    }
    responses = [
        first,
        {
            "accepted": False,
            "issues": ["Текст пересказывает проверку и победу вместо действий."],
            "retryInstruction": "Покажи действия от первого лица.",
        },
        repaired,
        {"accepted": True, "issues": [], "retryInstruction": ""},
    ]
    calls: list[dict] = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(responses.pop(0), ensure_ascii=False)
                        )
                    )
                ]
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

    result = full_story_service.generate_full_story(
        pet=pet,
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        model="test-model",
        timeout=30,
    )

    assert result.overall_title == "Мост после дождя"
    assert result.parts[0].story_text.startswith("Я упёрлась")
    assert calls[2]["response_format"]["json_schema"]["name"] == "full_story"
    assert "QUALITY_RETRY" in calls[2]["messages"][1]["content"]


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
        day_context={
            "localDate": "2026-07-12",
            "timezone": "Europe/Moscow",
            "parts": [
                {
                    "partNumber": 1,
                    "scheduledLocalTime": "09:00",
                    "dayPeriod": "утро",
                }
            ],
        },
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
        model="test-model",
        timeout=30,
    )

    prompt = captured[0]["messages"][1]["content"]
    assert "Один день холодящего мёда" in prompt
    assert "Доставить лекарство до заката" in prompt
    assert "только как запрет на повтор" in prompt
    assert "valenceTarget задаёт общий эмоциональный итог всей арки" in prompt
    assert '"scheduledLocalTime": "09:00"' in prompt
    assert '"dayPeriod": "утро"' in prompt


def test_full_story_part_image_receives_soft_local_time_context(monkeypatch) -> None:
    captured: dict = {}

    def fake_generate_background_story_image_bytes(**kwargs):
        captured.update(kwargs)
        return b"png"

    monkeypatch.setattr(
        full_story_service,
        "generate_background_story_image_bytes",
        fake_generate_background_story_image_bytes,
    )

    result = full_story_service.generate_full_story_part_image_bytes(
        pet=SimpleNamespace(),
        overall_title="Один длинный день",
        part={
            "title": "Первая встреча",
            "summary": "Герой вышел на площадь.",
            "storyText": "На площади начался спор.",
            "valence": "mixed",
            "scheduledLocalTime": "21:00",
            "dayPeriod": "ночь",
        },
    )

    assert result == b"png"
    assert "ночь, локальное время 21:00" in captured["story"].summary
    assert "для интерьера не добавляй внешнее время" in captured["story"].summary
