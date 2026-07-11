from __future__ import annotations

import json
from types import SimpleNamespace

from app.schemas import LocalPetChatContext
from app.services import full_story_service


def _plan_part(number: int, *, impacts: list[dict] | None = None) -> dict:
    functions = ("inciting_change", "complication", "turn", "resolution")
    return {
        "partNumber": number,
        "narrativeFunction": functions[number - 1],
        "title": f"Событие {number}",
        "summary": f"В части {number} положение заметно меняется.",
        "eventSvo": {
            "subject": "Мяу",
            "verb": "меняет",
            "object": f"положение {number}",
        },
        "event": {
            "beforeState": f"До события {number} цель ещё недоступна.",
            "trigger": f"Возникает препятствие {number}.",
            "protagonistGoal": f"Добиться результата {number}.",
            "oppositionGoal": f"Сохранить ресурс {number} для себя.",
            "opposition": f"Препятствие {number} мешает.",
            "decisiveAction": f"Мяу принимает решение {number}.",
            "result": f"Решение меняет ситуацию {number}.",
            "afterState": f"После события {number} возникает новое положение.",
        },
        "readerHook": f"Что произойдёт после изменения {number}?",
        "carryForward": f"Новое положение {number} сохраняется.",
        "stateChanges": [f"Предмет {number} остаётся у Мяу и не потрачен."],
        "valence": "mixed",
        "statImpacts": impacts or [],
    }


def _story_plan(*, title: str = "Четыре события") -> dict:
    return {
        "overallTitle": title,
        "arcPlan": {
            "goal": "Вернуть общий колокол до грозы.",
            "stakes": "Без колокола жители не услышат предупреждение.",
            "escalation": "Каждое событие меняет доступ к колоколу.",
            "finale": "Мяу возвращает колокол и подаёт сигнал.",
        },
        "parts": [
            _plan_part(
                1,
                impacts=[{"stat": "happiness", "amount": -4, "reason": "Колокол украли."}],
            ),
            _plan_part(2),
            _plan_part(3),
            _plan_part(
                4,
                impacts=[{"stat": "happiness", "amount": 7, "reason": "Сигнал подан."}],
            ),
        ],
    }


def _render(prefix: str = "Я увидела") -> dict:
    return {
        "parts": [
            {
                "partNumber": number,
                "storyParagraphs": [
                    f"{prefix}, как началось событие {number}.",
                    f"Мне помешали, и я сделала решающий выбор {number}.",
                    f"Результат изменил положение {number} и остался важен дальше.",
                ],
            }
            for number in range(1, 5)
        ]
    }


def _plan_verdict(accepted: bool, issue: str = "") -> dict:
    return {
        "accepted": accepted,
        "parts": [
            {
                "partNumber": number,
                "eventful": accepted,
                "understandable": True,
                "interesting": accepted,
                "causal": True,
                "distinct": True,
                "issue": issue if number == 2 else "",
            }
            for number in range(1, 5)
        ],
        "issues": [issue] if issue else [],
        "retryInstruction": "Замени вторую часть самостоятельным событием." if issue else "",
    }


def _quality_verdict(accepted: bool, issue: str = "") -> dict:
    return {
        "accepted": accepted,
        "issues": [issue] if issue else [],
        "retryInstruction": "Покажи центральное событие на сцене." if issue else "",
    }


def _pet() -> LocalPetChatContext:
    return LocalPetChatContext.model_validate(
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


class SequenceCompletions:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(self.responses.pop(0), ensure_ascii=False)
                    )
                )
            ]
        )


def _client(monkeypatch, responses: list[dict]):
    completions = SequenceCompletions(responses)
    monkeypatch.setattr(
        full_story_service,
        "get_settings",
        lambda: SimpleNamespace(
            openai_chat_timeout_seconds=30,
            openai_chat_reasoning_effort=None,
        ),
    )
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_full_story_plans_events_before_rendering(monkeypatch) -> None:
    client, completions = _client(
        monkeypatch,
        [_story_plan(), _plan_verdict(True), _render(), _quality_verdict(True)],
    )

    result = full_story_service.generate_full_story(
        pet=_pet(),
        client=client,
        model="test-model",
        timeout=30,
    )

    assert result.overall_title == "Четыре события"
    assert len(result.parts) == 4
    assert result.parts[0].story_text.startswith("Я увидела")
    assert result.parts[0].stat_impacts[0]["amount"] == -4
    assert [
        call["response_format"]["json_schema"]["name"] for call in completions.calls
    ] == [
        "full_story_plan",
        "full_story_plan_quality_check",
        "full_story_render",
        "full_story_quality_check",
    ]
    plan_prompt = completions.calls[0]["messages"][1]["content"]
    render_prompt = completions.calls[2]["messages"][1]["content"]
    assert '"name": "Мяу"' in plan_prompt
    assert "ритуалы с маленькими предметами" not in plan_prompt
    assert '"rhythm": "короткие фразы"' in plan_prompt
    assert "загадочные рукодельные метафоры" not in plan_prompt
    assert "Не пиши storyParagraphs" in plan_prompt
    assert "совпадение по времени не является причинностью" in completions.calls[0][
        "messages"
    ][0]["content"]
    assert "прямо назови его текущую практическую цель" in completions.calls[0][
        "messages"
    ][0]["content"]
    assert "образуют точный журнал состояния" in completions.calls[0]["messages"][0][
        "content"
    ]
    assert "случайного повтора соседних слов" in completions.calls[2]["messages"][0][
        "content"
    ]
    assert completions.calls[0]["timeout"] == 240.0
    assert '"eventSvo"' in render_prompt
    assert "Рассказ ведёт питомец от первого лица" in completions.calls[2]["messages"][0][
        "content"
    ]


