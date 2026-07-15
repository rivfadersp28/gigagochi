from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas import InteractiveTravelTaskPlan, LocalPetChatContext
from app.services import interactive_travel_service


def _pet() -> LocalPetChatContext:
    return LocalPetChatContext.model_validate(
        {
            "name": "Мяу",
            "description": "смелая кошка",
            "stage": "teen",
            "mood": "idle",
            "stats": {"hunger": 70, "happiness": 60, "energy": 80},
            "characterBible": {"identity": {"name": "Мяу", "species": "кошка"}},
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


def _test_story_tasks() -> list[dict[str, Any]]:
    return [
        {
            "id": "test-1",
            "situation": "Ночью нужно определить направление на север по звёздам.",
            "question": "Что укажет север?",
            "choices": ["Полярная звезда", "Полная луна", "Венера", "Облако"],
            "answer": "Полярная звезда",
            "outcomes": [
                "Я выбрал Полярную звезду! Север найден.",
                "Я выбрал полную луну... Она не указала север.",
                "Я выбрал Венеру... Путь оказался неверным.",
                "Я выбрал облако... Оно быстро исчезло.",
            ],
            "explanation": "Полярная звезда показывает направление на север",
        },
        {
            "id": "test-2",
            "situation": "Для подъёма груза нужно выбрать подходящий простой механизм.",
            "question": "Что поможет поднять груз?",
            "choices": ["Рычаг", "Компас", "Песок", "Факел"],
            "answer": "Рычаг",
            "outcomes": [
                "Я выбрал рычаг! Груз поднялся.",
                "Я выбрал компас... Груз не сдвинулся.",
                "Я выбрал песок... Груз стал ещё тяжелее.",
                "Я выбрал факел... Огонь не помог поднять груз.",
            ],
            "explanation": "Рычаг позволяет получить выигрыш в силе",
        },
        {
            "id": "test-3",
            "situation": "Перед путником четыре двери, но только одна ведёт наружу.",
            "question": "Какую дверь проверить?",
            "choices": ["Первую", "Вторую", "Третью", "Четвёртую"],
            "answer": "Третью",
            "outcomes": [
                "Я открыл первую дверь... За ней тупик.",
                "Я открыл вторую дверь... За ней стена.",
                "Я открыл третью дверь! Выход найден.",
                "Я открыл четвёртую дверь... За ней ловушка.",
            ],
            "explanation": "Подсказка однозначно указывает на третью дверь",
        },
        {
            "id": "test-4",
            "situation": "Для огня нужно обеспечить постоянный приток воздуха.",
            "question": "Что открыть для огня?",
            "choices": ["Заслонку", "Сундук", "Книгу", "Колокол"],
            "answer": "Заслонку",
            "outcomes": [
                "Я открыл заслонку! Огонь разгорелся.",
                "Я открыл сундук... Огонь начал гаснуть.",
                "Я открыл книгу... Она едва не загорелась.",
                "Я ударил в колокол... Огонь не изменился.",
            ],
            "explanation": "Открытая заслонка пропускает воздух к огню",
        },
    ]


def _client(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any] | str],
) -> tuple[Any, SequenceCompletions]:
    completions = SequenceCompletions(responses)
    monkeypatch.setattr(interactive_travel_service.random, "choice", lambda values: values[0])
    monkeypatch.setattr(
        interactive_travel_service.random,
        "sample",
        lambda values, count: list(values[:count]),
    )
    monkeypatch.setattr(
        interactive_travel_service,
        "_task_bank",
        lambda: tuple(_test_story_tasks()),
    )
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def _start(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, SequenceCompletions]:
    client, completions = _client(monkeypatch, [])
    travel = interactive_travel_service.start_interactive_travel(
        destination="в ночной лес",
        client=client,
        model="test-model",
    ).travel
    return travel, completions


def test_suggestions_do_not_call_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    client, completions = _client(monkeypatch, [])

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


def test_start_selects_four_tasks_with_deterministic_location_lead_ins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions = _client(monkeypatch, [])

    response = interactive_travel_service.start_interactive_travel(
        destination="  в ночной лес  ",
        client=client,
        model="test-model",
        include_debug=True,
    )

    travel = response.travel
    assert travel.destination == "в ночной лес"
    assert travel.generationStatus == "ready"
    assert travel.plan is not None
    assert travel.plan.version == interactive_travel_service.GENERATOR_VERSION
    assert [task.taskId for task in travel.plan.tasks] == [
        "test-1",
        "test-2",
        "test-3",
        "test-4",
    ]
    assert len(travel.parts) == 1
    assert travel.parts[0].storyText == (
        "По пути в ночной лес меня ждёт первая встреча. "
        "Ночью нужно определить направление на север по звёздам."
    )
    assert travel.parts[0].challenge == "Что укажет север?"
    assert travel.parts[0].actionSuggestions == _test_story_tasks()[0]["choices"]

    assert completions.calls == []
    assert response.debug is not None
    assert response.debug.promptDebug == []


def test_start_preserves_bank_fields_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    travel, _ = _start(monkeypatch)
    assert travel.plan is not None

    for planned, source in zip(travel.plan.tasks, _test_story_tasks(), strict=True):
        assert planned.situation == source["situation"]
        assert planned.question == source["question"]
        assert planned.choices == source["choices"]
        assert planned.correctChoice == source["answer"]
        assert planned.explanation == source["explanation"]
        assert planned.choiceOutcomes == source["outcomes"]


