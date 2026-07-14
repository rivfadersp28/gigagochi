from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.schemas import (
    InteractiveTravelPart,
    InteractiveTravelResult,
    InteractiveTravelState,
    LocalPetChatContext,
)
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
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def _start_payload(
    *,
    choices: Any = None,
) -> dict[str, Any]:
    raw_choices = (
        ["Осмотреть ключ", "Позвать сторожа", "Проверить колодец"]
        if choices is None
        else list(choices)
        if isinstance(choices, (list, tuple))
        else [choices]
    )
    raw_choices = [*raw_choices, "", ""][:3]
    return {
        "title": "Тайна старой деревни",
        "goal": "Я хочу раскрыть тайну деревни.",
        "situation": "У колодца лежит сломанный ключ.",
        "question": "Как открыть люк?",
        "choice1": raw_choices[0],
        "choice2": raw_choices[1],
        "choice3": raw_choices[2],
    }


def _intermediate_payload() -> dict[str, Any]:
    return {
        "result": "Я поднимаю ключ. Под ним находится карта.",
        "outcome": "positive",
        "bridge": "По карте я спускаюсь в подвал.",
        "nextSituation": "В подвале путь закрывает тяжёлая решётка.",
        "nextQuestion": "Как открыть решётку?",
        "nextChoice1": "Найти рычаг",
        "nextChoice2": "Позвать сторожа",
        "nextChoice3": "Снять петли",
    }


def _flexible_payload(*, complete: bool) -> dict[str, Any]:
    return _intermediate_payload() | {"complete": complete}


def _final_payload(*, outcome: str = "positive") -> dict[str, Any]:
    return {
        "result": (
            "Я открываю сундук. Тайна деревни раскрыта."
            if outcome == "positive"
            else "Я открываю сундук, но он пуст. Тайна деревни остаётся нераскрытой."
        ),
        "outcome": outcome,
    }


def _past_result(number: int) -> InteractiveTravelResult:
    consequence = f"Препятствие {number} осталось позади."
    return InteractiveTravelResult(
        text=consequence,
        adviceAssessment="helpful",
        reaction="Продолжаю.",
        reactionTone="determined",
        consequence=consequence,
        outcomeValence="positive",
        statImpacts=[],
    )


def _travel_with_pending(
    current_number: int,
    *,
    target: int | None = None,
    legacy_target_key: bool = False,
) -> InteractiveTravelState:
    parts: list[InteractiveTravelPart] = []
    for number in range(1, current_number + 1):
        resolved = number < current_number
        parts.append(
            InteractiveTravelPart(
                partNumber=number,
                title=f"Часть {number}",
                storyText=f"Передо мной препятствие {number}.",
                transition=(
                    None
                    if number == 1
                    else {
                        "elapsedHours": 0,
                        "summary": f"Я прошла препятствие {number - 1}.",
                        "departureHook": "Я иду дальше.",
                    }
                ),
                challenge=f"Что сделать с препятствием {number}?",
                actionSuggestions=["Осмотреться", "Позвать помощь", "Идти дальше"],
                answer=f"решение {number}" if resolved else None,
                result=_past_result(number) if resolved else None,
            )
        )
    arc_plan = {"goal": "раскрыть тайну деревни"}
    if target is not None:
        arc_plan["targetPartCount" if legacy_target_key else "partCount"] = str(target)
    return InteractiveTravelState(
        travelId="interactive-travel-test",
        generatedAt=datetime(2026, 7, 14, 12, tzinfo=UTC),
        destination="в старую деревню",
        overallTitle="Тайна старой деревни",
        arcPlan=arc_plan,
        parts=parts,
        completed=False,
    )


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


