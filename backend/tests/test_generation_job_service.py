from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from app.services.generation_job_service import GenerationJobService
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

        release_background.set()
        completed = _wait_for(service, submitted.jobId, lambda job: job.status == "succeeded")
        assert completed.result is not None
        assert completed.result.sadVideoUrl.endswith("teen-sad.mp4")
        assert completed.result.happyVideoUrl.endswith("teen-happy.mp4")
    finally:
        release_background.set()
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


def test_non_pilot_owner_finishes_after_normal_video_without_derived_assets() -> None:
    background_calls: list[str] = []
    service = GenerationJobService(
        image_workers=1,
        video_workers=1,
        generate_images=lambda _description: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=lambda _image_set: Path("teen-idle.mp4"),
        generate_background_image=lambda _image_set: background_calls.append("sad-image"),
        generate_background_video=lambda _image_set, _sad_path: background_calls.append(
            "sad-video"
        ),
        generate_happy_image=lambda _image_set: background_calls.append("happy-image"),
        generate_happy_video=lambda _image_set, _happy_path: background_calls.append("happy-video"),
        build_response=_build_response,
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "GENERATION_FAILED",
            "message": str(exc),
            "phase": phase,
        },
        derived_asset_owner_ids={99},
    )
    try:
        submitted = service.submit("мышонок", _user())
        completed = _wait_for(service, submitted.jobId, lambda job: job.status == "succeeded")

        assert completed.phase == "completed"
        assert completed.result is not None
        assert completed.result.videoUrl.endswith("teen-idle.mp4")
        assert completed.result.sadVideoUrl is None
        assert completed.result.happyVideoUrl is None
        assert background_calls == []
    finally:
        service.shutdown(wait=True)
