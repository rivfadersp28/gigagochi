from __future__ import annotations

import multiprocessing
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Event, Lock, Thread
from types import SimpleNamespace

import pytest

from app.schemas import GeneratePetJobResponse
from app.services.feature_owner import FeatureOwner
from app.services.generation_job_service import (
    GenerationIdempotencyConflictError,
    GenerationJobService,
    GenerationOwnerActiveError,
    GenerationQueueFullError,
)
from app.services.generation_job_store import GenerationJobStore, StoredGenerationJob
from app.services.prompt_debug import current_ai_log_context
from app.services.telegram_auth_service import TelegramUserContext


def _user(telegram_id: int = 42) -> TelegramUserContext:
    return TelegramUserContext(
        telegram_id=telegram_id,
        username="serge",
        first_name="Serge",
        language_code="ru",
        auth_date=datetime.now(UTC),
    )


def _response(
    sad_scene_path: Path | None,
    sad_video_path: Path | None,
    happy_scene_path: Path | None,
    happy_video_path: Path | None,
):
    idle_url = "/static/generated/asset-1/teen-idle.png"
    sad_url = "/static/generated/asset-1/teen-sad.png" if sad_scene_path else idle_url
    happy_url = "/static/generated/asset-1/teen-happy.png" if happy_scene_path else idle_url
    images = {
        stage: {
            "idle": idle_url,
            "happy": happy_url,
            "hungry": idle_url,
            "sad": sad_url,
        }
        for stage in ("baby", "teen", "adult")
    }
    return {
        "assetSetId": "asset-1",
        "generatedAt": datetime.now(UTC),
        "images": images,
        "videoUrl": "/static/generated/asset-1/teen-idle.mp4",
        "sadVideoUrl": ("/static/generated/asset-1/teen-sad.mp4" if sad_video_path else None),
        "happyVideoUrl": ("/static/generated/asset-1/teen-happy.mp4" if happy_video_path else None),
    }


def _wait_for(service: GenerationJobService, job_id: str, predicate, *, owner_id: int = 42):
    for _ in range(200):
        job = service.get(job_id, owner_id)
        if predicate(job):
            return job
        time.sleep(0.005)
    raise AssertionError("generation job did not reach expected state")


def _build_response(
    _image_set,
    _video_path,
    sad_path,
    sad_video_path,
    happy_path,
    happy_video_path,
):
    return _response(sad_path, sad_video_path, happy_path, happy_video_path)


def _seed_active_job(store_path: Path, job_id: str = "recovered-job") -> None:
    now = datetime.now(UTC)
    GenerationJobStore(store_path).save(
        StoredGenerationJob(
            owner_id=42,
            username="serge",
            first_name="Serge",
            description="мышонок",
            response=GeneratePetJobResponse(
                jobId=job_id,
                status="running",
                phase="generating_images",
                createdAt=now,
                updatedAt=now,
            ),
        )
    )


def _bind_generation_alias_in_process(
    store_path: str,
    request_key: str,
    description: str,
    gate,
    results,
) -> None:
    store = GenerationJobStore(store_path)
    now = datetime.now(UTC)
    candidate = StoredGenerationJob(
        owner_id=42,
        username="serge",
        first_name="Serge",
        description=description,
        image_provider="openai",
        response=GeneratePetJobResponse(
            jobId=f"candidate-{request_key.replace(':', '-')}",
            status="queued",
            phase="queued",
            createdAt=now,
            updatedAt=now,
        ),
    )
    gate.wait(timeout=5)
    creation = store.create_or_get(
        candidate,
        request_key=request_key,
        max_active_jobs=10,
    )
    results.put(
        (
            request_key,
            creation.created,
            creation.conflict,
            creation.job.response.jobId,
        )
    )


def _test_service(
    store_path: Path,
    generate_images,
    *,
    generate_video=None,
    generate_background_image=None,
    generate_background_video=None,
    generate_happy_image=None,
    generate_happy_video=None,
    build_failure=None,
    **kwargs,
) -> GenerationJobService:
    return GenerationJobService(
        store=GenerationJobStore(store_path),
        image_workers=1,
        video_workers=1,
        generate_images=generate_images,
        generate_video=generate_video or (lambda _image_set: Path("teen-idle.mp4")),
        generate_background_image=generate_background_image
        or (lambda _image_set, _provider: Path("teen-sad.png")),
        generate_background_video=generate_background_video
        or (lambda _image_set, _sad_path: Path("teen-sad.mp4")),
        generate_happy_image=generate_happy_image
        or (lambda _image_set, _provider: Path("teen-happy.png")),
        generate_happy_video=generate_happy_video
        or (lambda _image_set, _happy_path: Path("teen-happy.mp4")),
        build_response=_build_response,
        build_failure=build_failure
        or (
            lambda _job_id, phase, exc, _owner_id: {
                "code": "GENERATION_FAILED",
                "message": str(exc),
                "phase": phase,
            }
        ),
        **kwargs,
    )


def test_google_generation_failure_context_contains_safe_android_correlation(
    tmp_path: Path,
) -> None:
    contexts: list[dict[str, object]] = []
    owner = FeatureOwner("google", f"google:{'a' * 64}")
    request_key = "12345678-1234-4123-8123-123456789abc"

    def fail_images(_description: str, _provider: str):
        raise RuntimeError("provider failed")

    def capture_failure(_job_id: str, phase: str, exc: Exception, _owner_id: int):
        contexts.append(current_ai_log_context())
        return {"code": "GENERATION_FAILED", "message": str(exc), "phase": phase}

    service = _test_service(
        tmp_path / "generation-jobs.sqlite3",
        fail_images,
        build_failure=capture_failure,
    )
    try:
        submitted = service.submit_for_owner(
            "мышонок",
            owner,
            request_key=request_key,
        )
        for _ in range(200):
            failed = service.get_for_owner(submitted.jobId, owner)
            if failed.status == "failed":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("generation job did not fail")

        assert contexts == [
            {
                "jobId": submitted.jobId,
                "requestKeys": [request_key],
                "operation": "create",
                "owner": contexts[0]["owner"],
                "endpoint": "/api/generate-pet",
                "imageProvider": "openai",
            }
        ]
        assert str(contexts[0]["owner"]).startswith("google:")
        assert contexts[0]["owner"] != owner.storage_key
    finally:
        service.shutdown(wait=True)


