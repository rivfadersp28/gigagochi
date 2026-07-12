from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest

from app.services.generation_job_service import (
    GenerationJobService,
    GenerationQueueFullError,
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


def _wait_for(service: GenerationJobService, job_id: str, predicate):
    for _ in range(200):
        job = service.get(job_id, 42)
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


def test_foreground_result_is_available_before_background_assets() -> None:
    background_started = Event()
    release_background = Event()
    notification_sent = Event()
    notifications: list[int] = []

    def notify_ready(owner_id: int) -> None:
        notifications.append(owner_id)
        notification_sent.set()

    def generate_background_image(_image_set):
        background_started.set()
        assert release_background.wait(timeout=2)
        return Path("teen-sad.png")

    service = GenerationJobService(
        image_workers=1,
        video_workers=1,
        generate_images=lambda _description: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=generate_background_image,
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set: Path("teen-happy.png"),
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

        release_background.set()
        completed = _wait_for(service, submitted.jobId, lambda job: job.status == "succeeded")
        assert completed.result is not None
        assert completed.result.sadVideoUrl.endswith("teen-sad.mp4")
        assert completed.result.happyVideoUrl.endswith("teen-happy.mp4")
    finally:
        release_background.set()
        service.shutdown(wait=True)


def test_notification_failure_does_not_fail_generation() -> None:
    def notify_ready(_owner_id: int) -> None:
        raise RuntimeError("telegram unavailable")

    service = GenerationJobService(
        image_workers=1,
        video_workers=1,
        generate_images=lambda _description: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set: Path("teen-sad.png"),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set: Path("teen-happy.png"),
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


def test_background_failure_keeps_foreground_result() -> None:
    service = GenerationJobService(
        image_workers=1,
        video_workers=1,
        generate_images=lambda _description: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set: (_ for _ in ()).throw(
            RuntimeError("sad image failed")
        ),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set: Path("teen-happy.png"),
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


def test_every_owner_gets_derived_assets() -> None:
    background_calls: list[str] = []
    service = GenerationJobService(
        image_workers=1,
        video_workers=1,
        generate_images=lambda _description: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set: (
            background_calls.append("sad-image") or Path("teen-sad.png")
        ),
        generate_background_video=lambda _image_set, _sad_path: (
            background_calls.append("sad-video") or Path("teen-sad.mp4")
        ),
        generate_happy_image=lambda _image_set: (
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


def test_twenty_generation_pipelines_start_concurrently() -> None:
    barrier = Barrier(20)
    counter_lock = Lock()
    active = 0
    peak_active = 0

    def generate_images(_description: str):
        nonlocal active, peak_active
        with counter_lock:
            active += 1
            peak_active = max(peak_active, active)
        barrier.wait(timeout=5)
        with counter_lock:
            active -= 1
        return SimpleNamespace(asset_set_id="asset-1")

    service = GenerationJobService(
        image_workers=20,
        video_workers=20,
        max_queued_jobs=40,
        generate_images=generate_images,
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set: Path("teen-sad.png"),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set: Path("teen-happy.png"),
        generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
    )
    try:
        jobs = [service.submit(f"pet-{index}", _user()) for index in range(20)]
        for job in jobs:
            _wait_for(service, job.jobId, lambda response: response.status == "succeeded")
        assert peak_active == 20
        assert service.runtime_status()["queued"] == 0
        assert service.runtime_status()["running"] == 0
    finally:
        service.shutdown(wait=True)


def test_generation_queue_is_bounded() -> None:
    release = Event()

    def generate_images(_description: str):
        assert release.wait(timeout=5)
        return SimpleNamespace(asset_set_id="asset-1")

    service = GenerationJobService(
        image_workers=1,
        video_workers=1,
        max_queued_jobs=1,
        generate_images=generate_images,
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set: Path("teen-sad.png"),
        generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set: Path("teen-happy.png"),
        generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
    )
    try:
        service.submit("one", _user())
        service.submit("two", _user())
        with pytest.raises(GenerationQueueFullError):
            service.submit("three", _user())
    finally:
        release.set()
        service.shutdown(wait=True)


def test_completed_generation_job_survives_service_restart(tmp_path: Path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"

    def build_service() -> GenerationJobService:
        return GenerationJobService(
            image_workers=1,
            video_workers=1,
            store_path=str(store_path),
            generate_images=lambda _description: SimpleNamespace(asset_set_id="asset-1"),
            generate_video=lambda _image_set: Path("teen-idle.mp4"),
            generate_background_image=lambda _image_set: Path("teen-sad.png"),
            generate_background_video=lambda _image_set, _sad_path: Path("teen-sad.mp4"),
            generate_happy_image=lambda _image_set: Path("teen-happy.png"),
            generate_happy_video=lambda _image_set, _happy_path: Path("teen-happy.mp4"),
            build_response=_build_response,
            build_failure=lambda _job_id, phase, exc, _owner_id: {
                "code": "GENERATION_FAILED",
                "message": str(exc),
                "phase": phase,
            },
        )

    first = build_service()
    submitted = first.submit("мышонок", _user())
    completed = _wait_for(first, submitted.jobId, lambda job: job.status == "succeeded")
    stats = first.metrics_summary(days=30, owner_id=42)
    assert stats["totalJobs"] == 1
    assert stats["failedJobs"] == 0
    assert stats["normal"]["count"] == 1
    assert stats["full"]["count"] == 1
    assert stats["recent"][0]["ownerName"] == "Serge"
    first.shutdown(wait=True)

    second = build_service()
    try:
        restored = second.get(submitted.jobId, 42)
        assert restored == completed
    finally:
        second.shutdown(wait=True)
