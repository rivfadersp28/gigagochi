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
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def _start_payload() -> dict[str, Any]:
    return {
        "title": "Ночная тропа",
        "situation": (
            "У развилки оживает старый фонарь и просит найти север. "
            "В Северном полушарии Полярная звезда показывает направление на север, "
            "поэтому я ищу её в небе."
        ),
        "question": "Куда мне идти?",
        "choice1": "Найти Полярную звезду",
        "choice2": "Спросить фонарь",
        "choice3": "Подбросить монетку",
    }


def _continue_payload(number: int) -> dict[str, Any]:
    return {
        "result": f"Я выполняю выбранное действие и прохожу испытание {number}.",
        "outcome": "positive",
        "nextSituation": f"На тропе появляется говорящий мост {number}.",
        "nextQuestion": "Как перейти мост?",
        "nextChoice1": "Попросить мост пропустить меня",
        "nextChoice2": "Перепрыгнуть ручей",
        "nextChoice3": "Найти другую тропу",
    }


def _final_payload() -> dict[str, Any]:
    return {
        "result": "Я открываю старый сундук, и из него вылетает стая бумажных птиц.",
        "outcome": "positive",
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
    assert travel.arcPlan == {"generatorVersion": "simple-1", "funFact": TEST_FACT}
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
    assert "goal" not in call["messages"][1]["content"]
    assert TEST_FACT in call["messages"][1]["content"]
    assert "не выноси факт отдельно" in call["messages"][1]["content"]
    schema = call["response_format"]["json_schema"]
    assert schema["name"] == "interactive_travel_start_simple_v1"
    assert schema["schema"] == interactive_travel_service.START_SCHEMA


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
    travel, client = _start(monkeypatch, [_start_payload(), _continue_payload(1)])

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
    assert pending.result is None
    assert pending.partNumber == 2
    assert pending.transition is not None
    assert pending.transition.summary == resolved.result.consequence
    assert pending.transition.departureHook == "Я иду дальше."
    assert next_travel.arcPlan == travel.arcPlan


def test_long_result_fits_transition_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _continue_payload(1) | {"result": "Длинный результат " * 30}
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


def test_story_always_finishes_after_three_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    client, completions = _client(
        monkeypatch,
        [_start_payload(), _continue_payload(1), _continue_payload(2), _final_payload()],
    )
    travel = interactive_travel_service.start_interactive_travel(
        pet=_pet(),
        destination="в ночной лес",
        client=client,
        model="test-model",
    ).travel

    for advice in ("Найти Полярную звезду", "Перейти мост", "Открыть сундук"):
        travel = interactive_travel_service.continue_interactive_travel(
            pet=_pet(),
            travel=travel,
            advice=advice,
            client=client,
            model="test-model",
        ).travel

    assert travel.completed is True
    assert travel.outcomeValence == "positive"
    assert len(travel.parts) == 3
    assert all(part.result is not None for part in travel.parts)
    visible_story = " ".join(part.storyText for part in travel.parts)
    assert visible_story.count(TEST_FACT) == 1
    assert len(completions.calls) == 4
    final_call = completions.calls[-1]
    assert final_call["response_format"]["json_schema"]["name"] == (
        "interactive_travel_final_simple_v1"
    )


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
    travel, client = _start(monkeypatch, [_start_payload(), _continue_payload(1)])
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
        [_start_payload(), _continue_payload(1), _continue_payload(2), _final_payload()],
    )
    travel = interactive_travel_service.start_interactive_travel(
        pet=_pet(), destination="в лес", client=client, model="test-model"
    ).travel
    for advice in ("Первое", "Второе", "Третье"):
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
