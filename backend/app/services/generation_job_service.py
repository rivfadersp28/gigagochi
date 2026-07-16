from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Condition, Event, Lock, RLock, Thread
from typing import Any

from app.schemas import (
    GeneratePetAssetResponse,
    GeneratePetJobResponse,
    GeneratePetStaticAssetResponse,
)
from app.services.generation_job_store import GenerationJobStore, StoredGenerationJob
from app.services.prompt_debug import reset_prompt_log_context, set_prompt_log_context
from app.services.provider_task_checkpoint import generation_provider_task_scope
from app.services.telegram_auth_service import TelegramUserContext

logger = logging.getLogger(__name__)

GenerateImages = Callable[[str, str], Any]
GenerateImagesForJob = Callable[[str, str, str, str | None], Any]
GenerateVideo = Callable[[Any], Any]
GenerateBackgroundImage = Callable[[Any, str], Any]
GenerateBackgroundVideo = Callable[[Any, Any], Any]
GenerateHappyImage = Callable[[Any, str], Any]
GenerateHappyVideo = Callable[[Any, Any], Any]
GenerateComparisonImages = Callable[[str, Any], dict[str, Any]]
BuildResponse = Callable[
    [Any, Any, Any | None, Any | None, Any | None, Any | None],
    dict[str, Any],
]
BuildFailure = Callable[[str, str, Exception, int], dict[str, object]]
NotifyReady = Callable[[int], None]
CleanupFailedJobAssets = Callable[[str], None]
_UNSET = object()
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,95}$")
DEFAULT_IDEMPOTENCY_TTL = timedelta(days=2)
DEFAULT_GENERATION_METRICS_TTL = timedelta(days=365)
OUTFIT_GENERATION_PREFIX = "__OUTFIT_V1__"


class GenerationJobNotFoundError(LookupError):
    pass


class GenerationQueueFullError(RuntimeError):
    pass


class GenerationIdempotencyConflictError(RuntimeError):
    pass


class GenerationOwnerActiveError(RuntimeError):
    def __init__(self, job_id: str, description: str) -> None:
        super().__init__("another pet generation is already active for this owner")
        self.job_id = job_id
        self.description = description.strip()[:300]


@dataclass
class GenerationJobRecord:
    owner_id: int
    username: str | None
    first_name: str | None
    description: str
    image_provider: str
    response: GeneratePetJobResponse
    primary_complete: bool = False
    comparison_complete: bool = False