def test_start_maps_minimal_payload_to_state(monkeypatch: pytest.MonkeyPatch) -> None:
    client, completions = _client(monkeypatch, [_start_payload()])

    response = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="  в старую деревню  ",
        client=client,
        model="test-model",
    )

    travel = response.travel
    assert travel.travelId.startswith("interactive-travel-")
    assert travel.destination == "в старую деревню"
    assert travel.overallTitle == "Тайна старой деревни"
    assert travel.arcPlan == {"goal": "раскрыть тайну деревни"}
    assert travel.introReaction is not None
    assert travel.introReaction.text == (
        "Я отправляюсь в старую деревню. Моя цель — раскрыть тайну деревни."
    )
    assert travel.introReaction.tone == "determined"
    assert travel.completed is False
    assert travel.outcomeValence is None
    assert len(travel.parts) == 1
    part = travel.parts[0]
    assert part.title == "Часть 1"
    assert part.storyText == "У колодца лежит сломанный ключ."
    assert "Я хочу" not in part.storyText
    assert part.challenge == "Как открыть люк?"
    assert part.actionSuggestions == [
        "Осмотреть ключ",
        "Позвать сторожа",
        "Проверить колодец",
    ]
    assert part.answer is None
    assert part.result is None
    assert response.debug is None
    assert len(completions.calls) == 1


def test_start_uses_short_minimal_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, completions = _client(monkeypatch, [_start_payload()])

    interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в деревню",
        client=client,
        model="test-model",
    )

    call = completions.calls[0]
    assert len(call["messages"]) == 2
    assert len(call["messages"][0]["content"].split()) < 80
    assert "в настоящем времени" in call["messages"][0]["content"]
    assert len(call["messages"][1]["content"]) < 500
    schema_wrapper = call["response_format"]["json_schema"]
    assert schema_wrapper["name"] == "interactive_travel_start_simple"
    assert schema_wrapper["strict"] is True
    schema = schema_wrapper["schema"]
    assert schema == interactive_travel_service.START_SCHEMA
    assert set(schema["properties"]) == {
        "title",
        "goal",
        "situation",
        "question",
        "choice1",
        "choice2",
        "choice3",
    }
    assert "partCount" not in schema["properties"]
    assert "steps" not in schema["properties"]
    assert "choices" not in schema["properties"]
    assert "intro" not in schema["properties"]


def test_start_retries_malformed_json_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    client, completions = _client(monkeypatch, ["not-json", _start_payload()])

    response = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в деревню",
        client=client,
        model="test-model",
        include_debug=True,
    )

    assert response.travel.parts[0].storyText == "У колодца лежит сломанный ключ."
    assert len(completions.calls) == 2
    assert len(completions.calls[1]["messages"]) == 3
    assert completions.calls[1]["messages"][-1]["content"] == (
        "Верни только корректный JSON по исходной схеме."
    )
    assert response.debug is not None
    assert len(response.debug.promptDebug) == 4


def test_intermediate_resolves_current_part_and_adds_bridged_pending_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    travel = _travel_with_pending(1)
    client, completions = _client(monkeypatch, [_intermediate_payload()])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="  поднять   ключ  ",
        client=client,
        model="test-model",
        tie_break_valence="positive",
        include_debug=True,
    )

    next_travel = response.travel
    assert next_travel.completed is False
    assert next_travel.outcomeValence is None
    assert len(next_travel.parts) == 2
    resolved, pending = next_travel.parts
    assert resolved.storyText == travel.parts[0].storyText
    assert resolved.challenge == travel.parts[0].challenge
    assert resolved.answer == "поднять ключ"
    assert resolved.result is not None
    assert resolved.result.text == "Я поднимаю ключ. Под ним находится карта."
    assert resolved.result.consequence == resolved.result.text
    assert resolved.result.reaction == "Выбираю: поднять ключ."
    assert resolved.result.adviceAssessment == "helpful"
    assert resolved.result.outcomeValence == "positive"
    assert pending.partNumber == 2
    assert pending.title == "Часть 2"
    assert pending.storyText == "В подвале путь закрывает тяжёлая решётка."
    assert pending.challenge == "Как открыть решётку?"
    assert pending.actionSuggestions == ["Найти рычаг", "Позвать сторожа", "Снять петли"]
    assert pending.answer is None
    assert pending.result is None
    assert pending.transition is not None
    assert pending.transition.elapsedHours == 0
    assert pending.transition.summary == resolved.result.text
    assert pending.transition.departureHook == "По карте я спускаюсь в подвал."
    assert response.debug is not None
    assert len(response.debug.promptDebug) == 2

    call = completions.calls[0]
    user_prompt = call["messages"][1]["content"]
    assert "Я делаю: поднять ключ." in user_prompt
    assert f"Вопрос: {travel.parts[0].challenge}" in user_prompt
    assert "Раньше было:" not in user_prompt
    assert "Сейчас ситуация 1. История заканчивается естественно" in user_prompt
    assert "Это ещё не финал: цель пока не выполнена" in user_prompt
    assert "шаг" not in user_prompt.casefold()
    assert "Пиши в настоящем времени." in user_prompt
    assert call["response_format"]["json_schema"]["schema"] == (
        interactive_travel_service.INTERMEDIATE_SCHEMA
    )
    intermediate_schema = interactive_travel_service.INTERMEDIATE_SCHEMA
    assert "nextChoices" not in intermediate_schema["properties"]
    assert {"nextChoice1", "nextChoice2", "nextChoice3"}.issubset(intermediate_schema["properties"])


