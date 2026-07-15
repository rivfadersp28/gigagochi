from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.dependencies import get_telegram_user
from app.main import app
from app.routers import tma as tma_router
from app.schemas import (
    InteractiveTravelAnimationResponse,
    InteractiveTravelIllustrationResponse,
    InteractiveTravelResponse,
    InteractiveTravelSuggestionsResponse,
    LocalPetChatContext,
)
from app.services.interactive_travel_session_store import (
    InteractiveTravelSessionStore,
    interactive_travel_state_fingerprint,
)
from app.services.rate_limit_service import get_rate_limiter
from app.services.telegram_auth_service import TelegramUserContext


def _user(telegram_id: int = 42) -> TelegramUserContext:
    return TelegramUserContext(
        telegram_id=telegram_id,
        username="serge",
        first_name="Serge",
        language_code="ru",
        auth_date=datetime.now(UTC),
    )


def _pet_payload() -> dict:
    return {
        "petId": "pet-stable-1",
        "name": "Мяу",
        "description": "маленькая смелая кошка",
        "stage": "teen",
        "mood": "idle",
        "stats": {"hunger": 70, "happiness": 80, "energy": 90},
        "characterBible": {"identity": {"species": "кошка"}},
        "assetImages": {"teen": {"idle": "https://cdn.example.test/pets/miau-idle.png"}},
    }


def test_debug_demo_returns_prebuilt_story_without_generation(monkeypatch) -> None:
    settings = SimpleNamespace(
        allow_dev_tma_auth=False,
        diagnostic_telegram_ids={42},
    )
    monkeypatch.setattr("app.routers.tma.get_settings", lambda: settings)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("demo endpoint reached a generator")

    monkeypatch.setattr("app.routers.tma.start_interactive_travel", fail_if_called)
    monkeypatch.setattr("app.routers.tma.continue_interactive_travel", fail_if_called)
    monkeypatch.setattr("app.routers.tma.illustrate_interactive_travel_part", fail_if_called)
    monkeypatch.setattr("app.routers.tma.animate_interactive_travel_part", fail_if_called)
    app.dependency_overrides[get_telegram_user] = _user
    try:
        response = TestClient(app).get("/api/travel/interactive/debug/demo")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["demoId"] == "sergey-latest-2026-07-15"
    assert len(payload["travel"]["parts"]) == 3
    assert all(part["backgroundVideoUrl"] for part in payload["travel"]["parts"])


def test_pet_fingerprint_uses_stable_pet_id_with_legacy_fallback() -> None:
    original = LocalPetChatContext.model_validate(_pet_payload())
    changed = LocalPetChatContext.model_validate(
        {
            **_pet_payload(),
            "name": "Новое имя",
            "assetImages": {"teen": {"idle": "https://cdn.example.test/pets/new-derived-idle.png"}},
        }
    )
    different_pet = changed.model_copy(update={"petId": "pet-stable-2"})

    assert tma_router._interactive_travel_pet_fingerprint(original) == (
        tma_router._interactive_travel_pet_fingerprint(changed)
    )
    assert tma_router._interactive_travel_pet_fingerprint(original) != (
        tma_router._interactive_travel_pet_fingerprint(different_pet)
    )

    legacy = original.model_copy(update={"petId": None})
    changed_legacy = changed.model_copy(update={"petId": None})
    assert tma_router._interactive_travel_pet_fingerprint(legacy) != (
        tma_router._interactive_travel_pet_fingerprint(changed_legacy)
    )


def _travel_response(travel_id: str) -> InteractiveTravelResponse:
    return InteractiveTravelResponse.model_validate(
        {
            "travel": {
                "travelId": travel_id,
                "generatedAt": datetime.now(UTC),
                "destination": "облачный город",
                "overallTitle": "Путешествие",
                "arcPlan": {"goal": "добраться до башни"},
                "parts": [
                    {
                        "partNumber": 1,
                        "title": "Начало",
                        "storyText": "Передо мной появляется мост.",
                        "challenge": "Как перейти мост?",
                        "actionSuggestions": ["Осмотреться"],
                    }
                ],
            }
        }
    )


