from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.config import get_settings
from app.schemas import GeneratePetJobResponse
from app.services.generation_job_service import GenerationJobService
from app.services.generation_job_store import (
    GenerationJobStore,
    StoredGenerationJob,
)
from app.services.image_service import (
    OpenRouterVideoHTTPError,
    generate_kandinsky_image_bytes,
    generate_openrouter_video_bytes,
)
from app.services.provider_task_checkpoint import (
    find_current_provider_task,
    generation_provider_task_scope,
    implicit_provider_task_scope,
    mark_current_provider_task_failed,
    mark_current_provider_task_media_saved,
    provider_task_payload_fingerprint,
    save_current_provider_task,
)
from app.services.provider_task_receipt_store import (
    ProviderTaskReceiptAmbiguousError,
    ProviderTaskReceiptStore,
)
from app.services.telegram_auth_service import TelegramUserContext

TEST_ACCOUNT_NAMESPACE = "configured:test-account"


def _save_job(
    store: GenerationJobStore,
    *,
    job_id: str,
    owner_id: int = 42,
    status: str = "running",
    updated_at: datetime | None = None,
) -> None:
    now = updated_at or datetime.now(UTC)
    store.save(
        StoredGenerationJob(
            owner_id=owner_id,
            username="serge",
            first_name="Serge",
            description="мышонок",
            response=GeneratePetJobResponse(
                jobId=job_id,
                status=status,
                phase="generating_video",
                createdAt=now,
                updatedAt=now,
                error={"code": "SYNTHETIC"} if status == "failed" else None,
            ),
        )
    )


def _openrouter_settings() -> SimpleNamespace:
    return SimpleNamespace(
        openrouter_api_key="sk-or-test",
        openrouter_account_namespace="test-account",
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_video_model="x-ai/grok-imagine-video",
        openrouter_video_timeout_seconds=10,
        openrouter_video_poll_interval_seconds=1,
        openrouter_site_url=None,
        openrouter_app_title="Test Tamagotchi",
        backend_public_url=None,
        webapp_url=None,
    )


def _kandinsky_settings() -> SimpleNamespace:
    return SimpleNamespace(
        kandinsky_api_key="kandinsky-test",
        kandinsky_account_namespace="test-account",
        kandinsky_base_url="https://studio.kandinskylab.ai/api",
        kandinsky_t2i_task_type="k6-image-t2i",
        kandinsky_i2i_task_type="k6-i2i",
        kandinsky_image_resolution="1280x768",
        kandinsky_pet_image_resolution="768x1280",
        kandinsky_poll_interval_seconds=1,
        openai_image_timeout_seconds=10,
    )


def _provider_receipt_path() -> Path:
    return Path(get_settings().provider_task_receipt_store_path)


def _stored_receipt(scope_key: str, operation: str):
    path = _provider_receipt_path()
    with sqlite3.connect(path) as connection:
        identity = connection.execute(
            """
            SELECT provider, provider_origin, account_namespace, payload_fingerprint
            FROM provider_tasks
            WHERE scope_key = ? AND operation = ?
            """,
            (scope_key, operation),
        ).fetchone()
    assert identity is not None
    return ProviderTaskReceiptStore(path).get(
        scope_key=scope_key,
        provider=str(identity[0]),
        provider_origin=str(identity[1]),
        account_namespace=str(identity[2]),
        operation=operation,
        payload_fingerprint=str(identity[3]),
    )