def test_second_part_prompt_contains_previous_consequence_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    travel = _travel_with_pending(2)
    previous_result = travel.parts[0].result
    assert previous_result is not None
    previous_consequence = previous_result.consequence
    client, completions = _client(monkeypatch, [_intermediate_payload()])

    interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="открыть решётку",
        client=client,
        model="test-model",
        tie_break_valence="positive",
    )

    prompt = completions.calls[0]["messages"][1]["content"]
    assert f"До этого: {previous_consequence}" in prompt
    assert prompt.count(previous_consequence) == 1
    assert "Сейчас ситуация 2. История заканчивается естественно" in prompt
    assert completions.calls[0]["response_format"]["json_schema"]["schema"] == (
        interactive_travel_service.INTERMEDIATE_SCHEMA
    )


def test_continue_caps_bridge_and_next_question_to_one_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _intermediate_payload()
    payload["bridge"] = "Я спускаюсь в подвал. Я открываю дверь."
    payload["nextQuestion"] = "Как открыть решётку? Кого позвать?"
    client, _ = _client(monkeypatch, [payload])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=_travel_with_pending(1),
        advice="поднять ключ",
        client=client,
        model="test-model",
        tie_break_valence="positive",
    )

    pending = response.travel.parts[-1]
    assert pending.transition is not None
    assert pending.transition.departureHook == "Я спускаюсь в подвал."
    assert pending.challenge == "Как открыть решётку?"


@pytest.mark.parametrize("current_number", [3, 4, 5])
def test_flexible_complete_true_finishes_between_parts_three_and_five(
    monkeypatch: pytest.MonkeyPatch,
    current_number: int,
) -> None:
    travel = _travel_with_pending(current_number)
    original_question = travel.parts[-1].challenge
    client, completions = _client(monkeypatch, [_flexible_payload(complete=True)])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="открыть сундук",
        client=client,
        model="test-model",
        tie_break_valence="positive",
    )

    completed = response.travel
    assert completed.completed is True
    assert completed.outcomeValence == "positive"
    assert completed.statImpact is None
    assert len(completed.parts) == current_number
    final_part = completed.parts[-1]
    assert final_part.challenge == original_question
    assert final_part.answer == "открыть сундук"
    assert final_part.result is not None
    assert final_part.result.text == (
        "Я поднимаю ключ. Под ним находится карта. Мне удалось раскрыть тайну деревни."
    )
    assert final_part.result.consequence == "Я поднимаю ключ. Под ним находится карта."
    assert final_part.result.reaction == "Выбираю: открыть сундук."
    assert all(part.result is not None for part in completed.parts)
    assert response.debug is None

    call = completions.calls[0]
    assert call["response_format"]["json_schema"]["schema"] == (
        interactive_travel_service.FLEXIBLE_SCHEMA
    )
    assert call["response_format"]["json_schema"]["name"] == ("interactive_travel_flexible_simple")
    prompt = call["messages"][1]["content"]
    assert f"Сейчас ситуация {current_number}. История заканчивается естественно" in prompt
    assert "верни complete=true" in prompt
    assert "шаг" not in prompt.casefold()


