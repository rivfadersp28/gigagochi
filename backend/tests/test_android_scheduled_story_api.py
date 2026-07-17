from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.routers import android
from app.services.android_feature_store import AndroidFeatureStore
from app.services.google_auth_session_store import GoogleUserIdentity
from app.services.scheduled_short_story_service import ScheduledShortStoryEpisode

REQUEST_KEY = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 7, 17, 12, 15, tzinfo=UTC)


def identity(account_id: str) -> GoogleUserIdentity:
    return GoogleUserIdentity(1, account_id, "subject", None, None)


def request(pet_id: str = "pet-shared") -> android.AndroidDueStoryRequest:
    return android.AndroidDueStoryRequest.model_validate(
        {
            "pet": {
                "petId": pet_id,
                "name": "Тото",
                "description": "Ледяной дракон",
                "stage": "baby",
                "mood": "idle",
                "stats": {"hunger": 100, "happiness": 100, "energy": 100},
            }
        }
    )


def episode(story_id: str) -> ScheduledShortStoryEpisode:
    root = f"/static/generated/{story_id}"
    return ScheduledShortStoryEpisode(
        story_id=story_id,
        plan={
            "destination": "в лес",
            "title": "Лесной знак",
            "storyText": "Тото заметил следы у старого дуба.",
            "question": "Что делать?",
            "choices": ["Осмотреть", "Убежать", "Позвать", "Спрятаться"],
            "outcomes": ["Следы изучены.", "Тото ушёл.", "Друг пришёл.", "Тото замер."],
            "correctChoice": "Осмотреть",
        },
        situation_image_url=f"{root}/interactive-travel-part-01.png",
        situation_video_url=f"{root}/interactive-travel-part-01.mp4",
        outcome_image_urls=tuple(f"{root}/outcome-{index}.png" for index in range(4)),
        outcome_video_urls=tuple(f"{root}/outcome-{index}.mp4" for index in range(4)),
        outcome_files=tuple(f"outcome-{index}.mp4" for index in range(4)),
    )


def enable_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        android,
        "get_settings",
        lambda: SimpleNamespace(
            scheduled_short_story_enabled=True,
            scheduled_short_story_hours=[15],
            scheduled_short_story_timezone="Europe/Moscow",
        ),
    )
    monkeypatch.setattr(android, "_story_now", lambda: NOW)


def fetch_due(store: AndroidFeatureStore, account_id: str):
    background = BackgroundTasks()
    response = android.due_scheduled_story(
        request(), Response(), background, identity(account_id), store
    )
    return response, background


def run_background(background: BackgroundTasks) -> None:
    for task in background.tasks:
        task.func(*task.args, **task.kwargs)


def test_due_not_due_first_replay_and_owner_isolation(monkeypatch, tmp_path) -> None:
    store = AndroidFeatureStore(tmp_path / "android.sqlite3")
    calls: list[str] = []
    enable_schedule(monkeypatch)

    def generate(*, story_id, **_kwargs):
        calls.append(story_id)
        return episode(story_id)

    monkeypatch.setattr(android, "generate_scheduled_short_story_episode", generate)
    first, first_background = fetch_due(store, "raw-account-a")
    before_completion, duplicate_background = fetch_due(store, "raw-account-a")
    assert first.story is None
    assert before_completion.story is None
    assert calls == []
    assert len(first_background.tasks) == 1
    assert duplicate_background.tasks == []

    run_background(first_background)
    replay, replay_background = fetch_due(store, "raw-account-a")
    other_initial, other_background = fetch_due(store, "raw-account-b")
    run_background(other_background)
    other, _ = fetch_due(store, "raw-account-b")

    assert replay.story is not None
    assert replay_background.tasks == []
    assert other_initial.story is None
    assert replay.story.storyId != other.story.storyId
    assert len(calls) == 2
    raw_database = (tmp_path / "android.sqlite3").read_bytes()
    assert b"raw-account-a" not in raw_database
    assert b"raw-account-b" not in raw_database

    monkeypatch.setattr(android, "_story_now", lambda: NOW.replace(hour=2))
    not_due, _ = fetch_due(store, "raw-account-a")
    assert not_due.story is None
    assert len(calls) == 2


