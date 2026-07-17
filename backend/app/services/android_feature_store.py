from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.services.feature_owner import FeatureOwner

SQLITE_BUSY_TIMEOUT_MS = 5_000
REQUEST_IN_PROGRESS_MAX_AGE_MS = 5 * 60 * 1_000


class AndroidFeatureIdempotencyConflictError(RuntimeError):
    pass


class AndroidFeatureSessionBusyError(RuntimeError):
    pass


class AndroidFeatureSessionOutcomeUnknownError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AndroidFeatureRequestAttempt:
    state: Literal["created", "in_progress", "outcome_unknown", "completed"]
    resource_id: str | None
    response_json: str | None

    @property
    def created(self) -> bool:
        return self.state == "created"


def canonical_payload_fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class AndroidFeatureStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._instance_id = uuid.uuid4().hex
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS android_feature_requests (
                        owner_key TEXT NOT NULL,
                        operation TEXT NOT NULL,
                        request_key TEXT NOT NULL,
                        payload_fingerprint TEXT NOT NULL,
                        resource_id TEXT,
                        response_json TEXT,
                        state TEXT NOT NULL DEFAULT 'in_progress',
                        executor_instance_id TEXT,
                        created_at_ms INTEGER NOT NULL,
                        updated_at_ms INTEGER NOT NULL,
                        PRIMARY KEY (owner_key, operation, request_key)
                    ) WITHOUT ROWID
                    """
                )
                request_columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(android_feature_requests)")
                }
                if "state" not in request_columns:
                    connection.execute(
                        "ALTER TABLE android_feature_requests ADD COLUMN "
                        "state TEXT NOT NULL DEFAULT 'in_progress'"
                    )
                if "executor_instance_id" not in request_columns:
                    connection.execute(
                        "ALTER TABLE android_feature_requests ADD COLUMN "
                        "executor_instance_id TEXT"
                    )
                    connection.execute(
                        "UPDATE android_feature_requests SET state = 'completed' "
                        "WHERE response_json IS NOT NULL"
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _owner_key(owner: FeatureOwner) -> str:
        return str(owner.storage_key)

    def begin_request(
        self,
        *,
        owner: FeatureOwner,
        operation: str,
        request_key: str,
        payload: Any,
        resource_id: str | None = None,
    ) -> AndroidFeatureRequestAttempt:
        fingerprint = canonical_payload_fingerprint(payload)
        now = int(time.time() * 1_000)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT payload_fingerprint, resource_id, response_json, state,
                           executor_instance_id, updated_at_ms
                    FROM android_feature_requests
                    WHERE owner_key = ? AND operation = ? AND request_key = ?
                    """,
                    (self._owner_key(owner), operation, request_key),
                ).fetchone()
                if row is not None:
                    if str(row[0]) != fingerprint:
                        raise AndroidFeatureIdempotencyConflictError(request_key)
                    connection.commit()
                    state: Literal[
                        "created", "in_progress", "outcome_unknown", "completed"
                    ]
                    if str(row[3]) == "completed" and row[2] is not None:
                        state = "completed"
                    elif now - int(row[5]) <= REQUEST_IN_PROGRESS_MAX_AGE_MS:
                        state = "in_progress"
                    else:
                        state = "outcome_unknown"
                    return AndroidFeatureRequestAttempt(
                        state=state,
                        resource_id=str(row[1]) if row[1] is not None else None,
                        response_json=str(row[2]) if row[2] is not None else None,
                    )
                connection.execute(
                    """
                    INSERT INTO android_feature_requests (
                        owner_key, operation, request_key, payload_fingerprint,
                        resource_id, created_at_ms, updated_at_ms
                        , executor_instance_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._owner_key(owner),
                        operation,
                        request_key,
                        fingerprint,
                        resource_id,
                        now,
                        now,
                        self._instance_id,
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return AndroidFeatureRequestAttempt("created", resource_id, None)

    def commit_response(
        self,
        *,
        owner: FeatureOwner,
        operation: str,
        request_key: str,
        payload: Any,
        response_json: str,
    ) -> None:
        fingerprint = canonical_payload_fingerprint(payload)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE android_feature_requests
                SET response_json = ?, state = 'completed', updated_at_ms = ?
                WHERE owner_key = ? AND operation = ? AND request_key = ?
                  AND payload_fingerprint = ? AND state = 'in_progress'
                """,
                (
                    response_json,
                    int(time.time() * 1_000),
                    self._owner_key(owner),
                    operation,
                    request_key,
                    fingerprint,
                ),
            )
        if cursor.rowcount != 1:
            raise RuntimeError("android feature request disappeared before commit")

    def abort_request(
        self,
        *,
        owner: FeatureOwner,
        operation: str,
        request_key: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM android_feature_requests
                WHERE owner_key = ? AND operation = ? AND request_key = ?
                  AND response_json IS NULL
                """,
                (self._owner_key(owner), operation, request_key),
            )