@pytest.mark.parametrize("current_number", [3, 4, 5])
def test_flexible_complete_false_adds_the_next_pending_part(
    monkeypatch: pytest.MonkeyPatch,
    current_number: int,
) -> None:
    travel = _travel_with_pending(current_number)
    client, completions = _client(monkeypatch, [_flexible_payload(complete=False)])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="поднять ключ",
        client=client,
        model="test-model",
        tie_break_valence="positive",
    )

    assert response.travel.completed is False
    assert len(response.travel.parts) == current_number + 1
    resolved = response.travel.parts[-2]
    pending = response.travel.parts[-1]
    assert resolved.result is not None
    assert resolved.result.text == "Я поднимаю ключ. Под ним находится карта."
    assert resolved.result.reaction == "Выбираю: поднять ключ."
    assert pending.partNumber == current_number + 1
    assert pending.result is None
    assert completions.calls[0]["response_format"]["json_schema"]["schema"] == (
        interactive_travel_service.FLEXIBLE_SCHEMA
    )


def test_sixth_part_is_forced_final_with_exact_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    travel = _travel_with_pending(6)
    client, completions = _client(monkeypatch, [_final_payload()])

    response = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="открыть сундук",
        client=client,
        model="test-model",
        tie_break_valence="positive",
    )

    completed = response.travel
    assert completed.completed is True
    assert len(completed.parts) == 6
    final_part = completed.parts[-1]
    assert final_part.result is not None
    assert final_part.result.text == (
        "Я открываю сундук. Тайна деревни раскрыта. Мне удалось раскрыть тайну деревни."
    )
    assert final_part.result.consequence == "Я открываю сундук. Тайна деревни раскрыта."
    assert final_part.result.reaction == "Выбираю: открыть сундук."
    assert all(part.result is not None for part in completed.parts)

    call = completions.calls[0]
    assert call["response_format"]["json_schema"]["schema"] == (
        interactive_travel_service.FINAL_SCHEMA
    )
    assert set(interactive_travel_service.FINAL_SCHEMA["properties"]) == {
        "result",
        "outcome",
    }
    prompt = call["messages"][1]["content"]
    assert "Сейчас ситуация 6. История заканчивается естественно" in prompt
    assert "Это финал. Разреши цель" in prompt
    assert "result должен совпадать с outcome" in prompt
    assert "шаг" not in prompt.casefold()
    assert "Пиши в настоящем времени." in prompt
    assert "новую проблему" in prompt


