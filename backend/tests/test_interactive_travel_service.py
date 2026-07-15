from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas import InteractiveTravelResult, LocalPetChatContext
from app.services import interactive_travel_service

TEST_FACT = interactive_travel_service.FUN_FACTS[0]


def _pet() -> LocalPetChatContext:
    return LocalPetChatContext.model_validate(
        {
            "name": "Мяу",
            "description": "смелая кошка",
            "stage": "teen",
            "mood": "idle",
            "stats": {"hunger": 70, "happiness": 60, "energy": 80},
            "characterBible": {
                "identity": {"name": "Мяу", "species": "кошка"},
                "genesis": {"character_trait": "смелая", "does": ["исследует"]},
                "visual": {
                    "proportions": "маленькая кошка",
                    "growth_forms": {"teen": "кошка-подросток"},
                },
                "voice": {"sentence_rhythm": "короткие фразы"},
            },
        }
    )


class SequenceCompletions:
    def __init__(self, responses: list[dict[str, Any] | str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        response = self.responses.pop(0)
        content = (
            response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content),
                    finish_reason="stop",
                )
            ],
            model="test-model",
            usage=None,
        )


def _client(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any] | str],
) -> tuple[Any, SequenceCompletions]:
    completions = SequenceCompletions(responses)
    monkeypatch.setattr(
        interactive_travel_service,
        "get_settings",
        lambda: SimpleNamespace(
            full_story_model="test-model",
            openai_chat_model="test-model",
            openai_chat_timeout_seconds=30,
        ),
    )
    monkeypatch.setattr(interactive_travel_service.random, "choice", lambda values: values[0])
    monkeypatch.setattr(interactive_travel_service.random, "randint", lambda start, end: 3)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def _start_payload() -> dict[str, Any]:
    return {
        "title": "Ночная тропа",
        "goal": "Добраться до старой обсерватории и зажечь её сигнальный огонь",
        "ending": (
            "Я зажигаю сигнальный огонь, достигаю своей цели и могу возвращаться домой"
        ),
        "parts": [
            {
                "situation": (
                    "Я прихожу к развилке, где оживает старый фонарь и просит найти север. "
                    "В Северном полушарии Полярная звезда показывает направление на север."
                ),
                "question": "Куда мне идти?",
                "choices": [
                    "Найти Полярную звезду",
                    "Спросить фонарь",
                    "Подбросить монетку",
                ],
            },
            {
                "situation": (
                    "Я прихожу к мосту через реку. Подъёмный механизм заело, и путь закрыт."
                ),
                "question": "Как мне опустить мост?",
                "choices": ["Повернуть колесо", "Позвать смотрителя", "Переплыть реку"],
            },
            {
                "situation": (
                    "Я добираюсь до обсерватории. Каменная дверь не открывается без противовеса."
                ),
                "question": "Как мне открыть дверь?",
                "choices": ["Нажать на плиту", "Подвинуть камень", "Позвать хранителя"],
            },
            {
                "situation": (
                    "Я вхожу под купол обсерватории. Сигнальный огонь погас, а заслонку заклинило."
                ),
                "question": "Как мне зажечь огонь?",
                "choices": ["Поднять заслонку", "Найти рычаг", "Починить механизм"],
            },
        ],
    }


def _result_payload(
    number: int,
    *,
    outcome: str = "positive",
    stat: str = "none",
    amount: int = 0,
) -> dict[str, Any]:
    return {
        "result": f"Я выполняю выбранное действие и прохожу испытание {number}.",
        "outcome": outcome,
        "stat": stat,
        "amount": amount,
        "reason": "Выбор повлиял на моё состояние",
    }


def _legacy_continue_payload(number: int) -> dict[str, Any]:
    return {
        **_result_payload(number),
        "nextSituation": f"На тропе появляется говорящий мост {number}.",
        "nextQuestion": "Как перейти мост?",
        "nextChoice1": "Попросить мост пропустить меня",
        "nextChoice2": "Перепрыгнуть ручей",
        "nextChoice3": "Найти другую тропу",
    }


def _final_payload() -> dict[str, Any]:
    return {
        "result": "Я поднимаю заслонку, и огонь вспыхивает над куполом.",
        "outcome": "positive",
        "stat": "happiness",
        "amount": 5,
        "reason": "Я рад завершить приключение",
    }


def _start(monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, Any] | str]) -> Any:
    client, _ = _client(monkeypatch, responses)
    response = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в ночной лес",
        client=client,
        model="test-model",
    )
    return response.travel, client


