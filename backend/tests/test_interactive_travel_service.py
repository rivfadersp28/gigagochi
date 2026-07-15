from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas import InteractiveTravelResult, LocalPetChatContext
from app.services import interactive_travel_service


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
    monkeypatch.setattr(
        interactive_travel_service,
        "_task_bank",
        lambda: tuple(_test_story_tasks()),
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def _start_payload() -> dict[str, Any]:
    return {"fantasySetup": "У старого дуба меня встречает хранитель тропы"}


def _test_story_tasks() -> list[dict[str, Any]]:
    return [
        {
            "id": "test-1",
            "subject": "nature",
            "situation": "Ночью нужно определить направление на север по звёздам.",
            "question": "Что укажет север?",
            "choices": [
                "Полярная звезда",
                "Полная луна",
                "Венера",
                "Облако",
            ],
            "answer": "Полярная звезда",
            "explanation": "Полярная звезда показывает направление на север",
        },
        {
            "id": "test-2",
            "subject": "physics",
            "situation": "Для подъёма груза нужно выбрать подходящий простой механизм.",
            "question": "Что поможет поднять груз?",
            "choices": ["Рычаг", "Компас", "Песок", "Факел"],
            "answer": "Рычаг",
            "explanation": "Рычаг позволяет получить выигрыш в силе",
        },
        {
            "id": "test-3",
            "subject": "logic",
            "situation": "Перед путником четыре двери, но только одна ведёт наружу.",
            "question": "Какую дверь проверить?",
            "choices": ["Первую", "Вторую", "Третью", "Четвёртую"],
            "answer": "Третью",
            "explanation": "Подсказка однозначно указывает на третью дверь",
        },
        {
            "id": "test-4",
            "subject": "physics",
            "situation": "Для огня нужно обеспечить постоянный приток воздуха.",
            "question": "Что открыть для огня?",
            "choices": ["Заслонку", "Сундук", "Книгу", "Колокол"],
            "answer": "Заслонку",
            "explanation": "Открытая заслонка пропускает воздух к огню",
        },
    ]


def _result_payload(
    number: int,
    *,
    outcome: str = "positive",
    stat: str = "none",
    amount: int = 0,
) -> dict[str, Any]:
    payload = {
        "result": f"Я выполняю выбранное действие и прохожу испытание {number}.",
        "outcome": outcome,
        "stat": stat,
        "amount": amount,
        "reason": "Выбор повлиял на моё состояние",
    }
    if number < 4:
        payload["fantasySetup"] = (
            "У лесного ручья меня останавливает речной дух"
            if number == 1
            else "На поляне я встречаю каменного великана"
            if number == 2
            else "У выхода из леса меня ждёт огненная птица"
        )
    return payload


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


def test_start_builds_only_first_erudition_episode(
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
    assert travel.overallTitle == "Путешествие в ночной лес"
    assert travel.arcPlan["generatorVersion"] == interactive_travel_service.GENERATOR_VERSION
    assert travel.arcPlan["part1CorrectChoice"] == "Полярная звезда"
    assert travel.arcPlan["part1Subject"] == "nature"
    assert travel.arcPlan["part1Explanation"].startswith("Полярная звезда")
    assert travel.arcPlan["taskBankIds"] == "test-1"
    assert len(travel.arcPlan) == 5
    assert travel.completed is False
    assert travel.outcomeValence is None
    assert len(travel.parts) == 1
    part = travel.parts[0]
    assert part.storyText == (
        "У старого дуба меня встречает хранитель тропы. "
        "Ночью нужно определить направление на север по звёздам."
    )
    assert part.challenge == "Что укажет север?"
    assert part.actionSuggestions == [
        "Полярная звезда",
        "Полная луна",
        "Венера",
        "Облако",
    ]
    assert part.answer is None
    assert part.result is None
    assert response.debug is not None

    call = completions.calls[0]
    assert len(call["messages"]) == 2
    assert "Локация: в ночной лес" in call["messages"][1]["content"]
    assert "Условие: Ночью нужно определить направление" in call["messages"][1]["content"]
    assert "Правильный ответ" not in call["messages"][1]["content"]
    assert "Объяснение" not in call["messages"][1]["content"]
    assert "персонаж пользователя" not in call["messages"][1]["content"]
    assert "Мяу" not in call["messages"][1]["content"]
    assert "кошка" not in call["messages"][1]["content"]
    assert "ночной лес" in call["messages"][1]["content"]
    schema = call["response_format"]["json_schema"]
    assert schema["name"] == "interactive_travel_task_bank_location_sequential_v3"
    assert schema["schema"] == interactive_travel_service.START_SCHEMA
    assert list(schema["schema"]["properties"]) == ["fantasySetup"]


def test_task_bank_condition_is_not_repeated_inside_direct_question() -> None:
    task = _test_story_tasks()[0] | {
        "situation": "На воротах появилась цветная корочка.",
        "question": "Кто образует лишайник?",
    }

    part = interactive_travel_service._part_from_task(
        task=task,
        fantasy_setup="У ворот меня встречает страж",
        part_number=1,
    )

    assert part.challenge == "Кто образует лишайник?"


def test_task_bank_is_deduplicated_and_has_enough_tasks() -> None:
    interactive_travel_service._task_bank.cache_clear()
    tasks = interactive_travel_service._task_bank()

    assert len(tasks) == 100
    assert len({task["id"] for task in tasks}) == 100
    assert len({task["question"] for task in tasks}) == 100
    assert tasks[0]["answer"] == "Тридцать пять градусов"
    assert tasks[-1]["id"] == "traveler-100"
    assert all(task["answer"] in task["choices"] for task in tasks)


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


def test_start_retries_missing_setup_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incomplete = {"fantasySetup": ""}
    client, completions = _client(monkeypatch, [incomplete, _start_payload()])

    travel = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в лес",
        client=client,
        model="test-model",
    ).travel

    assert travel.parts[0].storyText.startswith("У старого дуба")
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


def test_start_preserves_task_bank_content_without_model_rewriting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"fantasySetup": "Короткая встреча в выбранной локации"}
    client, _ = _client(monkeypatch, [payload])

    travel = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в лес",
        client=client,
        model="test-model",
    ).travel

    task = _test_story_tasks()[0]
    assert travel.parts[0].storyText.endswith(task["situation"])
    assert travel.parts[0].challenge == task["question"]
    assert travel.parts[0].actionSuggestions == task["choices"]


