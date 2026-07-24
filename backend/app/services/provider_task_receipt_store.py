from __future__ import annotations

import fcntl
import hashlib
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Literal, cast

SQLITE_BUSY_TIMEOUT_SECONDS = 30
PROVIDER_OPERATION_LOCK_BUCKETS = 1_024
_PROVIDER_OPERATION_THREAD_LOCKS = tuple(Lock() for _ in range(PROVIDER_OPERATION_LOCK_BUCKETS))

ProviderTaskState = Literal["admitted", "accepted", "provider_failed", "media_saved"]


@dataclass(frozen=True)
class StoredProviderTaskReceipt:
    scope_key: str
    provider: str
    provider_origin: str
    account_namespace: str
    operation: str
    payload_fingerprint: str
    task_id: str | None
    polling_url: str | None
    state: ProviderTaskState
    created_at: datetime
    updated_at: datetime


class ProviderTaskReceiptConflictError(RuntimeError):
    pass


class ProviderTaskReceiptAmbiguousError(RuntimeError):
    pass


class ProviderTaskReceiptCapacityError(RuntimeError):
    pass


class ProviderTaskReceiptStore:
    """One durable state machine for every asynchronous paid provider task."""

    def __init__(self, path: str | Path, *, max_records: int = 100_000) -> None:
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self._path = Path(path)
        self._max_records = max_records
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_SECONDS * 1_000}")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            self._enable_wal_with_retry(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_tasks (
                    scope_key TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_origin TEXT NOT NULL,
                    account_namespace TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    payload_fingerprint TEXT NOT NULL,
                    task_id TEXT,
                    polling_url TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (
                        scope_key,
                        provider,
                        provider_origin,
                        account_namespace,
                        operation,
                        payload_fingerprint
                    ),
                    CHECK (state IN ('admitted', 'accepted', 'provider_failed', 'media_saved')),
                    CHECK (
                        (state = 'admitted' AND task_id IS NULL AND polling_url IS NULL)
                        OR (state != 'admitted' AND task_id IS NOT NULL)
                    )
                ) WITHOUT ROWID
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS provider_tasks_remote_idx
                ON provider_tasks(provider, provider_origin, account_namespace, task_id)
                WHERE task_id IS NOT NULL
                """
            )

    @staticmethod
    def _enable_wal_with_retry(connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + SQLITE_BUSY_TIMEOUT_SECONDS
        delay = 0.01
        while True:
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                error_code = getattr(exc, "sqlite_errorcode", None)
                primary_code = error_code & 0xFF if error_code is not None else None
                if primary_code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 0.1)

    @contextmanager
    def operation_lock(
        self,
        *,
        scope_key: str,
        provider: str,
        provider_origin: str,
        account_namespace: str,
        operation: str,
        payload_fingerprint: str,
    ) -> Iterator[None]:
        """Serialize one exact paid operation across threads and processes."""

        lock_key = "\0".join(
            (
                scope_key,
                provider,
                provider_origin,
                account_namespace,
                operation,
                payload_fingerprint,
            )
        ).encode("utf-8")
        bucket = (
            int.from_bytes(hashlib.blake2s(lock_key, digest_size=4).digest(), "big")
            % PROVIDER_OPERATION_LOCK_BUCKETS
        )
        lock_dir = self._path.parent / f".{self._path.name}.operation-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"bucket-{bucket:04d}.lock"
        with _PROVIDER_OPERATION_THREAD_LOCKS[bucket]:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def get(
        self,
        *,
        scope_key: str,
        provider: str,
        provider_origin: str,
        account_namespace: str,
        operation: str,
        payload_fingerprint: str,
    ) -> StoredProviderTaskReceipt | None:
        identity = self._identity(
            scope_key=scope_key,
            provider=provider,
            provider_origin=provider_origin,
            account_namespace=account_namespace,
            operation=operation,
            payload_fingerprint=payload_fingerprint,
        )
        with self._connect() as connection:
            row = self._select(connection, identity)
        return self._row(row) if row is not None else None

    def reserve_identity(
        self,
        *,
        scope_key: str,
        provider: str,
        provider_origin: str,
        account_namespace: str,
        operation: str,
        payload_fingerprint: str,
        created_at: datetime,
    ) -> Literal["created", "receipt_exists"]:
        """Persist pre-submit intent; an orphaned admitted row fails closed forever."""

        identity = self._identity(
            scope_key=scope_key,
            provider=provider,
            provider_origin=provider_origin,
            account_namespace=account_namespace,
            operation=operation,
            payload_fingerprint=payload_fingerprint,
        )
        timestamp = created_at.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._select(connection, identity)
            if row is not None:
                state = str(row[8])
                if state == "admitted":
                    raise ProviderTaskReceiptAmbiguousError(
                        "provider task admission has no durable remote task receipt"
                    )
                if state in {"accepted", "media_saved"}:
                    return "receipt_exists"
                if state != "provider_failed":
                    raise RuntimeError(f"invalid provider task state: {state}")
                cursor = connection.execute(
                    """
                    UPDATE provider_tasks
                    SET task_id = NULL, polling_url = NULL, state = 'admitted',
                        created_at = ?, updated_at = ?
                    WHERE scope_key = ? AND provider = ? AND provider_origin = ?
                      AND account_namespace = ? AND operation = ?
                      AND payload_fingerprint = ? AND state = 'provider_failed'
                    """,
                    (timestamp, timestamp, *identity),
                )
                if cursor.rowcount != 1:
                    raise ProviderTaskReceiptConflictError(
                        "provider task changed before retry admission"
                    )
                return "created"

            record_count = int(
                connection.execute("SELECT COUNT(*) FROM provider_tasks").fetchone()[0]
            )
            if record_count >= self._max_records:
                raise ProviderTaskReceiptCapacityError(
                    "provider task receipt store is full; paid submission refused"
                )
            connection.execute(
                """
                INSERT INTO provider_tasks (
                    scope_key, provider, provider_origin, account_namespace,
                    operation, payload_fingerprint, task_id, polling_url,
                    state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'admitted', ?, ?)
                """,
                (*identity, timestamp, timestamp),
            )
        return "created"

    def release_admission(
        self,
        *,
        scope_key: str,
        provider: str,
        provider_origin: str,
        account_namespace: str,
        operation: str,
        payload_fingerprint: str,
    ) -> bool:
        identity = self._identity(
            scope_key=scope_key,
            provider=provider,
            provider_origin=provider_origin,
            account_namespace=account_namespace,
            operation=operation,
            payload_fingerprint=payload_fingerprint,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM provider_tasks
                WHERE scope_key = ? AND provider = ? AND provider_origin = ?
                  AND account_namespace = ? AND operation = ?
                  AND payload_fingerprint = ? AND state = 'admitted' AND task_id IS NULL
                """,
                identity,
            )
        return cursor.rowcount == 1

    def delete_generation_jobs(self, job_ids: list[str]) -> int:
        """Delete provider references belonging to stopped generation jobs."""

        if not job_ids:
            return 0
        deleted = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for job_id in job_ids:
                cursor = connection.execute(
                    "DELETE FROM provider_tasks WHERE scope_key LIKE ?",
                    (f"job:{job_id}:%",),
                )
                deleted += max(0, cursor.rowcount)
        return deleted

    def stale_admissions(
        self,
        *,
        before: datetime,
        limit: int = 100,
    ) -> list[StoredProviderTaskReceipt]:
        if not 1 <= limit <= 10_000:
            raise ValueError("stale admission limit must be between 1 and 10000")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT scope_key, provider, provider_origin, account_namespace,
                       operation, payload_fingerprint, task_id, polling_url,
                       state, created_at, updated_at
                FROM provider_tasks
                WHERE state = 'admitted' AND updated_at < ?
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (before.isoformat(), limit),
            ).fetchall()
        return [self._row(row) for row in rows]

    def release_stale_admission(
        self,
        receipt: StoredProviderTaskReceipt,
        *,
        before: datetime,
    ) -> bool:
        """Operator-only resolution after externally checking provider billing/tasks."""

        if receipt.state != "admitted" or receipt.task_id is not None:
            raise ValueError("only ambiguous admitted receipts can be released")
        with self.operation_lock(
            scope_key=receipt.scope_key,
            provider=receipt.provider,
            provider_origin=receipt.provider_origin,
            account_namespace=receipt.account_namespace,
            operation=receipt.operation,
            payload_fingerprint=receipt.payload_fingerprint,
        ):
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    DELETE FROM provider_tasks
                    WHERE scope_key = ? AND provider = ? AND provider_origin = ?
                      AND account_namespace = ? AND operation = ?
                      AND payload_fingerprint = ? AND state = 'admitted'
                      AND task_id IS NULL AND updated_at < ?
                    """,
                    (
                        receipt.scope_key,
                        receipt.provider,
                        receipt.provider_origin,
                        receipt.account_namespace,
                        receipt.operation,
                        receipt.payload_fingerprint,
                        before.isoformat(),
                    ),
                )
        return cursor.rowcount == 1

    def save(self, receipt: StoredProviderTaskReceipt) -> StoredProviderTaskReceipt:
        if receipt.state != "accepted" or receipt.task_id is None:
            raise ValueError("new provider task receipt must be accepted with a task_id")
        identity = self._identity(
            scope_key=receipt.scope_key,
            provider=receipt.provider,
            provider_origin=receipt.provider_origin,
            account_namespace=receipt.account_namespace,
            operation=receipt.operation,
            payload_fingerprint=receipt.payload_fingerprint,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._select(connection, identity)
            if row is None:
                raise ProviderTaskReceiptConflictError(
                    "provider task receipt has no matching pre-submit admission"
                )
            existing = self._row(row)
            if existing.state != "admitted":
                if (
                    existing.task_id == receipt.task_id
                    and existing.polling_url == receipt.polling_url
                    and existing.state in {"accepted", "media_saved"}
                ):
                    return existing
                raise ProviderTaskReceiptConflictError(
                    "provider task operation already has another durable receipt"
                )
            try:
                cursor = connection.execute(
                    """
                    UPDATE provider_tasks
                    SET task_id = ?, polling_url = ?, state = 'accepted', updated_at = ?
                    WHERE scope_key = ? AND provider = ? AND provider_origin = ?
                      AND account_namespace = ? AND operation = ?
                      AND payload_fingerprint = ? AND state = 'admitted' AND task_id IS NULL
                    """,
                    (
                        receipt.task_id,
                        receipt.polling_url,
                        receipt.updated_at.isoformat(),
                        *identity,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ProviderTaskReceiptConflictError(
                    "remote provider task is already bound in this account namespace"
                ) from exc
            if cursor.rowcount != 1:
                raise ProviderTaskReceiptConflictError(
                    "provider task changed before receipt checkpoint"
                )
        stored = self.get(
            scope_key=receipt.scope_key,
            provider=receipt.provider,
            provider_origin=receipt.provider_origin,
            account_namespace=receipt.account_namespace,
            operation=receipt.operation,
            payload_fingerprint=receipt.payload_fingerprint,
        )
        if stored is None:
            raise RuntimeError("provider task receipt disappeared after save")
        return stored

    def mark_state(
        self,
        *,
        scope_key: str,
        provider: str,
        provider_origin: str,
        account_namespace: str,
        operation: str,
        payload_fingerprint: str,
        task_id: str,
        state: Literal["provider_failed", "media_saved"],
        updated_at: datetime,
    ) -> StoredProviderTaskReceipt:
        identity = self._identity(
            scope_key=scope_key,
            provider=provider,
            provider_origin=provider_origin,
            account_namespace=account_namespace,
            operation=operation,
            payload_fingerprint=payload_fingerprint,
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE provider_tasks
                SET state = ?, updated_at = ?
                WHERE scope_key = ? AND provider = ? AND provider_origin = ?
                  AND account_namespace = ? AND operation = ?
                  AND payload_fingerprint = ? AND task_id = ?
                  AND state IN ('accepted', ?)
                """,
                (
                    state,
                    updated_at.isoformat(),
                    *identity,
                    task_id,
                    state,
                ),
            )
            if cursor.rowcount != 1:
                raise ProviderTaskReceiptConflictError(
                    "provider task receipt changed before state checkpoint"
                )
        receipt = self.get(
            scope_key=scope_key,
            provider=provider,
            provider_origin=provider_origin,
            account_namespace=account_namespace,
            operation=operation,
            payload_fingerprint=payload_fingerprint,
        )
        if receipt is None:
            raise RuntimeError("provider task receipt disappeared after state checkpoint")
        return receipt

    @staticmethod
    def _identity(
        *,
        scope_key: str,
        provider: str,
        provider_origin: str,
        account_namespace: str,
        operation: str,
        payload_fingerprint: str,
    ) -> tuple[str, str, str, str, str, str]:
        return (
            scope_key,
            provider,
            provider_origin,
            account_namespace,
            operation,
            payload_fingerprint,
        )

    @staticmethod
    def _select(
        connection: sqlite3.Connection,
        identity: tuple[str, str, str, str, str, str],
    ) -> tuple[object, ...] | None:
        return connection.execute(
            """
            SELECT scope_key, provider, provider_origin, account_namespace,
                   operation, payload_fingerprint, task_id, polling_url,
                   state, created_at, updated_at
            FROM provider_tasks
            WHERE scope_key = ? AND provider = ? AND provider_origin = ?
              AND account_namespace = ? AND operation = ? AND payload_fingerprint = ?
            """,
            identity,
        ).fetchone()

    @staticmethod
    def _row(row: tuple[object, ...]) -> StoredProviderTaskReceipt:
        state_value = str(row[8])
        if state_value not in {"admitted", "accepted", "provider_failed", "media_saved"}:
            raise RuntimeError(f"invalid provider task receipt state: {state_value}")
        state = cast(ProviderTaskState, state_value)
        task_id = str(row[6]) if row[6] is not None else None
        if (state == "admitted") != (task_id is None):
            raise RuntimeError("invalid provider task receipt/task_id invariant")
        return StoredProviderTaskReceipt(
            scope_key=str(row[0]),
            provider=str(row[1]),
            provider_origin=str(row[2]),
            account_namespace=str(row[3]),
            operation=str(row[4]),
            payload_fingerprint=str(row[5]),
            task_id=task_id,
            polling_url=str(row[7]) if row[7] is not None else None,
            state=state,
            created_at=datetime.fromisoformat(str(row[9])),
            updated_at=datetime.fromisoformat(str(row[10])),
        )