def test_suggestions_do_not_call_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    client, completions = _client(monkeypatch, [])
    monkeypatch.setattr(
        interactive_travel_service.random,
        "sample",
        lambda values, count: list(values[:count]),
    )

    response = interactive_travel_service.generate_interactive_travel_suggestions(
        pet=_pet(),
        client=client,
        model="test-model",
        include_debug=True,
    )

    assert response.destinations == ["в подземелье", "на болото", "в лес"]
    assert response.debug is not None
    assert response.debug.promptDebug == []
    assert completions.calls == []


def test_start_uses_minimal_state_and_keeps_naturally_embedded_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions = _client(monkeypatch, [_start_payload()])

    response = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="  в ночной лес  ",
        client=client,
        model="test-model",
        include_debug=True,
    )

    travel = response.travel
    assert travel.destination == "в ночной лес"
    assert travel.overallTitle == "Ночная тропа"
    assert travel.arcPlan["generatorVersion"] == interactive_travel_service.GENERATOR_VERSION
    assert travel.arcPlan["funFact"] == TEST_FACT
    assert travel.arcPlan["storyGoal"] == (
        "Добраться до старой обсерватории и зажечь её сигнальный огонь."
    )
    assert travel.arcPlan["storyEnding"] == (
        "Я зажигаю сигнальный огонь, достигаю своей цели и могу возвращаться домой."
    )
    assert travel.arcPlan["part2Situation"] == (
        "Я прихожу к мосту через реку. Подъёмный механизм заело, и путь закрыт."
    )
    assert travel.arcPlan["part3Situation"].startswith("Я добираюсь до обсерватории")
    assert travel.arcPlan["part4Situation"].startswith("Я вхожу под купол")
    assert len(travel.arcPlan) == 19
    assert travel.completed is False
    assert travel.outcomeValence is None
    assert len(travel.parts) == 1
    part = travel.parts[0]
    assert part.storyText.count(TEST_FACT) == 1
    assert "Фанфакт:" not in part.storyText
    assert part.challenge == "Куда мне идти?"
    assert part.actionSuggestions == [
        "Найти Полярную звезду",
        "Спросить фонарь",
        "Подбросить монетку",
    ]
    assert part.answer is None
    assert part.result is None
    assert response.debug is not None

    call = completions.calls[0]
    assert len(call["messages"]) == 2
    assert "ровно из четырёх частей" in call["messages"][1]["content"]
    assert "ясную цель" in call["messages"][1]["content"]
    assert "отдельной развязке ending" in call["messages"][1]["content"]
    assert TEST_FACT in call["messages"][1]["content"]
    assert "только в часть 1" in call["messages"][1]["content"]
    assert "заранее написанную часть" in call["messages"][1]["content"]
    schema = call["response_format"]["json_schema"]
    assert schema["name"] == "interactive_travel_fixed_story_v2"
    assert schema["schema"] == interactive_travel_service.START_SCHEMA
    assert schema["schema"]["properties"]["parts"]["minItems"] == 4
    assert schema["schema"]["properties"]["parts"]["maxItems"] == 4


def test_start_retries_invalid_json_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client, completions = _client(monkeypatch, ["not-json", _start_payload()])

    response = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в лес",
        client=client,
        model="test-model",
    )

    assert response.travel.parts[0].storyText
    assert len(completions.calls) == 2
    assert len(completions.calls[1]["messages"]) == 3
    assert completions.calls[0]["response_format"] == completions.calls[1]["response_format"]


def test_start_retries_incomplete_four_part_plan_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incomplete = _start_payload()
    incomplete["parts"][3]["situation"] = ""
    client, completions = _client(monkeypatch, [incomplete, _start_payload()])

    travel = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в лес",
        client=client,
        model="test-model",
    ).travel

    assert travel.arcPlan["part4Situation"].startswith("Я вхожу под купол")
    assert len(completions.calls) == 2
    assert completions.calls[1]["messages"][-1]["content"] == (
        "Верни только корректный JSON по исходной схеме."
    )


def test_long_destination_keeps_intro_inside_api_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _client(monkeypatch, [_start_payload()])

    travel = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в " + "очень далёкое место " * 30,
        client=client,
        model="test-model",
    ).travel

    assert travel.introReaction is not None
    assert len(travel.introReaction.text) <= 220