def test_foreground_result_is_available_before_background_assets(tmp_path: Path) -> None:
    background_started = Event()
    release_background = Event()
    notification_sent = Event()
    notifications: list[int] = []
    video_calls: list[str] = []

    def notify_ready(owner_id: int) -> None:
        notifications.append(owner_id)
        notification_sent.set()

    def generate_background_image(_image_set, _image_provider):
        background_started.set()
        assert release_background.wait(timeout=2)
        return Path("teen-sad.png")

    service = GenerationJobService(
        store=GenerationJobStore(tmp_path / "generation-jobs.sqlite3"),
        image_workers=1,
        video_workers=1,
        generate_images=lambda _description, _provider: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=lambda _image_set: video_calls.append("idle-video") or Path("teen-idle.mp4"),
        generate_background_image=generate_background_image,
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set, _provider: Path("teen-happy.png"),
        generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
        notify_ready=notify_ready,
    )
    try:
        submitted = service.submit("мышонок", _user())
        assert background_started.wait(timeout=2)
        ready = service.get(submitted.jobId, 42)

        assert ready.status == "running"
        assert ready.phase == "generating_sad_image"
        assert ready.result is not None
        assert ready.result.videoUrl.endswith("teen-idle.mp4")
        assert ready.result.sadVideoUrl is None
        assert notification_sent.wait(timeout=2)
        assert notifications == [42]
        assert video_calls == ["idle-video"]

        release_background.set()
        completed = _wait_for(service, submitted.jobId, lambda job: job.status == "succeeded")
        assert completed.result is not None
        assert completed.result.sadVideoUrl.endswith("teen-sad.mp4")
        assert completed.result.happyVideoUrl.endswith("teen-happy.mp4")
    finally:
        release_background.set()
        service.shutdown(wait=True)


def test_notification_failure_does_not_fail_generation(tmp_path: Path) -> None:
    def notify_ready(_owner_id: int) -> None:
        raise RuntimeError("telegram unavailable")

    service = GenerationJobService(
        store=GenerationJobStore(tmp_path / "generation-jobs.sqlite3"),
        image_workers=1,
        video_workers=1,
        generate_images=lambda _description, _provider: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set, _provider: Path("teen-sad.png"),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set, _provider: Path("teen-happy.png"),
        generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
        notify_ready=notify_ready,
    )
    try:
        submitted = service.submit("мышонок", _user())
        completed = _wait_for(service, submitted.jobId, lambda job: job.status == "succeeded")
        assert completed.result is not None
    finally:
        service.shutdown(wait=True)


def test_outfit_completes_only_after_all_three_videos(tmp_path: Path) -> None:
    birth_notifications: list[int] = []
    outfit_notifications: list[int] = []
    downstream_calls: list[str] = []

    def record(stage: str, result: Path):
        def call(*_args, **_kwargs):
            downstream_calls.append(stage)
            return result

        return call

    service = _test_service(
        tmp_path / "generation-jobs.sqlite3",
        lambda _description, _provider: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=record("idle-video", Path("teen-idle.mp4")),
        generate_background_image=record("sad-image", Path("teen-sad.png")),
        generate_background_video=record("sad-video", Path("teen-sad.mp4")),
        generate_happy_image=record("happy-image", Path("teen-happy.png")),
        generate_happy_video=record("happy-video", Path("teen-happy.mp4")),
        notify_ready=birth_notifications.append,
        notify_outfit_ready=outfit_notifications.append,
    )
    try:
        submitted = service.submit("__OUTFIT_V1__{}", _user())
        completed = _wait_for(service, submitted.jobId, lambda job: job.phase == "completed")

        assert completed.status == "succeeded"
        assert completed.result is not None
        assert completed.result.videoUrl.endswith("teen-idle.mp4")
        assert completed.result.sadVideoUrl.endswith("teen-sad.mp4")
        assert completed.result.happyVideoUrl.endswith("teen-happy.mp4")
        assert completed.result.images.teen["idle"].endswith("teen-idle.png")
        assert completed.result.images.teen["sad"].endswith("teen-sad.png")
        assert completed.result.images.teen["happy"].endswith("teen-happy.png")
        assert birth_notifications == []
        assert outfit_notifications == [42]
        assert downstream_calls == [
            "idle-video",
            "sad-image",
            "sad-video",
            "happy-image",
            "happy-video",
        ]
    finally:
        service.shutdown(wait=True)


def test_background_failure_keeps_foreground_result(tmp_path: Path) -> None:
    service = GenerationJobService(
        store=GenerationJobStore(tmp_path / "generation-jobs.sqlite3"),
        image_workers=1,
        video_workers=1,
        generate_images=lambda _description, _provider: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set, _provider: (_ for _ in ()).throw(
            RuntimeError("sad image failed")
        ),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set, _provider: Path("teen-happy.png"),
        generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
    )
    try:
        submitted = service.submit("мышонок", _user())
        completed = _wait_for(service, submitted.jobId, lambda job: job.status == "succeeded")

        assert completed.result is not None
        assert completed.result.videoUrl.endswith("teen-idle.mp4")
        assert completed.result.sadVideoUrl is None
        assert completed.result.happyVideoUrl.endswith("teen-happy.mp4")
        assert completed.backgroundError is not None
        assert completed.backgroundError["message"] == "sad image failed"
    finally:
        service.shutdown(wait=True)


def test_every_owner_gets_derived_assets(tmp_path: Path) -> None:
    background_calls: list[str] = []
    service = GenerationJobService(
        store=GenerationJobStore(tmp_path / "generation-jobs.sqlite3"),
        image_workers=1,
        video_workers=1,
        generate_images=lambda _description, _provider: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set, _provider: (
            background_calls.append("sad-image") or Path("teen-sad.png")
        ),
        generate_background_video=lambda _image_set, _sad_path: (
            background_calls.append("sad-video") or Path("teen-sad.mp4")
        ),
        generate_happy_image=lambda _image_set, _provider: (
            background_calls.append("happy-image") or Path("teen-happy.png")
        ),
        generate_happy_video=lambda _image_set, _happy_path: (
            background_calls.append("happy-video") or Path("teen-happy.mp4")
        ),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
    )
    try:
        submitted = service.submit("мышонок", _user())
        completed = _wait_for(service, submitted.jobId, lambda job: job.status == "succeeded")

        assert completed.phase == "completed"
        assert completed.result is not None
        assert completed.result.videoUrl.endswith("teen-idle.mp4")
        assert completed.result.sadVideoUrl.endswith("teen-sad.mp4")
        assert completed.result.happyVideoUrl.endswith("teen-happy.mp4")
        assert background_calls == ["sad-image", "sad-video", "happy-image", "happy-video"]
    finally:
        service.shutdown(wait=True)