def test_full_story_retries_rejected_plan_before_rendering(monkeypatch) -> None:
    bad_plan = _story_plan(title="Четыре действия")
    good_plan = _story_plan(title="Четыре события")
    client, completions = _client(
        monkeypatch,
        [
            bad_plan,
            _plan_verdict(False, "Вторая часть описывает только переход к мосту."),
            good_plan,
            _plan_verdict(True),
            _render(),
            _quality_verdict(True),
        ],
    )

    result = full_story_service.generate_full_story(
        pet=_pet(), client=client, model="test-model", timeout=30
    )

    assert result.overall_title == "Четыре события"
    assert completions.calls[2]["response_format"]["json_schema"]["name"] == (
        "full_story_plan"
    )
    assert "PLAN_RETRY" in completions.calls[2]["messages"][1]["content"]


def test_full_story_allows_three_plan_attempts(monkeypatch) -> None:
    client, completions = _client(
        monkeypatch,
        [
            _story_plan(title="Слабый план 1"),
            _plan_verdict(False, "Вторая часть — подготовка."),
            _story_plan(title="Слабый план 2"),
            _plan_verdict(False, "Третья часть — наблюдение."),
            _story_plan(title="Четыре события"),
            _plan_verdict(True),
            _render(),
            _quality_verdict(True),
        ],
    )

    result = full_story_service.generate_full_story(
        pet=_pet(), client=client, model="test-model", timeout=30
    )

    assert result.overall_title == "Четыре события"
    assert completions.calls[4]["response_format"]["json_schema"]["name"] == (
        "full_story_plan"
    )
    assert "PREVIOUS_PLAN" in completions.calls[4]["messages"][1]["content"]


def test_full_story_retries_prose_without_changing_plan(monkeypatch) -> None:
    client, completions = _client(
        monkeypatch,
        [
            _story_plan(),
            _plan_verdict(True),
            _render(prefix="Я долго шла"),
            _quality_verdict(False, "Вторая часть пересказывает путь вместо события."),
            _render(prefix="Я увидела"),
            _quality_verdict(True),
        ],
    )

    result = full_story_service.generate_full_story(
        pet=_pet(), client=client, model="test-model", timeout=30
    )

    assert result.parts[0].story_text.startswith("Я увидела")
    assert completions.calls[4]["response_format"]["json_schema"]["name"] == (
        "full_story_render"
    )
    assert "RENDER_RETRY" in completions.calls[4]["messages"][1]["content"]
    assert '"eventSvo"' in completions.calls[4]["messages"][1]["content"]


def test_full_story_limits_and_normalizes_stat_impacts() -> None:
    plan = _story_plan()
    plan["parts"][0]["statImpacts"] = [
        {"stat": "energy", "amount": -20, "reason": "Тяжёлый вред."},
        {"stat": "happiness", "amount": 5, "reason": "Лишнее изменение."},
    ]
    plan["parts"][1]["statImpacts"] = [
        {"stat": "hunger", "amount": -3, "reason": "Пропущена еда."}
    ]
    plan["parts"][2]["statImpacts"] = [
        {"stat": "happiness", "amount": -4, "reason": "Сильная потеря."}
    ]
    plan["parts"][3]["statImpacts"] = [
        {"stat": "happiness", "amount": 8, "reason": "Финальная радость."}
    ]

    _, _, parts = full_story_service._normalize_payload(plan, _render())

    assert parts[0].stat_impacts[0]["amount"] == -15
    assert sum(len(part.stat_impacts) for part in parts) == 3
    assert parts[3].stat_impacts == ()
    stat_schema = full_story_service.FULL_STORY_PLAN_SCHEMA["properties"]["parts"][
        "items"
    ]["properties"]["statImpacts"]
    assert stat_schema["minItems"] == 0
    assert stat_schema["maxItems"] == 1


def test_full_story_plan_prompt_forbids_reusing_previous_arc(monkeypatch) -> None:
    client, completions = _client(
        monkeypatch,
        [_story_plan(), _plan_verdict(True), _render(), _quality_verdict(True)],
    )

    full_story_service.generate_full_story(
        pet=_pet(),
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
        client=client,
        model="test-model",
        timeout=30,
    )

    prompt = completions.calls[0]["messages"][1]["content"]
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
