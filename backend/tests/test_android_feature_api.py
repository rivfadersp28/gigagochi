from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi import BackgroundTasks, HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.routers import android
from app.schemas import (
    GeneratePetJobResponse,
    LocalChatResponse,
    LocalPetChatContext,
    LocalProactiveResponse,
    MemoryConsolidationResponse,
    MemoryExtractionResponse,
    TravelVideoPrototypeResponse,
)
from app.services.android_feature_store import AndroidFeatureStore
from app.services.generation_job_service import GenerationJobNotFoundError
from app.services.google_auth_session_store import GoogleUserIdentity
from app.services.travel_video_prototype_service import TravelVideoPrototypeNotFoundError

KEY = "11111111-1111-4111-8111-111111111111"
KEY_2 = "22222222-2222-4222-8222-222222222222"


def identity(account_id: str = "acct-owner-a") -> GoogleUserIdentity:
    return GoogleUserIdentity(1, account_id, "subject", None, None)


def pet(pet_id: str = "pet-shared") -> LocalPetChatContext:
    return LocalPetChatContext.model_validate(
        {
            "petId": pet_id,
            "name": "Листик",
            "description": "маленький питомец",
            "stage": "baby",
            "mood": "happy",
            "stats": {"hunger": 80, "happiness": 90, "energy": 75},
        }
    )


class FakeGenerationService:
    def __init__(self) -> None:
        self.jobs: dict[str, tuple[str, GeneratePetJobResponse]] = {}
        self.by_key: dict[tuple[str, str], GeneratePetJobResponse] = {}
        self.submit_calls = 0

    def submit_for_owner(self, description, owner, _provider, *, request_key):
        self.submit_calls += 1
        job = GeneratePetJobResponse(
            jobId=f"job-{self.submit_calls}",
            status="queued",
            phase="queued",
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
        )
        self.jobs[job.jobId] = (str(owner.storage_key), job)
        self.by_key[(str(owner.storage_key), request_key)] = job
        return job

    def find_by_request_key_for_owner(self, request_key, owner, _description=None, _provider=None):
        return self.by_key.get((str(owner.storage_key), request_key))

    def get_for_owner(self, job_id, owner):
        stored = self.jobs.get(job_id)
        if stored is None or stored[0] != str(owner.storage_key):
            raise GenerationJobNotFoundError(job_id)
        return stored[1]


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Basic token", "Bearer", "Bearer revoked"],
)
def test_android_api_fails_closed_and_errors_are_no_store(
    authorization: str | None,
) -> None:
    headers = {"Authorization": authorization} if authorization is not None else {}

    response = TestClient(app).get("/api/android/create/jobs/missing", headers=headers)

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_INVALID"
    assert response.headers["cache-control"] == "no-store"


def test_sync_completed_replay_is_exact_and_conflicting_payload_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = AndroidFeatureStore(tmp_path / "android.sqlite3")
    calls = 0

    def provider(_payload):
        nonlocal calls
        calls += 1
        return LocalChatResponse(reply="Привет")

    monkeypatch.setattr(android, "chat_with_local_pet", provider)
    monkeypatch.setattr(android, "_check_rate_limit", lambda *_args, **_kwargs: None)
    payload = android.AndroidChatRequest(requestKey=KEY, message="Привет", pet=pet())

    http_response = Response()
    first = android.chat(payload, http_response, identity(), store)
    replay = android.chat(payload, Response(), identity(), store)

    assert first == replay == LocalChatResponse(reply="Привет")
    assert http_response.headers["cache-control"] == "no-store"
    assert calls == 1
    with pytest.raises(HTTPException) as conflict:
        android.chat(
            payload.model_copy(update={"message": "Другое"}),
            Response(),
            identity(),
            store,
        )
    assert conflict.value.detail["code"] == "IDEMPOTENCY_CONFLICT"
    assert calls == 1


@pytest.mark.parametrize("kind", ["character_travel", "character_outfit"])
def test_android_chat_accepts_character_experience_memory(kind: str) -> None:
    payload = android.AndroidChatRequest.model_validate(
        {
            "requestKey": KEY,
            "message": "Что ты помнишь?",
            "pet": pet().model_dump(mode="json"),
            "memoryContext": {
                "relevantMemories": [
                    {
                        "id": f"{kind}:{KEY}",
                        "kind": kind,
                        "text": "Недавнее приключение персонажа.",
                        "memoryClass": "episode",
                        "recordedAt": "2026-07-21T12:00:00Z",
                        "occurredAt": "2026-07-21T12:00:00Z",
                    }
                ]
            },
        }
    )

    assert payload.memoryContext is not None
    assert payload.memoryContext.relevantMemories[0].kind == kind