class GenerationJobService:
    def __init__(
        self,
        *,
        store: GenerationJobStore,
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
        generate_images_for_job: GenerateImagesForJob | None = None,
        generate_comparison_images: GenerateComparisonImages | None = None,
        notify_ready: NotifyReady | None = None,
        notify_outfit_ready: NotifyReady | None = None,
        cleanup_failed_job_assets: CleanupFailedJobAssets | None = None,
        job_ttl: timedelta = timedelta(hours=1),
        idempotency_ttl: timedelta = DEFAULT_IDEMPOTENCY_TTL,
        metrics_ttl: timedelta = DEFAULT_GENERATION_METRICS_TTL,
        max_queued_jobs: int = 40,
        stuck_after: timedelta = timedelta(minutes=30),
    ) -> None:
        self._generate_images = generate_images
        self._generate_images_for_job = generate_images_for_job
        self._generate_video = generate_video
        self._generate_background_image = generate_background_image
        self._generate_background_video = generate_background_video
        self._generate_happy_image = generate_happy_image
        self._generate_happy_video = generate_happy_video
        self._build_response = build_response
        self._build_failure = build_failure
        self._generate_comparison_images = generate_comparison_images
        self._notify_ready_callback = notify_ready
        self._notify_outfit_ready_callback = notify_outfit_ready
        self._cleanup_failed_job_assets = cleanup_failed_job_assets
        self._job_ttl = job_ttl
        self._idempotency_ttl = max(job_ttl, idempotency_ttl)
        self._metrics_ttl = metrics_ttl
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
        self._recovery_lock = Lock()
        self._shutdown_lock = RLock()
        self._paid_stage_condition = Condition(self._shutdown_lock)
        self._running_paid_stages = 0
        self._shutting_down = False
        self._shutdown_complete = Event()
        self._shutdown_thread: Thread | None = None
        self._store = store
        self._recover_active_jobs()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def cleanup(self, now: datetime | None = None) -> None:
        effective_now = now or self._now()
        cutoff = effective_now - self._job_ttl
        request_key_cutoff = effective_now - self._idempotency_ttl
        deleted_jobs = self._store.delete_terminal_older_than(
            cutoff,
            request_key_cutoff=request_key_cutoff,
        )
        with self._lock:
            for stored in deleted_jobs:
                self._jobs.pop(stored.response.jobId, None)
        if self._cleanup_failed_job_assets is not None:
            candidates = [
                stored
                for stored in deleted_jobs
                if stored.response.status == "failed" and stored.response.result is None
            ]
            for stored in candidates:
                job_id = stored.response.jobId
                try:
                    self._cleanup_failed_job_assets(job_id)
                except Exception:
                    # Keep durable proof so a transient filesystem failure is retried
                    # instead of turning this partial asset directory into a permanent
                    # unregistered orphan.
                    self._store.save(stored)
                    logger.warning(
                        "failed_generation_asset_cleanup_failed jobId=%s",
                        job_id,
                        exc_info=True,
                    )
        self._store.delete_metrics_older_than(effective_now - self._metrics_ttl)

    def runtime_status(self) -> dict[str, int]:
        now = self._now()
        with self._lock:
            records = list(self._jobs.values())
        queued = sum(record.response.status == "queued" for record in records)
        running = sum(record.response.status == "running" for record in records)
        stuck = sum(
            record.response.status in {"queued", "running"}
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
        return self._store.metrics_summary(days=days, owner_id=owner_id)

    def submit(
        self,
        description: str,
        user: TelegramUserContext,
        image_provider: str = "openai",
        request_key: str | None = None,
    ) -> GeneratePetJobResponse:
        description = description.strip()
        if not description:
            raise ValueError("generation description must not be empty")
        normalized_provider = image_provider.strip().lower()
        if normalized_provider not in {"openai", "kandinsky"}:
            raise ValueError(f"Unsupported pet image provider: {image_provider}")
        if request_key is not None and not _IDEMPOTENCY_KEY_PATTERN.fullmatch(request_key):
            raise ValueError("invalid generation idempotency key")
        with self._shutdown_lock:
            if self._shutting_down:
                raise GenerationQueueFullError("generation service is shutting down")
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
            stored = StoredGenerationJob(
                owner_id=user.telegram_id,
                username=user.username,
                first_name=user.first_name,
                description=description,
                image_provider=normalized_provider,
                response=response,
            )
            try:
                creation = self._store.create_or_get(
                    stored,
                    request_key=request_key,
                    max_active_jobs=self._image_workers + self._max_queued_jobs,
                )
            except Exception:
                logger.exception("generation_job_create_failed jobId=%s", job_id)
                raise
            if not creation.created:
                if creation.conflict == "owner_active":
                    raise GenerationOwnerActiveError(
                        creation.job.response.jobId,
                        creation.job.description,
                    )
                if creation.conflict == "capacity":
                    raise GenerationQueueFullError("generation queue is full")
                return self._idempotent_replay(
                    creation.job,
                    description=description,
                    image_provider=normalized_provider,
                )
            with self._lock:
                self._jobs[job_id] = GenerationJobRecord(
                    owner_id=user.telegram_id,
                    username=user.username,
                    first_name=user.first_name,
                    description=description,
                    image_provider=normalized_provider,
                    response=response,
                    comparison_complete=(
                        self._generate_comparison_images is None
                        or description.startswith(OUTFIT_GENERATION_PREFIX)
                    ),
                )
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
            self._submit_image_job(job_id, description, normalized_provider)
        except Exception as exc:
            self._fail(job_id, exc, phase="generating_images")
        return response

    def get(self, job_id: str, owner_id: int) -> GeneratePetJobResponse:
        self.cleanup()
        with self._lock:
            record = self._jobs.get(job_id)
        if record is not None:
            if record.owner_id != owner_id:
                raise GenerationJobNotFoundError(job_id)
            return record.response
        stored = self._store.get(job_id)
        if stored is None or stored.owner_id != owner_id:
            raise GenerationJobNotFoundError(job_id)
        return stored.response

    def find_by_request_key(
        self,
        request_key: str,
        owner_id: int,
        description: str | None = None,
        image_provider: str | None = None,
    ) -> GeneratePetJobResponse | None:
        if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(request_key):
            raise ValueError("invalid generation idempotency key")
        self.cleanup()
        normalized_description = description.strip() if description is not None else None
        normalized_provider = image_provider.strip().lower() if image_provider is not None else None
        stored = self._store.get_by_request_key(owner_id, request_key)
        if stored is None:
            return None
        return self._idempotent_replay(
            stored,
            description=normalized_description,
            image_provider=normalized_provider,
        )

    @staticmethod
    def _stored_from_record(record: GenerationJobRecord) -> StoredGenerationJob:
        return StoredGenerationJob(
            owner_id=record.owner_id,
            username=record.username,
            first_name=record.first_name,
            description=record.description,
            image_provider=record.image_provider,
            response=record.response,
        )

    @staticmethod
    def _idempotent_replay(
        existing: StoredGenerationJob,
        *,
        description: str | None,
        image_provider: str | None,
    ) -> GeneratePetJobResponse:
        if (description is not None and existing.description != description) or (
            image_provider is not None and existing.image_provider != image_provider
        ):
            raise GenerationIdempotencyConflictError(
                "generation idempotency key was already used for another payload"
            )
        return existing.response

    def shutdown(self, *, wait: bool = False) -> None:
        finalize_here = False
        shutdown_thread: Thread | None
        with self._shutdown_lock:
            if not self._shutting_down:
                self._shutting_down = True
                if wait:
                    finalize_here = True
                else:
                    self._shutdown_thread = Thread(
                        target=self._finalize_shutdown,
                        name="pet-generation-shutdown",
                        daemon=True,
                    )
                    self._shutdown_thread.start()
            shutdown_thread = self._shutdown_thread
        if finalize_here:
            self._finalize_shutdown()
        elif wait:
            if shutdown_thread is not None:
                shutdown_thread.join()
            else:
                self._shutdown_complete.wait()

    def _finalize_shutdown(self) -> None:
        # Provider calls that crossed the fence finish; queued futures are cancelled.
        # Their durable active rows are intentionally left for the next startup.
        with self._paid_stage_condition:
            while self._running_paid_stages > 0:
                self._paid_stage_condition.wait(timeout=1.0)
        try:
            self._image_executor.shutdown(wait=True, cancel_futures=True)
            self._video_executor.shutdown(wait=True, cancel_futures=True)
        except Exception:
            logger.exception("generation_executor_shutdown_failed")
        finally:
            self._shutdown_complete.set()

    def _update(
        self,
        job_id: str,
        *,
        status_value: str,
        phase: str | None = None,
        result: GeneratePetAssetResponse | None | object = _UNSET,
        error: dict[str, object] | None | object = _UNSET,
        background_error: dict[str, object] | None | object = _UNSET,
    ) -> bool:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return False
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
            return self._persist_locked(job_id)

    def _stored_locked(self, job_id: str) -> StoredGenerationJob | None:
        record = self._jobs.get(job_id)
        if record is None:
            return None
        return self._stored_from_record(record)

    def _persist_locked(self, job_id: str) -> bool:
        stored = self._stored_locked(job_id)
        if stored is None:
            return False
        try:
            self._store.save(stored)
        except Exception:
            logger.warning(
                "generation_job_persist_retry jobId=%s",
                job_id,
                exc_info=True,
            )
            try:
                self._store.save(stored)
            except Exception:
                logger.exception("generation_job_persist_failed jobId=%s", job_id)
                self._jobs.pop(job_id, None)
                return False
        return True

    def _record_metric_queued(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            stored = StoredGenerationJob(
                owner_id=record.owner_id,
                username=record.username,
                first_name=record.first_name,
                description=record.description,
                image_provider=record.image_provider,
                response=record.response,
            )
        try:
            self._store.record_queued(stored)
        except Exception:
            logger.warning("generation_metric_queued_failed jobId=%s", job_id, exc_info=True)

    def _mark_metric(self, job_id: str, field: str, *, status: str | None = None) -> None:
        try:
            self._store.mark_metric(job_id, field, status=status)
        except Exception:
            logger.warning(
                "generation_metric_mark_failed jobId=%s field=%s",
                job_id,
                field,
                exc_info=True,
            )

    def _record_from_stored(self, stored: StoredGenerationJob) -> GenerationJobRecord:
        has_comparison = bool(stored.response.result and stored.response.result.kandinskyAssets)
        return GenerationJobRecord(
            owner_id=stored.owner_id,
            username=stored.username,
            first_name=stored.first_name,
            description=stored.description,
            image_provider=stored.image_provider,
            response=stored.response,
            primary_complete=stored.response.status == "succeeded",
            comparison_complete=(
                self._generate_comparison_images is None
                or has_comparison
                or stored.description.startswith(OUTFIT_GENERATION_PREFIX)
            ),
        )

    def _submit_image_job(
        self,
        job_id: str,
        description: str,
        image_provider: str,
    ) -> bool:
        return self._submit_stage(
            self._image_executor,
            self._run_image_job,
            job_id,
            description,
            image_provider,
        )

    def _submit_stage(
        self,
        executor: ThreadPoolExecutor,
        callback: Callable[..., None],
        job_id: str,
        *args: Any,
    ) -> bool:
        with self._shutdown_lock:
            if not self._shutting_down:
                executor.submit(callback, job_id, *args)
                return True
        self._abandon_for_shutdown(job_id)
        return False

    def _abandon_for_shutdown(self, job_id: str) -> bool:
        with self._shutdown_lock:
            if not self._shutting_down:
                return False
        with self._lock:
            self._jobs.pop(job_id, None)
        return True

    def _recover_active_jobs(self) -> None:
        with self._shutdown_lock, self._recovery_lock:
            if self._shutting_down:
                return
            recovered: list[StoredGenerationJob] = []
            for stored in self._store.active():
                response = stored.response.model_copy(
                    update={
                        "status": "queued",
                        "phase": "queued",
                        "updatedAt": self._now(),
                        "error": None,
                        "backgroundError": None,
                        "comparisonError": None,
                    }
                )
                recovered_job = StoredGenerationJob(
                    owner_id=stored.owner_id,
                    username=stored.username,
                    first_name=stored.first_name,
                    description=stored.description,
                    image_provider=stored.image_provider,
                    response=response,
                )
                try:
                    self._store.save(recovered_job)
                except Exception:
                    logger.exception(
                        "generation_job_recovery_persist_failed jobId=%s",
                        response.jobId,
                    )
                    continue
                with self._lock:
                    self._jobs[response.jobId] = self._record_from_stored(recovered_job)
                recovered.append(recovered_job)
            for stored in recovered:
                job_id = stored.response.jobId
                self._record_metric_queued(job_id)
                try:
                    self._submit_image_job(
                        job_id,
                        stored.description,
                        stored.image_provider,
                    )
                except Exception as exc:
                    self._fail(job_id, exc, phase="generating_images")
                    continue
                logger.warning(
                    "pet_generation_recovered jobId=%s ownerId=%s",
                    job_id,
                    stored.owner_id,
                )

    def _job_is_active(self, job_id: str) -> bool:
        with self._lock:
            record = self._jobs.get(job_id)
            return record is not None and record.response.status in {"queued", "running"}

    @contextmanager
    def _paid_stage(self, job_id: str, phase: str) -> Iterator[bool]:
        with self._paid_stage_condition:
            with self._lock:
                record = self._jobs.get(job_id)
                active = record is not None and record.response.status in {"queued", "running"}
            stage_allowed = active and not self._shutting_down
            if stage_allowed:
                self._running_paid_stages += 1
        if not stage_allowed:
            yield False
            return
        try:
            with generation_provider_task_scope(job_id=job_id, stage=phase):
                yield True
        finally:
            with self._paid_stage_condition:
                self._running_paid_stages -= 1
                self._paid_stage_condition.notify_all()

    def _fail(self, job_id: str, exc: Exception, *, phase: str) -> None:
        detail = self._build_failure(job_id, phase, exc, self._owner_id(job_id))
        if self._update(job_id, status_value="failed", phase=phase, error=detail):
            self._mark_metric(job_id, "failed_at", status="failed")

    def _finish_background_failure(self, job_id: str, exc: Exception, *, phase: str) -> None:
        detail = self._build_failure(job_id, phase, exc, self._owner_id(job_id))
        logger.warning(
            "pet_background_generation_failed jobId=%s phase=%s errorType=%s",
            job_id,
            phase,
            type(exc).__name__,
        )
        if not self._update(
            job_id,
            status_value="running",
            background_error=detail,
        ):
            return
        self._finish_primary_pipeline(job_id, completed_with_errors=True)

    def _record_background_failure(self, job_id: str, exc: Exception, *, phase: str) -> bool:
        detail = self._build_failure(job_id, phase, exc, self._owner_id(job_id))
        logger.warning(
            "pet_background_generation_failed jobId=%s phase=%s errorType=%s",
            job_id,
            phase,
            type(exc).__name__,
        )
        return self._update(
            job_id,
            status_value="running",
            background_error=detail,
        )

    def _owner_id(self, job_id: str) -> int:
        with self._lock:
            record = self._jobs.get(job_id)
            return record.owner_id if record is not None else 0

    def _image_provider(self, job_id: str) -> str:
        with self._lock:
            record = self._jobs.get(job_id)
            return record.image_provider if record is not None else "openai"

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

    def _notify_outfit_ready(self, job_id: str) -> None:
        if self._notify_outfit_ready_callback is None:
            return
        owner_id = self._owner_id(job_id)
        try:
            self._notify_outfit_ready_callback(owner_id)
        except Exception:
            logger.warning(
                "outfit_generation_notification_failed jobId=%s ownerId=%s",
                job_id,
                owner_id,
                exc_info=True,
            )

    def _prompt_context(self, job_id: str) -> Any:
        return set_prompt_log_context(
            {
                "jobId": job_id,
                "endpoint": "/api/generate-pet",
                "imageProvider": self._image_provider(job_id),
            }
        )

    def _description(self, job_id: str) -> str:
        with self._lock:
            record = self._jobs.get(job_id)
            return record.description if record is not None else ""

    def _existing_asset_set_id(self, job_id: str) -> str | None:
        with self._lock:
            record = self._jobs.get(job_id)
            result = record.response.result if record is not None else None
            return result.assetSetId if result is not None else None

    def _merge_comparison_assets(
        self,
        job_id: str,
        result: GeneratePetAssetResponse,
    ) -> GeneratePetAssetResponse:
        with self._lock:
            current = self._jobs.get(job_id)
            comparison = (
                current.response.result.kandinskyAssets
                if current is not None and current.response.result is not None
                else None
            )
        return result.model_copy(update={"kandinskyAssets": comparison})

    def _start_comparison_job(self, job_id: str, primary_image_set: Any) -> None:
        if self._generate_comparison_images is None:
            return
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.comparison_complete:
                return
        try:
            self._submit_stage(
                self._image_executor,
                self._run_comparison_job,
                job_id,
                self._description(job_id),
                primary_image_set,
            )
        except Exception as exc:
            self._finish_comparison_pipeline(job_id, error=exc)

    def _run_comparison_job(
        self,
        job_id: str,
        description: str,
        primary_image_set: Any,
    ) -> None:
        if not self._job_is_active(job_id):
            return
        started_at = time.monotonic()
        logger.info(
            "pet_generation_stage_started jobId=%s phase=generating_kandinsky",
            job_id,
        )
        prompt_log_token = set_prompt_log_context(
            {
                "jobId": job_id,
                "endpoint": "/api/generate-pet",
                "imageProvider": "kandinsky",
            }
        )
        try:
            with self._paid_stage(job_id, "generating_kandinsky") as stage_allowed:
                if not stage_allowed:
                    return
                payload = self._generate_comparison_images(description, primary_image_set)
            comparison = GeneratePetStaticAssetResponse.model_validate(payload)
        except Exception as exc:
            if self._abandon_for_shutdown(job_id):
                return
            self._finish_comparison_pipeline(job_id, error=exc)
            return
        finally:
            reset_prompt_log_context(prompt_log_token)
        if self._abandon_for_shutdown(job_id):
            return
        logger.info(
            "pet_generation_stage_completed jobId=%s phase=generating_kandinsky "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            comparison.assetSetId,
        )
        self._finish_comparison_pipeline(job_id, comparison=comparison)

    def _finish_primary_pipeline(
        self,
        job_id: str,
        *,
        result: GeneratePetAssetResponse | None = None,
        completed_with_errors: bool = False,
    ) -> bool:
        notify_outfit_ready = False
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return False
            was_completed = record.response.phase == "completed"
            record.primary_complete = True
            comparison_complete = record.comparison_complete
            comparison = (
                record.response.result.kandinskyAssets
                if record.response.result is not None
                else None
            )
            next_result = result or record.response.result
            if next_result is not None:
                next_result = next_result.model_copy(update={"kandinskyAssets": comparison})
            has_errors = completed_with_errors or record.response.comparisonError is not None
            record.response = record.response.model_copy(
                update={
                    "status": "succeeded" if comparison_complete else "running",
                    "phase": "completed" if comparison_complete else "generating_kandinsky",
                    "updatedAt": self._now(),
                    "result": next_result,
                }
            )
            persisted = self._persist_locked(job_id)
            if persisted and comparison_complete:
                self._mark_metric(
                    job_id,
                    "completed_at",
                    status="completed_with_errors" if has_errors else "completed",
                )
                notify_outfit_ready = (
                    not was_completed
                    and record.response.backgroundError is None
                    and record.description.startswith(OUTFIT_GENERATION_PREFIX)
                )
        if notify_outfit_ready:
            self._notify_outfit_ready(job_id)
        return persisted

    def _finish_comparison_pipeline(
        self,
        job_id: str,
        *,
        comparison: GeneratePetStaticAssetResponse | None = None,
        error: Exception | None = None,
    ) -> bool:
        detail = None
        if error is not None:
            detail = self._build_failure(
                job_id,
                "generating_kandinsky",
                error,
                self._owner_id(job_id),
            )
            logger.warning(
                "pet_comparison_generation_failed jobId=%s errorType=%s",
                job_id,
                type(error).__name__,
            )
        notify_outfit_ready = False
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return False
            was_completed = record.response.phase == "completed"
            record.comparison_complete = True
            primary_complete = record.primary_complete
            current_result = record.response.result
            has_primary_error = record.response.backgroundError is not None
            merged_result = (
                current_result.model_copy(update={"kandinskyAssets": comparison})
                if current_result is not None and comparison is not None
                else current_result
            )
            updates: dict[str, object] = {
                "status": "succeeded" if primary_complete else "running",
                "updatedAt": self._now(),
                "result": merged_result,
                "comparisonError": detail,
            }
            if primary_complete:
                updates["phase"] = "completed"
            record.response = record.response.model_copy(update=updates)
            persisted = self._persist_locked(job_id)
            if persisted and primary_complete:
                self._mark_metric(
                    job_id,
                    "completed_at",
                    status=(
                        "completed_with_errors"
                        if has_primary_error or detail is not None
                        else "completed"
                    ),
                )
                notify_outfit_ready = (
                    not was_completed
                    and not has_primary_error
                    and record.description.startswith(OUTFIT_GENERATION_PREFIX)
                )
        if notify_outfit_ready:
            self._notify_outfit_ready(job_id)
        return persisted

    def _run_image_job(
        self,
        job_id: str,
        description: str,
        image_provider: str,
    ) -> None:
        if not self._job_is_active(job_id):
            return
        started_at = time.monotonic()
        if not self._update(job_id, status_value="running", phase="generating_images"):
            return
        self._mark_metric(job_id, "images_started_at", status="running")
        logger.info("pet_generation_stage_started jobId=%s phase=generating_images", job_id)
        prompt_log_token = self._prompt_context(job_id)
        try:
            with self._paid_stage(job_id, "generating_images") as stage_allowed:
                if not stage_allowed:
                    return
                image_set = (
                    self._generate_images_for_job(
                        job_id,
                        description,
                        image_provider,
                        self._existing_asset_set_id(job_id),
                    )
                    if self._generate_images_for_job is not None
                    else self._generate_images(description, image_provider)
                )
        except Exception as exc:
            if self._abandon_for_shutdown(job_id):
                return
            self._fail(job_id, exc, phase="generating_images")
            return
        finally:
            reset_prompt_log_context(prompt_log_token)
        if self._abandon_for_shutdown(job_id):
            return

        logger.info(
            "pet_generation_stage_completed jobId=%s phase=generating_images "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            image_set.asset_set_id,
        )
        if not self._update(job_id, status_value="running", phase="generating_video"):
            return
        self._mark_metric(job_id, "images_ready_at", status="running")
        try:
            self._submit_stage(
                self._video_executor,
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
        if not self._job_is_active(job_id):
            return
        started_at = time.monotonic()
        logger.info(
            "pet_generation_stage_started jobId=%s phase=generating_video assetSetId=%s",
            job_id,
            image_set.asset_set_id,
        )
        prompt_log_token = self._prompt_context(job_id)
        try:
            with self._paid_stage(job_id, "generating_video") as stage_allowed:
                if not stage_allowed:
                    return
                video_path = self._generate_video(image_set)
            result = self._merge_comparison_assets(
                job_id,
                GeneratePetAssetResponse.model_validate(
                    self._build_response(image_set, video_path, None, None, None, None)
                ),
            )
        except Exception as exc:
            if self._abandon_for_shutdown(job_id):
                return
            self._fail(job_id, exc, phase="generating_video")
            return
        finally:
            reset_prompt_log_context(prompt_log_token)
        if self._abandon_for_shutdown(job_id):
            return

        logger.info(
            "pet_generation_foreground_ready jobId=%s phase=generating_video "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            image_set.asset_set_id,
        )
        if not self._update(
            job_id,
            status_value="running",
            phase="generating_sad_image",
            result=result,
        ):
            return
        self._mark_metric(job_id, "foreground_ready_at", status="running")
        self._start_comparison_job(job_id, image_set)
        try:
            self._submit_stage(
                self._image_executor,
                self._run_background_image_job,
                job_id,
                image_set,
                video_path,
            )
        except Exception as exc:
            if self._record_background_failure(job_id, exc, phase="generating_sad_image"):
                self._start_happy_image_job(job_id, image_set, video_path, None, None)
        if self._job_is_active(job_id) and not self._description(job_id).startswith(
            OUTFIT_GENERATION_PREFIX
        ):
            self._notify_ready(job_id)

    def _run_background_image_job(self, job_id: str, image_set: Any, video_path: Any) -> None:
        if not self._job_is_active(job_id):
            return
        started_at = time.monotonic()
        logger.info(
            "pet_generation_stage_started jobId=%s phase=generating_sad_image assetSetId=%s",
            job_id,
            image_set.asset_set_id,
        )
        prompt_log_token = self._prompt_context(job_id)
        try:
            with self._paid_stage(job_id, "generating_sad_image") as stage_allowed:
                if not stage_allowed:
                    return
                sad_scene_path = self._generate_background_image(
                    image_set,
                    self._image_provider(job_id),
                )
        except Exception as exc:
            if self._abandon_for_shutdown(job_id):
                return
            if self._record_background_failure(job_id, exc, phase="generating_sad_image"):
                self._start_happy_image_job(job_id, image_set, video_path, None, None)
            return
        finally:
            reset_prompt_log_context(prompt_log_token)
        if self._abandon_for_shutdown(job_id):
            return

        logger.info(
            "pet_generation_stage_completed jobId=%s phase=generating_sad_image "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            image_set.asset_set_id,
        )
        if not self._update(job_id, status_value="running", phase="generating_sad_video"):
            return
        try:
            self._submit_stage(
                self._video_executor,
                self._run_background_video_job,
                job_id,
                image_set,
                video_path,
                sad_scene_path,
            )
        except Exception as exc:
            if self._record_background_failure(job_id, exc, phase="generating_sad_video"):
                self._start_happy_image_job(job_id, image_set, video_path, None, None)

    def _run_background_video_job(
        self,
        job_id: str,
        image_set: Any,
        video_path: Any,
        sad_scene_path: Any,
    ) -> None:
        if not self._job_is_active(job_id):
            return
        started_at = time.monotonic()
        logger.info(
            "pet_generation_stage_started jobId=%s phase=generating_sad_video assetSetId=%s",
            job_id,
            image_set.asset_set_id,
        )
        prompt_log_token = self._prompt_context(job_id)
        try:
            with self._paid_stage(job_id, "generating_sad_video") as stage_allowed:
                if not stage_allowed:
                    return
                sad_video_path = self._generate_background_video(image_set, sad_scene_path)
        except Exception as exc:
            if self._abandon_for_shutdown(job_id):
                return
            if self._record_background_failure(job_id, exc, phase="generating_sad_video"):
                self._start_happy_image_job(job_id, image_set, video_path, None, None)
            return
        finally:
            reset_prompt_log_context(prompt_log_token)
        if self._abandon_for_shutdown(job_id):
            return

        logger.info(
            "pet_generation_stage_completed jobId=%s phase=generating_sad_video "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            image_set.asset_set_id,
        )
        if self._job_is_active(job_id):
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
        result = self._merge_comparison_assets(
            job_id,
            GeneratePetAssetResponse.model_validate(
                self._build_response(
                    image_set,
                    video_path,
                    sad_scene_path,
                    sad_video_path,
                    None,
                    None,
                )
            ),
        )
        if not self._update(
            job_id,
            status_value="running",
            phase="generating_happy_image",
            result=result,
        ):
            return
        try:
            self._submit_stage(
                self._image_executor,
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
        if not self._job_is_active(job_id):
            return
        started_at = time.monotonic()
        logger.info(
            "pet_generation_stage_started jobId=%s phase=generating_happy_image assetSetId=%s",
            job_id,
            image_set.asset_set_id,
        )
        prompt_log_token = self._prompt_context(job_id)
        try:
            with self._paid_stage(job_id, "generating_happy_image") as stage_allowed:
                if not stage_allowed:
                    return
                happy_scene_path = self._generate_happy_image(
                    image_set,
                    self._image_provider(job_id),
                )
        except Exception as exc:
            if self._abandon_for_shutdown(job_id):
                return
            self._finish_background_failure(job_id, exc, phase="generating_happy_image")
            return
        finally:
            reset_prompt_log_context(prompt_log_token)
        if self._abandon_for_shutdown(job_id):
            return

        logger.info(
            "pet_generation_stage_completed jobId=%s phase=generating_happy_image "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            image_set.asset_set_id,
        )
        if not self._update(job_id, status_value="running", phase="generating_happy_video"):
            return
        try:
            self._submit_stage(
                self._video_executor,
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
        if not self._job_is_active(job_id):
            return
        started_at = time.monotonic()
        logger.info(
            "pet_generation_stage_started jobId=%s phase=generating_happy_video assetSetId=%s",
            job_id,
            image_set.asset_set_id,
        )
        prompt_log_token = self._prompt_context(job_id)
        try:
            with self._paid_stage(job_id, "generating_happy_video") as stage_allowed:
                if not stage_allowed:
                    return
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
            if self._abandon_for_shutdown(job_id):
                return
            self._finish_background_failure(job_id, exc, phase="generating_happy_video")
            return
        finally:
            reset_prompt_log_context(prompt_log_token)
        if self._abandon_for_shutdown(job_id):
            return

        logger.info(
            "pet_generation_stage_completed jobId=%s phase=generating_happy_video "
            "durationSeconds=%.3f assetSetId=%s",
            job_id,
            time.monotonic() - started_at,
            image_set.asset_set_id,
        )
        self._finish_primary_pipeline(job_id, result=result)
