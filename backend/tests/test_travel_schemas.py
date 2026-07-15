import pytest
from pydantic import ValidationError

from app.schemas import (
    AnimateInteractiveTravelPartRequest,
    IllustrateInteractiveTravelPartRequest,
    InteractiveTravelPart,
    InteractiveTravelState,
    InteractiveTravelTransition,
    LocalPetChatContext,
    LocalPetPushSnapshotRequest,
    LocalPetStats,
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


@pytest.mark.parametrize(
    "arc_plan",
    [
        {f"key{index}": "value" for index in range(33)},
        {"key": "x" * 501},
        {"__proto__": "value"},
    ],
)
def test_interactive_travel_state_bounds_arc_plan(arc_plan: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        InteractiveTravelState(
            travelId="interactive-travel-synthetic",
            generatedAt="2026-07-15T12:00:00Z",
            destination="гора",
            overallTitle="Путь",
            arcPlan=arc_plan,
            parts=[
                InteractiveTravelPart(
                    partNumber=1,
                    title="Начало",
                    storyText="Я подошёл к тропе.",
                    challenge="Куда идти?",
                )
            ],
        )
