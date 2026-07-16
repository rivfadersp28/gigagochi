from __future__ import annotations

import multiprocessing
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.services.image_service import _provider_account_namespace
from app.services.provider_task_checkpoint import (
    find_current_provider_task,
    implicit_provider_task_scope,
    provider_task_payload_fingerprint,
    save_current_provider_task,
)
from app.services.provider_task_receipt_store import (
    ProviderTaskReceiptAmbiguousError,
    ProviderTaskReceiptCapacityError,
    ProviderTaskReceiptConflictError,
    ProviderTaskReceiptStore,
    StoredProviderTaskReceipt,
)

TEST_ACCOUNT_NAMESPACE = "configured:test-account"


def _concurrent_implicit_receipt_worker(store_path: str, gate, results) -> None:
    fingerprint = provider_task_payload_fingerprint({"prompt": "same global payload"})
    gate.wait(timeout=5)
    with implicit_provider_task_scope(
        store_path,
        max_records=10,
        operation="video:story/video",
        provider="openrouter",
        provider_origin="https://openrouter.ai/api/v1/videos",
        account_namespace=TEST_ACCOUNT_NAMESPACE,
        payload_fingerprint=fingerprint,
    ):
        existing = find_current_provider_task(
            operation="video:story/video",
            provider="openrouter",
            provider_origin="https://openrouter.ai/api/v1/videos",
            account_namespace=TEST_ACCOUNT_NAMESPACE,
            payload_fingerprint=fingerprint,
        )
        if existing is not None:
            results.put(("resumed", existing.task_id))
            return
        saved = save_current_provider_task(
            operation="video:story/video",
            provider="openrouter",
            provider_origin="https://openrouter.ai/api/v1/videos",
            account_namespace=TEST_ACCOUNT_NAMESPACE,
            task_id="global-remote-task",
            polling_url=None,
            payload_fingerprint=fingerprint,
        )
        assert saved is not None
        results.put(("submitted", saved.task_id))


def _receipt(
    *,
    operation: str,
    fingerprint: str,
    task_id: str,
    account_namespace: str = TEST_ACCOUNT_NAMESPACE,
    scope_key: str = "global",
) -> StoredProviderTaskReceipt:
    now = datetime.now(UTC)
    return StoredProviderTaskReceipt(
        scope_key=scope_key,
        provider="openrouter",
        provider_origin="https://openrouter.ai/api/v1/videos",
        account_namespace=account_namespace,
        operation=operation,
        payload_fingerprint=fingerprint,
        task_id=task_id,
        polling_url=None,
        state="accepted",
        created_at=now,
        updated_at=now,
    )


def test_stale_ambiguous_admission_requires_explicit_release(tmp_path: Path) -> None:
    store = ProviderTaskReceiptStore(tmp_path / "provider-receipts.sqlite3")
    created_at = datetime.now(UTC) - timedelta(hours=2)
    identity = {
        "scope_key": "job:stale:generating_video",
        "provider": "openrouter",
        "provider_origin": "https://openrouter.ai/api/v1/videos",
        "account_namespace": TEST_ACCOUNT_NAMESPACE,
        "operation": "video:pet_creation/scene_video",
        "payload_fingerprint": "a" * 64,
    }
    assert store.reserve_identity(**identity, created_at=created_at) == "created"

    stale = store.stale_admissions(before=datetime.now(UTC) - timedelta(minutes=30))

    assert len(stale) == 1
    assert stale[0].scope_key == identity["scope_key"]
    assert store.release_stale_admission(
        stale[0],
        before=datetime.now(UTC) - timedelta(minutes=30),
    )
    assert store.reserve_identity(
        **identity,
        created_at=datetime.now(UTC),
    ) == "created"