def test_start_does_not_call_supplied_llm_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions = _client(monkeypatch, ["not-json"])

    travel = interactive_travel_service.start_interactive_travel(
        destination="в лес",
        client=client,
        model="test-model",
    ).travel

    assert travel.plan is not None
    assert completions.calls == []
    assert [task.leadIn for task in travel.plan.tasks] == (
        interactive_travel_service._location_lead_ins("в лес")
    )


def test_continue_is_deterministic_and_does_not_call_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    travel, completions = _start(monkeypatch)

    response = interactive_travel_service.continue_interactive_travel(
        travel=travel,
        advice="Полная луна",
        include_debug=True,
    )

    next_travel = response.travel
    assert completions.calls == []
    assert len(next_travel.parts) == 2
    resolved, pending = next_travel.parts
    assert resolved.answer == "Полная луна"
    assert resolved.result is not None
    assert resolved.result.outcomeValence == "negative"
    assert resolved.result.text == "Я выбрал полную луну... Она не указала север."
    assert resolved.result.consequence == (
        "Этот вариант не подходит; правильный ответ — Полярная звезда."
    )
    assert pending.storyText == (
        "По пути в ночной лес меня ждёт вторая встреча. "
        "Для подъёма груза нужно выбрать подходящий простой механизм."
    )
    assert pending.challenge == _test_story_tasks()[1]["question"]
    assert pending.actionSuggestions == _test_story_tasks()[1]["choices"]
    assert response.debug is not None
    assert response.debug.promptDebug == []


def test_four_answers_complete_exactly_four_parts_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    travel, completions = _start(monkeypatch)

    for answer in ("Полярная звезда", "Рычаг", "Третью", "Заслонку"):
        travel = interactive_travel_service.continue_interactive_travel(
            travel=travel,
            advice=answer,
        ).travel

    assert travel.completed is True
    assert travel.outcomeValence == "positive"
    assert len(travel.parts) == 4
    assert all(part.result is not None for part in travel.parts)
    assert completions.calls == []
    assert travel.plan is not None
    assert [part.storyText for part in travel.parts] == [
        f"{task.leadIn} {task.situation}" for task in travel.plan.tasks
    ]


def test_continue_rejects_non_bank_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    travel, completions = _start(monkeypatch)

    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="INTERACTIVE_TRAVEL_ANSWER_INVALID",
    ):
        interactive_travel_service.continue_interactive_travel(
            travel=travel,
            advice="Свой свободный ответ",
        )

    assert completions.calls == []


def test_completed_story_is_rejected_without_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    travel, completions = _start(monkeypatch)
    for answer in ("Полярная звезда", "Рычаг", "Третью", "Заслонку"):
        travel = interactive_travel_service.continue_interactive_travel(
            travel=travel,
            advice=answer,
        ).travel

    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="INTERACTIVE_TRAVEL_ALREADY_COMPLETED",
    ):
        interactive_travel_service.continue_interactive_travel(
            travel=travel,
            advice="Заслонку",
        )

    assert completions.calls == []


def test_task_bank_is_valid_and_has_one_hundred_unique_tasks() -> None:
    interactive_travel_service._task_bank.cache_clear()
    tasks = interactive_travel_service._task_bank()

    assert len(tasks) == 100
    assert len({task["id"] for task in tasks}) == 100
    assert len({task["question"] for task in tasks}) == 100
    assert all(len(task["choices"]) == 4 for task in tasks)
    assert all(len(task["outcomes"]) == 4 for task in tasks)
    assert all(task["answer"] in task["choices"] for task in tasks)
    assert all(task["explanation"] is None for task in tasks)

    for task in tasks:
        planned = InteractiveTravelTaskPlan(
            taskId=task["id"],
            leadIn="В выбранной локации началась новая встреча.",
            situation=task["situation"],
            question=task["question"],
            choices=task["choices"],
            correctChoice=task["answer"],
            explanation=task["explanation"],
            choiceOutcomes=task["outcomes"],
        )
        for choice, outcome in zip(task["choices"], task["outcomes"], strict=True):
            result = interactive_travel_service._deterministic_result(
                planned,
                selected_choice=choice,
            )
            assert result.text == outcome


def test_task_bank_rejects_answer_text_that_disagrees_with_its_letter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    invalid_bank = tmp_path / "invalid-task-bank.md"
    invalid_bank.write_text(
        interactive_travel_service.TASK_BANK_PATH.read_text(encoding="utf-8").replace(
            "**Ответ:** А) Пышную шерсть",
            "**Ответ:** А) Тонкое железо",
            1,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(interactive_travel_service, "TASK_BANK_PATH", invalid_bank)
    interactive_travel_service._task_bank.cache_clear()

    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="INTERACTIVE_TRAVEL_TASK_BANK_INVALID",
    ):
        interactive_travel_service._task_bank()

    interactive_travel_service._task_bank.cache_clear()


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
    assert image_calls[0]["pet"] == pet
    assert video_calls == [{"travel_id": "interactive-travel-test", "part_number": 2}]