def test_old_target_part_count_seven_remains_playable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    travel = _travel_with_pending(6, target=7, legacy_target_key=True)
    travel.arcPlan["goal"] = "Я хочу раскрыть тайну деревни."
    client, completions = _client(
        monkeypatch,
        [_intermediate_payload(), _final_payload(outcome="negative")],
    )

    before_final = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="поднять ключ",
        client=client,
        model="test-model",
        tie_break_valence="positive",
    )
    completed = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=before_final.travel,
        advice="открыть сундук",
        client=client,
        model="test-model",
        tie_break_valence="negative",
    )

    assert before_final.travel.completed is False
    assert len(before_final.travel.parts) == 7
    assert before_final.travel.parts[-1].result is None
    assert completed.travel.completed is True
    assert len(completed.travel.parts) == 7
    assert completed.travel.outcomeValence == "negative"
    assert completed.travel.parts[-1].result is not None
    assert completed.travel.parts[-1].result.text == (
        "Я открываю сундук, но он пуст. Тайна деревни остаётся нераскрытой. "
        "Мне не удалось раскрыть тайну деревни."
    )
    assert completed.travel.parts[-1].result.reaction == "Выбираю: открыть сундук."
    assert completions.calls[0]["response_format"]["json_schema"]["schema"] == (
        interactive_travel_service.INTERMEDIATE_SCHEMA
    )
    assert completions.calls[1]["response_format"]["json_schema"]["schema"] == (
        interactive_travel_service.FINAL_SCHEMA
    )
    legacy_intermediate_prompt = completions.calls[0]["messages"][1]["content"]
    assert "Это ещё не финал: цель пока не выполнена" in legacy_intermediate_prompt
    assert "шаг" not in legacy_intermediate_prompt.casefold()
    legacy_final_prompt = completions.calls[1]["messages"][1]["content"]
    assert "Это финал. Разреши цель" in legacy_final_prompt
    assert "result должен совпадать с outcome" in legacy_final_prompt
    assert "Моя цель — раскрыть тайну деревни." in legacy_final_prompt
    assert "Я хочу раскрыть" not in legacy_final_prompt
    assert "шаг" not in legacy_final_prompt.casefold()


def test_continue_rejects_completed_state_before_calling_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    travel = _travel_with_pending(3, target=3)
    client, completions = _client(monkeypatch, [_final_payload()])
    completed = interactive_travel_service.continue_interactive_travel(
        pet=_pet(),
        travel=travel,
        advice="открыть сундук",
        client=client,
        model="test-model",
        tie_break_valence="positive",
    ).travel
    assert completions.calls[0]["response_format"]["json_schema"]["schema"] == (
        interactive_travel_service.FINAL_SCHEMA
    )

    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="ALREADY_COMPLETED",
    ):
        interactive_travel_service.continue_interactive_travel(
            pet=_pet(),
            travel=completed,
            advice="идти дальше",
            client=client,
            model="test-model",
        )

    assert len(completions.calls) == 1


def test_continue_rejects_state_without_pending_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    travel = _travel_with_pending(1)
    travel.parts[-1].answer = "готово"
    travel.parts[-1].result = _past_result(1)
    client, completions = _client(monkeypatch, [])

    with pytest.raises(
        interactive_travel_service.InteractiveTravelGenerationError,
        match="PENDING_PART_MISSING",
    ):
        interactive_travel_service.continue_interactive_travel(
            pet=_pet(),
            travel=travel,
            advice="идти дальше",
            client=client,
            model="test-model",
        )

    assert completions.calls == []


def test_continue_rejects_invalid_tie_break_before_calling_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    travel = _travel_with_pending(1)
    client, completions = _client(monkeypatch, [])

    with pytest.raises(ValueError, match="tie_break_valence"):
        interactive_travel_service.continue_interactive_travel(
            pet=_pet(),
            travel=travel,
            advice="осмотреться",
            client=client,
            model="test-model",
            tie_break_valence="neutral",
        )

    assert completions.calls == []


def test_sentence_caps_story_to_two_and_question_or_bridge_to_one() -> None:
    source = "Первое предложение. Второе предложение. Третье предложение."

    assert (
        interactive_travel_service._sentence(
            source,
            fallback="Запасной текст",
            limit=220,
        )
        == "Первое предложение. Второе предложение."
    )
    assert (
        interactive_travel_service._sentence(
            source,
            fallback="Что делать",
            limit=120,
            question=True,
            max_sentences=1,
        )
        == "Первое предложение?"
    )
    assert (
        interactive_travel_service._sentence(
            source,
            fallback="Я иду дальше",
            limit=180,
            max_sentences=1,
        )
        == "Первое предложение."
    )


def test_choices_flatten_nested_lists_and_parse_repr_lists() -> None:
    assert interactive_travel_service._choices(
        [
            [[{"text": "Обнюхать карту"}]],
            "['Перевернуть карту']",
            [[{"label": "Позвать помощь"}]],
        ]
    ) == ["Обнюхать карту", "Перевернуть карту", "Позвать помощь"]


