from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest

from app.schemas import InteractiveTravelResponse, InteractiveTravelState
from app.services.interactive_travel_session_store import (
    DEFAULT_INTERACTIVE_TRAVEL_STORE_PATH,
    InteractiveTravelActiveError,
    InteractiveTravelPetMismatchError,
    InteractiveTravelSessionCancelledError,
    InteractiveTravelSessionCapacityError,
    InteractiveTravelSessionOwnerMismatchError,
    InteractiveTravelSessionStore,
    InteractiveTravelStateConflictError,
    interactive_travel_state_fingerprint,
)


def _started_response(travel_id: str) -> InteractiveTravelResponse:
    return InteractiveTravelResponse.model_validate(
        {
            "travel": {
                "travelId": travel_id,
                "generatedAt": "2026-07-15T00:00:00Z",
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


def _continued_response(travel: InteractiveTravelState) -> InteractiveTravelResponse:
    payload = travel.model_dump(mode="json")
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
    return InteractiveTravelResponse(travel=InteractiveTravelState.model_validate(payload))


def _completed_response(travel: InteractiveTravelState) -> InteractiveTravelResponse:
    payload = travel.model_dump(mode="json")
    result = {
        "text": "Я нахожу верный путь.",
        "adviceAssessment": "helpful",
        "reaction": "Получилось!",
        "reactionTone": "enthusiastic",
        "consequence": "Путь открыт.",
        "outcomeValence": "positive",
        "statImpacts": [],
    }
    payload["parts"][-1].update({"answer": "Постучать", "result": result})
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
    return InteractiveTravelResponse(travel=InteractiveTravelState.model_validate(payload))


def _seed_started_session(
    store: InteractiveTravelSessionStore,
    *,
    telegram_id: int = 42,
) -> InteractiveTravelResponse:
    attempt = store.preflight_start(
        telegram_id=telegram_id,
        pet_fingerprint="pet-a",
        request_fingerprint="start-a",
    )
    response = _started_response(attempt.travel_id)
    assert store.commit_start(attempt, response).committed is True
    return response


def test_default_store_is_private_state_on_shared_generated_volume() -> None:
    path = Path(DEFAULT_INTERACTIVE_TRAVEL_STORE_PATH)

    assert path.parts[:2] == ("static", "generated")
    assert ".private" in path.parts
    assert path.suffix == ".sqlite3"


def test_store_has_two_semantic_tables_without_operation_lease_columns(tmp_path) -> None:
    path = tmp_path / "travel.sqlite3"
    InteractiveTravelSessionStore(path)

    with sqlite3.connect(path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(interactive_travel_sessions)")
        }

    assert {"interactive_travel_owners", "interactive_travel_sessions"} <= tables
    assert (
        not {
            "status",
            "operation_token",
            "operation_fingerprint",
            "operation_base_fingerprint",
            "operation_started_at",
        }
        & columns
    )


def test_owner_binding_cancellation_and_pruning_share_the_session_store(tmp_path) -> None:
    path = tmp_path / "travel.sqlite3"
    store = InteractiveTravelSessionStore(
        path,
        retention=timedelta(days=1),
        max_records=2,
    )
    store.register_owner("interactive-travel-active", 42)
    store.register_owner("interactive-travel-cancelled", 42)
    store.cancel("interactive-travel-cancelled", 42)
    with pytest.raises(InteractiveTravelSessionOwnerMismatchError):
        store.assert_active_owner("interactive-travel-active", 43)
    with pytest.raises(InteractiveTravelSessionCancelledError):
        store.assert_active_owner("interactive-travel-cancelled", 42)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE interactive_travel_owners
            SET cancelled_at = '2000-01-01T00:00:00+00:00'
            WHERE travel_id = 'interactive-travel-cancelled'
            """
        )

    restarted = InteractiveTravelSessionStore(
        path,
        retention=timedelta(days=1),
        max_records=2,
    )
    restarted.register_owner("interactive-travel-current", 42)

    assert restarted.get_owner("interactive-travel-active") is not None
    assert restarted.get_owner("interactive-travel-cancelled") is None
    with pytest.raises(InteractiveTravelSessionCapacityError):
        restarted.register_owner("interactive-travel-over-capacity", 42)


def test_two_start_generations_commit_one_durable_winner_and_replay_loser(tmp_path) -> None:
    path = tmp_path / "travel.sqlite3"
    first_store = InteractiveTravelSessionStore(path)
    second_store = InteractiveTravelSessionStore(path)
    first = first_store.preflight_start(
        telegram_id=42,
        pet_fingerprint="pet-a",
        request_fingerprint="start-a",
    )
    second = second_store.preflight_start(
        telegram_id=42,
        pet_fingerprint="pet-a",
        request_fingerprint="start-a",
    )
    barrier = Barrier(2)

    def commit(store, attempt):
        barrier.wait(timeout=2)
        return store.commit_start(attempt, _started_response(attempt.travel_id))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result(timeout=3)
            for future in (
                executor.submit(commit, first_store, first),
                executor.submit(commit, second_store, second),
            )
        ]

    assert sorted(result.committed for result in results) == [False, True]
    assert results[0].response == results[1].response
    replay = InteractiveTravelSessionStore(path).preflight_start(
        telegram_id=42,
        pet_fingerprint="pet-a",
        request_fingerprint="start-a",
    )
    assert replay.replay == results[0].response


def test_changed_start_request_conflicts_after_another_commit(tmp_path) -> None:
    store = InteractiveTravelSessionStore(tmp_path / "travel.sqlite3")
    first = store.preflight_start(
        telegram_id=42,
        pet_fingerprint="pet-a",
        request_fingerprint="start-a",
    )
    changed = store.preflight_start(
        telegram_id=42,
        pet_fingerprint="pet-a",
        request_fingerprint="start-b",
    )
    store.commit_start(first, _started_response(first.travel_id))

    with pytest.raises(InteractiveTravelActiveError):
        store.commit_start(changed, _started_response(changed.travel_id))


def test_continue_cas_replays_same_request_and_rejects_different_advice(tmp_path) -> None:
    store = InteractiveTravelSessionStore(tmp_path / "travel.sqlite3")
    started = _seed_started_session(store)
    same_first = store.preflight_continue(
        telegram_id=42,
        pet_fingerprint="pet-a",
        travel=started.travel,
        request_fingerprint="advice-a",
    )
    same_second = store.preflight_continue(
        telegram_id=42,
        pet_fingerprint="pet-a",
        travel=started.travel,
        request_fingerprint="advice-a",
    )
    different = store.preflight_continue(
        telegram_id=42,
        pet_fingerprint="pet-a",
        travel=started.travel,
        request_fingerprint="advice-b",
    )
    response = _continued_response(started.travel)

    assert store.commit_continue(same_first, response).committed is True
    replay = store.commit_continue(same_second, response)
    assert replay.committed is False
    assert replay.response == response
    with pytest.raises(InteractiveTravelStateConflictError):
        store.commit_continue(different, response)

    persisted = store.get(started.travel.travelId)
    assert persisted is not None
    assert persisted.revision == 1
    assert persisted.state_fingerprint == interactive_travel_state_fingerprint(response.travel)


def test_continue_cas_ignores_late_media_but_rejects_story_mutation(tmp_path) -> None:
    store = InteractiveTravelSessionStore(tmp_path / "travel.sqlite3")
    started = _seed_started_session(store)
    with_media_payload = started.travel.model_dump(mode="json")
    with_media_payload["parts"][0]["backgroundImageUrl"] = (
        "/static/generated/synthetic/part-01.png?v=1"
    )
    with_media = InteractiveTravelState.model_validate(with_media_payload)

    store.preflight_continue(
        telegram_id=42,
        pet_fingerprint="pet-a",
        travel=with_media,
        request_fingerprint="advice-a",
    )

    mutated_payload = started.travel.model_dump(mode="json")
    mutated_payload["parts"][0]["storyText"] = "Подменённая клиентом история."
    mutated = InteractiveTravelState.model_validate(mutated_payload)
    with pytest.raises(InteractiveTravelStateConflictError):
        store.preflight_continue(
            telegram_id=42,
            pet_fingerprint="pet-a",
            travel=mutated,
            request_fingerprint="advice-b",
        )


def test_media_reauthorization_requires_the_exact_narrative_fingerprint(tmp_path) -> None:
    store = InteractiveTravelSessionStore(tmp_path / "travel.sqlite3")
    started = _seed_started_session(store)
    common = {
        "travel_id": started.travel.travelId,
        "telegram_id": 42,
        "part_number": 1,
    }
    authorized = store.authorize_side_effect(kind="animate", **common)
    store.authorize_side_effect(
        kind="illustrate",
        pet_fingerprint="pet-a",
        destination="облачный город",
        title="Начало",
        story_text="Передо мной появляется мост.",
        **common,
    )
    attempt = store.preflight_continue(
        telegram_id=42,
        pet_fingerprint="pet-a",
        travel=started.travel,
        request_fingerprint="advice-a",
    )
    store.commit_continue(attempt, _continued_response(started.travel))

    with pytest.raises(InteractiveTravelStateConflictError):
        store.authorize_side_effect(
            kind="animate",
            expected_state_fingerprint=authorized,
            **common,
        )
    with pytest.raises(InteractiveTravelSessionOwnerMismatchError):
        store.authorize_side_effect(
            kind="animate",
            **(common | {"telegram_id": 43}),
        )
    with pytest.raises(InteractiveTravelPetMismatchError):
        store.authorize_side_effect(
            kind="illustrate",
            pet_fingerprint="pet-b",
            destination="облачный город",
            title="Начало",
            story_text="Передо мной появляется мост.",
            **common,
        )


def test_cancel_fences_an_inflight_continue_and_deletes_narrative_state(tmp_path) -> None:
    store = InteractiveTravelSessionStore(tmp_path / "travel.sqlite3")
    started = _seed_started_session(store)
    attempt = store.preflight_continue(
        telegram_id=42,
        pet_fingerprint="pet-a",
        travel=started.travel,
        request_fingerprint="advice-a",
    )

    store.cancel(started.travel.travelId, 42)

    with pytest.raises(InteractiveTravelSessionCancelledError):
        store.commit_continue(attempt, _continued_response(started.travel))
    assert store.get(started.travel.travelId) is None
    owner = store.get_owner(started.travel.travelId)
    assert owner is not None
    assert owner.cancelled_at is not None


def test_backfilled_owner_allows_legacy_continue_adoption_but_not_second_active(
    tmp_path,
) -> None:
    store = InteractiveTravelSessionStore(tmp_path / "travel.sqlite3")
    travel_id = "interactive-travel-adopted-session"
    store.register_owner(travel_id, 42)
    legacy = _started_response(travel_id)
    attempt = store.preflight_continue(
        telegram_id=42,
        pet_fingerprint="pet-a",
        travel=legacy.travel,
        request_fingerprint="advice-a",
    )
    continued = _continued_response(legacy.travel)
    store.commit_continue(attempt, continued)
    other_id = "interactive-travel-other-owner-session"
    store.register_owner(other_id, 42)

    with pytest.raises(InteractiveTravelActiveError):
        store.preflight_continue(
            telegram_id=42,
            pet_fingerprint="pet-a",
            travel=_started_response(other_id).travel,
            request_fingerprint="advice-b",
        )


def test_pruning_completed_session_keeps_owner_proof(tmp_path) -> None:
    path = tmp_path / "travel.sqlite3"
    store = InteractiveTravelSessionStore(
        path,
        retention=timedelta(days=1),
        max_records=3,
    )
    started = _seed_started_session(store)
    first = store.preflight_continue(
        telegram_id=42,
        pet_fingerprint="pet-a",
        travel=started.travel,
        request_fingerprint="advice-a",
    )
    continued = _continued_response(started.travel)
    store.commit_continue(first, continued)
    second = store.preflight_continue(
        telegram_id=42,
        pet_fingerprint="pet-a",
        travel=continued.travel,
        request_fingerprint="advice-b",
    )
    store.commit_continue(second, _completed_response(continued.travel))
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE interactive_travel_sessions
            SET completed_at = '2000-01-01T00:00:00+00:00'
            WHERE travel_id = ?
            """,
            (started.travel.travelId,),
        )

    store.preflight_start(
        telegram_id=42,
        pet_fingerprint="pet-a",
        request_fingerprint="new-start",
    )

    assert store.get(started.travel.travelId) is None
    owner = store.get_owner(started.travel.travelId)
    assert owner is not None
    assert owner.cancelled_at is None


def test_initialize_migrates_old_leased_rows_and_drops_transient_state(tmp_path) -> None:
    path = tmp_path / "travel.sqlite3"
    active = _started_response("interactive-travel-migrated-active")
    active_state = active.travel.model_dump_json()
    active_response = active.model_dump_json()
    active_fingerprint = interactive_travel_state_fingerprint(active.travel)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE interactive_travel_owners (
                travel_id TEXT PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                cancelled_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE interactive_travel_sessions (
                travel_id TEXT PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                pet_fingerprint TEXT NOT NULL,
                start_fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                state_json TEXT,
                state_fingerprint TEXT,
                response_json TEXT,
                revision INTEGER NOT NULL,
                operation_token TEXT,
                operation_fingerprint TEXT,
                operation_base_fingerprint TEXT,
                operation_started_at TEXT,
                last_operation_fingerprint TEXT,
                last_operation_base_fingerprint TEXT,
                last_response_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO interactive_travel_owners
            VALUES (?, 42, '2026-07-15T00:00:00+00:00', NULL)
            """,
            (active.travel.travelId,),
        )
        connection.execute(
            """
            INSERT INTO interactive_travel_sessions (
                travel_id, telegram_id, pet_fingerprint, start_fingerprint, status,
                state_json, state_fingerprint, response_json, revision,
                created_at, updated_at
            ) VALUES (?, 42, 'pet-a', 'start-a', 'active', ?, ?, ?, 0,
                      '2026-07-15T00:00:00+00:00', '2026-07-15T00:00:00+00:00')
            """,
            (active.travel.travelId, active_state, active_fingerprint, active_response),
        )
        connection.execute(
            """
            INSERT INTO interactive_travel_sessions (
                travel_id, telegram_id, pet_fingerprint, start_fingerprint, status,
                revision, operation_token, operation_started_at, created_at, updated_at
            ) VALUES ('interactive-travel-transient', 84, 'pet-b', 'start-b', 'starting',
                      0, 'lease-token', '2026-07-15T00:00:00+00:00',
                      '2026-07-15T00:00:00+00:00', '2026-07-15T00:00:00+00:00')
            """
        )

    store = InteractiveTravelSessionStore(path)

    assert store.get(active.travel.travelId) is not None
    assert store.get("interactive-travel-transient") is None
