from __future__ import annotations

import math
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.schemas import GeneratePetJobResponse


@dataclass(frozen=True)
class StoredGenerationJob:
    owner_id: int
    username: str | None
    first_name: str | None
    description: str
    response: GeneratePetJobResponse


class GenerationJobStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS generation_jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_id INTEGER NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    response_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS generation_jobs_status_idx "
                "ON generation_jobs(status, updated_at)"
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
            "failedJobs": sum(
                str(row[6]) in {"failed", "completed_with_errors"} for row in rows
            ),
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
    def empty_metrics_summary(cls, *, days: int) -> dict[str, object]:
        return {
            "windowDays": days,
            "totalJobs": 0,
            "activeJobs": 0,
            "failedJobs": 0,
            "normal": cls._duration_summary([]),
            "full": cls._duration_summary([]),
            "recent": [],
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
                    status, updated_at, response_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    username=excluded.username,
                    first_name=excluded.first_name,
                    description=excluded.description,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    response_json=excluded.response_json
                """,
                (
                    response.jobId,
                    job.owner_id,
                    job.username,
                    job.first_name,
                    job.description,
                    response.status,
                    response.updatedAt.isoformat(),
                    response.model_dump_json(),
                ),
            )

    def get(self, job_id: str) -> StoredGenerationJob | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT owner_id, username, first_name, description, response_json
                FROM generation_jobs WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        return self._row(row) if row is not None else None

    def active(self) -> list[StoredGenerationJob]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT owner_id, username, first_name, description, response_json
                FROM generation_jobs
                WHERE status IN ('queued', 'running')
                ORDER BY updated_at ASC
                """
            ).fetchall()
        return [self._row(row) for row in rows]

    def delete_older_than(self, cutoff: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM generation_jobs WHERE updated_at < ?",
                (cutoff.isoformat(),),
            )

    @staticmethod
    def _row(row: tuple[object, ...]) -> StoredGenerationJob:
        return StoredGenerationJob(
            owner_id=int(row[0]),
            username=str(row[1]) if row[1] is not None else None,
            first_name=str(row[2]) if row[2] is not None else None,
            description=str(row[3]),
            response=GeneratePetJobResponse.model_validate_json(str(row[4])),
        )