def test_choice_is_exact_idempotent_and_owner_fenced(monkeypatch, tmp_path) -> None:
    store = AndroidFeatureStore(tmp_path / "android.sqlite3")
    enable_schedule(monkeypatch)
    monkeypatch.setattr(
        android,
        "generate_scheduled_short_story_episode",
        lambda *, story_id, **_kwargs: episode(story_id),
    )
    due_initial, background = fetch_due(store, "account-a")
    assert due_initial.story is None
    run_background(background)
    due, _ = fetch_due(store, "account-a")
    story_id = due.story.storyId if due.story is not None else ""
    choice = android.AndroidScheduledStoryChoiceRequest(
        requestKey=REQUEST_KEY,
        choice="Осмотреть",
    )

    selected = android.choose_scheduled_story(
        story_id, choice, Response(), identity("account-a"), store
    )
    replay = android.choose_scheduled_story(
        story_id, choice, Response(), identity("account-a"), store
    )

    assert selected == replay
    assert selected.selectedChoice == "Осмотреть"
    assert selected.result is not None
    assert selected.result.text == "Следы изучены."
    assert selected.resultVideoUrl.endswith("outcome-0.mp4")
    with pytest.raises(HTTPException) as conflict:
        android.choose_scheduled_story(
            story_id,
            choice.model_copy(update={"choice": "Убежать"}),
            Response(),
            identity("account-a"),
            store,
        )
    assert conflict.value.detail["code"] == "STORY_ALREADY_CHOSEN"
    with pytest.raises(HTTPException) as fenced:
        android.choose_scheduled_story(
            story_id, choice, Response(), identity("account-b"), store
        )
    assert fenced.value.detail["code"] == "STORY_NOT_FOUND"


def test_generation_failure_claim_is_not_replayed(monkeypatch, tmp_path) -> None:
    store = AndroidFeatureStore(tmp_path / "android.sqlite3")
    enable_schedule(monkeypatch)
    calls = 0

    def fail(**_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider outcome unknown")

    monkeypatch.setattr(android, "generate_scheduled_short_story_episode", fail)
    initial, background = fetch_due(store, "account-a")
    assert initial.story is None
    run_background(background)
    replay, replay_background = fetch_due(store, "account-a")

    assert replay.story is None
    assert replay_background.tasks == []
    assert calls == 1


def test_story_dtos_reject_missing_pet_id_and_unsafe_media() -> None:
    with pytest.raises(ValidationError):
        android.AndroidDueStoryRequest.model_validate(
            {"pet": request().pet.model_copy(update={"petId": None})}
        )
    with pytest.raises(ValidationError):
        android.AndroidScheduledStory.model_validate(
            {
                "storyId": "android-story-" + "a" * 32,
                "petId": "pet-a",
                "title": "История",
                "text": "Текст",
                "question": "Что делать?",
                "choices": ["А", "Б", "В", "Г"],
                "createdAt": NOW,
                "videoUrl": "https://evil.example/video.mp4",
            }
        )


def test_story_endpoint_requires_bearer_and_strict_result_and_text() -> None:
    unauthorized = TestClient(app).post(
        "/api/android/stories/due",
        json=request().model_dump(mode="json"),
    )
    assert unauthorized.status_code == 401
    assert unauthorized.headers["cache-control"] == "no-store"

    base = {
        "storyId": "android-story-" + "a" * 32,
        "petId": "pet-a",
        "title": "История",
        "text": "Текст",
        "question": "Что делать?",
        "choices": ["А", "Б", "В", "Г"],
        "createdAt": NOW,
    }
    with pytest.raises(ValidationError):
        android.AndroidScheduledStory.model_validate({**base, "text": "x" * 701})
    with pytest.raises(ValidationError):
        android.AndroidScheduledStory.model_validate(
            {**base, "selectedChoice": "А", "result": {"text": "нет полей"}}
        )


def test_story_table_owner_scope_allows_same_pet_and_slot(tmp_path) -> None:
    store = AndroidFeatureStore(tmp_path / "android.sqlite3")
    owner_a = android._owner(identity("account-a"))
    owner_b = android._owner(identity("account-b"))
    slot = "2026-07-17T12:00:00Z"
    first = store.claim_scheduled_story(
        owner=owner_a, pet_id="pet-shared", slot_utc=slot, story_id="story-a"
    )
    second = store.claim_scheduled_story(
        owner=owner_b, pet_id="pet-shared", slot_utc=slot, story_id="story-b"
    )

    assert first.state == "created"
    assert second.state == "created"
    with sqlite3.connect(store.path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM android_scheduled_stories"
        ).fetchone()[0]
    assert count == 2