def test_openrouter_video_resumes_durable_task_after_poll_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    submit_calls = 0
    poll_calls: list[tuple[str, str | None]] = []
    crash_poll = True

    class SubmitResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "id": "openrouter-task-1",
                "status": "queued",
                "polling_url": "/api/v1/videos/openrouter-task-1",
            }

    def submit(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        return SubmitResponse()

    def poll(_settings, job_id: str, *, polling_url: str | None = None):
        poll_calls.append((job_id, polling_url))
        receipt = _stored_receipt(
            "job:openrouter-job:generating_video",
            "video:pet_creation/scene_video",
        )
        assert receipt is not None
        if crash_poll:
            raise RuntimeError("synthetic crash after durable OpenRouter submit")
        return {"status": "completed"}

    monkeypatch.setattr("app.services.image_service.get_settings", _openrouter_settings)
    monkeypatch.setattr("app.services.image_service._submit_openrouter_video_job", submit)
    monkeypatch.setattr("app.services.image_service._poll_openrouter_video_job", poll)
    monkeypatch.setattr(
        "app.services.image_service._download_openrouter_video_bytes",
        lambda _settings, task_id: b"video:" + task_id.encode(),
    )

    with generation_provider_task_scope(
        job_id="openrouter-job",
        stage="generating_video",
    ):
        with pytest.raises(RuntimeError, match="synthetic crash"):
            generate_openrouter_video_bytes(
                None,
                source_bytes=b"same source image",
                label="pet_creation/scene_video",
            )

    crash_poll = False
    with generation_provider_task_scope(
        job_id="openrouter-job",
        stage="generating_video",
    ):
        result = generate_openrouter_video_bytes(
            None,
            source_bytes=b"same source image",
            label="pet_creation/scene_video",
        )
        mark_current_provider_task_media_saved("video:pet_creation/scene_video")

    receipt = _stored_receipt(
        "job:openrouter-job:generating_video",
        "video:pet_creation/scene_video",
    )
    assert result == b"video:openrouter-task-1"
    assert submit_calls == 1
    assert poll_calls == [
        (
            "openrouter-task-1",
            "https://openrouter.ai/api/v1/videos/openrouter-task-1",
        ),
        (
            "openrouter-task-1",
            "https://openrouter.ai/api/v1/videos/openrouter-task-1",
        ),
    ]
    assert receipt is not None
    assert receipt.state == "media_saved"
    assert receipt.provider == "openrouter"
    assert receipt.provider_origin == "https://openrouter.ai/api/v1/videos"
    assert len(receipt.payload_fingerprint) == 64

    # Models a deleted or zero-byte local file after the receipt was marked saved.
    # The remote task is polled/downloaded again, but a second paid POST is not sent.
    with generation_provider_task_scope(
        job_id="openrouter-job",
        stage="generating_video",
    ):
        restored_missing_file = generate_openrouter_video_bytes(
            None,
            source_bytes=b"same source image",
            label="pet_creation/scene_video",
        )
    assert restored_missing_file == result
    assert submit_calls == 1
    assert len(poll_calls) == 3


def test_kandinsky_comparison_resumes_durable_task_after_poll_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    create_calls = 0
    wait_calls: list[str] = []
    crash_poll = True

    def create_task(*_args, **_kwargs) -> str:
        nonlocal create_calls
        create_calls += 1
        return "kandinsky-task-1"

    def wait_done(_settings, *, task_id: str, **_kwargs):
        wait_calls.append(task_id)
        receipt = _stored_receipt(
            "job:comparison-job:generating_kandinsky",
            "image:pet_creation/image",
        )
        assert receipt is not None
        if crash_poll:
            raise RuntimeError("synthetic crash after durable Kandinsky submit")
        return {"status": "done"}

    monkeypatch.setattr("app.services.image_service.get_settings", _kandinsky_settings)
    monkeypatch.setattr("app.services.image_service._kandinsky_create_task", create_task)
    monkeypatch.setattr("app.services.image_service._kandinsky_wait_done", wait_done)
    monkeypatch.setattr(
        "app.services.image_service._kandinsky_download_result",
        lambda *_args, **_kwargs: b"synthetic-kandinsky-image",
    )

    with generation_provider_task_scope(
        job_id="comparison-job",
        stage="generating_kandinsky",
    ):
        with pytest.raises(RuntimeError, match="synthetic crash"):
            generate_kandinsky_image_bytes(
                "same comparison prompt",
                label="pet_creation/image",
            )

    crash_poll = False
    with generation_provider_task_scope(
        job_id="comparison-job",
        stage="generating_kandinsky",
    ):
        result = generate_kandinsky_image_bytes(
            "same comparison prompt",
            label="pet_creation/image",
        )
        mark_current_provider_task_media_saved("image:pet_creation/image")

    receipt = _stored_receipt(
        "job:comparison-job:generating_kandinsky",
        "image:pet_creation/image",
    )
    assert result == b"synthetic-kandinsky-image"
    assert create_calls == 1
    assert wait_calls == ["kandinsky-task-1", "kandinsky-task-1"]
    assert receipt is not None
    assert receipt.state == "media_saved"
    assert receipt.provider == "kandinsky"
    assert receipt.polling_url == ("https://studio.kandinskylab.ai/api/tasks/kandinsky-task-1")


def test_openrouter_video_without_generation_scope_resumes_after_poll_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "provider-receipts.sqlite3"
    monkeypatch.setenv("PROVIDER_TASK_RECEIPT_STORE_PATH", str(receipt_path))
    submit_calls = 0
    poll_calls: list[str] = []
    crash_poll = True

    class SubmitResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "id": "implicit-openrouter-task",
                "status": "queued",
                "polling_url": "/api/v1/videos/implicit-openrouter-task",
            }

    def submit(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        return SubmitResponse()

    def poll(_settings, job_id: str, *, polling_url: str | None = None):
        poll_calls.append(job_id)
        with sqlite3.connect(receipt_path) as connection:
            assert connection.execute("SELECT task_id FROM provider_tasks").fetchone() == (
                "implicit-openrouter-task",
            )
        assert polling_url == ("https://openrouter.ai/api/v1/videos/implicit-openrouter-task")
        if crash_poll:
            raise RuntimeError("synthetic crash after implicit OpenRouter receipt")
        return {"status": "completed"}

    monkeypatch.setattr("app.services.image_service.get_settings", _openrouter_settings)
    monkeypatch.setattr("app.services.image_service._submit_openrouter_video_job", submit)
    monkeypatch.setattr("app.services.image_service._poll_openrouter_video_job", poll)
    monkeypatch.setattr(
        "app.services.image_service._download_openrouter_video_bytes",
        lambda _settings, task_id: b"video:" + task_id.encode(),
    )

    with pytest.raises(RuntimeError, match="synthetic crash"):
        generate_openrouter_video_bytes(
            None,
            source_bytes=b"implicit source",
            label="story/video",
            prompt="same implicit prompt",
        )

    crash_poll = False
    result = generate_openrouter_video_bytes(
        None,
        source_bytes=b"implicit source",
        label="story/video",
        prompt="same implicit prompt",
    )

    assert result == b"video:implicit-openrouter-task"
    assert submit_calls == 1
    assert poll_calls == ["implicit-openrouter-task", "implicit-openrouter-task"]


def test_kandinsky_without_generation_scope_resumes_after_poll_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "provider-receipts.sqlite3"
    monkeypatch.setenv("PROVIDER_TASK_RECEIPT_STORE_PATH", str(receipt_path))
    create_calls = 0
    wait_calls: list[str] = []
    crash_poll = True

    def create_task(*_args, **_kwargs) -> str:
        nonlocal create_calls
        create_calls += 1
        return "implicit-kandinsky-task"

    def wait_done(_settings, *, task_id: str, **_kwargs):
        wait_calls.append(task_id)
        with sqlite3.connect(receipt_path) as connection:
            assert connection.execute("SELECT task_id FROM provider_tasks").fetchone() == (
                "implicit-kandinsky-task",
            )
        if crash_poll:
            raise RuntimeError("synthetic crash after implicit Kandinsky receipt")
        return {"status": "done"}

    monkeypatch.setattr("app.services.image_service.get_settings", _kandinsky_settings)
    monkeypatch.setattr("app.services.image_service._kandinsky_create_task", create_task)
    monkeypatch.setattr("app.services.image_service._kandinsky_wait_done", wait_done)
    monkeypatch.setattr(
        "app.services.image_service._kandinsky_download_result",
        lambda *_args, **_kwargs: b"implicit-kandinsky-image",
    )

    with pytest.raises(RuntimeError, match="synthetic crash"):
        generate_kandinsky_image_bytes("same implicit prompt", label="story/image")

    crash_poll = False
    result = generate_kandinsky_image_bytes("same implicit prompt", label="story/image")

    assert result == b"implicit-kandinsky-image"
    assert create_calls == 1
    assert wait_calls == ["implicit-kandinsky-task", "implicit-kandinsky-task"]
    receipt = ProviderTaskReceiptStore(receipt_path).get(
        scope_key="global",
        operation="image:story/image",
        provider="kandinsky",
        provider_origin="https://studio.kandinskylab.ai/api",
        account_namespace=TEST_ACCOUNT_NAMESPACE,
        payload_fingerprint=provider_task_payload_fingerprint(
            {
                "task_type": "k6-image-t2i",
                "params": {
                    "query": "same implicit prompt",
                    "resolution": "1280x768",
                },
            }
        ),
    )
    assert receipt is not None
    assert receipt.state == "accepted"


def test_unavailable_implicit_receipt_store_blocks_paid_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submit_calls = 0

    class UnavailableStore:
        def __init__(self, *_args, **_kwargs) -> None:
            raise OSError("synthetic receipt volume unavailable")

    def submit(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        raise AssertionError("paid submit must not run without a receipt store")

    monkeypatch.setattr("app.services.image_service.get_settings", _openrouter_settings)
    monkeypatch.setattr(
        "app.services.provider_task_checkpoint.ProviderTaskReceiptStore",
        UnavailableStore,
    )
    monkeypatch.setattr("app.services.image_service._submit_openrouter_video_job", submit)

    with pytest.raises(OSError, match="receipt volume unavailable"):
        generate_openrouter_video_bytes(
            None,
            source_bytes=b"synthetic source",
            label="story/video",
        )
    assert submit_calls == 0


def test_ambiguous_submit_without_task_id_blocks_every_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "provider-receipts.sqlite3"
    settings = _openrouter_settings()
    settings.provider_task_receipt_store_path = str(receipt_path)
    submit_calls = 0

    class MissingIdResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "queued"}

    def submit(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        return MissingIdResponse()

    monkeypatch.setattr("app.services.image_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.image_service._submit_openrouter_video_job", submit)

    with pytest.raises(RuntimeError, match="missing job id"):
        generate_openrouter_video_bytes(None, source_bytes=b"ambiguous", label="story/video")
    with pytest.raises(ProviderTaskReceiptAmbiguousError, match="no durable remote"):
        generate_openrouter_video_bytes(None, source_bytes=b"ambiguous", label="story/video")

    assert submit_calls == 1
    with sqlite3.connect(receipt_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM provider_tasks WHERE state = 'admitted' AND task_id IS NULL"
        ).fetchone() == (1,)


def test_definite_http_rejection_releases_admission_before_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "provider-receipts.sqlite3"
    settings = _openrouter_settings()
    settings.provider_task_receipt_store_path = str(receipt_path)
    submit_calls = 0

    def submit(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        if submit_calls == 1:
            return httpx.Response(
                400,
                request=httpx.Request("POST", "https://openrouter.ai/api/v1/videos"),
                json={"error": "definite rejection"},
            )
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/videos"),
            json={"id": "accepted-after-rejection", "status": "queued"},
        )

    monkeypatch.setattr("app.services.image_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.image_service._submit_openrouter_video_job", submit)
    monkeypatch.setattr(
        "app.services.image_service._poll_openrouter_video_job",
        lambda *_args, **_kwargs: {"status": "completed"},
    )
    monkeypatch.setattr(
        "app.services.image_service._download_openrouter_video_bytes",
        lambda *_args, **_kwargs: b"accepted",
    )

    with pytest.raises(OpenRouterVideoHTTPError) as rejected:
        generate_openrouter_video_bytes(None, source_bytes=b"retryable", label="story/video")
    assert rejected.value.status_code == 400
    assert (
        generate_openrouter_video_bytes(
            None,
            source_bytes=b"retryable",
            label="story/video",
        )
        == b"accepted"
    )
    assert submit_calls == 2


def test_generation_orphan_admission_blocks_every_retry() -> None:
    fingerprint = provider_task_payload_fingerprint({"prompt": "ambiguous"})

    with generation_provider_task_scope(
        job_id="ambiguous-generation-job",
        stage="generating_video",
    ):
        with implicit_provider_task_scope(
            None,
            operation="video:pet_creation/scene_video",
            provider="openrouter",
            provider_origin="https://openrouter.ai/api/v1/videos",
            account_namespace=TEST_ACCOUNT_NAMESPACE,
            payload_fingerprint=fingerprint,
        ):
            pass

    with generation_provider_task_scope(
        job_id="ambiguous-generation-job",
        stage="generating_video",
    ):
        with pytest.raises(ProviderTaskReceiptAmbiguousError, match="no durable remote"):
            with implicit_provider_task_scope(
                None,
                operation="video:pet_creation/scene_video",
                provider="openrouter",
                provider_origin="https://openrouter.ai/api/v1/videos",
                account_namespace=TEST_ACCOUNT_NAMESPACE,
                payload_fingerprint=fingerprint,
            ):
                pass

    with sqlite3.connect(_provider_receipt_path()) as connection:
        assert connection.execute(
            "SELECT state, task_id FROM provider_tasks WHERE scope_key = ?",
            ("job:ambiguous-generation-job:generating_video",),
        ).fetchone() == ("admitted", None)


def test_terminal_not_found_allows_one_attempt_then_ambiguous_crash_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "provider-receipts.sqlite3"
    settings = _openrouter_settings()
    settings.provider_task_receipt_store_path = str(receipt_path)
    submit_calls = 0

    def submit(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        if submit_calls == 1:
            return httpx.Response(
                200,
                request=httpx.Request("POST", "https://openrouter.ai/api/v1/videos"),
                json={"id": "expired-task", "status": "queued"},
            )
        raise httpx.ReadTimeout(
            "ambiguous second submission",
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/videos"),
        )

    monkeypatch.setattr("app.services.image_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.image_service._submit_openrouter_video_job", submit)
    monkeypatch.setattr(
        "app.services.image_service._poll_openrouter_video_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OpenRouterVideoHTTPError(404, "expired")),
    )

    with pytest.raises(OpenRouterVideoHTTPError):
        generate_openrouter_video_bytes(None, source_bytes=b"expired", label="story/video")
    with pytest.raises(httpx.ReadTimeout):
        generate_openrouter_video_bytes(None, source_bytes=b"expired", label="story/video")
    with pytest.raises(ProviderTaskReceiptAmbiguousError):
        generate_openrouter_video_bytes(None, source_bytes=b"expired", label="story/video")
    assert submit_calls == 2


def test_poll_auth_error_keeps_accepted_receipt_resumable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "provider-receipts.sqlite3"
    settings = _openrouter_settings()
    settings.provider_task_receipt_store_path = str(receipt_path)
    submit_calls = 0
    poll_calls = 0

    def submit(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/videos"),
            json={"id": "auth-retry-task", "status": "queued"},
        )

    def poll(*_args, **_kwargs):
        nonlocal poll_calls
        poll_calls += 1
        if poll_calls == 1:
            raise OpenRouterVideoHTTPError(401, "rotated credential")
        return {"status": "completed"}

    monkeypatch.setattr("app.services.image_service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.image_service._submit_openrouter_video_job", submit)
    monkeypatch.setattr("app.services.image_service._poll_openrouter_video_job", poll)
    monkeypatch.setattr(
        "app.services.image_service._download_openrouter_video_bytes",
        lambda *_args, **_kwargs: b"resumed",
    )

    with pytest.raises(OpenRouterVideoHTTPError) as auth_error:
        generate_openrouter_video_bytes(None, source_bytes=b"auth", label="story/video")
    assert auth_error.value.status_code == 401
    assert (
        generate_openrouter_video_bytes(
            None,
            source_bytes=b"auth",
            label="story/video",
        )
        == b"resumed"
    )
    assert submit_calls == 1


def test_provider_task_receipt_isolated_by_job_and_stage() -> None:
    fingerprint = provider_task_payload_fingerprint({"prompt": "same"})

    with generation_provider_task_scope(
        job_id="owner-42-job",
        stage="generating_video",
    ):
        with implicit_provider_task_scope(
            None,
            operation="video:pet_creation/scene_video",
            provider="openrouter",
            provider_origin="https://openrouter.ai/api/v1/videos",
            account_namespace=TEST_ACCOUNT_NAMESPACE,
            payload_fingerprint=fingerprint,
        ):
            save_current_provider_task(
                operation="video:pet_creation/scene_video",
                provider="openrouter",
                provider_origin="https://openrouter.ai/api/v1/videos",
                account_namespace=TEST_ACCOUNT_NAMESPACE,
                task_id="isolated-task",
                polling_url=None,
                payload_fingerprint=fingerprint,
            )

    for job_id, stage in (
        ("owner-42-job", "generating_sad_video"),
        ("owner-43-job", "generating_video"),
    ):
        with generation_provider_task_scope(
            job_id=job_id,
            stage=stage,
        ):
            assert (
                find_current_provider_task(
                    operation="video:pet_creation/scene_video",
                    provider="openrouter",
                    provider_origin="https://openrouter.ai/api/v1/videos",
                    account_namespace=TEST_ACCOUNT_NAMESPACE,
                    payload_fingerprint=fingerprint,
                )
                is None
            )


def test_terminal_provider_failure_allows_deliberate_new_payload_retry() -> None:
    first_fingerprint = provider_task_payload_fingerprint({"prompt": "first"})
    second_fingerprint = provider_task_payload_fingerprint({"prompt": "repaired"})
    operation = "image:pet_creation/image"

    with generation_provider_task_scope(
        job_id="retry-job",
        stage="generating_images",
    ):
        with implicit_provider_task_scope(
            None,
            operation=operation,
            provider="kandinsky",
            provider_origin="https://studio.kandinskylab.ai/api",
            account_namespace=TEST_ACCOUNT_NAMESPACE,
            payload_fingerprint=first_fingerprint,
        ):
            save_current_provider_task(
                operation=operation,
                provider="kandinsky",
                provider_origin="https://studio.kandinskylab.ai/api",
                account_namespace=TEST_ACCOUNT_NAMESPACE,
                task_id="failed-task",
                polling_url=None,
                payload_fingerprint=first_fingerprint,
            )
            mark_current_provider_task_failed(operation)

    with generation_provider_task_scope(
        job_id="retry-job",
        stage="generating_images",
    ):
        with implicit_provider_task_scope(
            None,
            operation=operation,
            provider="kandinsky",
            provider_origin="https://studio.kandinskylab.ai/api",
            account_namespace=TEST_ACCOUNT_NAMESPACE,
            payload_fingerprint=second_fingerprint,
        ):
            assert (
                find_current_provider_task(
                    operation=operation,
                    provider="kandinsky",
                    provider_origin="https://studio.kandinskylab.ai/api",
                    account_namespace=TEST_ACCOUNT_NAMESPACE,
                    payload_fingerprint=second_fingerprint,
                )
                is None
            )
            replacement = save_current_provider_task(
                operation=operation,
                provider="kandinsky",
                provider_origin="https://studio.kandinskylab.ai/api",
                account_namespace=TEST_ACCOUNT_NAMESPACE,
                task_id="replacement-task",
                polling_url=None,
                payload_fingerprint=second_fingerprint,
            )

    assert replacement is not None
    assert replacement.task_id == "replacement-task"
    assert replacement.state == "accepted"


def test_provider_task_receipt_survives_terminal_job_pruning(tmp_path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    store = GenerationJobStore(store_path)
    updated_at = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    _save_job(
        store,
        job_id="pruned-provider-job",
        status="failed",
        updated_at=updated_at,
    )
    fingerprint = provider_task_payload_fingerprint({"prompt": "old"})
    with generation_provider_task_scope(
        job_id="pruned-provider-job",
        stage="generating_video",
    ):
        with implicit_provider_task_scope(
            None,
            operation="video:pet_creation/scene_video",
            provider="openrouter",
            provider_origin="https://openrouter.ai/api/v1/videos",
            account_namespace=TEST_ACCOUNT_NAMESPACE,
            payload_fingerprint=fingerprint,
        ):
            save_current_provider_task(
                operation="video:pet_creation/scene_video",
                provider="openrouter",
                provider_origin="https://openrouter.ai/api/v1/videos",
                account_namespace=TEST_ACCOUNT_NAMESPACE,
                task_id="pruned-task",
                polling_url=None,
                payload_fingerprint=fingerprint,
            )

    deleted = store.delete_terminal_older_than(updated_at + timedelta(seconds=1))

    assert [job.response.jobId for job in deleted] == ["pruned-provider-job"]
    stored = _stored_receipt(
        "job:pruned-provider-job:generating_video",
        "video:pet_creation/scene_video",
    )
    assert stored is not None
    assert stored.task_id == "pruned-task"
    with sqlite3.connect(store_path) as connection:
        provider_tables = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name LIKE '%provider_task%'"
        ).fetchall()
    assert provider_tables == []


def test_generation_paid_stage_exposes_job_stage_checkpoint_scope(tmp_path) -> None:
    store_path = tmp_path / "generation-jobs.sqlite3"
    observed_receipts = []
    fingerprint = provider_task_payload_fingerprint({"synthetic": "video"})

    def generate_video(_image_set):
        with implicit_provider_task_scope(
            None,
            operation="video:pet_creation/scene_video",
            provider="openrouter",
            provider_origin="https://openrouter.ai/api/v1/videos",
            account_namespace=TEST_ACCOUNT_NAMESPACE,
            payload_fingerprint=fingerprint,
        ):
            receipt = save_current_provider_task(
                operation="video:pet_creation/scene_video",
                provider="openrouter",
                provider_origin="https://openrouter.ai/api/v1/videos",
                account_namespace=TEST_ACCOUNT_NAMESPACE,
                task_id="service-scoped-task",
                polling_url=None,
                payload_fingerprint=fingerprint,
            )
            assert receipt is not None
            observed_receipts.append(receipt)
            mark_current_provider_task_media_saved("video:pet_creation/scene_video")
        return Path("teen-idle.mp4")

    images = {
        stage: {
            state: f"/static/generated/asset-1/{stage}-{state}.png"
            for state in ("idle", "happy", "hungry", "sad")
        }
        for stage in ("baby", "teen", "adult")
    }
    service = GenerationJobService(
        store=GenerationJobStore(store_path),
        image_workers=1,
        video_workers=1,
        generate_images=lambda _description, _provider: SimpleNamespace(asset_set_id="asset-1"),
        generate_video=generate_video,
        generate_background_image=lambda _image_set, _provider: Path("teen-sad.png"),
        generate_background_video=lambda _image_set, _path: Path("teen-sad.mp4"),
        generate_happy_image=lambda _image_set, _provider: Path("teen-happy.png"),
        generate_happy_video=lambda _image_set, _path: Path("teen-happy.mp4"),
        build_response=lambda *_args: {
            "assetSetId": "asset-1",
            "generatedAt": datetime.now(UTC),
            "images": images,
            "videoUrl": "/static/generated/asset-1/teen-idle.mp4",
        },
        build_failure=lambda _job_id, phase, exc, _owner_id: {
            "code": "SYNTHETIC_FAILURE",
            "phase": phase,
            "message": str(exc),
        },
    )
    user = TelegramUserContext(
        telegram_id=42,
        username="serge",
        first_name="Serge",
        language_code="ru",
        auth_date=datetime.now(UTC),
    )
    try:
        submitted = service.submit("мышонок", user)
        for _ in range(200):
            completed = service.get(submitted.jobId, 42)
            if completed.status == "succeeded":
                break
            time.sleep(0.005)
        else:
            raise AssertionError("synthetic generation job did not complete")
    finally:
        service.shutdown(wait=True)

    assert len(observed_receipts) == 1
    receipt = observed_receipts[0]
    assert receipt.scope_key == f"job:{submitted.jobId}:generating_video"
    stored = _stored_receipt(
        receipt.scope_key,
        "video:pet_creation/scene_video",
    )
    assert stored is not None
    assert stored.state == "media_saved"