def test_start_normalizes_duplicate_choices_with_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _start_payload(choices=["  Осмотреться  ", "осмотреться", "   "])
    client, _ = _client(monkeypatch, [payload])

    response = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в деревню",
        client=client,
        model="test-model",
    )

    assert response.travel.parts[0].actionSuggestions == [
        "Осмотреться",
        "Позвать помощь",
        "Идти дальше",
    ]


def test_start_splits_semicolon_separated_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _start_payload(choices="Осмотреть ключ; Позвать сторожа; Проверить колодец")
    client, _ = _client(monkeypatch, [payload])

    response = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в деревню",
        client=client,
        model="test-model",
    )

    assert response.travel.parts[0].actionSuggestions == [
        "Осмотреть ключ",
        "Позвать сторожа",
        "Проверить колодец",
    ]


@pytest.mark.parametrize(
    ("dict_key", "glued_choices"),
    [
        ("string", "Осмотреть ключ; Позвать сторожа; Проверить колодец"),
        ("text", "Осмотреть ключ\nПозвать сторожа\nПроверить колодец"),
        ("value", "Осмотреть ключ. Позвать сторожа! Проверить колодец?"),
        ("label", "Осмотреть ключ; Позвать сторожа; Проверить колодец"),
    ],
)
def test_start_extracts_glued_choices_from_provider_dicts(
    monkeypatch: pytest.MonkeyPatch,
    dict_key: str,
    glued_choices: str,
) -> None:
    payload = _start_payload(choices={dict_key: glued_choices})
    client, _ = _client(monkeypatch, [payload])

    response = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в деревню",
        client=client,
        model="test-model",
    )

    assert response.travel.parts[0].actionSuggestions == [
        "Осмотреть ключ",
        "Позвать сторожа",
        "Проверить колодец",
    ]


def test_start_ignores_malformed_and_overlong_choice_dicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _start_payload(
        choices=[
            {"unknown": "не использовать"},
            {"value": "Слишком длинный вариант " * 5},
            {"label": "Осмотреться"},
        ]
    )
    client, _ = _client(monkeypatch, [payload])

    response = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в деревню",
        client=client,
        model="test-model",
    )

    assert response.travel.parts[0].actionSuggestions == [
        "Осмотреться",
        "Позвать помощь",
        "Идти дальше",
    ]


def test_media_helpers_forward_all_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    pet = _pet()
    image_arguments: dict[str, Any] = {}
    video_arguments: dict[str, Any] = {}

    def fake_image(**kwargs: Any) -> str:
        image_arguments.update(kwargs)
        return "/static/travel/part-2.png"

    def fake_video(**kwargs: Any) -> str:
        video_arguments.update(kwargs)
        return "/static/travel/part-2.mp4"

    monkeypatch.setattr(
        interactive_travel_service,
        "generate_interactive_travel_part_image",
        fake_image,
    )
    monkeypatch.setattr(
        interactive_travel_service,
        "generate_interactive_travel_part_video",
        fake_video,
    )

    image = interactive_travel_service.illustrate_interactive_travel_part(
        pet=pet,
        travel_id="travel-1",
        destination="в деревню",
        part_number=2,
        title="Часть 2",
        story_text="Передо мной закрытая дверь.",
    )
    video = interactive_travel_service.animate_interactive_travel_part(
        travel_id="travel-1",
        part_number=2,
    )

    assert image.partNumber == 2
    assert image.imageUrl == "/static/travel/part-2.png"
    assert image_arguments == {
        "pet": pet,
        "travel_id": "travel-1",
        "destination": "в деревню",
        "part_number": 2,
        "title": "Часть 2",
        "story_text": "Передо мной закрытая дверь.",
    }
    assert video.partNumber == 2
    assert video.videoUrl == "/static/travel/part-2.mp4"
    assert video_arguments == {"travel_id": "travel-1", "part_number": 2}
