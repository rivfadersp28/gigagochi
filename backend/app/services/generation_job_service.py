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
BuildFailure = Callable[[str, str, Exception], dict[str, object]]
_UNSET = object()


class GenerationJobNotFoundError(LookupError):
    pass


@dataclass
class GenerationJobRecord:
    owner_id: int
    username: str | None
    first_name: str | None
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
        derived_asset_owner_ids: set[int] | None = None,
        job_ttl: timedelta = timedelta(hours=1),
    ) -> None:
        self._generate_images = generate_images
        self._generate_video = generate_video
        self._generate_background_image = generate_background_image
        self._generate_background_video = generate_background_video
        self._generate_happy_image = generate_happy_image
        self._generate_happy_video = generate_happy_video
        self._build_response = build_response
        self._build_failure = build_failure
        self._derived_asset_owner_ids = derived_asset_owner_ids
        self._job_ttl = job_ttl
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
            self._jobs[job_id] = GenerationJobRecord(
                owner_id=user.telegram_id,
                username=user.username,
                first_name=user.first_name,
                response=response,
            )
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
            self._image_executor.submit(
                self._run_image_job,
                job_id,
                description,
                (
                    self._derived_asset_owner_ids is None
                    or user.telegram_id in self._derived_asset_owner_ids
                ),
            )
        except Exception as exc:
            self._fail(job_id, exc, phase="generating_images")
        return response

    def get(self, job_id: str, owner_id: int) -> GeneratePetJobResponse:
        self.cleanup()
        with self._lock:
            record = self._jobs.get(job_id)
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

    def _fail(self, job_id: str, exc: Exception, *, phase: str) -> None:
        detail = self._build_failure(job_id, phase, exc)
        self._update(job_id, status_value="failed", phase=phase, error=detail)

    def _finish_background_failure(self, job_id: str, exc: Exception, *, phase: str) -> None:
        detail = self._build_failure(job_id, phase, exc)
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

    def _record_background_failure(self, job_id: str, exc: Exception, *, phase: str) -> None:
        detail = self._build_failure(job_id, phase, exc)
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
        generate_derived_assets: bool,
    ) -> None:
        started_at = time.monotonic()
        self._update(job_id, status_value="running", phase="generating_images")
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
        self._update(job_id, status_value="running", phase="generating_video")
        try:
            self._video_executor.submit(
                self._run_video_job,
                job_id,
                image_set,
                generate_derived_assets,
            )
        except Exception as exc:
            self._fail(job_id, exc, phase="generating_video")

    def _run_video_job(
        self,
        job_id: str,
        image_set: Any,
        generate_derived_assets: bool,
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
        if not generate_derived_assets:
            self._update(
                job_id,
                status_value="succeeded",
                phase="completed",
                result=result,
            )
            return
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