def test_twenty_generation_pipelines_start_concurrently(tmp_path: Path) -> None:
    barrier = Barrier(20)
    counter_lock = Lock()
    active = 0
    peak_active = 0

    def generate_images(_description: str, _image_provider: str):
        nonlocal active, peak_active
        with counter_lock:
            active += 1
            peak_active = max(peak_active, active)
        barrier.wait(timeout=5)
        with counter_lock:
            active -= 1
        return SimpleNamespace(asset_set_id="asset-1")

    service = GenerationJobService(
        store=GenerationJobStore(tmp_path / "generation-jobs.sqlite3"),
        image_workers=20,
        video_workers=20,
        max_queued_jobs=40,
        generate_images=generate_images,
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set, _provider: Path("teen-sad.png"),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set, _provider: Path("teen-happy.png"),
        generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
    )
    try:
        jobs = [
            (service.submit(f"pet-{index}", _user(42 + index)), 42 + index) for index in range(20)
        ]
        for job, owner_id in jobs:
            _wait_for(
                service,
                job.jobId,
                lambda response: response.status == "succeeded",
                owner_id=owner_id,
            )
        assert peak_active == 20
        assert service.runtime_status()["queued"] == 0
        assert service.runtime_status()["running"] == 0
    finally:
        service.shutdown(wait=True)


