from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from app.schemas import GeneratePetAssetResponse, GeneratePetJobResponse
from app.services.generation_job_store import GenerationJobStore, StoredGenerationJob
from app.services.prompt_debug import reset_prompt_log_context, set_prompt_log_context
from app.services.telegram_auth_service import TelegramUserContext

logger = logging.getLogger(__name__)

GenerateImages = Callable[[str], Any]
GenerateVideo = Callable[[Any], Any]
GenerateBackgroundImage = Callable[[Any], Any]
GenerateBackgroundVideo = Callable[[Any, Any], Any]
GenerateHappyImage = Callable[[Any], Any]
GenerateHappyVideo = Callable[[Any, Any], Any]
BuildResponse = Callable[
    [Any, Any, Any | None, Any | None, Any | None, Any | None],
    dict[str, Any],
]
BuildFailure = Callable[[str, str, Exception, int], dict[str, object]]
NotifyReady = Callable[[int], None]
_UNSET = object()


class GenerationJobNotFoundError(LookupError):
    pass


class GenerationQueueFullError(RuntimeError):
    pass


@dataclass
class GenerationJobRecord:
    owner_id: int
    username: str | None
    first_name: str | None
    description: str
    response: GeneratePetJobResponse


class GenerationJobService:
    def __init__(
        self,
        *,
        image_workers: int,
        video_workers: int,
        generate_images: GenerateImages,
        generate_video: GenerateVideo,
        generate_background_image: GenerateBackgroundImage,
        generate_background_video: GenerateBackgroundVideo,
        generate_happy_image: GenerateHappyImage,
        generate_happy_video: GenerateHappyVideo,
        build_response: BuildResponse,
        build_failure: BuildFailure,
        notify_ready: NotifyReady | None = None,
        job_ttl: timedelta = timedelta(hours=1),
        store_path: str | None = None,
        max_queued_jobs: int = 40,
        stuck_after: timedelta = timedelta(minutes=30),
    ) -> None:
        self._generate_images = generate_images
        self._generate_video = generate_video
        self._generate_background_image = generate_background_image
        self._generate_background_video = generate_background_video
        self._generate_happy_image = generate_happy_image
        self._generate_happy_video = generate_happy_video
        self._build_response = build_response
        self._build_failure = build_failure
        self._notify_ready_callback = notify_ready
        self._job_ttl = job_ttl
        self._max_queued_jobs = max(0, max_queued_jobs)
        self._stuck_after = stuck_after
        self._image_workers = image_workers
        self._video_workers = video_workers
        self._image_executor = ThreadPoolExecutor(
            max_workers=image_workers,
            thread_name_prefix="pet-image",
        )
        self._video_executor = ThreadPoolExecutor(
            max_workers=video_workers,
            thread_name_prefix="pet-video",
        )
        self._jobs: dict[str, GenerationJobRecord] = {}
        self._lock = Lock()
        self._store = GenerationJobStore(store_path) if store_path else None
        self._recover_active_jobs()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def cleanup(self, now: datetime | None = None) -> None:
        cutoff = (now or self._now()) - self._job_ttl
        with self._lock:
            expired_job_ids = [
                job_id
                for job_id, record in self._jobs.items()
                if record.response.updatedAt < cutoff
            ]
            for job_id in expired_job_ids:
                self._jobs.pop(job_id, None)
        if self._store is not None:
            self._store.delete_older_than(cutoff)

    def runtime_status(self) -> dict[str, int]:
        now = self._now()
        with self._lock:
            records = list(self._jobs.values())
        queued = sum(record.response.status == "queued" for record in records)
        running = sum(record.response.status == "running" for record in records)
        stuck = sum(
            record.response.status == "running"
            and now - record.response.updatedAt > self._stuck_after
            for record in records
        )
        return {
            "imageWorkers": self._image_workers,
            "videoWorkers": self._video_workers,
            "queued": queued,
            "running": running,
            "stuck": stuck,
            "queueCapacity": self._max_queued_jobs,
        }

    def metrics_summary(self, *, days: int, owner_id: int | None) -> dict[str, object]:
        if self._store is None:
            return GenerationJobStore.empty_metrics_summary(days=days)
        return self._store.metrics_summary(days=days, owner_id=owner_id)

    def submit(self, description: str, user: TelegramUserContext) -> GeneratePetJobResponse:
        self.cleanup()
        now = self._now()
        job_id = str(uuid.uuid4())
        response = GeneratePetJobResponse(
            jobId=job_id,
            status="queued",
            phase="queued",
            createdAt=now,
            updatedAt=now,
        )
        with self._lock:
            active_count = sum(
                record.response.status in {"queued", "running"}
                for record in self._jobs.values()
            )
            if active_count >= self._image_workers + self._max_queued_jobs:
                raise GenerationQueueFullError("generation queue is full")
            self._jobs[job_id] = GenerationJobRecord(
                owner_id=user.telegram_id,
                username=user.username,
                first_name=user.first_name,
                description=description,
                response=response,
            )
            self._persist_locked(job_id)
        self._record_metric_queued(job_id)
        logger.info(
            "pet_generation_queued jobId=%s ownerId=%s username=%s firstName=%s "
            "imageWorkers=%s videoWorkers=%s",
            job_id,
            user.telegram_id,
            user.username,
            user.first_name,
            self._image_workers,
            self._video_workers,
        )
        try:
            self._submit_image_job(job_id, description)
        except Exception as exc:
            self._fail(job_id, exc, phase="generating_images")
        return response

    def get(self, job_id: str, owner_id: int) -> GeneratePetJobResponse:
        self.cleanup()
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None and self._store is not None:
                stored = self._store.get(job_id)
                if stored is not None:
                    record = self._record_from_stored(stored)
                    self._jobs[job_id] = record
            if record is None or record.owner_id != owner_id:
                raise GenerationJobNotFoundError(job_id)
            return record.response

    def shutdown(self, *, wait: bool = False) -> None:
        self._image_executor.shutdown(wait=wait, cancel_futures=True)
        self._video_executor.shutdown(wait=wait, cancel_futures=True)

    def _update(
        self,
        job_id: str,
        *,
        status_value: str,
        phase: str | None = None,
        result: GeneratePetAssetResponse | None | object = _UNSET,
        error: dict[str, object] | None | object = _UNSET,
        background_error: dict[str, object] | None | object = _UNSET,
    ) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            updates: dict[str, object] = {
                "status": status_value,
                "updatedAt": self._now(),
            }
            if result is not _UNSET:
                updates["result"] = result
            if error is not _UNSET:
                updates["error"] = error
            if background_error is not _UNSET:
                updates["backgroundError"] = background_error
            if phase is not None:
                updates["phase"] = phase
            record.response = record.response.model_copy(update=updates)
            self._persist_locked(job_id)

    def _persist_locked(self, job_id: str) -> None:
        if self._store is None:
            return
        record = self._jobs.get(job_id)
        if record is None:
            return
        self._store.save(
            StoredGenerationJob(
                owner_id=record.owner_id,
                username=record.username,
                first_name=record.first_name,
                description=record.description,
                response=record.response,
            )
        )

    def _record_metric_queued(self, job_id: str) -> None:
        if self._store is None:
            return
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            stored = StoredGenerationJob(
                owner_id=record.owner_id,
                username=record.username,
                first_name=record.first_name,
                description=record.description,
                response=record.response,
            )
        try:
            self._store.record_queued(stored)
        except Exception:
            logger.warning("generation_metric_queued_failed jobId=%s", job_id, exc_info=True)

    def _mark_metric(self, job_id: str, field: str, *, status: str | None = None) -> None:
        if self._store is None:
            return
        try:
            self._store.mark_metric(job_id, field, status=status)
        except Exception:
            logger.warning(
                "generation_metric_mark_failed jobId=%s field=%s",
                job_id,
                field,
                exc_info=True,
            )

    @staticmethod
    def _record_from_stored(stored: StoredGenerationJob) -> GenerationJobRecord:
        return GenerationJobRecord(
            owner_id=stored.owner_id,
            username=stored.username,
            first_name=stored.first_name,
            description=stored.description,
            response=stored.response,
        )

    def _submit_image_job(self, job_id: str, description: str) -> None:
        self._image_executor.submit(self._run_image_job, job_id, description)

    def _recover_active_jobs(self) -> None:
        if self._store is None:
            return
        for stored in self._store.active():
            response = stored.response.model_copy(
                update={
                    "status": "queued",
                    "phase": "queued",
                    "updatedAt": self._now(),
                    "error": None,
                }
            )
            record = self._record_from_stored(
                StoredGenerationJob(
                    owner_id=stored.owner_id,
                    username=stored.username,
                    first_name=stored.first_name,
                    description=stored.description,
                    response=response,
                )
            )
            with self._lock:
                self._jobs[response.jobId] = record
                self._persist_locked(response.jobId)
            self._record_metric_queued(response.jobId)
            self._submit_image_job(response.jobId, stored.description)
            logger.warning(
                "pet_generation_recovered jobId=%s ownerId=%s",
                response.jobId,
                stored.owner_id,
            )

    def _fail(self, job_id: str, exc: Exception, *, phase: str) -> None:
        detail = self._build_failure(job_id, phase, exc, self._owner_id(job_id))
        self._update(job_id, status_value="failed", phase=phase, error=detail)
        self._mark_metric(job_id, "failed_at", status="failed")

    def _finish_background_failure(self, job_id: str, exc: Exception, *, phase: str) -> None:
        detail = self._build_failure(job_id, phase, exc, self._owner_id(job_id))
        logger.warning(
            "pet_background_generation_failed jobId=%s phase=%s errorType=%s",
            job_id,
            phase,
            type(exc).__name__,
        )
        self._update(
            job_id,
            status_value="succeeded",
            phase="completed",
            background_error=detail,
        )
        self._mark_metric(job_id, "completed_at", status="completed_with_errors")

    def _record_background_failure(self, job_id: str, exc: Exception, *, phase: str) -> None:
        detail = self._build_failure(job_id, phase, exc, self._owner_id(job_id))
        logger.warning(
            "pet_background_generation_failed jobId=%s phase=%s errorType=%s",
            job_id,
            phase,
            type(exc).__name__,
        )
        self._update(
            job_id,
            status_value="running",
            background_error=detail,
        )

    def _owner_id(self, job_id: str) -> int:
        with self._lock:
            record = self._jobs.get(job_id)
            return record.owner_id if record is not None else 0

    def _notify_ready(self, job_id: str) -> None:
        if self._notify_ready_callback is None:
            return
        owner_id = self._owner_id(job_id)
        try:
            self._notify_ready_callback(owner_id)
        except Exception:
            logger.warning(
                "pet_generation_notification_failed jobId=%s ownerId=%s",
                job_id,
                owner_id,
                exc_info=True,
            )

    def _prompt_context(self, job_id: str) -> Any:
        return set_prompt_log_context(
            {
                "jobId": job_id,
                "endpoint": "/api/generate-pet",
            }
        )

    def _run_image_job(
        self,
        job_id: str,
        description: str,
    ) -> None:
        started_at = time.monotonic()
        self._update(job_id, status_value="running", phase="generating_images")
        self._mark_metric(job_id, "images_started_at", status="running")
        logger.info("pet_generation_stage_started jobId=%s phase=generating_images", job_id)
        prompt_log_token = self._prompt_context(job_id)
        try:
            image_set = self._generate_images(description)
        except Exception as exc:
            self._fail(job_id, exc, phase="generating_images")
            return
        finally:
            reset_prompt_log_context(prompt_log_token)

        logger.info(
            "pet_generation_stage_completed jobId=%s phase=generating_images "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            image_set.asset_set_id,
        )
        self._mark_metric(job_id, "images_ready_at", status="running")
        self._update(job_id, status_value="running", phase="generating_video")
        try:
            self._video_executor.submit(
                self._run_video_job,
                job_id,
                image_set,
            )
        except Exception as exc:
            self._fail(job_id, exc, phase="generating_video")

    def _run_video_job(
        self,
        job_id: str,
        image_set: Any,
    ) -> None:
        started_at = time.monotonic()
        logger.info(
            "pet_generation_stage_started jobId=%s phase=generating_video assetSetId=%s",
            job_id,
            image_set.asset_set_id,
        )
        prompt_log_token = self._prompt_context(job_id)
        try:
            video_path = self._generate_video(image_set)
            result = GeneratePetAssetResponse.model_validate(
                self._build_response(image_set, video_path, None, None, None, None)
            )
        except Exception as exc:
            self._fail(job_id, exc, phase="generating_video")
            return
        finally:
            reset_prompt_log_context(prompt_log_token)

        logger.info(
            "pet_generation_foreground_ready jobId=%s phase=generating_video "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            image_set.asset_set_id,
        )
        self._mark_metric(job_id, "foreground_ready_at", status="running")
        self._update(
            job_id,
            status_value="running",
            phase="generating_sad_image",
            result=result,
        )
        try:
            self._image_executor.submit(
                self._run_background_image_job,
                job_id,
                image_set,
                video_path,
            )
        except Exception as exc:
            self._record_background_failure(job_id, exc, phase="generating_sad_image")
            self._start_happy_image_job(job_id, image_set, video_path, None, None)
        self._notify_ready(job_id)

    def _run_background_image_job(self, job_id: str, image_set: Any, video_path: Any) -> None:
        started_at = time.monotonic()
        logger.info(
            "pet_generation_stage_started jobId=%s phase=generating_sad_image assetSetId=%s",
            job_id,
            image_set.asset_set_id,
        )
        prompt_log_token = self._prompt_context(job_id)
        try:
            sad_scene_path = self._generate_background_image(image_set)
        except Exception as exc:
            self._record_background_failure(job_id, exc, phase="generating_sad_image")
            self._start_happy_image_job(job_id, image_set, video_path, None, None)
            return
        finally:
            reset_prompt_log_context(prompt_log_token)

        logger.info(
            "pet_generation_stage_completed jobId=%s phase=generating_sad_image "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            image_set.asset_set_id,
        )
        self._update(job_id, status_value="running", phase="generating_sad_video")
        try:
            self._video_executor.submit(
                self._run_background_video_job,
                job_id,
                image_set,
                video_path,
                sad_scene_path,
            )
        except Exception as exc:
            self._record_background_failure(job_id, exc, phase="generating_sad_video")
            self._start_happy_image_job(job_id, image_set, video_path, None, None)

    def _run_background_video_job(
        self,
        job_id: str,
        image_set: Any,
        video_path: Any,
        sad_scene_path: Any,
    ) -> None:
        started_at = time.monotonic()
        logger.info(
            "pet_generation_stage_started jobId=%s phase=generating_sad_video assetSetId=%s",
            job_id,
            image_set.asset_set_id,
        )
        prompt_log_token = self._prompt_context(job_id)
        try:
            sad_video_path = self._generate_background_video(image_set, sad_scene_path)
        except Exception as exc:
            self._record_background_failure(job_id, exc, phase="generating_sad_video")
            self._start_happy_image_job(job_id, image_set, video_path, None, None)
            return
        finally:
            reset_prompt_log_context(prompt_log_token)

        logger.info(
            "pet_generation_stage_completed jobId=%s phase=generating_sad_video "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            image_set.asset_set_id,
        )
        self._mark_metric(job_id, "sad_ready_at", status="running")
        self._start_happy_image_job(
            job_id,
            image_set,
            video_path,
            sad_scene_path,
            sad_video_path,
        )

    def _start_happy_image_job(
        self,
        job_id: str,
        image_set: Any,
        video_path: Any,
        sad_scene_path: Any | None,
        sad_video_path: Any | None,
    ) -> None:
        result = GeneratePetAssetResponse.model_validate(
            self._build_response(
                image_set,
                video_path,
                sad_scene_path,
                sad_video_path,
                None,
                None,
            )
        )
        self._update(
            job_id,
            status_value="running",
            phase="generating_happy_image",
            result=result,
        )
        try:
            self._image_executor.submit(
                self._run_happy_image_job,
                job_id,
                image_set,
                video_path,
                sad_scene_path,
                sad_video_path,
            )
        except Exception as exc:
            self._finish_background_failure(job_id, exc, phase="generating_happy_image")

    def _run_happy_image_job(
        self,
        job_id: str,
        image_set: Any,
        video_path: Any,
        sad_scene_path: Any | None,
        sad_video_path: Any | None,
    ) -> None:
        started_at = time.monotonic()
        logger.info(
            "pet_generation_stage_started jobId=%s phase=generating_happy_image assetSetId=%s",
            job_id,
            image_set.asset_set_id,
        )
        prompt_log_token = self._prompt_context(job_id)
        try:
            happy_scene_path = self._generate_happy_image(image_set)
        except Exception as exc:
            self._finish_background_failure(job_id, exc, phase="generating_happy_image")
            return
        finally:
            reset_prompt_log_context(prompt_log_token)

        logger.info(
            "pet_generation_stage_completed jobId=%s phase=generating_happy_image "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            image_set.asset_set_id,
        )
        self._update(job_id, status_value="running", phase="generating_happy_video")
        try:
            self._video_executor.submit(
                self._run_happy_video_job,
                job_id,
                image_set,
                video_path,
                sad_scene_path,
                sad_video_path,
                happy_scene_path,
            )
        except Exception as exc:
            self._finish_background_failure(job_id, exc, phase="generating_happy_video")

    def _run_happy_video_job(
        self,
        job_id: str,
        image_set: Any,
        video_path: Any,
        sad_scene_path: Any | None,
        sad_video_path: Any | None,
        happy_scene_path: Any,
    ) -> None:
        started_at = time.monotonic()
        logger.info(
            "pet_generation_stage_started jobId=%s phase=generating_happy_video assetSetId=%s",
            job_id,
            image_set.asset_set_id,
        )
        prompt_log_token = self._prompt_context(job_id)
        try:
            happy_video_path = self._generate_happy_video(image_set, happy_scene_path)
            result = GeneratePetAssetResponse.model_validate(
                self._build_response(
                    image_set,
                    video_path,
                    sad_scene_path,
                    sad_video_path,
                    happy_scene_path,
                    happy_video_path,
                )
            )
        except Exception as exc:
            self._finish_background_failure(job_id, exc, phase="generating_happy_video")
            return
        finally:
            reset_prompt_log_context(prompt_log_token)

        logger.info(
            "pet_generation_stage_completed jobId=%s phase=generating_happy_video "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            image_set.asset_set_id,
        )
        self._update(
            job_id,
            status_value="succeeded",
            phase="completed",
            result=result,
        )
        self._mark_metric(job_id, "completed_at", status="completed")