def test_continue_resolves_choice_and_adds_next_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    travel, client = _start(
        monkeypatch,
        [_start_payload(), _result_payload(1, outcome="negative", stat="energy", amount=-7)],
    )

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="Спросить фонарь",
        client=client,
        model="test-model",
    )

    next_travel = response.travel
    assert next_travel.completed is False
    assert len(next_travel.parts) == 2
    resolved, pending = next_travel.parts
    assert resolved.answer == "Спросить фонарь"
    assert resolved.result is not None
    assert resolved.result.outcomeValence == "negative"
    assert resolved.result.statImpacts[0].stat == "energy"
    assert resolved.result.statImpacts[0].amount == -7
    assert pending.result is None
    assert pending.partNumber == 2
    assert pending.transition is not None
    assert pending.transition.elapsedHours == 3
    assert pending.transition.summary == resolved.result.consequence
    assert pending.transition.departureHook == "Я продолжаю путь. Проходит 3 часа."
    assert pending.storyText == (
        "Я прихожу к мосту через реку. Подъёмный механизм заело, и путь закрыт."
    )
    assert pending.challenge == "Как мне опустить мост?"
    assert pending.actionSuggestions == [
        "Повернуть колесо",
        "Позвать смотрителя",
        "Переплыть реку",
    ]
    assert all(len(choice.split()) <= 3 for choice in pending.actionSuggestions)
    assert next_travel.arcPlan == travel.arcPlan
    result_call = client.chat.completions.calls[1]
    result_schema = result_call["response_format"]["json_schema"]
    assert result_schema["name"] == "interactive_travel_part_result_fixed_v1"
    assert "nextSituation" not in result_schema["schema"]["properties"]
    assert travel.arcPlan["part2Situation"] in result_call["messages"][1]["content"]


def test_choices_are_limited_to_three_words() -> None:
    choices = interactive_travel_service._choices(
        [
            "Осторожно подойти к старому мосту",
            "Очень громко позвать лесного сторожа",
            "Быстро убежать по дальней тропе",
        ]
    )

    assert choices == [
        "Осторожно подойти",
        "Очень громко позвать",
        "Быстро убежать",
    ]
    assert interactive_travel_service._choices(["Идти по северной тропе"])[0] == (
        "Идти по тропе"
    )
    assert interactive_travel_service.CHOICE_SCHEMA["maxLength"] == 40


def test_long_result_fits_transition_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _result_payload(1) | {"result": "Длинный результат " * 30}
    travel, client = _start(monkeypatch, [_start_payload(), payload])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="Идти дальше",
        client=client,
        model="test-model",
    )

    transition = response.travel.parts[-1].transition
    assert transition is not None
    assert len(transition.summary) <= 240
    assert interactive_travel_service.RESULT_PROPERTIES["result"]["maxLength"] == 240


def test_story_always_finishes_after_four_parts_with_saved_ending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions = _client(
        monkeypatch,
        [
            _start_payload(),
            _result_payload(1),
            _result_payload(2),
            _result_payload(3),
            _final_payload(),
        ],
    )
    travel = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в ночной лес",
        client=client,
        model="test-model",
    ).travel

    for advice in (
        "Найти Полярную звезду",
        "Повернуть колесо",
        "Нажать на плиту",
        "Поднять заслонку",
    ):
        travel = interactive_travel_service.continue_interactive_travel(
            pet=_pet(),
            travel=travel,
            advice=advice,
            client=client,
            model="test-model",
        ).travel

    assert travel.completed is True
    assert travel.outcomeValence == "positive"
    assert len(travel.parts) == 4
    assert all(part.result is not None for part in travel.parts)
    assert [part.transition.elapsedHours for part in travel.parts[1:]] == [3, 3, 3]
    assert travel.parts[1].storyText.startswith("Я прихожу к мосту")
    assert travel.parts[2].storyText.startswith("Я добираюсь до обсерватории")
    assert travel.parts[3].storyText.startswith("Я вхожу под купол")
    visible_story = " ".join(part.storyText for part in travel.parts)
    assert visible_story.count(TEST_FACT) == 1
    assert len(completions.calls) == 5
    final_result = travel.parts[-1].result
    assert final_result is not None
    ending = travel.arcPlan["storyEnding"]
    assert final_result.text.endswith(ending)
    assert final_result.text.count(ending) == 1
    assert ending not in final_result.consequence
    final_call = completions.calls[-1]
    assert final_call["response_format"]["json_schema"]["name"] == (
        "interactive_travel_part_result_fixed_v1"
    )
    assert travel.arcPlan["storyGoal"] in final_call["messages"][1]["content"]
    assert travel.arcPlan["storyEnding"] in final_call["messages"][1]["content"]
    assert "сервер добавит её следом" in final_call["messages"][1]["content"]