def test_continue_resolves_choice_and_adds_next_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    travel, client = _start(
        monkeypatch,
        [
            _start_payload(),
            {
                "result": "Я выбираю неверный ответ и задерживаюсь у дуба.",
                "fantasySetup": "У лесного ручья меня останавливает речной дух",
            },
        ],
    )

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="Полная луна",
        client=client,
        model="test-model",
    )

    next_travel = response.travel
    assert next_travel.completed is False
    assert len(next_travel.parts) == 2
    resolved, pending = next_travel.parts
    assert resolved.answer == "Полная луна"
    assert resolved.result is not None
    assert resolved.result.outcomeValence == "negative"
    assert resolved.result.statImpacts == []
    assert pending.result is None
    assert pending.partNumber == 2
    assert pending.transition is not None
    assert pending.transition.elapsedHours == 1
    assert pending.transition.summary == resolved.result.consequence
    assert pending.transition.departureHook == (
        "Чуть позже в этой же локации происходит новая встреча."
    )
    assert pending.storyText == (
        "У лесного ручья меня останавливает речной дух. "
        "Для подъёма груза нужно выбрать подходящий простой механизм."
    )
    assert pending.challenge == "Что поможет поднять груз?"
    assert pending.actionSuggestions == [
        "Рычаг",
        "Компас",
        "Песок",
        "Факел",
    ]
    assert all(len(choice.split()) <= 3 for choice in pending.actionSuggestions)
    assert next_travel.arcPlan["taskBankIds"] == "test-1,test-2"
    assert next_travel.arcPlan["part2CorrectChoice"] == "Рычаг"
    assert "Правильный ответ: Полярная звезда" in resolved.result.text
    assert "Почему:" in resolved.result.text
    result_call = client.chat.completions.calls[1]
    result_schema = result_call["response_format"]["json_schema"]
    assert result_schema["name"] == "interactive_travel_part_result_and_next_episode_v3"
    assert list(result_schema["schema"]["properties"]) == ["result", "fantasySetup"]
    assert "Следующая задача:" in result_call["messages"][1]["content"]
    assert "Не связывай её сюжетом" in result_call["messages"][1]["content"]
    assert "Локация: в ночной лес" in result_call["messages"][1]["content"]


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
    assert interactive_travel_service._choices(["Идти по северной тропе"])[0] == ("Идти по тропе")
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


def test_story_generates_four_independent_episodes_and_goes_home(
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
        "Полярная звезда",
        "Рычаг",
        "Третью",
        "Заслонку",
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
    assert [part.transition.elapsedHours for part in travel.parts[1:]] == [1, 1, 1]
    assert travel.parts[1].storyText.startswith("У лесного ручья")
    assert travel.parts[2].storyText.startswith("На поляне")
    assert travel.parts[3].storyText.startswith("У выхода из леса")
    assert len(completions.calls) == 5
    final_result = travel.parts[-1].result
    assert final_result is not None
    assert "Правильный ответ: Заслонку" in final_result.text
    assert "Почему:" in final_result.text
    assert "домой" not in final_result.consequence.casefold()
    assert "домой" not in final_result.text.casefold()
    final_call = completions.calls[-1]
    assert final_call["response_format"]["json_schema"]["name"] == (
        "interactive_travel_part_result_fixed_v3"
    )
    assert (
        "реплику о возвращении домой интерфейс добавит"
        in final_call["messages"][1]["content"].casefold()
    )


@pytest.mark.parametrize(
    "arc_plan",
    [
        {},
        {"funFact": "старый факт"},
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
