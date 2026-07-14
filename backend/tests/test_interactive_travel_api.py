from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_telegram_user
from app.main import app
from app.schemas import (
    InteractiveTravelAnimationResponse,
    InteractiveTravelIllustrationResponse,
    InteractiveTravelSuggestionsResponse,
)
from app.services.telegram_auth_service import TelegramUserContext


def _user() -> TelegramUserContext:
    return TelegramUserContext(
        telegram_id=42,
        username="serge",
        first_name="Serge",
        language_code="ru",
        auth_date=datetime.now(UTC),
    )


def _pet_payload() -> dict:
    return {
        "name": "Мяу",
        "description": "маленькая смелая кошка",
        "stage": "teen",
        "mood": "idle",
        "stats": {"hunger": 70, "happiness": 80, "energy": 90},
        "characterBible": {"identity": {"species": "кошка"}},
        "assetImages": {"teen": {"idle": "https://cdn.example.test/pets/miau-idle.png"}},
    }


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=False,
            enable_in_memory_rate_limit=False,
            interactive_travel_pilot_telegram_ids={42},
        ),
    )
    app.dependency_overrides[get_telegram_user] = _user
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_travel_routes_reject_users_outside_pilot(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=False,
            enable_in_memory_rate_limit=False,
            interactive_travel_pilot_telegram_ids={62943754},
        ),
    )
    app.dependency_overrides[get_telegram_user] = _user
    try:
        response = TestClient(app).post(
            "/api/travel/interactive/suggestions",
            json={"pet": _pet_payload()},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INTERACTIVE_TRAVEL_NOT_AVAILABLE"


def test_suggestions_route_returns_three_destinations(client, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_suggestions(*, pet, include_debug: bool):
        captured.update(
            pet_name=pet.name,
            asset_images=pet.assetImages,
            include_debug=include_debug,
        )
        return InteractiveTravelSuggestionsResponse(
            destinations=["К старому маяку", "В город облаков", "На ярмарку теней"]
        )

    monkeypatch.setattr(
        "app.routers.tma.generate_interactive_travel_suggestions",
        fake_suggestions,
    )

    response = client.post(
        "/api/travel/interactive/suggestions",
        json={"pet": _pet_payload()},
    )

    assert response.status_code == 200
    assert response.json() == {
        "destinations": ["К старому маяку", "В город облаков", "На ярмарку теней"]
    }
    assert captured["pet_name"] == "Мяу"
    assert captured["asset_images"] is not None
    assert captured["include_debug"] is False


def test_illustrate_route_passes_bounded_part_data(client, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_illustrate(**kwargs):
        captured.update(kwargs)
        return InteractiveTravelIllustrationResponse(
            partNumber=2,
            imageUrl="/static/generated/travel/interactive-travel-part-02.png?v=1",
        )

    monkeypatch.setattr(
        "app.routers.tma.illustrate_interactive_travel_part",
        fake_illustrate,
    )

    response = client.post(
        "/api/travel/interactive/illustrate",
        json={
            "pet": _pet_payload(),
            "travelId": "interactive-travel-abc123",
            "destination": "облачный город",
            "partNumber": 2,
            "title": "Мост над пропастью",
            "storyText": "Передо мной закрывается мост.",
        },
    )

    assert response.status_code == 200
    assert response.json()["partNumber"] == 2
    assert captured["travel_id"] == "interactive-travel-abc123"
    assert captured["destination"] == "облачный город"
    assert captured["part_number"] == 2
    assert captured["pet"].assetImages is not None


def test_illustrate_route_rejects_unsafe_travel_id(client) -> None:
    response = client.post(
        "/api/travel/interactive/illustrate",
        json={
            "pet": _pet_payload(),
            "travelId": "../../outside",
            "destination": "город",
            "partNumber": 1,
            "title": "Начало",
            "storyText": "История начинается.",
        },
    )

    assert response.status_code == 422


def test_animate_route_uses_generated_part_identity(client, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_animate(**kwargs):
        captured.update(kwargs)
        return InteractiveTravelAnimationResponse(
            partNumber=2,
            videoUrl="/static/generated/travel/interactive-travel-part-02.mp4?v=1",
        )

    monkeypatch.setattr("app.routers.tma.animate_interactive_travel_part", fake_animate)

    response = client.post(
        "/api/travel/interactive/animate",
        json={"travelId": "interactive-travel-abc123", "partNumber": 2},
    )

    assert response.status_code == 200
    assert response.json()["videoUrl"].endswith("interactive-travel-part-02.mp4?v=1")
    assert captured == {"travel_id": "interactive-travel-abc123", "part_number": 2}