def test_android_memory_and_proactive_routes_are_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = AndroidFeatureStore(tmp_path / "android.sqlite3")
    calls = {"extract": 0, "consolidate": 0, "proactive": 0}

    def extract(_payload):
        calls["extract"] += 1
        return MemoryExtractionResponse(operations=[{"type": "capture_learning"}])

    def consolidate(_payload):
        calls["consolidate"] += 1
        return MemoryConsolidationResponse(operations=[{"type": "rewrite_summary"}])

    def proactive(_payload):
        calls["proactive"] += 1
        return LocalProactiveResponse(reply="Как прошёл экзамен?")

    monkeypatch.setattr(android, "extract_user_memory_operations", extract)
    monkeypatch.setattr(android, "consolidate_user_memory", consolidate)
    monkeypatch.setattr(android, "generate_proactive_pet_message", proactive)
    monkeypatch.setattr(android, "_check_rate_limit", lambda *_args, **_kwargs: None)

    extraction = android.AndroidMemoryExtractionRequest(
        requestKey=KEY,
        message="У меня завтра экзамен",
        reply="Буду держать лапки.",
        pet=pet(),
    )
    consolidation = android.AndroidMemoryConsolidationRequest(
        requestKey=KEY_2,
        pendingLearnings=[{"observation": "У пользователя экзамен"}],
    )
    proactive_request = android.AndroidProactiveRequest(
        requestKey="33333333-3333-4333-8333-333333333333",
        pet=pet(),
        memoryContext={
            "proactiveCandidate": {
                "memoryIds": ["exam"],
                "reason": "Сегодня экзамен пользователя",
            }
        },
    )

    first_extraction = android.extract_memory(extraction, Response(), identity(), store)
    assert first_extraction == android.extract_memory(
        extraction,
        Response(),
        identity(),
        store,
    )
    assert android.consolidate_memory(
        consolidation, Response(), identity(), store
    ) == android.consolidate_memory(consolidation, Response(), identity(), store)
    assert android.proactive(proactive_request, Response(), identity(), store) == android.proactive(
        proactive_request, Response(), identity(), store
    )
    assert calls == {"extract": 1, "consolidate": 1, "proactive": 1}


def test_generation_jobs_isolate_same_key_and_poll_by_google_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = AndroidFeatureStore(tmp_path / "android.sqlite3")
    service = FakeGenerationService()
    monkeypatch.setattr(android, "_check_rate_limit", lambda *_args, **_kwargs: None)
    create = android.AndroidCreateJobRequest(
        requestKey=KEY,
        petId="pet-shared",
        description="маленький питомец",
    )

    first = android.create_job(create, Response(), identity("acct-a"), store, service)
    second = android.create_job(create, Response(), identity("acct-b"), store, service)
    replay = android.create_job(create, Response(), identity("acct-a"), store, service)

    assert first.job.jobId != second.job.jobId
    assert replay == first
    assert service.submit_calls == 2
    assert (
        android.create_job_status(first.job.jobId, Response(), identity("acct-a"), service).job
        == first.job
    )
    with pytest.raises(HTTPException) as fenced:
        android.create_job_status(first.job.jobId, Response(), identity("acct-b"), service)
    assert fenced.value.detail["code"] == "JOB_NOT_FOUND"

    outfit = android.AndroidOutfitJobRequest(
        requestKey=KEY_2,
        petId="pet-shared",
        prompt="красная шляпа",
        idleImageUrl="/static/generated/idle.png",
        sadImageUrl="/static/generated/sad.png",
        happyImageUrl="/static/generated/happy.png",
    )
    outfit_job = android.outfit_job(outfit, Response(), identity("acct-a"), store, service)
    assert (
        android.outfit_job_status(outfit_job.job.jobId, Response(), identity("acct-a"), service).job
        == outfit_job.job
    )
    with pytest.raises(HTTPException):
        android.outfit_job_status(outfit_job.job.jobId, Response(), identity("acct-b"), service)