def _continued_travel_response(travel_id: str) -> InteractiveTravelResponse:
    payload = _travel_response(travel_id).travel.model_dump(mode="json")
    payload["parts"][0].update(
        {
            "answer": "Осмотреться",
            "result": {
                "text": "Я нахожу безопасную тропу.",
                "adviceAssessment": "helpful",
                "reaction": "Отличный совет!",
                "reactionTone": "determined",
                "consequence": "Путь найден.",
                "outcomeValence": "positive",
                "statImpacts": [],
            },
        }
    )
    payload["parts"].append(
        {
            "partNumber": 2,
            "title": "Часть 2",
            "storyText": "Я подхожу к башне.",
            "transition": {"elapsedHours": 0, "summary": "Путь найден."},
            "challenge": "Как открыть дверь?",
            "actionSuggestions": ["Постучать"],
        }
    )
    return InteractiveTravelResponse.model_validate({"travel": payload})


def _completed_travel_response(travel_id: str) -> InteractiveTravelResponse:
    payload = _continued_travel_response(travel_id).travel.model_dump(mode="json")
    result = {
        "text": "Я нахожу верный путь.",
        "adviceAssessment": "helpful",
        "reaction": "Получилось!",
        "reactionTone": "enthusiastic",
        "consequence": "Путь открыт.",
        "outcomeValence": "positive",
        "statImpacts": [],
    }
    payload["parts"][1].update({"answer": "Постучать", "result": result})
    payload["parts"].append(
        {
            "partNumber": 3,
            "title": "Финал",
            "storyText": "Я добираюсь до вершины башни.",
            "transition": {"elapsedHours": 1, "summary": "Дверь открылась."},
            "challenge": "Как завершить путь?",
            "actionSuggestions": [],
            "answer": "Поднять флаг",
            "result": result,
        }
    )
    payload["completed"] = True
    payload["outcomeValence"] = "positive"
    return InteractiveTravelResponse.model_validate({"travel": payload})


def _seed_authoritative_session(
    path,
    travel: InteractiveTravelResponse,
    *,
    telegram_id: int = 42,
) -> InteractiveTravelSessionStore:
    store = InteractiveTravelSessionStore(path)
    store.register_owner(travel.travel.travelId, telegram_id)
    now = datetime.now(UTC).isoformat()
    state_fingerprint = interactive_travel_state_fingerprint(travel.travel)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO interactive_travel_sessions (
                travel_id, telegram_id, pet_fingerprint, start_fingerprint,
                state_json, state_fingerprint, response_json, revision,
                completed_at, created_at, updated_at
            ) VALUES (?, ?, ?, 'synthetic-seed', ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                travel.travel.travelId,
                telegram_id,
                tma_router._interactive_travel_pet_fingerprint(
                    LocalPetChatContext.model_validate(_pet_payload())
                ),
                travel.travel.model_dump_json(),
                state_fingerprint,
                travel.model_dump_json(),
                now if travel.travel.completed else None,
                now,
                now,
            ),
        )
    return store


def _seed_completed_authoritative_session(
    path,
    travel_id: str,
) -> InteractiveTravelResponse:
    completed = _completed_travel_response(travel_id)
    _seed_authoritative_session(path, completed)
    return completed


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    owner_path = tmp_path / "owners.sqlite3"
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=False,
            enable_in_memory_rate_limit=False,
            interactive_travel_pilot_telegram_ids={42},
            interactive_travel_owner_store_path=str(owner_path),
        ),
    )
    _seed_authoritative_session(
        owner_path,
        _travel_response("interactive-travel-abc123"),
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
            partNumber=1,
            imageUrl="/static/generated/travel/interactive-travel-part-01.png?v=1",
        )

    monkeypatch.setattr(
        "app.routers.tma.illustrate_interactive_travel_part",
        fake_illustrate,
    )
    monkeypatch.setattr(
        "app.routers.tma.patch_interactive_travel_finale_media",
        lambda travel_id, **kwargs: captured.update(archive=(travel_id, kwargs)) or True,
    )

    response = client.post(
        "/api/travel/interactive/illustrate",
        json={
            "pet": _pet_payload(),
            "travelId": "interactive-travel-abc123",
            "destination": "облачный город",
            "partNumber": 1,
            "title": "Начало",
            "storyText": "Передо мной появляется мост.",
        },
    )

    assert response.status_code == 200
    assert response.json()["partNumber"] == 1
    assert captured["travel_id"] == "interactive-travel-abc123"
    assert captured["destination"] == "облачный город"
    assert captured["part_number"] == 1
    assert captured["pet"].assetImages is not None
    assert captured["archive"] == (
        "interactive-travel-abc123",
        {
            "part_number": 1,
            "image_url": "/static/generated/travel/interactive-travel-part-01.png?v=1",
        },
    )


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
            partNumber=1,
            videoUrl="/static/generated/travel/interactive-travel-part-01.mp4?v=1",
        )

    monkeypatch.setattr("app.routers.tma.animate_interactive_travel_part", fake_animate)
    monkeypatch.setattr(
        "app.routers.tma.patch_interactive_travel_finale_media",
        lambda travel_id, **kwargs: captured.update(archive=(travel_id, kwargs)) or True,
    )

    response = client.post(
        "/api/travel/interactive/animate",
        json={"travelId": "interactive-travel-abc123", "partNumber": 1},
    )

    assert response.status_code == 200
    assert response.json()["videoUrl"].endswith("interactive-travel-part-01.mp4?v=1")
    assert captured == {
        "travel_id": "interactive-travel-abc123",
        "part_number": 1,
        "archive": (
            "interactive-travel-abc123",
            {
                "part_number": 1,
                "video_url": "/static/generated/travel/interactive-travel-part-01.mp4?v=1",
            },
        ),
    }


