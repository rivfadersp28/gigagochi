from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app.config import get_settings
from app.services.provider_task_receipt_store import (
    ProviderTaskReceiptStore,
    StoredProviderTaskReceipt,
)

_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GLOBAL_SCOPE_KEY = "global"


@dataclass(frozen=True)
class _ProviderTaskIdentity:
    operation: str
    provider: str
    provider_origin: str
    account_namespace: str
    payload_fingerprint: str


@dataclass
class _ProviderTaskScope:
    store: ProviderTaskReceiptStore
    scope_key: str
    identity: _ProviderTaskIdentity | None = None
    admission_active: bool = False
    receipts: dict[str, StoredProviderTaskReceipt] = field(default_factory=dict)


_CURRENT_SCOPE: ContextVar[_ProviderTaskScope | None] = ContextVar(
    "provider_task_scope",
    default=None,
)


def provider_task_payload_fingerprint(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def has_current_provider_task_scope() -> bool:
    return _CURRENT_SCOPE.get() is not None


def provider_task_runtime_status() -> dict[str, object]:
    settings = get_settings()
    try:
        store = _store_for(
            settings.provider_task_receipt_store_path,
            settings.provider_task_receipt_store_max_records,
        )
        stale = store.stale_admissions(
            before=datetime.now(UTC)
            - timedelta(seconds=settings.provider_task_admission_stale_seconds),
            limit=1000,
        )
        return {
            "status": "degraded" if stale else "ok",
            "staleAdmissions": len(stale),
        }
    except Exception as exc:
        return {"status": "degraded", "staleAdmissions": -1, "error": str(exc)}


@lru_cache(maxsize=32)
def _cached_store(path: str, max_records: int) -> ProviderTaskReceiptStore:
    return ProviderTaskReceiptStore(path, max_records=max_records)


def _store_for(path: str | Path, max_records: int) -> ProviderTaskReceiptStore:
    normalized_path = str(Path(path).expanduser().resolve(strict=False))
    return _cached_store(normalized_path, max_records)


@contextmanager
def generation_provider_task_scope(
    *,
    job_id: str,
    stage: str,
) -> Iterator[None]:
    _validate_scope_value("job_id", job_id)
    _validate_scope_value("stage", stage)
    scope_key = f"job:{job_id}:{stage}"
    _validate_scope_value("scope_key", scope_key, max_length=640)
    settings = get_settings()
    store = _store_for(
        settings.provider_task_receipt_store_path,
        settings.provider_task_receipt_store_max_records,
    )
    token = _CURRENT_SCOPE.set(_ProviderTaskScope(store=store, scope_key=scope_key))
    try:
        yield
    finally:
        _CURRENT_SCOPE.reset(token)


@contextmanager
def implicit_provider_task_scope(
    store_path: str | Path | None,
    *,
    max_records: int = 100_000,
    operation: str,
    provider: str,
    provider_origin: str,
    account_namespace: str,
    payload_fingerprint: str,
) -> Iterator[None]:
    """Reserve one paid submit in the current job scope or the global scope."""

    identity = _ProviderTaskIdentity(
        operation=operation,
        provider=provider,
        provider_origin=provider_origin,
        account_namespace=account_namespace,
        payload_fingerprint=payload_fingerprint,
    )
    _validate_identity(identity)
    current = _CURRENT_SCOPE.get()
    if current is not None and current.identity is not None:
        _require_identity(current, identity)
        yield
        return

    if current is None:
        if store_path is None:
            raise RuntimeError("PROVIDER_TASK_RECEIPT_STORE_PATH_REQUIRED")
        base = _ProviderTaskScope(
            store=_store_for(store_path, max_records),
            scope_key=_GLOBAL_SCOPE_KEY,
        )
    else:
        base = current

    with base.store.operation_lock(
        scope_key=base.scope_key,
        provider=provider,
        provider_origin=provider_origin,
        account_namespace=account_namespace,
        operation=operation,
        payload_fingerprint=payload_fingerprint,
    ):
        outcome = base.store.reserve_identity(
            scope_key=base.scope_key,
            provider=provider,
            provider_origin=provider_origin,
            account_namespace=account_namespace,
            operation=operation,
            payload_fingerprint=payload_fingerprint,
            created_at=datetime.now(UTC),
        )
        if outcome == "created":
            base.receipts.pop(operation, None)
        active = _ProviderTaskScope(
            store=base.store,
            scope_key=base.scope_key,
            identity=identity,
            admission_active=outcome == "created",
            receipts=base.receipts,
        )
        token = _CURRENT_SCOPE.set(active)
        try:
            yield
        finally:
            _CURRENT_SCOPE.reset(token)


def find_current_provider_task(
    *,
    operation: str,
    provider: str,
    provider_origin: str,
    account_namespace: str,
    payload_fingerprint: str,
) -> StoredProviderTaskReceipt | None:
    scope = _CURRENT_SCOPE.get()
    if scope is None:
        return None
    identity = _ProviderTaskIdentity(
        operation=operation,
        provider=provider,
        provider_origin=provider_origin,
        account_namespace=account_namespace,
        payload_fingerprint=payload_fingerprint,
    )
    _validate_identity(identity)
    if scope.identity is not None:
        _require_identity(scope, identity)
    existing = scope.store.get(
        scope_key=scope.scope_key,
        operation=operation,
        provider=provider,
        provider_origin=provider_origin,
        account_namespace=account_namespace,
        payload_fingerprint=payload_fingerprint,
    )
    if existing is None or existing.state in {"admitted", "provider_failed"}:
        return None
    if existing.task_id is None:
        raise RuntimeError("accepted provider task receipt has no task_id")
    scope.receipts[operation] = existing
    return existing


def save_current_provider_task(
    *,
    operation: str,
    provider: str,
    provider_origin: str,
    account_namespace: str,
    task_id: str,
    polling_url: str | None,
    payload_fingerprint: str,
) -> StoredProviderTaskReceipt | None:
    scope = _CURRENT_SCOPE.get()
    if scope is None:
        return None
    identity = _ProviderTaskIdentity(
        operation=operation,
        provider=provider,
        provider_origin=provider_origin,
        account_namespace=account_namespace,
        payload_fingerprint=payload_fingerprint,
    )
    _validate_identity(identity)
    _require_identity(scope, identity)
    _validate_remote_value("task_id", task_id, max_length=1_024)
    if polling_url is not None:
        _validate_remote_value("polling_url", polling_url, max_length=4_096)
    now = datetime.now(UTC)
    receipt = scope.store.save(
        StoredProviderTaskReceipt(
            scope_key=scope.scope_key,
            operation=operation,
            provider=provider,
            provider_origin=provider_origin,
            account_namespace=account_namespace,
            task_id=task_id,
            polling_url=polling_url,
            payload_fingerprint=payload_fingerprint,
            state="accepted",
            created_at=now,
            updated_at=now,
        )
    )
    scope.admission_active = False
    scope.receipts[operation] = receipt
    return receipt


def release_current_provider_task_admission(operation: str) -> bool:
    """Release only after the caller proved that the paid POST was not accepted."""

    scope = _CURRENT_SCOPE.get()
    if (
        scope is None
        or scope.identity is None
        or not scope.admission_active
        or scope.identity.operation != operation
    ):
        return False
    identity = scope.identity
    released = scope.store.release_admission(
        scope_key=scope.scope_key,
        operation=identity.operation,
        provider=identity.provider,
        provider_origin=identity.provider_origin,
        account_namespace=identity.account_namespace,
        payload_fingerprint=identity.payload_fingerprint,
    )
    if released:
        scope.admission_active = False
    return released


def mark_current_provider_task_failed(operation: str) -> None:
    _mark_current_provider_task(operation, state="provider_failed")


def mark_current_provider_task_media_saved(operation: str) -> None:
    _mark_current_provider_task(operation, state="media_saved")


def _mark_current_provider_task(
    operation: str,
    *,
    state: Literal["provider_failed", "media_saved"],
) -> None:
    scope = _CURRENT_SCOPE.get()
    if scope is None:
        return
    receipt = scope.receipts.get(operation)
    if receipt is None:
        return
    if receipt.task_id is None:
        raise RuntimeError("provider task receipt has no task_id")
    updated = scope.store.mark_state(
        scope_key=scope.scope_key,
        operation=operation,
        provider=receipt.provider,
        provider_origin=receipt.provider_origin,
        account_namespace=receipt.account_namespace,
        task_id=receipt.task_id,
        payload_fingerprint=receipt.payload_fingerprint,
        state=state,
        updated_at=datetime.now(UTC),
    )
    scope.receipts[operation] = updated


def _require_identity(
    scope: _ProviderTaskScope,
    identity: _ProviderTaskIdentity,
) -> None:
    if scope.identity != identity:
        raise RuntimeError("implicit provider task identity changed inside operation scope")


def _validate_identity(identity: _ProviderTaskIdentity) -> None:
    _validate_scope_value("operation", identity.operation)
    _validate_scope_value("provider", identity.provider)
    _validate_scope_value("account_namespace", identity.account_namespace)
    _validate_remote_value("provider_origin", identity.provider_origin, max_length=2_048)
    if not _FINGERPRINT_PATTERN.fullmatch(identity.payload_fingerprint):
        raise ValueError("payload_fingerprint must be a lowercase SHA-256 digest")


def _validate_scope_value(name: str, value: str, *, max_length: int = 256) -> None:
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > max_length
        or any(ord(character) < 32 for character in cleaned)
    ):
        raise ValueError(f"invalid provider task {name}")


def _validate_remote_value(name: str, value: str, *, max_length: int) -> None:
    cleaned = value.strip()
    if (
        not cleaned
        or len(cleaned) > max_length
        or any(ord(character) < 32 for character in cleaned)
    ):
        raise ValueError(f"invalid provider task {name}")