def test_travel_video_submit_replay_and_poll_are_owner_fenced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = AndroidFeatureStore(tmp_path / "android.sqlite3")
    records: dict[str, tuple[str, TravelVideoPrototypeResponse]] = {}
    create_calls = 0

    def create(*, owner, prompt, request_key, pet):
        nonlocal create_calls
        create_calls += 1
        job_id = android.travel_video_job_id_for_owner(owner, request_key)
        response = TravelVideoPrototypeResponse(
            jobId=job_id,
            status="queued",
            prompt=prompt,
            createdAt="2026-07-17T00:00:00+00:00",
            updatedAt="2026-07-17T00:00:00+00:00",
        )
        records[job_id] = (str(owner.storage_key), response)
        return response

    def read(job_id, *, owner):
        record = records.get(job_id)
        if record is None or record[0] != str(owner.storage_key):
            raise TravelVideoPrototypeNotFoundError(job_id)
        return record[1]

    monkeypatch.setattr(android, "create_travel_video_prototype_for_owner", create)
    monkeypatch.setattr(android, "read_travel_video_prototype_for_owner", read)
    monkeypatch.setattr(
        android,
        "should_resume_travel_video_prototype_for_owner",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(android, "_check_rate_limit", lambda *_args, **_kwargs: None)
    payload = android.AndroidTravelVideoJobRequest(
        requestKey=KEY,
        pet=pet(),
        prompt="На Луну",
    )

    first = android.travel_video_job(
        payload, Response(), BackgroundTasks(), identity("acct-a"), store
    )
    second = android.travel_video_job(
        payload, Response(), BackgroundTasks(), identity("acct-b"), store
    )
    replay = android.travel_video_job(
        payload, Response(), BackgroundTasks(), identity("acct-a"), store
    )

    assert first.jobId != second.jobId
    assert "acct-a" not in first.jobId
    assert replay == first
    assert create_calls == 2
    assert (
        android.travel_video_status(first.jobId, Response(), BackgroundTasks(), identity("acct-a"))
        == first
    )
    with pytest.raises(HTTPException) as fenced:
        android.travel_video_status(first.jobId, Response(), BackgroundTasks(), identity("acct-b"))
    assert fenced.value.detail["code"] == "JOB_NOT_FOUND"


def test_sync_restart_never_repeats_ambiguous_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "android.sqlite3"
    calls = 0

    def provider(_payload):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider outcome is unknown")

    monkeypatch.setattr(android, "chat_with_local_pet", provider)
    monkeypatch.setattr(android, "_check_rate_limit", lambda *_args, **_kwargs: None)
    payload = android.AndroidChatRequest(requestKey=KEY, message="Привет", pet=pet())

    cached_store = AndroidFeatureStore(path)
    with pytest.raises(RuntimeError):
        android.chat(payload, Response(), identity(), cached_store)
    with pytest.raises(HTTPException) as recent:
        android.chat(payload, Response(), identity(), AndroidFeatureStore(path))
    assert recent.value.detail["code"] == "REQUEST_IN_PROGRESS"

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE android_feature_requests SET updated_at_ms = 0 WHERE request_key = ?",
            (KEY,),
        )
    with pytest.raises(HTTPException) as stale:
        android.chat(payload, Response(), identity(), cached_store)
    assert stale.value.detail["code"] == "OUTCOME_UNKNOWN"
    assert stale.value.headers is None or "Retry-After" not in stale.value.headers
    assert calls == 1


def test_rate_keys_are_operation_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = AndroidFeatureStore(tmp_path / "android.sqlite3")
    rate_keys: list[str] = []

    def rate(_bucket, _owner, *, request_key):
        rate_keys.append(request_key)
        return None

    monkeypatch.setattr(android, "_check_rate_limit", rate)
    monkeypatch.setattr(
        android, "chat_with_local_pet", lambda _payload: LocalChatResponse(reply="ok")
    )
    android.chat(
        android.AndroidChatRequest(requestKey=KEY, message="Привет", pet=pet()),
        Response(),
        identity(),
        store,
    )
    assert rate_keys == [f"android:chat:{KEY}"]


def test_rate_rejection_aborts_feature_reservation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = AndroidFeatureStore(tmp_path / "android.sqlite3")

    def rate(*_args, **_kwargs):
        raise HTTPException(status_code=429, detail={"code": "RATE_LIMITED"})

    monkeypatch.setattr(android, "_check_rate_limit", rate)
    payload = android.AndroidChatRequest(requestKey=KEY, message="Привет", pet=pet())
    with pytest.raises(HTTPException) as rejected:
        android.chat(payload, Response(), identity(), store)
    assert rejected.value.status_code == 429
    with sqlite3.connect(store.path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM android_feature_requests").fetchone()[0] == 0
        )


def test_rate_store_failure_aborts_reservation_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = AndroidFeatureStore(tmp_path / "android.sqlite3")
    provider_calls = 0

    def broken_rate_store(*_args, **_kwargs):
        raise OSError("rate-limit SQLite unavailable")

    def provider(_payload):
        nonlocal provider_calls
        provider_calls += 1
        return LocalChatResponse(reply="unexpected")

    monkeypatch.setattr(android, "_check_rate_limit", broken_rate_store)
    monkeypatch.setattr(android, "chat_with_local_pet", provider)
    payload = android.AndroidChatRequest(requestKey=KEY, message="Привет", pet=pet())

    with pytest.raises(OSError, match="rate-limit SQLite unavailable"):
        android.chat(payload, Response(), identity(), store)

    with sqlite3.connect(store.path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM android_feature_requests").fetchone()[0] == 0
        )
    assert provider_calls == 0


def test_android_router_is_hidden_from_public_openapi() -> None:
    assert all(not path.startswith("/api/android") for path in app.openapi()["paths"])


@pytest.mark.parametrize(
    "request_key",
    [
        "outfit-11111111-1111-4111-8111-111111111111",
        "11111111-1111-1111-8111-111111111111",
        "11111111-1111-4111-7111-111111111111",
        "11111111-1111-4111-8111-11111111111A",
    ],
)
def test_android_mutation_keys_reject_noncanonical_uuid_v4(request_key: str) -> None:
    with pytest.raises(ValidationError):
        android.AndroidCreateJobRequest(
            requestKey=request_key,
            petId="pet-shared",
            description="маленький питомец",
        )