@pytest.mark.parametrize(
    "mutation",
    ["pet", "destination", "part", "title", "story"],
)
def test_illustrate_rejects_mutated_authoritative_fields_before_side_effects(
    client,
    monkeypatch,
    mutation,
) -> None:
    payload = {
        "pet": _pet_payload(),
        "travelId": "interactive-travel-abc123",
        "destination": "облачный город",
        "partNumber": 1,
        "title": "Начало",
        "storyText": "Передо мной появляется мост.",
    }
    if mutation == "pet":
        payload["pet"]["petId"] = "pet-stable-2"
    elif mutation == "destination":
        payload["destination"] = "подменённый город"
    elif mutation == "part":
        payload["partNumber"] = 2
    elif mutation == "title":
        payload["title"] = "Подменённый заголовок"
    else:
        payload["storyText"] = "Подменённая история."

    monkeypatch.setattr(
        "app.routers.tma.illustrate_interactive_travel_part",
        lambda **_kwargs: pytest.fail("mutated request reached image provider"),
    )
    monkeypatch.setattr(
        "app.routers.tma.patch_interactive_travel_finale_media",
        lambda *_args, **_kwargs: pytest.fail("mutated request reached finale file"),
    )

    response = client.post("/api/travel/interactive/illustrate", json=payload)

    assert response.status_code == 409
    expected_code = (
        "INTERACTIVE_TRAVEL_PET_MISMATCH"
        if mutation == "pet"
        else "INTERACTIVE_TRAVEL_STATE_CONFLICT"
    )
    assert response.json()["detail"]["code"] == expected_code