@pytest.mark.parametrize(
    "arc_plan",
    [
        {},
        {"funFact": TEST_FACT},
        {
            "contractVersion": "4",
            "goal": "раскрыть тайну деревни",
            "targetState": "тайна ещё скрыта",
        },
    ],
)
def test_continue_ignores_old_goal_contracts(
    monkeypatch: pytest.MonkeyPatch,
    arc_plan: dict[str, str],
) -> None:
    travel, client = _start(monkeypatch, [_start_payload(), _legacy_continue_payload(1)])
    travel.arcPlan = arc_plan

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="Идти дальше",
        client=client,
        model="test-model",
    )

    assert response.travel.completed is False
    assert response.travel.arcPlan == arc_plan


def test_completed_story_is_rejected_before_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    client, completions = _client(
        monkeypatch,
        [
            _start_payload(),
            _result_payload(1),
            _result_payload(2),
            _result_payload(3),
            _final_payload(),
        ],
    )
    travel = interactive_travel_service.start_interactive_travel(
        pet=_pet(), destination="в лес", client=client, model="test-model"
    ).travel
    for advice in ("Первое", "Второе", "Третье", "Четвёртое"):
        travel = interactive_travel_service.continue_interactive_travel(
            pet=_pet(), travel=travel, advice=advice, client=client, model="test-model"
        ).travel
    call_count = len(completions.calls)

    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="INTERACTIVE_TRAVEL_ALREADY_COMPLETED",
    ):
        interactive_travel_service.continue_interactive_travel(
            pet=_pet(), travel=travel, advice="Ещё", client=client, model="test-model"
        )

    assert len(completions.calls) == call_count


def test_resolved_pending_tail_is_rejected_before_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions = _client(monkeypatch, [_start_payload()])
    travel = interactive_travel_service.start_interactive_travel(
        pet=_pet(), destination="в лес", client=client, model="test-model"
    ).travel
    travel.parts[-1].answer = "Готово"
    travel.parts[-1].result = InteractiveTravelResult(
        text="Готово.",
        adviceAssessment="helpful",
        reaction="Продолжаю.",
        reactionTone="determined",
        consequence="Готово.",
        outcomeValence="positive",
        statImpacts=[],
    )

    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="INTERACTIVE_TRAVEL_PENDING_PART_MISSING",
    ):
        interactive_travel_service.continue_interactive_travel(
            pet=_pet(), travel=travel, advice="Ещё", client=client, model="test-model"
        )

    assert len(completions.calls) == 1


def test_invalid_tie_break_is_rejected_before_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    client, completions = _client(monkeypatch, [_start_payload()])
    travel = interactive_travel_service.start_interactive_travel(
        pet=_pet(), destination="в лес", client=client, model="test-model"
    ).travel

    with pytest.raises(ValueError, match="tie_break_valence"):
        interactive_travel_service.continue_interactive_travel(
            pet=_pet(),
            travel=travel,
            advice="Ещё",
            client=client,
            model="test-model",
            tie_break_valence="neutral",
        )

    assert len(completions.calls) == 1


def test_media_helpers_keep_public_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    pet = _pet()
    image_calls: list[dict[str, Any]] = []
    video_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        interactive_travel_service,
        "generate_interactive_travel_part_image",
        lambda **kwargs: image_calls.append(kwargs) or "/media/part.png",
    )
    monkeypatch.setattr(
        interactive_travel_service,
        "generate_interactive_travel_part_video",
        lambda **kwargs: video_calls.append(kwargs) or "/media/part.mp4",
    )

    image = interactive_travel_service.illustrate_interactive_travel_part(
        pet=pet,
        travel_id="interactive-travel-test",
        destination="в лес",
        part_number=2,
        title="Часть 2",
        story_text="История.",
    )
    video = interactive_travel_service.animate_interactive_travel_part(
        travel_id="interactive-travel-test",
        part_number=2,
    )

    assert image.imageUrl == "/media/part.png"
    assert video.videoUrl == "/media/part.mp4"
    assert image_calls == [
        {
            "pet": pet,
            "travel_id": "interactive-travel-test",
            "destination": "в лес",
            "part_number": 2,
            "title": "Часть 2",
            "story_text": "История.",
        }
    ]
    assert video_calls == [{"travel_id": "interactive-travel-test", "part_number": 2}]