def test_implicit_operation_lock_serializes_exact_payload_across_processes(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "provider-receipts.sqlite3"
    context = multiprocessing.get_context("spawn")
    gate = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_concurrent_implicit_receipt_worker,
            args=(str(store_path), gate, results),
        )
        for _ in range(2)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert sorted(results.get(timeout=2) for _process in processes) == [
        ("resumed", "global-remote-task"),
        ("submitted", "global-remote-task"),
    ]
    with sqlite3.connect(store_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM provider_tasks").fetchone() == (1,)


def test_receipt_capacity_is_reserved_before_submission_and_never_evicts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider-receipts.sqlite3"
    store = ProviderTaskReceiptStore(path, max_records=1)
    first = provider_task_payload_fingerprint({"payload": 1})
    second = provider_task_payload_fingerprint({"payload": 2})
    store.reserve_identity(
        scope_key="global",
        provider="kandinsky",
        provider_origin="https://studio.kandinskylab.ai/api",
        account_namespace=TEST_ACCOUNT_NAMESPACE,
        operation="image:story/image",
        payload_fingerprint=first,
        created_at=datetime.now(UTC),
    )

    with pytest.raises(ProviderTaskReceiptAmbiguousError):
        store.reserve_identity(
            scope_key="global",
            provider="kandinsky",
            provider_origin="https://studio.kandinskylab.ai/api",
            account_namespace=TEST_ACCOUNT_NAMESPACE,
            operation="image:story/image",
            payload_fingerprint=first,
            created_at=datetime.now(UTC),
        )

    with pytest.raises(ProviderTaskReceiptCapacityError, match="submission refused"):
        store.reserve_identity(
            scope_key="global",
            provider="kandinsky",
            provider_origin="https://studio.kandinskylab.ai/api",
            account_namespace=TEST_ACCOUNT_NAMESPACE,
            operation="image:story/image",
            payload_fingerprint=second,
            created_at=datetime.now(UTC),
        )

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT payload_fingerprint, state, task_id FROM provider_tasks"
        ).fetchall() == [(first, "admitted", None)]


def test_remote_task_binding_is_scoped_to_non_secret_account_namespace(tmp_path: Path) -> None:
    store = ProviderTaskReceiptStore(tmp_path / "provider-receipts.sqlite3", max_records=2)
    fingerprint = provider_task_payload_fingerprint({"payload": "same"})
    for account_namespace in ("configured:account-a", "configured:account-b"):
        store.reserve_identity(
            scope_key="global",
            provider="openrouter",
            provider_origin="https://openrouter.ai/api/v1/videos",
            account_namespace=account_namespace,
            operation="video:story",
            payload_fingerprint=fingerprint,
            created_at=datetime.now(UTC),
        )
        store.save(
            _receipt(
                operation="video:story",
                fingerprint=fingerprint,
                task_id="provider-local-task-id",
                account_namespace=account_namespace,
            )
        )


def test_account_namespace_uses_configured_id_or_credential_hash() -> None:
    fallback_a = _provider_account_namespace(
        type("Settings", (), {"openrouter_api_key": "secret-account-a"})(),
        "openrouter",
    )
    fallback_b = _provider_account_namespace(
        type("Settings", (), {"openrouter_api_key": "secret-account-b"})(),
        "openrouter",
    )
    configured_a = _provider_account_namespace(
        type(
            "Settings",
            (),
            {
                "openrouter_api_key": "rotated-secret-a",
                "openrouter_account_namespace": "stable-account",
            },
        )(),
        "openrouter",
    )
    configured_b = _provider_account_namespace(
        type(
            "Settings",
            (),
            {
                "openrouter_api_key": "rotated-secret-b",
                "openrouter_account_namespace": "stable-account",
            },
        )(),
        "openrouter",
    )

    assert fallback_a.startswith("credential-sha256:")
    assert fallback_a != fallback_b
    assert "secret-account" not in fallback_a
    assert configured_a == configured_b == "configured:stable-account"