def test_animate_rejects_unknown_authoritative_part_before_side_effects(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.routers.tma.animate_interactive_travel_part",
        lambda **_kwargs: pytest.fail("unknown part reached video provider"),
    )
    monkeypatch.setattr(
        "app.routers.tma.patch_interactive_travel_finale_media",
        lambda *_args, **_kwargs: pytest.fail("unknown part reached finale file"),
    )

    response = client.post(
        "/api/travel/interactive/animate",
        json={"travelId": "interactive-travel-abc123", "partNumber": 2},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INTERACTIVE_TRAVEL_STATE_CONFLICT"


@pytest.mark.parametrize("session_state", ["missing", "cancelled"])
def test_media_rejects_non_authoritative_or_unsuitable_session_before_provider(
    monkeypatch,
    tmp_path,
    session_state,
) -> None:
    owner_path = tmp_path / "owners.sqlite3"
    store = InteractiveTravelSessionStore(owner_path)
    travel_id = "interactive-travel-unsuitable"
    if session_state == "cancelled":
        _seed_authoritative_session(owner_path, _travel_response(travel_id))
        store.cancel(travel_id, 42)
    else:
        store.register_owner(travel_id, 42)
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=False,
            enable_in_memory_rate_limit=False,
            interactive_travel_pilot_telegram_ids={42},
            interactive_travel_owner_store_path=str(owner_path),
        ),
    )
    monkeypatch.setattr(
        "app.routers.tma.illustrate_interactive_travel_part",
        lambda **_kwargs: pytest.fail("unsuitable session reached image provider"),
    )
    app.dependency_overrides[get_telegram_user] = _user
    try:
        response = TestClient(app).post(
            "/api/travel/interactive/illustrate",
            json={
                "pet": _pet_payload(),
                "travelId": travel_id,
                "destination": "облачный город",
                "partNumber": 1,
                "title": "Начало",
                "storyText": "Передо мной появляется мост.",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409


def test_media_result_is_not_persisted_after_narrative_state_changes(
    monkeypatch,
    tmp_path,
) -> None:
    owner_path = tmp_path / "owners.sqlite3"
    started = _travel_response("interactive-travel-media-race")
    store = _seed_authoritative_session(owner_path, started)
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=False,
            enable_in_memory_rate_limit=False,
            interactive_travel_pilot_telegram_ids={42},
            interactive_travel_owner_store_path=str(owner_path),
        ),
    )

    def advance_state_during_provider(**kwargs):
        attempt = store.preflight_continue(
            telegram_id=42,
            pet_fingerprint=tma_router._interactive_travel_pet_fingerprint(
                LocalPetChatContext.model_validate(_pet_payload())
            ),
            travel=started.travel,
            request_fingerprint="synthetic-concurrent-advice",
        )
        store.commit_continue(attempt, _continued_travel_response(started.travel.travelId))
        return InteractiveTravelIllustrationResponse(
            partNumber=kwargs["part_number"],
            imageUrl="/static/generated/synthetic/stale-part.png?v=1",
        )

    monkeypatch.setattr(
        "app.routers.tma.illustrate_interactive_travel_part",
        advance_state_during_provider,
    )
    monkeypatch.setattr(
        "app.routers.tma.patch_interactive_travel_finale_media",
        lambda *_args, **_kwargs: pytest.fail("stale media reached finale persistence"),
    )
    app.dependency_overrides[get_telegram_user] = _user
    try:
        response = TestClient(app).post(
            "/api/travel/interactive/illustrate",
            json={
                "pet": _pet_payload(),
                "travelId": started.travel.travelId,
                "destination": started.travel.destination,
                "partNumber": 1,
                "title": started.travel.parts[0].title,
                "storyText": started.travel.parts[0].storyText,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INTERACTIVE_TRAVEL_STATE_CONFLICT"


def test_finale_capture_requires_completed_exact_authoritative_state(
    monkeypatch,
    tmp_path,
) -> None:
    travel_id = "interactive-travel-authoritative-finale"
    owner_path = tmp_path / "owners.sqlite3"
    completed = _seed_completed_authoritative_session(owner_path, travel_id)
    settings = SimpleNamespace(
        allow_dev_tma_auth=False,
        enable_in_memory_rate_limit=False,
        interactive_travel_pilot_telegram_ids={42},
        interactive_travel_owner_store_path=str(owner_path),
    )
    saves: list[InteractiveTravelResponse] = []
    monkeypatch.setattr("app.routers.tma.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.routers.tma.save_interactive_travel_finale",
        lambda travel, **_kwargs: saves.append(InteractiveTravelResponse(travel=travel)),
    )
    app.dependency_overrides[get_telegram_user] = _user
    api = TestClient(app)
    stale_payload = completed.travel.model_dump(mode="json")
    stale_payload["parts"][0]["storyText"] = "Подменённая завершённая история."
    try:
        stale = api.post(
            "/api/travel/interactive/finale/capture",
            json={"travel": stale_payload},
        )
        exact = api.post(
            "/api/travel/interactive/finale/capture",
            json={"travel": completed.travel.model_dump(mode="json")},
        )
    finally:
        app.dependency_overrides.clear()

    assert stale.status_code == 409
    assert saves == [completed]
    assert exact.status_code == 200
    assert exact.json() == {"saved": True}


def test_finale_capture_rejects_active_state_before_file_write(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.tma.save_interactive_travel_finale",
        lambda *_args, **_kwargs: pytest.fail("active state reached finale file"),
    )

    response = client.post(
        "/api/travel/interactive/finale/capture",
        json={
            "travel": _travel_response("interactive-travel-abc123").travel.model_dump(mode="json")
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INTERACTIVE_TRAVEL_STATE_CONFLICT"


def test_media_admission_rejects_user_and_global_before_sync_provider(
    monkeypatch,
    tmp_path,
) -> None:
    owner_path = tmp_path / "owners.sqlite3"
    _seed_authoritative_session(
        owner_path,
        _travel_response("interactive-travel-owner-42"),
        telegram_id=42,
    )
    _seed_authoritative_session(
        owner_path,
        _travel_response("interactive-travel-owner-43"),
        telegram_id=43,
    )
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=False,
            enable_in_memory_rate_limit=False,
            interactive_travel_pilot_telegram_ids={42, 43},
            interactive_travel_owner_store_path=str(owner_path),
            http_media_global_concurrency=1,
            http_media_per_user_concurrency=1,
            http_admission_retry_after_seconds=9,
        ),
    )
    entered = Event()
    release = Event()
    calls = 0

    def blocking_illustrate(**kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=3)
        return InteractiveTravelIllustrationResponse(
            partNumber=kwargs["part_number"],
            imageUrl="/static/generated/synthetic/part.png?v=1",
        )

    async def user_from_header(request: Request) -> TelegramUserContext:
        return _user(int(request.headers.get("x-test-user", "42")))

    monkeypatch.setattr(
        "app.routers.tma.illustrate_interactive_travel_part",
        blocking_illustrate,
    )
    monkeypatch.setattr(
        "app.routers.tma.patch_interactive_travel_finale_media",
        lambda *_args, **_kwargs: False,
    )
    app.dependency_overrides[get_telegram_user] = user_from_header
    api = TestClient(app)

    def payload(travel_id: str) -> dict[str, object]:
        return {
            "pet": _pet_payload(),
            "travelId": travel_id,
            "destination": "облачный город",
            "partNumber": 1,
            "title": "Начало",
            "storyText": "Передо мной появляется мост.",
        }

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(
                api.post,
                "/api/travel/interactive/illustrate",
                headers={"x-test-user": "42"},
                json=payload("interactive-travel-owner-42"),
            )
            assert entered.wait(timeout=2)
            try:
                same_user = api.post(
                    "/api/travel/interactive/illustrate",
                    headers={"x-test-user": "42"},
                    json=payload("interactive-travel-owner-42"),
                )
                other_user = api.post(
                    "/api/travel/interactive/illustrate",
                    headers={"x-test-user": "43"},
                    json=payload("interactive-travel-owner-43"),
                )
            finally:
                release.set()
            first_response = first.result(timeout=2)
    finally:
        app.dependency_overrides.clear()

    assert first_response.status_code == 200
    assert same_user.status_code == 429
    assert same_user.json()["detail"]["code"] == "REQUEST_ADMISSION_USER_LIMIT"
    assert same_user.headers["Retry-After"] == "9"
    assert other_user.status_code == 503
    assert other_user.json()["detail"]["code"] == "REQUEST_ADMISSION_GLOBAL_LIMIT"
    assert other_user.headers["Retry-After"] == "9"
    assert calls == 1


def test_start_persists_private_owner_before_return(monkeypatch, tmp_path) -> None:
    owner_path = tmp_path / "owners.sqlite3"
    monkeypatch.setattr(
        "app.routers.tma.get_settings",
        lambda: SimpleNamespace(
            allow_dev_tma_auth=False,
            enable_in_memory_rate_limit=False,
            interactive_travel_pilot_telegram_ids={42},
            interactive_travel_owner_store_path=str(owner_path),
        ),
    )
    monkeypatch.setattr(
        "app.routers.tma.start_interactive_travel",
        lambda **kwargs: _travel_response(kwargs["travel_id"]),
    )

    app.dependency_overrides[get_telegram_user] = _user
    try:
        response = TestClient(app).post(
            "/api/travel/interactive/start",
            json={"pet": _pet_payload(), "destination": "облачный город"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    travel_id = response.json()["travel"]["travelId"]
    owner = InteractiveTravelSessionStore(owner_path).get_owner(travel_id)
    assert owner is not None
    assert owner.telegram_id == 42
    assert owner.cancelled_at is None


def test_concurrent_start_commits_one_result_and_replays_afterward(
    monkeypatch,
    tmp_path,
) -> None:
    owner_path = tmp_path / "owners.sqlite3"
    settings = SimpleNamespace(
        allow_dev_tma_auth=False,
        enable_in_memory_rate_limit=False,
        interactive_travel_pilot_telegram_ids={42},
        interactive_travel_owner_store_path=str(owner_path),
        http_llm_global_concurrency=4,
        http_llm_per_user_concurrency=2,
    )
    generation_barrier = Barrier(2)
    calls = 0

    def concurrent_start(**kwargs):
        nonlocal calls
        calls += 1
        generation_barrier.wait(timeout=3)
        return _travel_response(kwargs["travel_id"])

    monkeypatch.setattr("app.routers.tma.get_settings", lambda: settings)
    monkeypatch.setattr("app.routers.tma.start_interactive_travel", concurrent_start)
    app.dependency_overrides[get_telegram_user] = _user
    api = TestClient(app)
    payload = {"pet": _pet_payload(), "destination": "облачный город"}
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    api.post,
                    "/api/travel/interactive/start",
                    json=payload,
                )
                for _ in range(2)
            ]
            concurrent_responses = [future.result(timeout=4) for future in futures]

        replay = api.post("/api/travel/interactive/start", json=payload)
        changed = api.post(
            "/api/travel/interactive/start",
            json={"pet": _pet_payload(), "destination": "другой город"},
        )
    finally:
        app.dependency_overrides.clear()

    assert [response.status_code for response in concurrent_responses] == [200, 200]
    assert concurrent_responses[0].json() == concurrent_responses[1].json()
    assert replay.status_code == 200
    assert replay.json() == concurrent_responses[0].json()
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "INTERACTIVE_TRAVEL_ALREADY_ACTIVE"
    assert calls == 2


def test_concurrent_continue_accepts_one_advice_and_replays_only_that_retry(
    monkeypatch,
    tmp_path,
) -> None:
    travel_id = "interactive-travel-concurrent-continue"
    owner_path = tmp_path / "owners.sqlite3"
    InteractiveTravelSessionStore(owner_path).register_owner(travel_id, 42)
    settings = SimpleNamespace(
        allow_dev_tma_auth=False,
        enable_in_memory_rate_limit=False,
        interactive_travel_pilot_telegram_ids={42},
        interactive_travel_owner_store_path=str(owner_path),
        http_llm_global_concurrency=4,
        http_llm_per_user_concurrency=2,
    )
    generation_barrier = Barrier(2)
    calls = 0

    def concurrent_continue(**_kwargs):
        nonlocal calls
        calls += 1
        generation_barrier.wait(timeout=3)
        return _continued_travel_response(travel_id)

    monkeypatch.setattr("app.routers.tma.get_settings", lambda: settings)
    monkeypatch.setattr("app.routers.tma.continue_interactive_travel", concurrent_continue)
    app.dependency_overrides[get_telegram_user] = _user
    api = TestClient(app)
    base_payload = {
        "pet": _pet_payload(),
        "travel": _travel_response(travel_id).travel.model_dump(mode="json"),
    }
    first_payload = base_payload | {"advice": "Осмотреться"}
    second_payload = base_payload | {"advice": "Прыгнуть вслепую"}
    payloads = [first_payload, second_payload]
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    api.post,
                    "/api/travel/interactive/continue",
                    json=payload,
                )
                for payload in payloads
            ]
            responses = [future.result(timeout=4) for future in futures]

        winner_index = next(
            index for index, response in enumerate(responses) if response.status_code == 200
        )
        loser_index = 1 - winner_index
        replay = api.post("/api/travel/interactive/continue", json=payloads[winner_index])
        stale = api.post("/api/travel/interactive/continue", json=payloads[loser_index])
    finally:
        app.dependency_overrides.clear()

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert responses[loser_index].json()["detail"]["code"] == ("INTERACTIVE_TRAVEL_STATE_CONFLICT")
    assert replay.status_code == 200
    assert replay.json() == responses[winner_index].json()
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "INTERACTIVE_TRAVEL_STATE_CONFLICT"
    assert calls == 2