def test_selected_provider_reaches_every_image_stage(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    service = GenerationJobService(
        store=GenerationJobStore(tmp_path / "generation-jobs.sqlite3"),
        image_workers=1,
        video_workers=1,
        generate_images=lambda _description, provider: (
            calls.append(("initial", provider)) or SimpleNamespace(asset_set_id="asset-1")
        ),
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set, provider: (
            calls.append(("sad", provider)) or Path("teen-sad.png")
        ),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set, provider: (
            calls.append(("happy", provider)) or Path("teen-happy.png")
        ),
        generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
    )
    try:
        submitted = service.submit("мышонок", _user(), "kandinsky")
        _wait_for(service, submitted.jobId, lambda job: job.status == "succeeded")

        assert calls == [
            ("initial", "kandinsky"),
            ("sad", "kandinsky"),
            ("happy", "kandinsky"),
        ]
    finally:
        service.shutdown(wait=True)


def test_kandinsky_static_comparison_is_attached_without_videos(tmp_path: Path) -> None:
    comparison_images = {
        stage: {
            "idle": "/static/generated/kandinsky/idle.png",
            "happy": "/static/generated/kandinsky/happy.png",
            "hungry": "/static/generated/kandinsky/idle.png",
            "sad": "/static/generated/kandinsky/sad.png",
        }
        for stage in ("baby", "teen", "adult")
    }
    service = GenerationJobService(
        store=GenerationJobStore(tmp_path / "generation-jobs.sqlite3"),
        image_workers=2,
        video_workers=1,
        generate_images=lambda _description, provider: (
            pytest.fail("primary provider must stay OpenAI")
            if provider != "openai"
            else SimpleNamespace(asset_set_id="asset-1")
        ),
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set, _provider: Path("teen-sad.png"),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set, _provider: Path("teen-happy.png"),
        generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
        generate_comparison_images=lambda _description, _primary_image_set: {
            "assetSetId": "asset-kandinsky",
            "generatedAt": datetime.now(UTC),
            "images": comparison_images,
        },
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
    )
    try:
        submitted = service.submit("мышонок", _user())
        completed = _wait_for(service, submitted.jobId, lambda job: job.status == "succeeded")

        assert completed.result is not None
        assert completed.result.videoUrl is not None
        assert completed.result.kandinskyAssets is not None
        assert completed.result.kandinskyAssets.assetSetId == "asset-kandinsky"
        assert completed.result.kandinskyAssets.images.teen["happy"].endswith("happy.png")
        assert completed.comparisonError is None
    finally:
        service.shutdown(wait=True)


def test_kandinsky_failure_does_not_fail_openai_assets(tmp_path: Path) -> None:
    service = GenerationJobService(
        store=GenerationJobStore(tmp_path / "generation-jobs.sqlite3"),
        image_workers=2,
        video_workers=1,
        generate_images=lambda _description, _provider: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set, _provider: Path("teen-sad.png"),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set, _provider: Path("teen-happy.png"),
        generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
        generate_comparison_images=lambda _description, _primary_image_set: (_ for _ in ()).throw(
            RuntimeError("kandinsky unavailable")
        ),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
    )
    try:
        submitted = service.submit("мышонок", _user())
        completed = _wait_for(service, submitted.jobId, lambda job: job.status == "succeeded")

        assert completed.result is not None
        assert completed.result.happyVideoUrl is not None
        assert completed.result.kandinskyAssets is None
        assert completed.comparisonError is not None
        assert completed.comparisonError["message"] == "kandinsky unavailable"
    finally:
        service.shutdown(wait=True)


def test_generation_queue_is_bounded(tmp_path: Path) -> None:
    release = Event()

    def generate_images(_description: str, _image_provider: str):
        assert release.wait(timeout=5)
        return SimpleNamespace(asset_set_id="asset-1")

    service = GenerationJobService(
        store=GenerationJobStore(tmp_path / "generation-jobs.sqlite3"),
        image_workers=1,
        video_workers=1,
        max_queued_jobs=1,
        generate_images=generate_images,
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set, _provider: Path("teen-sad.png"),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set, _provider: Path("teen-happy.png"),
        generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
    )
    try:
        service.submit("one", _user(42))
        service.submit("two", _user(43))
        with pytest.raises(GenerationQueueFullError):
            service.submit("three", _user(44))
    finally:
        release.set()
        service.shutdown(wait=True)


def test_runtime_status_counts_stale_queued_job_as_stuck(tmp_path: Path) -> None:
    release = Event()
    first_started = Event()

    def generate_images(_description: str, _image_provider: str):
        first_started.set()
        assert release.wait(timeout=5)
        return SimpleNamespace(asset_set_id="asset-1")

    service = GenerationJobService(
        store=GenerationJobStore(tmp_path / "generation-jobs.sqlite3"),
        image_workers=1,
        video_workers=1,
        max_queued_jobs=1,
        stuck_after=timedelta(minutes=1),
        generate_images=generate_images,
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set, _provider: Path("teen-sad.png"),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set, _provider: Path("teen-happy.png"),
        generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
    )
    try:
        first = service.submit("one", _user(42))
        assert first_started.wait(timeout=2)
        second = service.submit("two", _user(43))
        observed_at = max(first.updatedAt, second.updatedAt) + timedelta(minutes=2)
        service._now = lambda: observed_at  # type: ignore[method-assign]

        status = service.runtime_status()

        assert status["running"] == 1
        assert status["queued"] == 1
        assert status["stuck"] == 2
    finally:
        release.set()
        service.shutdown(wait=True)


def test_completed_generation_job_survives_service_restart(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"

    def build_service() -> GenerationJobService:
        return GenerationJobService(
            store=GenerationJobStore(store_path),
            image_workers=1,
            video_workers=1,
            generate_images=lambda _description, _provider: SimpleNamespace(asset_set_id="asset-1"),
            generate_video=lambda _image_set: Path("teen-idle.mp4"),
            generate_background_image=lambda _image_set, _provider: Path("teen-sad.png"),
            generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
            generate_happy_image=lambda _image_set, _provider: Path("teen-happy.png"),
            generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
            build_response=_build_response,
            build_failure=lambda _job_id, phase, exc, _owner_id: {
                "code": "GENERATION_FAILED",
                "message": str(exc),
                "phase": phase,
            },
        )

    first = build_service()
    submitted = first.submit("мышонок", _user(), "kandinsky")
    completed = _wait_for(first, submitted.jobId, lambda job: job.status == "succeeded")
    stats = first.metrics_summary(days=30, owner_id=42)
    assert stats["totalJobs"] == 1
    assert stats["failedJobs"] == 0
    assert stats["normal"]["count"] == 1
    assert stats["full"]["count"] == 1
    assert stats["recent"][0]["ownerName"] == "Serge"
    stored = GenerationJobStore(store_path).get(submitted.jobId)
    assert stored is not None
    assert stored.image_provider == "kandinsky"
    first.shutdown(wait=True)

    second = build_service()
    try:
        restored = second.get(submitted.jobId, 42)
        assert restored == completed
    finally:
        second.shutdown(wait=True)


def test_recovered_job_preserves_partial_result_and_passes_asset_hint(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    now = datetime.now(UTC)
    GenerationJobStore(store_path).save(
        StoredGenerationJob(
            owner_id=42,
            username="serge",
            first_name="Serge",
            description="мышонок",
            response=GeneratePetJobResponse(
                jobId="recovered-job",
                status="running",
                phase="generating_happy_video",
                createdAt=now,
                updatedAt=now,
                result=_response(Path("sad.png"), Path("sad.mp4"), None, None),
                error={"code": "OLD_PRIMARY_ERROR"},
                backgroundError={"code": "OLD_BACKGROUND_ERROR"},
                comparisonError={"code": "OLD_COMPARISON_ERROR"},
            ),
        )
    )
    image_started = Event()
    release_image = Event()
    captured: list[tuple[str, str, str, str | None]] = []

    def generate_images_for_job(
        job_id: str,
        description: str,
        provider: str,
        existing_asset_set_id: str | None,
    ):
        captured.append((job_id, description, provider, existing_asset_set_id))
        image_started.set()
        assert release_image.wait(timeout=2)
        return SimpleNamespace(asset_set_id="asset-1")

    service = GenerationJobService(
        store=GenerationJobStore(store_path),
        image_workers=1,
        video_workers=1,
        generate_images=lambda _description, _provider: pytest.fail(
            "recovery must use the job-aware image callback"
        ),
        generate_images_for_job=generate_images_for_job,
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set, _provider: Path("teen-sad.png"),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set, _provider: Path("teen-happy.png"),
        generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
    )
    try:
        assert image_started.wait(timeout=2)
        recovered = service.get("recovered-job", 42)

        assert recovered.status == "running"
        assert recovered.phase == "generating_images"
        assert recovered.result is not None
        assert recovered.result.assetSetId == "asset-1"
        assert recovered.error is None
        assert recovered.backgroundError is None
        assert recovered.comparisonError is None
        assert captured == [("recovered-job", "мышонок", "openai", "asset-1")]
    finally:
        release_image.set()
        service.shutdown(wait=True)


def test_store_cleanup_keeps_old_active_jobs(tmp_path: Path) -> None:
    store = GenerationJobStore(tmp_path / "generation-jobs.sqlite3")
    old = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

    def save(job_id: str, status: str, phase: str) -> None:
        store.save(
            StoredGenerationJob(
                owner_id=42,
                username="serge",
                first_name="Serge",
                description="мышонок",
                response=GeneratePetJobResponse(
                    jobId=job_id,
                    status=status,
                    phase=phase,
                    createdAt=old,
                    updatedAt=old,
                ),
            )
        )

    save("running-job", "running", "generating_images")
    save("failed-job", "failed", "completed")

    store.delete_terminal_older_than(old + timedelta(hours=1))

    assert store.get("running-job") is not None
    assert store.get("failed-job") is None


def test_service_cleanup_deletes_assets_only_for_expired_failed_job_without_result(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    store = GenerationJobStore(store_path)
    old = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)

    def save(job_id: str, status: str, *, with_result: bool = False) -> None:
        store.save(
            StoredGenerationJob(
                owner_id=42,
                username="serge",
                first_name="Serge",
                description="мышонок",
                response=GeneratePetJobResponse(
                    jobId=job_id,
                    status=status,
                    phase="completed",
                    createdAt=old,
                    updatedAt=old,
                    result=_response(None, None, None, None) if with_result else None,
                ),
            )
        )

    save("failed-partial", "failed")
    save("failed-with-result", "failed", with_result=True)
    save("succeeded-pet", "succeeded", with_result=True)
    cleaned: list[str] = []
    service = _test_service(
        store_path,
        lambda _description, _provider: SimpleNamespace(asset_set_id="unused"),
        cleanup_failed_job_assets=cleaned.append,
    )
    try:
        service.cleanup(now=datetime(2026, 7, 15, 12, 0, tzinfo=UTC))
    finally:
        service.shutdown(wait=True)

    assert cleaned == ["failed-partial"]
    assert store.get("failed-partial") is None
    assert store.get("failed-with-result") is None
    assert store.get("succeeded-pet") is None


def test_service_cleanup_restores_failed_job_when_asset_deletion_fails(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    store = GenerationJobStore(store_path)
    old = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    store.save(
        StoredGenerationJob(
            owner_id=42,
            username="serge",
            first_name="Serge",
            description="мышонок",
            response=GeneratePetJobResponse(
                jobId="failed-partial",
                status="failed",
                phase="generating_video",
                createdAt=old,
                updatedAt=old,
            ),
        )
    )

    def fail_cleanup(_job_id: str) -> None:
        raise OSError("volume is temporarily read-only")

    service = _test_service(
        store_path,
        lambda _description, _provider: SimpleNamespace(asset_set_id="unused"),
        cleanup_failed_job_assets=fail_cleanup,
    )
    try:
        service.cleanup(now=datetime(2026, 7, 15, 12, 0, tzinfo=UTC))
    finally:
        service.shutdown(wait=True)

    assert store.get("failed-partial") is not None


def test_generation_job_store_uses_full_sqlite_synchronous_mode(tmp_path: Path) -> None:
    store = GenerationJobStore(tmp_path / "generation-jobs.sqlite3")

    with store._connect() as connection:
        synchronous_mode = connection.execute("PRAGMA synchronous").fetchone()

    assert synchronous_mode == (2,)


def test_generation_job_store_migrates_legacy_table_with_lease_columns(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    with sqlite3.connect(store_path) as connection:
        connection.execute(
            """
            CREATE TABLE generation_jobs (
                job_id TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                description TEXT NOT NULL,
                image_provider TEXT NOT NULL DEFAULT 'openai',
                request_key TEXT,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                response_json TEXT NOT NULL
            )
            """
        )

        now = datetime.now(UTC)
        legacy_response = GeneratePetJobResponse(
            jobId="legacy-job",
            status="queued",
            phase="queued",
            createdAt=now,
            updatedAt=now,
        )
        connection.execute(
            """
            INSERT INTO generation_jobs (
                job_id, owner_id, username, first_name, description,
                image_provider, request_key, status, updated_at, response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-job",
                42,
                "legacy",
                "Legacy",
                "мышонок",
                "openai",
                None,
                "queued",
                now.isoformat(),
                legacy_response.model_dump_json(),
            ),
        )

    store = GenerationJobStore(store_path)

    with sqlite3.connect(store_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(generation_jobs)")}
    assert {"lease_owner", "lease_until"} <= columns
    legacy = store.get("legacy-job")
    assert legacy is not None
    assert legacy.owner_namespace == "telegram"
    assert legacy.notification_chat_id == 42


def test_google_generation_row_ignores_corrupt_telegram_notification_target(
    tmp_path: Path,
) -> None:
    store = GenerationJobStore(tmp_path / "generation-jobs.sqlite3")
    now = datetime.now(UTC)
    store.save(
        StoredGenerationJob(
            owner_id="google:" + "a" * 64,
            username=None,
            first_name=None,
            description="мышонок",
            response=GeneratePetJobResponse(
                jobId="google-corrupt-notification",
                status="queued",
                phase="queued",
                createdAt=now,
                updatedAt=now,
            ),
            owner_namespace="google",
            notification_chat_id=62943754,
        )
    )

    restored = store.get("google-corrupt-notification")

    assert restored is not None
    assert restored.owner_namespace == "google"
    assert restored.notification_chat_id is None


def test_recovered_google_generation_never_calls_telegram_notification(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    store = GenerationJobStore(store_path)
    now = datetime.now(UTC)
    owner = FeatureOwner("google", "google:" + "a" * 64)
    store.save(
        StoredGenerationJob(
            owner_id=owner.storage_key,
            username=None,
            first_name=None,
            description="мышонок",
            response=GeneratePetJobResponse(
                jobId="recovered-google-job",
                status="queued",
                phase="queued",
                createdAt=now,
                updatedAt=now,
            ),
            owner_namespace="google",
            notification_chat_id=62943754,
        )
    )
    notifications: list[int] = []
    service = _test_service(
        store_path,
        lambda _description, _provider: SimpleNamespace(asset_set_id="asset-1"),
        notify_ready=notifications.append,
        notify_outfit_ready=notifications.append,
    )
    try:
        for _ in range(200):
            recovered = service.get_for_owner("recovered-google-job", owner)
            if recovered.status == "succeeded":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("recovered Google generation did not complete")
    finally:
        service.shutdown(wait=True)
    assert notifications == []


def test_migrated_legacy_telegram_job_keeps_recovery_callback(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    now = datetime.now(UTC)
    response = GeneratePetJobResponse(
        jobId="legacy-recovered-job",
        status="queued",
        phase="queued",
        createdAt=now,
        updatedAt=now,
    )
    with sqlite3.connect(store_path) as connection:
        connection.execute(
            """
            CREATE TABLE generation_jobs (
                job_id TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                description TEXT NOT NULL,
                image_provider TEXT NOT NULL DEFAULT 'openai',
                request_key TEXT,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                response_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO generation_jobs (
                job_id, owner_id, username, first_name, description,
                image_provider, request_key, status, updated_at, response_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                response.jobId,
                42,
                "legacy",
                "Legacy",
                "мышонок",
                "openai",
                None,
                "queued",
                now.isoformat(),
                response.model_dump_json(),
            ),
        )
    notifications: list[int] = []
    service = _test_service(
        store_path,
        lambda _description, _provider: SimpleNamespace(asset_set_id="asset-1"),
        notify_ready=notifications.append,
        notify_outfit_ready=notifications.append,
    )
    try:
        completed = _wait_for(
            service,
            response.jobId,
            lambda job: job.status == "succeeded",
        )
        assert completed.status == "succeeded"
    finally:
        service.shutdown(wait=True)
    assert notifications == [42]


def test_service_cleanup_prunes_metrics_after_365_days_independently(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    store = GenerationJobStore(store_path)
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)

    def record_metric(job_id: str, created_at: datetime) -> None:
        store.record_queued(
            StoredGenerationJob(
                owner_id=42,
                username="serge",
                first_name="Serge",
                description="мышонок",
                response=GeneratePetJobResponse(
                    jobId=job_id,
                    status="failed",
                    phase="generating_images",
                    createdAt=created_at,
                    updatedAt=created_at,
                ),
            )
        )

    record_metric("expired-metric", now - timedelta(days=366))
    record_metric("retained-metric", now - timedelta(days=364))
    service = _test_service(
        store_path,
        lambda _description, _provider: SimpleNamespace(asset_set_id="unused"),
    )
    try:
        service.cleanup(now=now)
        with sqlite3.connect(store_path) as connection:
            job_ids = {
                str(row[0])
                for row in connection.execute(
                    "SELECT job_id FROM generation_job_metrics ORDER BY job_id"
                )
            }
        assert job_ids == {"retained-metric"}
    finally:
        service.shutdown(wait=True)


def test_concurrent_generation_store_initialization_waits_for_wal_lock(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    barrier = Barrier(8)

    def initialize(_index: int) -> bool:
        barrier.wait()
        GenerationJobStore(store_path)
        return True

    with ThreadPoolExecutor(max_workers=8) as executor:
        initialized = list(executor.map(initialize, range(8)))

    assert initialized == [True] * 8


def test_startup_requeues_every_durable_active_job_locally(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    store = GenerationJobStore(store_path)
    now = datetime.now(UTC)
    for index in range(3):
        store.save(
            StoredGenerationJob(
                owner_id=42 + index,
                username="serge",
                first_name="Serge",
                description=f"pet-{index}",
                response=GeneratePetJobResponse(
                    jobId=f"backlog-{index}",
                    status="running",
                    phase="generating_video",
                    createdAt=now,
                    updatedAt=now,
                ),
            )
        )
    first_started = Event()
    release_first = Event()

    def generate_images(_description: str, _provider: str):
        first_started.set()
        assert release_first.wait(timeout=5)
        return SimpleNamespace(asset_set_id="asset-1")

    service = _test_service(store_path, generate_images, max_queued_jobs=0)
    try:
        assert first_started.wait(timeout=2)
        status = service.runtime_status()

        assert status["running"] == 1
        assert status["queued"] == 2
        assert set(service._jobs) == {"backlog-0", "backlog-1", "backlog-2"}
        assert {job.response.phase for job in store.active()} <= {
            "queued",
            "generating_images",
        }
    finally:
        release_first.set()
        service.shutdown(wait=True)


def test_shutdown_drains_running_stage_cancels_queued_and_restart_resumes(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    first_started = Event()
    release_first = Event()
    calls: list[str] = []

    def generate_images(description: str, _provider: str):
        calls.append(description)
        first_started.set()
        assert release_first.wait(timeout=5)
        return SimpleNamespace(asset_set_id="asset-1")

    service = _test_service(store_path, generate_images, max_queued_jobs=1)
    first = service.submit("первый", _user(42))
    assert first_started.wait(timeout=2)
    second = service.submit("второй", _user(43))
    shutdown_thread = Thread(target=service.shutdown, kwargs={"wait": True})
    shutdown_thread.start()
    try:
        time.sleep(0.05)
        assert shutdown_thread.is_alive()
        assert calls == ["первый"]
        with pytest.raises(GenerationQueueFullError, match="shutting down"):
            service.submit("третий", _user(44))
        release_first.set()
        shutdown_thread.join(timeout=3)
    finally:
        release_first.set()
        service.shutdown(wait=True)

    assert not shutdown_thread.is_alive()
    assert calls == ["первый"]
    for job_id in (first.jobId, second.jobId):
        stored = GenerationJobStore(store_path).get(job_id)
        assert stored is not None
        assert stored.response.status in {"queued", "running"}
        assert stored.response.error is None

    resumed = _test_service(
        store_path,
        lambda description, _provider: SimpleNamespace(asset_set_id=f"asset-{description}"),
    )
    try:
        for job, owner_id in ((first, 42), (second, 43)):
            completed = _wait_for(
                resumed,
                job.jobId,
                lambda response: response.status == "succeeded",
                owner_id=owner_id,
            )
            assert completed.phase == "completed"
    finally:
        resumed.shutdown(wait=True)


def test_create_failure_does_not_leave_local_or_durable_ghost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    service = _test_service(
        store_path,
        lambda _description, _provider: pytest.fail("provider must not run"),
        max_queued_jobs=0,
    )

    def fail_create(*_args, **_kwargs):
        raise OSError("synthetic create failure")

    monkeypatch.setattr(service._store, "create_or_get", fail_create)
    try:
        with pytest.raises(OSError, match="synthetic create failure"):
            service.submit("мышонок", _user())

        assert service._jobs == {}
        assert GenerationJobStore(store_path).active() == []
    finally:
        service.shutdown(wait=True)


@pytest.mark.parametrize("save_failures", [1, 2])
def test_durable_save_retries_once_and_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    save_failures: int,
) -> None:
    store_path = tmp_path / f"generation-jobs-{save_failures}.sqlite3"
    video_calls = 0

    def generate_video(_image_set):
        nonlocal video_calls
        video_calls += 1
        return Path("teen-idle.mp4")

    service = _test_service(
        store_path,
        lambda _description, _provider: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=generate_video,
    )
    original_save = service._store.save_claimed
    attempted_phases: list[str] = []
    failures_remaining = save_failures

    def flaky_save(job, *, lease_owner, lease_until):
        nonlocal failures_remaining
        attempted_phases.append(job.response.phase)
        if job.response.phase == "generating_video" and failures_remaining > 0:
            failures_remaining -= 1
            raise OSError("synthetic save failure")
        return original_save(job, lease_owner=lease_owner, lease_until=lease_until)

    monkeypatch.setattr(service._store, "save_claimed", flaky_save)
    try:
        submitted = service.submit("мышонок", _user())
        if save_failures == 1:
            completed = _wait_for(service, submitted.jobId, lambda job: job.status == "succeeded")
            assert completed.phase == "completed"
            assert video_calls == 1
        else:
            for _ in range(200):
                with service._lock:
                    if submitted.jobId not in service._jobs:
                        break
                time.sleep(0.005)
            else:
                raise AssertionError("persistent save failure did not drop local job")

            stored = GenerationJobStore(store_path).get(submitted.jobId)
            assert stored is not None
            assert stored.response.phase == "generating_images"
            assert video_calls == 0

        assert attempted_phases[:3] == [
            "generating_images",
            "generating_video",
            "generating_video",
        ]
    finally:
        service.shutdown(wait=True)


def test_owner_active_alias_precedes_queue_capacity(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    image_started = Event()
    release_image = Event()

    def generate_images(_description: str, _provider: str):
        image_started.set()
        assert release_image.wait(timeout=5)
        return SimpleNamespace(asset_set_id="asset-1")

    service = _test_service(
        store_path,
        generate_images,
        max_queued_jobs=0,
    )
    try:
        first = service.submit(
            "первый",
            _user(),
            request_key="create-pet:full-queue-primary-0001",
        )
        assert image_started.wait(timeout=2)

        with pytest.raises(GenerationOwnerActiveError) as error:
            service.submit(
                "первый",
                _user(),
                request_key="create-pet:full-queue-alias-0001",
            )

        assert error.value.job_id == first.jobId
        replay = service.find_by_request_key(
            "create-pet:full-queue-alias-0001",
            42,
            "первый",
            "openai",
        )
        assert replay is not None
        assert replay.jobId == first.jobId
        with sqlite3.connect(store_path) as connection:
            key_rows = connection.execute(
                """
                SELECT request_key, job_id
                FROM generation_job_request_keys
                ORDER BY request_key
                """
            ).fetchall()
            dormant = connection.execute(
                "SELECT request_key, lease_owner, lease_until FROM generation_jobs"
            ).fetchone()
        assert key_rows == [
            ("create-pet:full-queue-alias-0001", first.jobId),
            ("create-pet:full-queue-primary-0001", first.jobId),
        ]
        assert dormant is not None
        assert dormant[0] is None
        assert dormant[1] is not None
        assert dormant[2] is not None
    finally:
        release_image.set()
        service.shutdown(wait=True)


def test_second_service_does_not_run_job_owned_by_live_lease(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    started = Event()
    release = Event()
    duplicate_calls: list[str] = []

    def blocking_images(_description: str, _provider: str):
        started.set()
        assert release.wait(timeout=5)
        return SimpleNamespace(asset_set_id="asset-1")

    first = _test_service(store_path, blocking_images)
    submitted = first.submit("мышонок", _user())
    assert started.wait(timeout=2)
    second = _test_service(
        store_path,
        lambda description, _provider: duplicate_calls.append(description),
    )
    try:
        time.sleep(0.05)
        assert duplicate_calls == []
        assert second.get(submitted.jobId, 42).status == "running"
    finally:
        release.set()
        first.shutdown(wait=True)
        second.shutdown(wait=True)


def test_watchdog_recovers_job_after_previous_process_lease_expires(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    _seed_active_job(store_path)
    store = GenerationJobStore(store_path)
    assert store.claim(
        "recovered-job",
        lease_owner="dead-process",
        lease_until=datetime.now(UTC) + timedelta(minutes=5),
        now=datetime.now(UTC),
    )
    recovered = Event()

    service = _test_service(
        store_path,
        lambda _description, _provider: recovered.set() or SimpleNamespace(asset_set_id="asset-1"),
        watchdog_interval_seconds=0.01,
    )
    try:
        assert not recovered.wait(timeout=0.05)
        with sqlite3.connect(store_path) as connection:
            connection.execute(
                "UPDATE generation_jobs SET lease_until = ? WHERE job_id = ?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), "recovered-job"),
            )
        assert recovered.wait(timeout=2)
        completed = _wait_for(
            service,
            "recovered-job",
            lambda job: job.status == "succeeded",
        )
        assert completed.phase == "completed"
    finally:
        service.shutdown(wait=True)


def test_watchdog_fails_stale_job_and_releases_owner(tmp_path: Path) -> None:
    release = Event()

    def blocked_images(_description: str, _provider: str):
        assert release.wait(timeout=5)
        return SimpleNamespace(asset_set_id="asset-1")

    service = _test_service(
        tmp_path / "generation-jobs.sqlite3",
        blocked_images,
        stuck_after=timedelta(milliseconds=20),
        lease_duration=timedelta(seconds=30),
        watchdog_interval_seconds=0.01,
    )
    try:
        submitted = service.submit("мышонок", _user())
        failed = _wait_for(service, submitted.jobId, lambda job: job.status == "failed")
        assert failed.phase == "watchdog_timeout"
        assert failed.error and failed.error["phase"] == "watchdog_timeout"
        replacement = service.submit("новый мышонок", _user())
        assert replacement.jobId != submitted.jobId
    finally:
        release.set()
        service.shutdown(wait=True)


def test_owner_active_different_payload_is_not_aliased(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    image_started = Event()
    release_image = Event()
    image_calls = 0

    def generate_images(_description: str, _provider: str):
        nonlocal image_calls
        image_calls += 1
        image_started.set()
        assert release_image.wait(timeout=5)
        return SimpleNamespace(asset_set_id=f"asset-{image_calls}")

    service = _test_service(store_path, generate_images)
    try:
        first = service.submit(
            "кот",
            _user(),
            request_key="create-pet:different-primary-0001",
        )
        assert image_started.wait(timeout=2)

        with pytest.raises(GenerationOwnerActiveError):
            service.submit(
                "собака",
                _user(),
                request_key="create-pet:different-contender-0001",
            )

        store = GenerationJobStore(store_path)
        assert store.get_by_request_key(42, "create-pet:different-contender-0001") is None

        release_image.set()
        _wait_for(service, first.jobId, lambda job: job.status == "succeeded")
        second = service.submit(
            "собака",
            _user(),
            request_key="create-pet:different-contender-0001",
        )

        assert second.jobId != first.jobId
        _wait_for(service, second.jobId, lambda job: job.status == "succeeded")
        assert image_calls == 2
    finally:
        release_image.set()
        service.shutdown(wait=True)


def test_generation_owner_active_aliases_are_atomic_across_processes(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    _seed_active_job(store_path, job_id="shared-active-job")
    context = multiprocessing.get_context("spawn")
    gate = context.Barrier(2)
    results = context.Queue()
    aliases = [
        ("create-pet:process-alias-0001", "мышонок"),
        ("create-pet:process-alias-0002", "мышонок"),
    ]
    processes = [
        context.Process(
            target=_bind_generation_alias_in_process,
            args=(str(store_path), request_key, description, gate, results),
        )
        for request_key, description in aliases
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    process_results = sorted(results.get(timeout=2) for _process in processes)
    assert process_results == [
        (request_key, False, "owner_active", "shared-active-job")
        for request_key, _description in aliases
    ]
    restarted_store = GenerationJobStore(store_path)
    for request_key, description in aliases:
        replay = restarted_store.get_by_request_key(42, request_key)
        assert replay is not None
        assert replay.response.jobId == "shared-active-job"
        assert replay.description == description
    with sqlite3.connect(store_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM generation_jobs").fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM generation_job_request_keys"
        ).fetchone() == (2,)


def test_generation_owner_active_alias_replays_after_restart(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    image_started = Event()
    release_image = Event()

    def generate_images(_description: str, _provider: str):
        image_started.set()
        assert release_image.wait(timeout=5)
        return SimpleNamespace(asset_set_id="asset-1")

    first_service = _test_service(store_path, generate_images)
    submitted = first_service.submit(
        "исходный питомец",
        _user(),
        request_key="create-pet:restart-primary-0001",
    )
    assert image_started.wait(timeout=2)
    with pytest.raises(GenerationOwnerActiveError):
        first_service.submit(
            "исходный питомец",
            _user(),
            request_key="create-pet:restart-alias-0001",
        )
    release_image.set()
    completed = _wait_for(
        first_service,
        submitted.jobId,
        lambda job: job.status == "succeeded",
    )
    first_service.shutdown(wait=True)

    replay_calls = 0

    def unexpected_generate(_description: str, _provider: str):
        nonlocal replay_calls
        replay_calls += 1
        return SimpleNamespace(asset_set_id="unexpected")

    restarted = _test_service(store_path, unexpected_generate)
    try:
        replay = restarted.submit(
            "исходный питомец",
            _user(),
            request_key="create-pet:restart-alias-0001",
        )
        assert replay == completed
        assert replay_calls == 0
        with pytest.raises(GenerationIdempotencyConflictError):
            restarted.submit(
                "изменённый alias payload",
                _user(),
                request_key="create-pet:restart-alias-0001",
            )
    finally:
        restarted.shutdown(wait=True)


def test_idempotent_terminal_job_survives_restart_without_resubmission(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    first_calls = 0

    def generate_images(_description: str, _provider: str):
        nonlocal first_calls
        first_calls += 1
        return SimpleNamespace(asset_set_id="asset-1")

    first = _test_service(store_path, generate_images)
    submitted = first.submit(
        "мышонок",
        _user(),
        request_key="create-pet:restart-0001",
    )
    completed = _wait_for(first, submitted.jobId, lambda job: job.status == "succeeded")
    first.shutdown(wait=True)

    replay_calls = 0

    def unexpected_generate(_description: str, _provider: str):
        nonlocal replay_calls
        replay_calls += 1
        return SimpleNamespace(asset_set_id="asset-2")

    second = _test_service(store_path, unexpected_generate)
    try:
        replay = second.submit(
            "мышонок",
            _user(),
            request_key="create-pet:restart-0001",
        )

        assert replay == completed
        assert first_calls == 1
        assert replay_calls == 0
        with pytest.raises(GenerationIdempotencyConflictError):
            second.submit(
                "изменённый payload",
                _user(),
                request_key="create-pet:restart-0001",
            )
    finally:
        second.shutdown(wait=True)


def test_keyed_terminal_job_outlives_regular_job_ttl(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    created_at = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    store = GenerationJobStore(store_path)

    def save(job_id: str, request_key: str | None) -> None:
        job = StoredGenerationJob(
            owner_id=42,
            username="serge",
            first_name="Serge",
            description="мышонок",
            response=GeneratePetJobResponse(
                jobId=job_id,
                status="failed",
                phase="generating_images",
                createdAt=created_at,
                updatedAt=created_at,
                error={"code": "SYNTHETIC"},
            ),
        )
        if request_key is None:
            store.save(job)
        else:
            assert store.create_or_get(
                job,
                request_key=request_key,
                max_active_jobs=1,
            ).created

    save("legacy-terminal", None)
    save("keyed-terminal", "create-pet:retention-0001")
    service = _test_service(
        store_path,
        lambda _description, _provider: SimpleNamespace(asset_set_id="unused"),
        job_ttl=timedelta(hours=1),
        idempotency_ttl=timedelta(days=2),
    )
    try:
        service.cleanup(now=created_at + timedelta(hours=2))
        assert store.get("legacy-terminal") is None
        assert store.get("keyed-terminal") is not None

        service.cleanup(now=created_at + timedelta(days=2, seconds=1))
        assert store.get("keyed-terminal") is None
    finally:
        service.shutdown(wait=True)


def test_alias_target_uses_idempotency_ttl_and_prunes_with_receipt(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    created_at = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    store = GenerationJobStore(store_path)
    active = StoredGenerationJob(
        owner_id=42,
        username="serge",
        first_name="Serge",
        description="active без primary key",
        response=GeneratePetJobResponse(
            jobId="alias-retention-target",
            status="running",
            phase="generating_images",
            createdAt=created_at,
            updatedAt=created_at,
        ),
    )
    store.save(active)
    alias_candidate = StoredGenerationJob(
        owner_id=42,
        username="serge",
        first_name="Serge",
        description="active без primary key",
        response=GeneratePetJobResponse(
            jobId="unused-alias-candidate",
            status="queued",
            phase="queued",
            createdAt=created_at,
            updatedAt=created_at,
        ),
    )
    creation = store.create_or_get(
        alias_candidate,
        request_key="create-pet:alias-retention-0001",
        max_active_jobs=1,
    )
    assert creation.conflict == "owner_active"
    store.save(
        StoredGenerationJob(
            **{
                **active.__dict__,
                "response": active.response.model_copy(
                    update={
                        "status": "failed",
                        "phase": "generating_images",
                        "updatedAt": created_at,
                        "error": {"code": "SYNTHETIC"},
                    }
                ),
            }
        )
    )
    service = _test_service(
        store_path,
        lambda _description, _provider: SimpleNamespace(asset_set_id="unused"),
        job_ttl=timedelta(hours=1),
        idempotency_ttl=timedelta(days=2),
    )
    try:
        service.cleanup(now=created_at + timedelta(hours=2))
        assert store.get("alias-retention-target") is not None
        assert store.get_by_request_key(42, "create-pet:alias-retention-0001") is not None

        service.cleanup(now=created_at + timedelta(days=2, seconds=1))
        assert store.get("alias-retention-target") is None
        assert store.get_by_request_key(42, "create-pet:alias-retention-0001") is None
        with sqlite3.connect(store_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM generation_job_request_keys"
            ).fetchone() == (0,)
    finally:
        service.shutdown(wait=True)
