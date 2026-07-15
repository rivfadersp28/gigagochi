import pytest
from pydantic import ValidationError

from app.schemas import (
    AnimateInteractiveTravelPartRequest,
    ContinueInteractiveTravelRequest,
    IllustrateInteractiveTravelPartRequest,
    InteractiveTravelState,
    InteractiveTravelTransition,
    LocalPetChatContext,
    LocalPetPushSnapshotRequest,
    LocalPetStats,
    StartInteractiveTravelRequest,
)


def _pet() -> LocalPetChatContext:
    return LocalPetChatContext(
        description="синтетический питомец",
        stage="teen",
        mood="idle",
        stats=LocalPetStats(hunger=50, happiness=50, energy=50),
    )


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (
            IllustrateInteractiveTravelPartRequest,
            {
                "pet": _pet(),
                "travelId": "550e8400-e29b-41d4-a716-446655440000",
                "destination": "гора",
                "partNumber": 1,
                "title": "Путь",
                "storyText": "Питомец идёт по тропе.",
            },
        ),
        (
            AnimateInteractiveTravelPartRequest,
            {
                "travelId": "550e8400-e29b-41d4-a716-446655440000",
                "partNumber": 1,
            },
        ),
    ],
)
def test_interactive_travel_media_requests_reject_pet_asset_ids(schema, payload) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_interactive_travel_transition_bounds_departure_hook() -> None:
    transition = InteractiveTravelTransition(
        elapsedHours=4,
        summary="Мир изменился.",
        departureHook="я" * 280,
    )

    assert len(transition.departureHook or "") == 280
    with pytest.raises(ValidationError):
        InteractiveTravelTransition(
            elapsedHours=4,
            summary="Мир изменился.",
            departureHook="я" * 281,
        )


def test_push_snapshot_rejects_pet_id_that_can_alter_a_route_path() -> None:
    with pytest.raises(ValidationError):
        LocalPetPushSnapshotRequest(petId="../admin", pet=_pet())


def test_local_pet_chat_context_normalizes_and_validates_optional_pet_id() -> None:
    assert _pet().model_copy(update={"petId": "pet-1"}).petId == "pet-1"
    assert (
        LocalPetChatContext.model_validate(
            {
                **_pet().model_dump(mode="json"),
                "petId": "  pet-1  ",
            }
        ).petId
        == "pet-1"
    )
    with pytest.raises(ValidationError):
        LocalPetChatContext.model_validate(
            {
                **_pet().model_dump(mode="json"),
                "petId": "../foreign",
            }
        )


def _task(number: int) -> dict[str, object]:
    return {
        "taskId": f"traveler-{number:03d}",
        "leadIn": f"Встреча {number}.",
        "situation": f"Ситуация {number}.",
        "question": f"Вопрос {number}?",
        "choices": [f"{letter}{number}" for letter in "АБВГ"],
        "correctChoice": f"А{number}",
        "explanation": f"Объяснение {number}.",
    }


def _ready_state_payload() -> dict[str, object]:
    tasks = [_task(number) for number in range(1, 5)]
    return {
        "travelId": "interactive-travel-synthetic",
        "generatedAt": "2026-07-15T12:00:00Z",
        "destination": "гора",
        "overallTitle": "Путь",
        "plan": {"version": "task-bank-location-v4", "tasks": tasks},
        "parts": [
            {
                "partNumber": 1,
                "title": "Начало",
                "storyText": "Встреча 1. Ситуация 1.",
                "challenge": "Вопрос 1?",
                "actionSuggestions": ["А1", "Б1", "В1", "Г1"],
            }
        ],
    }


def test_ready_interactive_travel_requires_typed_plan() -> None:
    with pytest.raises(ValidationError):
        InteractiveTravelState.model_validate(
            {
                **_ready_state_payload(),
                "plan": None,
            }
        )


def test_generating_interactive_travel_rejects_typed_plan() -> None:
    with pytest.raises(ValidationError):
        InteractiveTravelState.model_validate(
            {
                **_ready_state_payload(),
                "generationStatus": "generating",
            }
        )


def test_typed_plan_requires_four_unique_tasks_and_exact_choice() -> None:
    payload = _ready_state_payload()
    plan = payload["plan"]
    assert isinstance(plan, dict)
    tasks = plan["tasks"]
    assert isinstance(tasks, list)
    tasks[1] = tasks[0]
    with pytest.raises(ValidationError):
        InteractiveTravelState.model_validate(payload)

    payload = _ready_state_payload()
    plan = payload["plan"]
    assert isinstance(plan, dict)
    tasks = plan["tasks"]
    assert isinstance(tasks, list)
    assert isinstance(tasks[0], dict)
    tasks[0]["correctChoice"] = "Нет такого ответа"
    with pytest.raises(ValidationError):
        InteractiveTravelState.model_validate(payload)


def test_optional_task_outcomes_require_one_outcome_per_choice() -> None:
    payload = _ready_state_payload()
    plan = payload["plan"]
    assert isinstance(plan, dict)
    tasks = plan["tasks"]
    assert isinstance(tasks, list)
    assert isinstance(tasks[0], dict)
    tasks[0].pop("explanation")
    tasks[0]["choiceOutcomes"] = ["А" * 700, "Исход Б", "Исход В", "Исход Г"]

    state = InteractiveTravelState.model_validate(payload)
    assert state.plan is not None
    assert state.plan.tasks[0].explanation is None
    assert state.plan.tasks[0].choiceOutcomes == ["А" * 700, "Исход Б", "Исход В", "Исход Г"]

    tasks[0]["choiceOutcomes"] = ["Исход А", "Исход Б", "Исход В"]
    with pytest.raises(ValidationError):
        InteractiveTravelState.model_validate(payload)

    tasks[0]["choiceOutcomes"] = ["А" * 701, "Исход Б", "Исход В", "Исход Г"]
    with pytest.raises(ValidationError):
        InteractiveTravelState.model_validate(payload)


def test_ready_part_must_match_planned_bank_fields() -> None:
    payload = _ready_state_payload()
    parts = payload["parts"]
    assert isinstance(parts, list)
    assert isinstance(parts[0], dict)
    parts[0]["challenge"] = "Переписанный вопрос?"

    with pytest.raises(ValidationError):
        InteractiveTravelState.model_validate(payload)


@pytest.mark.parametrize(
    "request_schema",
    [StartInteractiveTravelRequest, ContinueInteractiveTravelRequest],
)
def test_interactive_travel_text_requests_exclude_history_and_memory(request_schema) -> None:
    properties = request_schema.model_json_schema()["properties"]

    assert "history" not in properties
    assert "memoryContext" not in properties
