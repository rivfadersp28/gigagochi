from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
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