def test_failed_start_refunds_quota_without_persisting_placeholder(
    monkeypatch,
    tmp_path,
) -> None:
    owner_path = tmp_path / "owners.sqlite3"
    rate_path = tmp_path / "rate.sqlite3"
    settings = SimpleNamespace(
        allow_dev_tma_auth=False,
        enable_in_memory_rate_limit=True,
        interactive_travel_pilot_telegram_ids={42},
        interactive_travel_owner_store_path=str(owner_path),
        rate_limit_store_path=str(rate_path),
        interactive_travel_rate_limit_per_day=1,
    )
    travel_ids: list[str] = []

    def fail_then_succeed(**kwargs):
        travel_ids.append(kwargs["travel_id"])
        if len(travel_ids) == 1:
            raise RuntimeError("synthetic text failure")
        return _travel_response(kwargs["travel_id"])

    monkeypatch.setattr("app.routers.tma.get_settings", lambda: settings)
    monkeypatch.setattr("app.routers.tma.start_interactive_travel", fail_then_succeed)
    app.dependency_overrides[get_telegram_user] = _user
    payload = {"pet": _pet_payload(), "destination": "облачный город"}
    try:
        api = TestClient(app)
        failed = api.post("/api/travel/interactive/start", json=payload)
        succeeded = api.post("/api/travel/interactive/start", json=payload)
        replay = api.post("/api/travel/interactive/start", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert failed.status_code == 502
    assert succeeded.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == succeeded.json()
    assert len(travel_ids) == 2
    assert travel_ids[0] != travel_ids[1]

    store = InteractiveTravelSessionStore(owner_path)
    assert store.get(travel_ids[0]) is None
    assert store.get_owner(travel_ids[0]) is None
    with sqlite3.connect(rate_path) as connection:
        event_count = connection.execute(
            """
            SELECT COUNT(*) FROM rate_limit_events
            WHERE bucket = 'interactive_travel' AND user_id = 42
            """
        ).fetchone()
    assert event_count == (1,)


def test_rate_rejected_start_discards_unowned_session_before_generation(
    monkeypatch,
    tmp_path,
) -> None:
    owner_path = tmp_path / "owners.sqlite3"
    rate_path = tmp_path / "rate.sqlite3"
    settings = SimpleNamespace(
        allow_dev_tma_auth=False,
        enable_in_memory_rate_limit=True,
        interactive_travel_pilot_telegram_ids={42},
        interactive_travel_owner_store_path=str(owner_path),
        rate_limit_store_path=str(rate_path),
        interactive_travel_rate_limit_per_day=1,
    )
    get_rate_limiter(rate_path).check(
        "interactive_travel",
        42,
        limit=1,
        window=timedelta(days=1),
        request_key="synthetic-existing-operation",
    )
    monkeypatch.setattr("app.routers.tma.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.routers.tma.start_interactive_travel",
        lambda **_kwargs: pytest.fail("rate-rejected start reached text generation"),
    )
    app.dependency_overrides[get_telegram_user] = _user
    try:
        response = TestClient(app).post(
            "/api/travel/interactive/start",
            json={"pet": _pet_payload(), "destination": "облачный город"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 429
    with sqlite3.connect(owner_path) as connection:
        session_count = connection.execute(
            "SELECT COUNT(*) FROM interactive_travel_sessions"
        ).fetchone()
        owner_table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'interactive_travel_owners'
            """
        ).fetchone()
        owner_count = (
            connection.execute("SELECT COUNT(*) FROM interactive_travel_owners").fetchone()
            if owner_table is not None
            else (0,)
        )
    assert session_count == (0,)
    assert owner_count == (0,)


def test_failed_continue_refunds_quota_for_retry(monkeypatch, tmp_path) -> None:
    travel_id = "interactive-travel-continue-refund"
    owner_path = tmp_path / "owners.sqlite3"
    rate_path = tmp_path / "rate.sqlite3"
    InteractiveTravelSessionStore(owner_path).register_owner(travel_id, 42)
    settings = SimpleNamespace(
        allow_dev_tma_auth=False,
        enable_in_memory_rate_limit=True,
        interactive_travel_pilot_telegram_ids={42},
        interactive_travel_owner_store_path=str(owner_path),
        rate_limit_store_path=str(rate_path),
        interactive_travel_rate_limit_per_day=1,
    )
    calls = 0

    def fail_then_succeed(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic text failure")
        return _continued_travel_response(travel_id)

    monkeypatch.setattr("app.routers.tma.get_settings", lambda: settings)
    monkeypatch.setattr("app.routers.tma.continue_interactive_travel", fail_then_succeed)
    app.dependency_overrides[get_telegram_user] = _user
    payload = {
        "pet": _pet_payload(),
        "travel": _travel_response(travel_id).travel.model_dump(mode="json"),
        "advice": "Осмотреться",
    }
    try:
        api = TestClient(app)
        failed = api.post("/api/travel/interactive/continue", json=payload)
        succeeded = api.post("/api/travel/interactive/continue", json=payload)
        replay = api.post("/api/travel/interactive/continue", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert failed.status_code == 502
    assert succeeded.status_code == 200
    assert replay.status_code == 200
    assert calls == 2
    persisted = InteractiveTravelSessionStore(owner_path).get(travel_id)
    assert persisted is not None
    assert persisted.revision == 1


@pytest.mark.parametrize(
    "route_kind",
    ["illustrate", "animate", "continue", "capture", "cancel", "reset"],
)
def test_existing_travel_rejects_cross_owner_before_media_or_reset(
    route_kind,
    monkeypatch,
    tmp_path,
) -> None:
    travel_id = "interactive-travel-private-owner"
    owner_path = tmp_path / "owners.sqlite3"
    InteractiveTravelSessionStore(owner_path).register_owner(travel_id, 42)
    settings = SimpleNamespace(
        allow_dev_tma_auth=False,
        enable_in_memory_rate_limit=False,
        interactive_travel_pilot_telegram_ids={42, 43},
        diagnostic_telegram_ids={43},
        interactive_travel_owner_store_path=str(owner_path),
    )
    monkeypatch.setattr("app.routers.tma.get_settings", lambda: settings)
    called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("cross-owner request reached media/reset")

    monkeypatch.setattr("app.routers.tma.illustrate_interactive_travel_part", fail_if_called)
    monkeypatch.setattr("app.routers.tma.animate_interactive_travel_part", fail_if_called)
    monkeypatch.setattr("app.routers.tma.continue_interactive_travel", fail_if_called)
    monkeypatch.setattr("app.routers.tma.save_interactive_travel_finale", fail_if_called)
    monkeypatch.setattr("app.routers.tma.cancel_interactive_travel_generation", fail_if_called)
    monkeypatch.setattr("app.routers.tma.reset_interactive_travel_generation", fail_if_called)
    app.dependency_overrides[get_telegram_user] = lambda: _user(43)
    try:
        api = TestClient(app)
        if route_kind == "illustrate":
            response = api.post(
                "/api/travel/interactive/illustrate",
                json={
                    "pet": _pet_payload(),
                    "travelId": travel_id,
                    "destination": "город",
                    "partNumber": 1,
                    "title": "Начало",
                    "storyText": "История начинается.",
                },
            )
        elif route_kind == "animate":
            response = api.post(
                "/api/travel/interactive/animate",
                json={"travelId": travel_id, "partNumber": 1},
            )
        elif route_kind == "continue":
            response = api.post(
                "/api/travel/interactive/continue",
                json={
                    "pet": _pet_payload(),
                    "travel": _travel_response(travel_id).travel.model_dump(mode="json"),
                    "advice": "Осмотреться",
                },
            )
        elif route_kind == "capture":
            response = api.post(
                "/api/travel/interactive/finale/capture",
                json={"travel": _travel_response(travel_id).travel.model_dump(mode="json")},
            )
        elif route_kind == "cancel":
            response = api.post(f"/api/travel/interactive/{travel_id}/cancel")
        else:
            response = api.post(f"/api/travel/interactive/{travel_id}/debug/reset")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "INTERACTIVE_TRAVEL_OWNER_MISMATCH"
    assert called is False


def test_owner_cancel_is_idempotent_without_diagnostic_or_quota_reset(
    monkeypatch,
    tmp_path,
) -> None:
    travel_id = "interactive-travel-owner-cancel"
    owner_path = tmp_path / "owners.sqlite3"
    owner_store = InteractiveTravelSessionStore(owner_path)
    owner_store.register_owner(travel_id, 42)
    settings = SimpleNamespace(
        allow_dev_tma_auth=False,
        interactive_travel_pilot_telegram_ids={42},
        interactive_travel_owner_store_path=str(owner_path),
        diagnostic_telegram_ids=set(),
    )
    lifecycle_calls: list[str] = []
    monkeypatch.setattr("app.routers.tma.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.routers.tma.cancel_interactive_travel_generation",
        lambda value: lifecycle_calls.append(value),
    )
    monkeypatch.setattr(
        "app.routers.tma.get_rate_limiter",
        lambda *_args, **_kwargs: pytest.fail("normal cancel must not touch rate limits"),
    )
    app.dependency_overrides[get_telegram_user] = _user
    try:
        api = TestClient(app)
        first = api.post(f"/api/travel/interactive/{travel_id}/cancel")
        second = api.post(f"/api/travel/interactive/{travel_id}/cancel")
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert first.json() == {"cancelled": True}
    assert second.status_code == 200
    assert second.json() == {"cancelled": True}
    assert lifecycle_calls == [travel_id, travel_id]
    cancelled = InteractiveTravelSessionStore(owner_path).get_owner(travel_id)
    assert cancelled is not None
    assert cancelled.cancelled_at is not None


@pytest.mark.parametrize("allowed_ids", [{42}, {42, 43}])
def test_unknown_pre_owner_travel_is_never_first_caller_claimed(
    monkeypatch,
    tmp_path,
    allowed_ids,
) -> None:
    settings = SimpleNamespace(
        allow_dev_tma_auth=False,
        enable_in_memory_rate_limit=False,
        interactive_travel_pilot_telegram_ids=allowed_ids,
        interactive_travel_owner_store_path=str(tmp_path / "owners.sqlite3"),
    )
    monkeypatch.setattr("app.routers.tma.get_settings", lambda: settings)
    app.dependency_overrides[get_telegram_user] = _user
    try:
        response = TestClient(app).post(
            "/api/travel/interactive/animate",
            json={"travelId": "interactive-travel-old-session", "partNumber": 1},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INTERACTIVE_TRAVEL_OWNER_UNKNOWN"
    assert (
        InteractiveTravelSessionStore(settings.interactive_travel_owner_store_path).get_owner(
            "interactive-travel-old-session"
        )
        is None
    )
