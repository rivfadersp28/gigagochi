from __future__ import annotations

import math
import sqlite3
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from app.schemas import GeneratePetJobResponse

SQLITE_BUSY_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class StoredGenerationJob:
    owner_id: int | str
    username: str | None
    first_name: str | None
    description: str
    response: GeneratePetJobResponse
    image_provider: str = "openai"
    owner_namespace: str = "telegram"
    notification_chat_id: int | None = None


@dataclass(frozen=True)
class GenerationJobCreateResult:
    created: bool
    job: StoredGenerationJob
    conflict: Literal["request_key", "owner_active", "capacity", "job_id"] | None = None


class GenerationOwnerDeletionBusyError(RuntimeError):
    pass


class GenerationJobStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_SECONDS * 1_000}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            self._enable_wal_with_retry(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    description TEXT NOT NULL,
                    image_provider TEXT NOT NULL DEFAULT 'openai',
                    request_key TEXT,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_until TEXT
                )
                """
            )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(generation_jobs)")
            }
            if "image_provider" not in columns:
                connection.execute(
                    "ALTER TABLE generation_jobs ADD COLUMN "
                    "image_provider TEXT NOT NULL DEFAULT 'openai'"
                )
            if "lease_owner" not in columns:
                connection.execute("ALTER TABLE generation_jobs ADD COLUMN lease_owner TEXT")
            if "lease_until" not in columns:
                connection.execute("ALTER TABLE generation_jobs ADD COLUMN lease_until TEXT")
            if "owner_namespace" not in columns:
                connection.execute(
                    "ALTER TABLE generation_jobs ADD COLUMN "
                    "owner_namespace TEXT NOT NULL DEFAULT 'telegram'"
                )
            if "notification_chat_id" not in columns:
                connection.execute(
                    "ALTER TABLE generation_jobs ADD COLUMN notification_chat_id INTEGER"
                )
                connection.execute(
                    "UPDATE generation_jobs SET notification_chat_id = owner_id "
                    "WHERE owner_namespace = 'telegram'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS generation_jobs_status_idx "
                "ON generation_jobs(status, updated_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_job_request_keys (
                    owner_id INTEGER NOT NULL,
                    request_key TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (owner_id, request_key),
                    FOREIGN KEY (job_id) REFERENCES generation_jobs(job_id) ON DELETE CASCADE
                ) WITHOUT ROWID
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS generation_job_request_keys_job_idx "
                "ON generation_job_request_keys(job_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_job_metrics (
                    job_id TEXT PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    queued_at TEXT NOT NULL,
                    images_started_at TEXT,
                    images_ready_at TEXT,
                    foreground_ready_at TEXT,
                    sad_ready_at TEXT,
                    completed_at TEXT,
                    failed_at TEXT,
                    status TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS generation_metrics_owner_time_idx "
                "ON generation_job_metrics(owner_id, queued_at)"
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
                error_code = exc.sqlite_errorcode
                if error_code is None or error_code & 0xFF not in {
                    sqlite3.SQLITE_BUSY,
                    sqlite3.SQLITE_LOCKED,
                }:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 0.1)

    def record_queued(self, job: StoredGenerationJob) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO generation_job_metrics (
                    job_id, owner_id, username, first_name, queued_at, status
                ) VALUES (?, ?, ?, ?, ?, 'queued')
                ON CONFLICT(job_id) DO NOTHING
                """,
                (
                    job.response.jobId,
                    job.owner_id,
                    job.username,
                    job.first_name,
                    job.response.createdAt.isoformat(),
                ),
            )

    def mark_metric(self, job_id: str, field: str, *, status: str | None = None) -> None:
        allowed_fields = {
            "images_started_at",
            "images_ready_at",
            "foreground_ready_at",
            "sad_ready_at",
            "completed_at",
            "failed_at",
        }
        if field not in allowed_fields:
            raise ValueError(f"Unsupported generation metric field: {field}")
        timestamp = datetime.now(UTC).isoformat()
        assignments = [f"{field} = COALESCE({field}, ?)"]
        values: list[object] = [timestamp]
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        values.append(job_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE generation_job_metrics SET {', '.join(assignments)} WHERE job_id = ?",
                values,
            )

    def metrics_summary(self, *, days: int, owner_id: int | None) -> dict[str, object]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        where = "queued_at >= ?"
        values: list[object] = [cutoff]
        if owner_id is not None:
            where += " AND owner_id = ?"
            values.append(owner_id)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT job_id, username, first_name, queued_at, foreground_ready_at,
                       completed_at, status
                FROM generation_job_metrics
                WHERE {where}
                ORDER BY queued_at DESC
                """,
                values,
            ).fetchall()

        normal_values = self._durations(rows, 3, 4)
        full_values = self._durations(rows, 3, 5)
        return {
            "windowDays": days,
            "totalJobs": len(rows),
            "activeJobs": sum(str(row[6]) in {"queued", "running"} for row in rows),
            "failedJobs": sum(str(row[6]) in {"failed", "completed_with_errors"} for row in rows),
            "normal": self._duration_summary(normal_values),
            "full": self._duration_summary(full_values),
            "recent": [
                {
                    "jobId": str(row[0]),
                    "ownerName": str(row[2] or row[1]) if row[2] or row[1] else None,
                    "queuedAt": str(row[3]),
                    "status": str(row[6]),
                    "normalSeconds": self._duration(row[3], row[4]),
                    "fullSeconds": self._duration(row[3], row[5]),
                }
                for row in rows[:10]
            ],
        }

    @classmethod
    def _durations(
        cls,
        rows: list[tuple[object, ...]],
        start_index: int,
        end_index: int,
    ) -> list[float]:
        return [
            duration
            for row in rows
            if (duration := cls._duration(row[start_index], row[end_index])) is not None
        ]

    @staticmethod
    def _duration(start: object, end: object) -> float | None:
        if not start or not end:
            return None
        seconds = (
            datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))
        ).total_seconds()
        return round(max(0.0, seconds), 3)

    @staticmethod
    def _duration_summary(values: list[float]) -> dict[str, float | int | None]:
        if not values:
            return {
                "count": 0,
                "averageSeconds": None,
                "medianSeconds": None,
                "p95Seconds": None,
                "minSeconds": None,
                "maxSeconds": None,
            }
        ordered = sorted(values)
        p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
        return {
            "count": len(ordered),
            "averageSeconds": round(statistics.fmean(ordered), 3),
            "medianSeconds": round(statistics.median(ordered), 3),
            "p95Seconds": round(ordered[p95_index], 3),
            "minSeconds": round(ordered[0], 3),
            "maxSeconds": round(ordered[-1], 3),
        }

    def save(self, job: StoredGenerationJob) -> None:
        response = job.response
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO generation_jobs (
                    job_id, owner_id, username, first_name, description,
                    image_provider, status, updated_at, response_json,
                    owner_namespace, notification_chat_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    username=excluded.username,
                    first_name=excluded.first_name,
                    description=excluded.description,
                    image_provider=excluded.image_provider,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    response_json=excluded.response_json,
                    owner_namespace=excluded.owner_namespace,
                    notification_chat_id=excluded.notification_chat_id
                """,
                (
                    response.jobId,
                    job.owner_id,
                    job.username,
                    job.first_name,
                    job.description,
                    job.image_provider,
                    response.status,
                    response.updatedAt.isoformat(),
                    response.model_dump_json(),
                    job.owner_namespace,
                    job.notification_chat_id,
                ),
            )

    def claim(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_until: datetime,
        now: datetime,
    ) -> bool:
        """Claim one active job when it is unowned or its previous lease expired."""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE generation_jobs
                SET lease_owner = ?, lease_until = ?
                WHERE job_id = ?
                  AND status IN ('queued', 'running')
                  AND (
                    lease_owner IS NULL OR lease_until IS NULL OR lease_until < ?
                    OR lease_owner = ?
                  )
                """,
                (
                    lease_owner,
                    lease_until.isoformat(),
                    job_id,
                    now.isoformat(),
                    lease_owner,
                ),
            )
        return cursor.rowcount == 1

    def save_claimed(
        self,
        job: StoredGenerationJob,
        *,
        lease_owner: str,
        lease_until: datetime,
    ) -> bool:
        """Persist only while this process still owns the durable lease."""

        response = job.response
        terminal = response.status in {"succeeded", "failed"}
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE generation_jobs
                SET owner_id = ?, username = ?, first_name = ?, description = ?,
                    image_provider = ?, status = ?, updated_at = ?, response_json = ?,
                    owner_namespace = ?, notification_chat_id = ?,
                    lease_owner = ?, lease_until = ?
                WHERE job_id = ? AND lease_owner = ?
                """,
                (
                    job.owner_id,
                    job.username,
                    job.first_name,
                    job.description,
                    job.image_provider,
                    response.status,
                    response.updatedAt.isoformat(),
                    response.model_dump_json(),
                    job.owner_namespace,
                    job.notification_chat_id,
                    None if terminal else lease_owner,
                    None if terminal else lease_until.isoformat(),
                    response.jobId,
                    lease_owner,
                ),
            )
        return cursor.rowcount == 1

    def renew_claims(
        self,
        job_ids: list[str],
        *,
        lease_owner: str,
        lease_until: datetime,
    ) -> set[str]:
        if not job_ids:
            return set()
        renewed: set[str] = set()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for job_id in job_ids:
                cursor = connection.execute(
                    """
                    UPDATE generation_jobs
                    SET lease_until = ?
                    WHERE job_id = ? AND lease_owner = ?
                      AND status IN ('queued', 'running')
                    """,
                    (lease_until.isoformat(), job_id, lease_owner),
                )
                if cursor.rowcount == 1:
                    renewed.add(job_id)
        return renewed

    def release_claims(self, *, lease_owner: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE generation_jobs
                SET lease_owner = NULL, lease_until = NULL
                WHERE lease_owner = ? AND status IN ('queued', 'running')
                """,
                (lease_owner,),
            )
        return cursor.rowcount

    def create_or_get(
        self,
        job: StoredGenerationJob,
        *,
        request_key: str | None,
        max_active_jobs: int,
    ) -> GenerationJobCreateResult:
        if max_active_jobs < 1:
            raise ValueError("max_active_jobs must be positive")
        response = job.response
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if request_key is not None:
                keyed = self._request_key_row(
                    connection,
                    owner_id=job.owner_id,
                    request_key=request_key,
                )
                if keyed is not None:
                    return GenerationJobCreateResult(
                        created=False,
                        job=self._row(keyed),
                        conflict="request_key",
                    )

            active_owner_row = connection.execute(
                """
                SELECT job_id, owner_id, username, first_name, description,
                       image_provider, response_json, owner_namespace, notification_chat_id
                FROM generation_jobs
                WHERE owner_id = ? AND status IN ('queued', 'running')
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (job.owner_id,),
            ).fetchone()
            if active_owner_row is not None:
                active_job = self._row(active_owner_row[1:])
                if (
                    request_key is not None
                    and job.description == active_job.description
                    and job.image_provider == active_job.image_provider
                ):
                    self._bind_request_key(
                        connection,
                        owner_id=job.owner_id,
                        request_key=request_key,
                        job_id=str(active_owner_row[0]),
                        created_at=response.createdAt,
                    )
                return GenerationJobCreateResult(
                    created=False,
                    job=active_job,
                    conflict="owner_active",
                )

            active_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM generation_jobs WHERE status IN ('queued', 'running')"
                ).fetchone()[0]
            )
            if active_count >= max_active_jobs:
                return GenerationJobCreateResult(
                    created=False,
                    job=job,
                    conflict="capacity",
                )

            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO generation_jobs (
                    job_id, owner_id, username, first_name, description,
                    image_provider, status, updated_at, response_json,
                    owner_namespace, notification_chat_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    response.jobId,
                    job.owner_id,
                    job.username,
                    job.first_name,
                    job.description,
                    job.image_provider,
                    response.status,
                    response.updatedAt.isoformat(),
                    response.model_dump_json(),
                    job.owner_namespace,
                    job.notification_chat_id,
                ),
            )
            if cursor.rowcount == 1:
                if request_key is not None:
                    self._bind_request_key(
                        connection,
                        owner_id=job.owner_id,
                        request_key=request_key,
                        job_id=response.jobId,
                        created_at=response.createdAt,
                    )
                return GenerationJobCreateResult(created=True, job=job)
            existing = connection.execute(
                """
                SELECT owner_id, username, first_name, description,
                       image_provider, response_json, owner_namespace, notification_chat_id
                FROM generation_jobs WHERE job_id = ?
                """,
                (response.jobId,),
            ).fetchone()
            if existing is None:
                raise RuntimeError("generation job insert was ignored without a matching job")
            return GenerationJobCreateResult(
                created=False,
                job=self._row(existing),
                conflict="job_id",
            )

    @staticmethod
    def _bind_request_key(
        connection: sqlite3.Connection,
        *,
        owner_id: int | str,
        request_key: str,
        job_id: str,
        created_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO generation_job_request_keys (
                owner_id, request_key, job_id, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (owner_id, request_key, job_id, created_at.isoformat()),
        )

    def get(self, job_id: str) -> StoredGenerationJob | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT owner_id, username, first_name, description,
                       image_provider, response_json, owner_namespace, notification_chat_id
                FROM generation_jobs WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        return self._row(row) if row is not None else None

    def get_by_request_key(
        self, owner_id: int | str, request_key: str
    ) -> StoredGenerationJob | None:
        with self._connect() as connection:
            row = self._request_key_row(
                connection,
                owner_id=owner_id,
                request_key=request_key,
            )
        return self._row(row) if row is not None else None

    def request_keys_for_job(self, job_id: str, *, limit: int = 8) -> list[str]:
        if limit not in range(1, 33):
            raise ValueError("request key lookup limit must be in 1..32")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT request_key
                FROM generation_job_request_keys
                WHERE job_id = ?
                ORDER BY created_at ASC, request_key ASC
                LIMIT ?
                """,
                (job_id, limit),
            ).fetchall()
        return [str(row[0]) for row in rows]

    @staticmethod
    def _request_key_row(
        connection: sqlite3.Connection,
        *,
        owner_id: int | str,
        request_key: str,
    ) -> tuple[object, ...] | None:
        return connection.execute(
            """
            SELECT jobs.owner_id, jobs.username, jobs.first_name,
                   jobs.description, jobs.image_provider, jobs.response_json,
                   jobs.owner_namespace, jobs.notification_chat_id
            FROM generation_job_request_keys AS keys
            JOIN generation_jobs AS jobs
              ON jobs.job_id = keys.job_id AND jobs.owner_id = keys.owner_id
            WHERE keys.owner_id = ? AND keys.request_key = ?
            """,
            (owner_id, request_key),
        ).fetchone()

    def active(self) -> list[StoredGenerationJob]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT owner_id, username, first_name, description,
                       image_provider, response_json, owner_namespace, notification_chat_id
                FROM generation_jobs
                WHERE status IN ('queued', 'running')
                ORDER BY updated_at ASC
                """
            ).fetchall()
        return [self._row(row) for row in rows]

    def delete_terminal_older_than(
        self,
        cutoff: datetime,
        *,
        request_key_cutoff: datetime | None = None,
    ) -> list[StoredGenerationJob]:
        keyed_cutoff = request_key_cutoff or cutoff
        predicate = """
            status IN ('succeeded', 'failed')
            AND (
                (
                    NOT EXISTS (
                        SELECT 1 FROM generation_job_request_keys AS keys
                        WHERE keys.job_id = generation_jobs.job_id
                    )
                    AND updated_at < ?
                )
                OR (
                    EXISTS (
                        SELECT 1 FROM generation_job_request_keys AS keys
                        WHERE keys.job_id = generation_jobs.job_id
                    )
                    AND updated_at < ?
                )
            )
        """
        values = (cutoff.isoformat(), keyed_cutoff.isoformat())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"""
                SELECT owner_id, username, first_name, description,
                       image_provider, response_json, owner_namespace, notification_chat_id
                FROM generation_jobs
                WHERE {predicate}
                """,
                values,
            ).fetchall()
            connection.execute(
                f"DELETE FROM generation_jobs WHERE {predicate}",
                values,
            )
        return [self._row(row) for row in rows]

    def delete_metrics_older_than(self, cutoff: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM generation_job_metrics WHERE queued_at < ?",
                (cutoff.isoformat(),),
            )

    def delete_owner(self, owner_id: int | str) -> tuple[StoredGenerationJob, ...]:
        """Atomically refuse active work or remove every durable owner job row."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                active = connection.execute(
                    """
                    SELECT 1 FROM generation_jobs
                    WHERE owner_id = ? AND status IN ('queued', 'running')
                    LIMIT 1
                    """,
                    (owner_id,),
                ).fetchone()
                if active is not None:
                    raise GenerationOwnerDeletionBusyError(str(owner_id))
                rows = connection.execute(
                    """
                    SELECT owner_id, username, first_name, description,
                           image_provider, response_json, owner_namespace,
                           notification_chat_id
                    FROM generation_jobs WHERE owner_id = ?
                    """,
                    (owner_id,),
                ).fetchall()
                connection.execute(
                    "DELETE FROM generation_job_metrics WHERE owner_id = ?",
                    (owner_id,),
                )
                connection.execute(
                    "DELETE FROM generation_jobs WHERE owner_id = ?",
                    (owner_id,),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return tuple(self._row(row) for row in rows)

    def owner_jobs_for_deletion(self, owner_id: int | str) -> tuple[StoredGenerationJob, ...]:
        with self._connect() as connection:
            active = connection.execute(
                """
                SELECT 1 FROM generation_jobs
                WHERE owner_id = ? AND status IN ('queued', 'running')
                LIMIT 1
                """,
                (owner_id,),
            ).fetchone()
            if active is not None:
                raise GenerationOwnerDeletionBusyError(str(owner_id))
            rows = connection.execute(
                """
                SELECT owner_id, username, first_name, description,
                       image_provider, response_json, owner_namespace,
                       notification_chat_id
                FROM generation_jobs WHERE owner_id = ?
                """,
                (owner_id,),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: tuple[object, ...]) -> StoredGenerationJob:
        return StoredGenerationJob(
            owner_id=row[0] if isinstance(row[0], int) else str(row[0]),
            username=str(row[1]) if row[1] is not None else None,
            first_name=str(row[2]) if row[2] is not None else None,
            description=str(row[3]),
            image_provider=str(row[4] or "openai"),
            response=GeneratePetJobResponse.model_validate_json(str(row[5])),
            owner_namespace=str(row[6] or "telegram"),
            notification_chat_id=(
                int(row[7])
                if str(row[6] or "telegram") == "telegram" and row[7] is not None
                else None
            ),
        )