def test_remote_task_id_cannot_be_bound_to_two_payloads(tmp_path: Path) -> None:
    store = ProviderTaskReceiptStore(tmp_path / "provider-receipts.sqlite3", max_records=2)
    first = provider_task_payload_fingerprint({"payload": 1})
    second = provider_task_payload_fingerprint({"payload": 2})
    for operation, fingerprint in (("video:first", first), ("video:second", second)):
        store.reserve_identity(
            scope_key="global",
            provider="openrouter",
            provider_origin="https://openrouter.ai/api/v1/videos",
            account_namespace=TEST_ACCOUNT_NAMESPACE,
            operation=operation,
            payload_fingerprint=fingerprint,
            created_at=datetime.now(UTC),
        )

    store.save(_receipt(operation="video:first", fingerprint=first, task_id="same-remote-task"))
    with pytest.raises(ProviderTaskReceiptConflictError):
        store.save(
            _receipt(
                operation="video:second",
                fingerprint=second,
                task_id="same-remote-task",
            )
        )


def test_provider_failure_retries_in_place_without_consuming_capacity(tmp_path: Path) -> None:
    path = tmp_path / "provider-tasks.sqlite3"
    store = ProviderTaskReceiptStore(path, max_records=1)
    fingerprint = provider_task_payload_fingerprint({"payload": "retry"})
    identity = {
        "scope_key": "job:job-1:generating_video",
        "provider": "openrouter",
        "provider_origin": "https://openrouter.ai/api/v1/videos",
        "account_namespace": TEST_ACCOUNT_NAMESPACE,
        "operation": "video:pet_creation/scene_video",
        "payload_fingerprint": fingerprint,
    }
    store.reserve_identity(**identity, created_at=datetime.now(UTC))
    first = store.save(
        _receipt(
            scope_key=identity["scope_key"],
            operation=identity["operation"],
            fingerprint=fingerprint,
            task_id="terminal-task",
        )
    )
    assert first.task_id is not None
    store.mark_state(
        **identity,
        task_id=first.task_id,
        state="provider_failed",
        updated_at=datetime.now(UTC),
    )

    assert store.reserve_identity(**identity, created_at=datetime.now(UTC)) == "created"
    admitted = store.get(**identity)
    assert admitted is not None
    assert admitted.state == "admitted"
    assert admitted.task_id is None

    replacement = store.save(
        _receipt(
            scope_key=identity["scope_key"],
            operation=identity["operation"],
            fingerprint=fingerprint,
            task_id="replacement-task",
        )
    )
    assert replacement.state == "accepted"
    assert replacement.task_id == "replacement-task"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM provider_tasks").fetchone() == (1,)


def test_scope_is_part_of_identity_but_not_remote_task_uniqueness(tmp_path: Path) -> None:
    path = tmp_path / "provider-tasks.sqlite3"
    store = ProviderTaskReceiptStore(path, max_records=2)
    fingerprint = provider_task_payload_fingerprint({"payload": "same"})
    operation = "video:pet_creation/scene_video"
    for scope_key in ("job:first:generating_video", "job:second:generating_video"):
        store.reserve_identity(
            scope_key=scope_key,
            provider="openrouter",
            provider_origin="https://openrouter.ai/api/v1/videos",
            account_namespace=TEST_ACCOUNT_NAMESPACE,
            operation=operation,
            payload_fingerprint=fingerprint,
            created_at=datetime.now(UTC),
        )

    store.save(
        _receipt(
            scope_key="job:first:generating_video",
            operation=operation,
            fingerprint=fingerprint,
            task_id="same-remote-task",
        )
    )
    with pytest.raises(ProviderTaskReceiptConflictError, match="already bound"):
        store.save(
            _receipt(
                scope_key="job:second:generating_video",
                operation=operation,
                fingerprint=fingerprint,
                task_id="same-remote-task",
            )
        )

    with sqlite3.connect(path) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name LIKE 'provider_task%'"
        ).fetchall()
        index_sql = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name = 'provider_tasks_remote_idx'"
        ).fetchone()
    assert tables == [("provider_tasks",)]
    assert index_sql is not None
    assert "WHERE task_id IS NOT NULL" in str(index_sql[0])
