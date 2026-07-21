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


@dataclass(frozen=True, slots=True)
class AndroidScheduledStoryRecord:
    owner_key: str
    pet_id: str
    slot_utc: str
    story_id: str
    state: Literal["generating", "ready", "outcome_unknown"]
    episode_json: str | None
    selected_request_key: str | None
    selected_choice: str | None
    result_json: str | None
    created_at: str | None


@dataclass(frozen=True, slots=True)
class AndroidScheduledStoryClaim:
    state: Literal["created", "in_progress", "outcome_unknown", "ready"]
    record: AndroidScheduledStoryRecord


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
                        "ALTER TABLE android_feature_requests ADD COLUMN executor_instance_id TEXT"
                    )
                    connection.execute(
                        "UPDATE android_feature_requests SET state = 'completed' "
                        "WHERE response_json IS NOT NULL"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS android_scheduled_stories (
                        owner_key TEXT NOT NULL,
                        pet_id TEXT NOT NULL,
                        slot_utc TEXT NOT NULL,
                        story_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        episode_json TEXT,
                        selected_request_key TEXT,
                        selected_choice TEXT,
                        result_json TEXT,
                        created_at TEXT,
                        updated_at_ms INTEGER NOT NULL,
                        PRIMARY KEY (owner_key, pet_id, slot_utc),
                        UNIQUE (owner_key, story_id)
                    ) WITHOUT ROWID
                    """
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
                    state: Literal["created", "in_progress", "outcome_unknown", "completed"]
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

    def claim_scheduled_story(
        self,
        *,
        owner: FeatureOwner,
        pet_id: str,
        slot_utc: str,
        story_id: str,
    ) -> AndroidScheduledStoryClaim:
        now = int(time.time() * 1_000)
        owner_key = self._owner_key(owner)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT owner_key, pet_id, slot_utc, story_id, state, episode_json,
                           selected_request_key, selected_choice, result_json, created_at,
                           updated_at_ms
                    FROM android_scheduled_stories
                    WHERE owner_key = ? AND pet_id = ? AND slot_utc = ?
                    """,
                    (owner_key, pet_id, slot_utc),
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO android_scheduled_stories (
                            owner_key, pet_id, slot_utc, story_id, state, updated_at_ms
                        ) VALUES (?, ?, ?, ?, 'generating', ?)
                        """,
                        (owner_key, pet_id, slot_utc, story_id, now),
                    )
                    row = (
                        owner_key,
                        pet_id,
                        slot_utc,
                        story_id,
                        "generating",
                        None,
                        None,
                        None,
                        None,
                        None,
                        now,
                    )
                    claim_state: Literal["created", "in_progress", "outcome_unknown", "ready"] = (
                        "created"
                    )
                elif str(row[4]) == "ready" and row[5] is not None:
                    claim_state = "ready"
                elif (
                    str(row[4]) == "outcome_unknown"
                    or now - int(row[10]) > REQUEST_IN_PROGRESS_MAX_AGE_MS
                ):
                    claim_state = "outcome_unknown"
                else:
                    claim_state = "in_progress"
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return AndroidScheduledStoryClaim(claim_state, self._story_record(row))

    def commit_scheduled_story(
        self,
        *,
        owner: FeatureOwner,
        pet_id: str,
        slot_utc: str,
        story_id: str,
        episode_json: str,
        created_at: str,
    ) -> AndroidScheduledStoryRecord:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE android_scheduled_stories
                SET state = 'ready', episode_json = ?, created_at = ?, updated_at_ms = ?
                WHERE owner_key = ? AND pet_id = ? AND slot_utc = ?
                  AND story_id = ? AND state = 'generating' AND episode_json IS NULL
                """,
                (
                    episode_json,
                    created_at,
                    int(time.time() * 1_000),
                    self._owner_key(owner),
                    pet_id,
                    slot_utc,
                    story_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("scheduled story claim disappeared before commit")
        return self.read_scheduled_story(owner=owner, story_id=story_id)

    def mark_scheduled_story_outcome_unknown(
        self,
        *,
        owner: FeatureOwner,
        pet_id: str,
        slot_utc: str,
        story_id: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE android_scheduled_stories
                SET state = 'outcome_unknown', updated_at_ms = ?
                WHERE owner_key = ? AND pet_id = ? AND slot_utc = ? AND story_id = ?
                  AND state = 'generating'
                """,
                (
                    int(time.time() * 1_000),
                    self._owner_key(owner),
                    pet_id,
                    slot_utc,
                    story_id,
                ),
            )

    def read_scheduled_story(
        self,
        *,
        owner: FeatureOwner,
        story_id: str,
    ) -> AndroidScheduledStoryRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT owner_key, pet_id, slot_utc, story_id, state, episode_json,
                       selected_request_key, selected_choice, result_json, created_at,
                       updated_at_ms
                FROM android_scheduled_stories
                WHERE owner_key = ? AND story_id = ?
                """,
                (self._owner_key(owner), story_id),
            ).fetchone()
        if row is None:
            raise KeyError(story_id)
        return self._story_record(row)

    def choose_scheduled_story(
        self,
        *,
        owner: FeatureOwner,
        story_id: str,
        request_key: str,
        selected_choice: str,
        result_json: str,
    ) -> AndroidScheduledStoryRecord:
        owner_key = self._owner_key(owner)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT owner_key, pet_id, slot_utc, story_id, state, episode_json,
                           selected_request_key, selected_choice, result_json, created_at,
                           updated_at_ms
                    FROM android_scheduled_stories
                    WHERE owner_key = ? AND story_id = ?
                    """,
                    (owner_key, story_id),
                ).fetchone()
                if row is None or str(row[4]) != "ready" or row[5] is None:
                    raise KeyError(story_id)
                if row[7] is not None:
                    if str(row[7]) != selected_choice:
                        raise AndroidFeatureIdempotencyConflictError(story_id)
                    connection.commit()
                    return self._story_record(row)
                connection.execute(
                    """
                    UPDATE android_scheduled_stories
                    SET selected_request_key = ?, selected_choice = ?, result_json = ?,
                        updated_at_ms = ?
                    WHERE owner_key = ? AND story_id = ? AND selected_choice IS NULL
                    """,
                    (
                        request_key,
                        selected_choice,
                        result_json,
                        int(time.time() * 1_000),
                        owner_key,
                        story_id,
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.read_scheduled_story(owner=owner, story_id=story_id)

    @staticmethod
    def _story_record(row: sqlite3.Row | tuple[Any, ...]) -> AndroidScheduledStoryRecord:
        return AndroidScheduledStoryRecord(
            owner_key=str(row[0]),
            pet_id=str(row[1]),
            slot_utc=str(row[2]),
            story_id=str(row[3]),
            state=str(row[4]),  # type: ignore[arg-type]
            episode_json=str(row[5]) if row[5] is not None else None,
            selected_request_key=str(row[6]) if row[6] is not None else None,
            selected_choice=str(row[7]) if row[7] is not None else None,
            result_json=str(row[8]) if row[8] is not None else None,
            created_at=str(row[9]) if row[9] is not None else None,
        )
