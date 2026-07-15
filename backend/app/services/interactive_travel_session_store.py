from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app.schemas import InteractiveTravelResponse, InteractiveTravelState

DEFAULT_INTERACTIVE_TRAVEL_STORE_PATH = (
    "static/generated/.private/interactive_travel_owners.sqlite3"
)
DEFAULT_INTERACTIVE_TRAVEL_RETENTION = timedelta(days=180)
DEFAULT_INTERACTIVE_TRAVEL_MAX_RECORDS = 100_000
SQLITE_BUSY_TIMEOUT_SECONDS = 30


class InteractiveTravelSessionError(RuntimeError):
    pass


class InteractiveTravelActiveError(InteractiveTravelSessionError):
    def __init__(self, travel_id: str) -> None:
        super().__init__(travel_id)
        self.travel_id = travel_id


class InteractiveTravelOwnerMissingError(InteractiveTravelSessionError):
    pass


class InteractiveTravelSessionOwnerMismatchError(InteractiveTravelSessionError):
    pass


class InteractiveTravelPetMismatchError(InteractiveTravelSessionError):
    pass


class InteractiveTravelSessionCancelledError(InteractiveTravelSessionError):
    pass


class InteractiveTravelSessionCompletedError(InteractiveTravelSessionError):
    pass


class InteractiveTravelStateConflictError(InteractiveTravelSessionError):
    pass


class InteractiveTravelSessionCapacityError(InteractiveTravelSessionError):
    pass


@dataclass(frozen=True, slots=True)
class InteractiveTravelOwner:
    travel_id: str
    telegram_id: int
    created_at: datetime
    cancelled_at: datetime | None


@dataclass(frozen=True, slots=True)
class InteractiveTravelStartAttempt:
    travel_id: str
    telegram_id: int
    pet_fingerprint: str
    request_fingerprint: str
    replay: InteractiveTravelResponse | None = None


@dataclass(frozen=True, slots=True)
class InteractiveTravelContinueAttempt:
    travel_id: str
    telegram_id: int
    pet_fingerprint: str
    base_fingerprint: str
    request_fingerprint: str
    replay: InteractiveTravelResponse | None = None


@dataclass(frozen=True, slots=True)
class InteractiveTravelCommitResult:
    response: InteractiveTravelResponse
    committed: bool


@dataclass(frozen=True, slots=True)
class InteractiveTravelSession:
    travel_id: str
    telegram_id: int
    pet_fingerprint: str
    revision: int
    state_fingerprint: str
    completed: bool


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint_payload(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _state_payload_without_media(travel: InteractiveTravelState) -> dict[str, Any]:
    payload = travel.model_dump(mode="json")
    for part in payload["parts"]:
        part.pop("backgroundImageUrl", None)
        part.pop("backgroundVideoUrl", None)
    return payload


def interactive_travel_state_fingerprint(travel: InteractiveTravelState) -> str:
    """Fingerprint narrative state while allowing server-produced media to arrive late."""

    return fingerprint_payload(_state_payload_without_media(travel))


def _response_json(response: InteractiveTravelResponse) -> str:
    return _canonical_json(response.model_dump(mode="json", exclude_none=True))


def _parse_response(value: object) -> InteractiveTravelResponse:
    if not isinstance(value, str) or not value:
        raise InteractiveTravelStateConflictError("interactive travel replay is missing")
    try:
        return InteractiveTravelResponse.model_validate_json(value)
    except (TypeError, ValueError):
        raise InteractiveTravelStateConflictError("interactive travel replay is invalid") from None


def _parse_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class InteractiveTravelSessionStore:
    """Owner proof and narrative CAS in one SQLite database with two tables."""

    def __init__(
        self,
        path: str | Path,
        *,
        retention: timedelta = DEFAULT_INTERACTIVE_TRAVEL_RETENTION,
        max_records: int = DEFAULT_INTERACTIVE_TRAVEL_MAX_RECORDS,
    ) -> None:
        if retention.total_seconds() <= 0:
            raise ValueError("retention must be positive")
        if max_records <= 0:
            raise ValueError("max_records must be positive")
        self.path = Path(path).expanduser().resolve()
        self.retention = retention
        self.max_records = max_records
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=SQLITE_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_SECONDS * 1_000}")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

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

    @staticmethod
    def _create_session_table(connection: sqlite3.Connection, table: str) -> None:
        if table not in {"interactive_travel_sessions", "interactive_travel_sessions_v2"}:
            raise ValueError("invalid interactive travel session table")
        connection.execute(
            f"""
            CREATE TABLE {table} (
                travel_id TEXT PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                pet_fingerprint TEXT NOT NULL,
                start_fingerprint TEXT NOT NULL,
                state_json TEXT NOT NULL,
                state_fingerprint TEXT NOT NULL,
                response_json TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
                last_request_fingerprint TEXT,
                last_base_fingerprint TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    @classmethod
    def _migrate_leased_sessions(cls, connection: sqlite3.Connection) -> None:
        connection.execute("DROP TABLE IF EXISTS interactive_travel_sessions_v2")
        cls._create_session_table(connection, "interactive_travel_sessions_v2")
        connection.execute(
            """
            INSERT OR IGNORE INTO interactive_travel_sessions_v2 (
                travel_id, telegram_id, pet_fingerprint, start_fingerprint,
                state_json, state_fingerprint, response_json, revision,
                last_request_fingerprint, last_base_fingerprint, completed_at,
                created_at, updated_at
            )
            SELECT travel_id, telegram_id, pet_fingerprint, start_fingerprint,
                   state_json, state_fingerprint, response_json, revision,
                   last_operation_fingerprint, last_operation_base_fingerprint,
                   CASE WHEN status = 'completed' THEN updated_at ELSE NULL END,
                   created_at, updated_at
            FROM interactive_travel_sessions
            WHERE status IN ('active', 'completed')
                  AND state_json IS NOT NULL
                  AND state_fingerprint IS NOT NULL
                  AND response_json IS NOT NULL
            """
        )
        connection.execute("DROP TABLE interactive_travel_sessions")
        connection.execute(
            "ALTER TABLE interactive_travel_sessions_v2 RENAME TO interactive_travel_sessions"
        )

    def _initialize(self) -> None:
        with self._connect() as connection:
            self._enable_wal_with_retry(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS interactive_travel_owners (
                        travel_id TEXT PRIMARY KEY,
                        telegram_id INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        cancelled_at TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS interactive_travel_owners_owner_idx
                    ON interactive_travel_owners (telegram_id, created_at)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS interactive_travel_owners_created_idx
                    ON interactive_travel_owners (created_at)
                    """
                )
                session_table = connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'interactive_travel_sessions'
                    """
                ).fetchone()
                if session_table is None:
                    self._create_session_table(connection, "interactive_travel_sessions")
                else:
                    columns = {
                        str(row[1])
                        for row in connection.execute(
                            "PRAGMA table_info(interactive_travel_sessions)"
                        )
                    }
                    if "status" in columns:
                        self._migrate_leased_sessions(connection)
                    elif "completed_at" not in columns:
                        raise RuntimeError("unsupported interactive travel session schema")
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS interactive_travel_sessions_active_owner_idx
                    ON interactive_travel_sessions (telegram_id)
                    WHERE completed_at IS NULL
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS interactive_travel_sessions_updated_idx
                    ON interactive_travel_sessions (updated_at)
                    """
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _prune(self, connection: sqlite3.Connection, now: datetime) -> None:
        cutoff = (now - self.retention).isoformat()
        connection.execute(
            """
            DELETE FROM interactive_travel_sessions
            WHERE completed_at IS NOT NULL AND completed_at < ?
            """,
            (cutoff,),
        )
        connection.execute(
            """
            DELETE FROM interactive_travel_owners
            WHERE cancelled_at IS NOT NULL AND cancelled_at < ?
            """,
            (cutoff,),
        )

    def _ensure_owner_capacity(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT COUNT(*) FROM interactive_travel_owners").fetchone()
        if row is None or int(row[0]) >= self.max_records:
            raise InteractiveTravelSessionCapacityError("interactive travel owner registry is full")

    def _ensure_session_capacity(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT COUNT(*) FROM interactive_travel_sessions").fetchone()
        if row is None or int(row[0]) >= self.max_records:
            raise InteractiveTravelSessionCapacityError(
                "interactive travel session registry is full"
            )

    @staticmethod
    def _active_for_owner(
        connection: sqlite3.Connection,
        telegram_id: int,
    ) -> tuple[object, ...] | None:
        return connection.execute(
            """
            SELECT travel_id, pet_fingerprint, start_fingerprint, response_json
            FROM interactive_travel_sessions
            WHERE telegram_id = ? AND completed_at IS NULL
            LIMIT 1
            """,
            (telegram_id,),
        ).fetchone()

    @staticmethod
    def _owner_row(
        connection: sqlite3.Connection,
        travel_id: str,
    ) -> tuple[object, ...] | None:
        return connection.execute(
            """
            SELECT travel_id, telegram_id, created_at, cancelled_at
            FROM interactive_travel_owners
            WHERE travel_id = ?
            """,
            (travel_id,),
        ).fetchone()

    @staticmethod
    def _row_to_owner(row: tuple[object, ...]) -> InteractiveTravelOwner:
        return InteractiveTravelOwner(
            travel_id=str(row[0]),
            telegram_id=int(row[1]),
            created_at=_parse_datetime(row[2]),
            cancelled_at=_parse_datetime(row[3]) if row[3] is not None else None,
        )

    @classmethod
    def _assert_active_owner_row(
        cls,
        row: tuple[object, ...] | None,
        travel_id: str,
        telegram_id: int,
    ) -> InteractiveTravelOwner:
        if row is None:
            raise InteractiveTravelOwnerMissingError(travel_id)
        owner = cls._row_to_owner(row)
        if owner.telegram_id != telegram_id:
            raise InteractiveTravelSessionOwnerMismatchError(travel_id)
        if owner.cancelled_at is not None:
            raise InteractiveTravelSessionCancelledError(travel_id)
        return owner

    def get_owner(self, travel_id: str) -> InteractiveTravelOwner | None:
        with self._connect() as connection:
            row = self._owner_row(connection, travel_id)
        return self._row_to_owner(row) if row is not None else None

    def register_owner(self, travel_id: str, telegram_id: int) -> InteractiveTravelOwner:
        now_datetime = datetime.now(UTC)
        now = now_datetime.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._prune(connection, now_datetime)
                row = self._owner_row(connection, travel_id)
                if row is None:
                    self._ensure_owner_capacity(connection)
                    connection.execute(
                        """
                        INSERT INTO interactive_travel_owners (
                            travel_id, telegram_id, created_at, cancelled_at
                        ) VALUES (?, ?, ?, NULL)
                        """,
                        (travel_id, telegram_id, now),
                    )
                    row = self._owner_row(connection, travel_id)
                owner = self._assert_active_owner_row(row, travel_id, telegram_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return owner

    def assert_active_owner(self, travel_id: str, telegram_id: int) -> None:
        with self._connect() as connection:
            row = self._owner_row(connection, travel_id)
        self._assert_active_owner_row(row, travel_id, telegram_id)

    def preflight_start(
        self,
        *,
        telegram_id: int,
        pet_fingerprint: str,
        request_fingerprint: str,
    ) -> InteractiveTravelStartAttempt:
        now_datetime = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._prune(connection, now_datetime)
                active = self._active_for_owner(connection, telegram_id)
                if active is not None:
                    travel_id = str(active[0])
                    if str(active[1]) != pet_fingerprint or str(active[2]) != request_fingerprint:
                        raise InteractiveTravelActiveError(travel_id)
                    replay = _parse_response(active[3])
                    attempt = InteractiveTravelStartAttempt(
                        travel_id,
                        telegram_id,
                        pet_fingerprint,
                        request_fingerprint,
                        replay,
                    )
                else:
                    self._ensure_owner_capacity(connection)
                    self._ensure_session_capacity(connection)
                    attempt = InteractiveTravelStartAttempt(
                        f"interactive-travel-{uuid.uuid4().hex}",
                        telegram_id,
                        pet_fingerprint,
                        request_fingerprint,
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return attempt

    def commit_start(
        self,
        attempt: InteractiveTravelStartAttempt,
        response: InteractiveTravelResponse,
    ) -> InteractiveTravelCommitResult:
        if attempt.replay is not None:
            raise ValueError("start replay cannot be committed")
        if response.travel.travelId != attempt.travel_id:
            raise ValueError("interactive travel start returned a different id")
        if response.travel.completed:
            raise ValueError("interactive travel start cannot be completed")
        state_json = _canonical_json(response.travel.model_dump(mode="json"))
        state_fingerprint = interactive_travel_state_fingerprint(response.travel)
        response_json = _response_json(response)
        now_datetime = datetime.now(UTC)
        now = now_datetime.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._prune(connection, now_datetime)
                active = self._active_for_owner(connection, attempt.telegram_id)
                if active is not None:
                    travel_id = str(active[0])
                    if (
                        str(active[1]) != attempt.pet_fingerprint
                        or str(active[2]) != attempt.request_fingerprint
                    ):
                        raise InteractiveTravelActiveError(travel_id)
                    result = InteractiveTravelCommitResult(
                        _parse_response(active[3]),
                        committed=False,
                    )
                    connection.commit()
                    return result
                if self._owner_row(connection, attempt.travel_id) is not None:
                    raise InteractiveTravelStateConflictError(attempt.travel_id)
                self._ensure_owner_capacity(connection)
                self._ensure_session_capacity(connection)
                connection.execute(
                    """
                    INSERT INTO interactive_travel_owners (
                        travel_id, telegram_id, created_at, cancelled_at
                    ) VALUES (?, ?, ?, NULL)
                    """,
                    (attempt.travel_id, attempt.telegram_id, now),
                )
                connection.execute(
                    """
                    INSERT INTO interactive_travel_sessions (
                        travel_id, telegram_id, pet_fingerprint, start_fingerprint,
                        state_json, state_fingerprint, response_json, revision,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        attempt.travel_id,
                        attempt.telegram_id,
                        attempt.pet_fingerprint,
                        attempt.request_fingerprint,
                        state_json,
                        state_fingerprint,
                        response_json,
                        now,
                        now,
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return InteractiveTravelCommitResult(response, committed=True)

    @staticmethod
    def _session_row(
        connection: sqlite3.Connection,
        travel_id: str,
    ) -> tuple[object, ...] | None:
        return connection.execute(
            """
            SELECT telegram_id, pet_fingerprint, start_fingerprint,
                   state_fingerprint, response_json, revision,
                   last_request_fingerprint, last_base_fingerprint, completed_at
            FROM interactive_travel_sessions
            WHERE travel_id = ?
            """,
            (travel_id,),
        ).fetchone()

    @staticmethod
    def _replay_if_matching_request(
        row: tuple[object, ...],
        *,
        base_fingerprint: str,
        request_fingerprint: str,
    ) -> InteractiveTravelResponse | None:
        if (
            row[6] is not None
            and str(row[6]) == request_fingerprint
            and row[7] is not None
            and str(row[7]) == base_fingerprint
        ):
            return _parse_response(row[4])
        return None

    def preflight_continue(
        self,
        *,
        telegram_id: int,
        pet_fingerprint: str,
        travel: InteractiveTravelState,
        request_fingerprint: str,
    ) -> InteractiveTravelContinueAttempt:
        travel_id = travel.travelId
        base_fingerprint = interactive_travel_state_fingerprint(travel)
        now_datetime = datetime.now(UTC)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._prune(connection, now_datetime)
                self._assert_active_owner_row(
                    self._owner_row(connection, travel_id),
                    travel_id,
                    telegram_id,
                )
                row = self._session_row(connection, travel_id)
                if row is None:
                    active = self._active_for_owner(connection, telegram_id)
                    if active is not None and str(active[0]) != travel_id:
                        raise InteractiveTravelActiveError(str(active[0]))
                    self._ensure_session_capacity(connection)
                    attempt = InteractiveTravelContinueAttempt(
                        travel_id,
                        telegram_id,
                        pet_fingerprint,
                        base_fingerprint,
                        request_fingerprint,
                    )
                else:
                    if int(row[0]) != telegram_id:
                        raise InteractiveTravelSessionOwnerMismatchError(travel_id)
                    if str(row[1]) != pet_fingerprint:
                        raise InteractiveTravelPetMismatchError(travel_id)
                    stored_fingerprint = str(row[3])
                    if stored_fingerprint != base_fingerprint:
                        replay = self._replay_if_matching_request(
                            row,
                            base_fingerprint=base_fingerprint,
                            request_fingerprint=request_fingerprint,
                        )
                        if replay is None:
                            raise InteractiveTravelStateConflictError(travel_id)
                        attempt = InteractiveTravelContinueAttempt(
                            travel_id,
                            telegram_id,
                            pet_fingerprint,
                            base_fingerprint,
                            request_fingerprint,
                            replay,
                        )
                    elif row[8] is not None:
                        raise InteractiveTravelSessionCompletedError(travel_id)
                    else:
                        attempt = InteractiveTravelContinueAttempt(
                            travel_id,
                            telegram_id,
                            pet_fingerprint,
                            base_fingerprint,
                            request_fingerprint,
                        )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return attempt

    def commit_continue(
        self,
        attempt: InteractiveTravelContinueAttempt,
        response: InteractiveTravelResponse,
    ) -> InteractiveTravelCommitResult:
        if attempt.replay is not None:
            raise ValueError("continue replay cannot be committed")
        if response.travel.travelId != attempt.travel_id:
            raise ValueError("interactive travel continue returned a different id")
        state_json = _canonical_json(response.travel.model_dump(mode="json"))
        state_fingerprint = interactive_travel_state_fingerprint(response.travel)
        response_json = _response_json(response)
        now_datetime = datetime.now(UTC)
        now = now_datetime.isoformat()
        completed_at = now if response.travel.completed else None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._prune(connection, now_datetime)
                self._assert_active_owner_row(
                    self._owner_row(connection, attempt.travel_id),
                    attempt.travel_id,
                    attempt.telegram_id,
                )
                row = self._session_row(connection, attempt.travel_id)
                if row is None:
                    active = self._active_for_owner(connection, attempt.telegram_id)
                    if active is not None and str(active[0]) != attempt.travel_id:
                        raise InteractiveTravelActiveError(str(active[0]))
                    self._ensure_session_capacity(connection)
                    connection.execute(
                        """
                        INSERT INTO interactive_travel_sessions (
                            travel_id, telegram_id, pet_fingerprint, start_fingerprint,
                            state_json, state_fingerprint, response_json, revision,
                            last_request_fingerprint, last_base_fingerprint,
                            completed_at, created_at, updated_at
                        ) VALUES (?, ?, ?, '', ?, ?, ?, 1, ?, ?, ?, ?, ?)
                        """,
                        (
                            attempt.travel_id,
                            attempt.telegram_id,
                            attempt.pet_fingerprint,
                            state_json,
                            state_fingerprint,
                            response_json,
                            attempt.request_fingerprint,
                            attempt.base_fingerprint,
                            completed_at,
                            now,
                            now,
                        ),
                    )
                else:
                    if int(row[0]) != attempt.telegram_id:
                        raise InteractiveTravelSessionOwnerMismatchError(attempt.travel_id)
                    if str(row[1]) != attempt.pet_fingerprint:
                        raise InteractiveTravelPetMismatchError(attempt.travel_id)
                    if str(row[3]) != attempt.base_fingerprint:
                        replay = self._replay_if_matching_request(
                            row,
                            base_fingerprint=attempt.base_fingerprint,
                            request_fingerprint=attempt.request_fingerprint,
                        )
                        if replay is None:
                            raise InteractiveTravelStateConflictError(attempt.travel_id)
                        connection.commit()
                        return InteractiveTravelCommitResult(replay, committed=False)
                    if row[8] is not None:
                        raise InteractiveTravelSessionCompletedError(attempt.travel_id)
                    connection.execute(
                        """
                        UPDATE interactive_travel_sessions
                        SET state_json = ?, state_fingerprint = ?, response_json = ?,
                            revision = revision + 1,
                            last_request_fingerprint = ?, last_base_fingerprint = ?,
                            completed_at = ?, updated_at = ?
                        WHERE travel_id = ? AND state_fingerprint = ?
                              AND completed_at IS NULL
                        """,
                        (
                            state_json,
                            state_fingerprint,
                            response_json,
                            attempt.request_fingerprint,
                            attempt.base_fingerprint,
                            completed_at,
                            now,
                            attempt.travel_id,
                            attempt.base_fingerprint,
                        ),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return InteractiveTravelCommitResult(response, committed=True)

    def cancel(self, travel_id: str, telegram_id: int) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._owner_row(connection, travel_id)
                if row is None:
                    raise InteractiveTravelOwnerMissingError(travel_id)
                owner = self._row_to_owner(row)
                if owner.telegram_id != telegram_id:
                    raise InteractiveTravelSessionOwnerMismatchError(travel_id)
                connection.execute(
                    """
                    UPDATE interactive_travel_owners
                    SET cancelled_at = COALESCE(cancelled_at, ?)
                    WHERE travel_id = ?
                    """,
                    (now, travel_id),
                )
                connection.execute(
                    "DELETE FROM interactive_travel_sessions WHERE travel_id = ?",
                    (travel_id,),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def authorize_side_effect(
        self,
        *,
        travel_id: str,
        telegram_id: int,
        kind: Literal["illustrate", "animate", "finale"],
        expected_state_fingerprint: str | None = None,
        pet_fingerprint: str | None = None,
        destination: str | None = None,
        part_number: int | None = None,
        title: str | None = None,
        story_text: str | None = None,
        travel: InteractiveTravelState | None = None,
    ) -> str:
        with self._connect() as connection:
            self._assert_active_owner_row(
                self._owner_row(connection, travel_id),
                travel_id,
                telegram_id,
            )
            row = connection.execute(
                """
                SELECT pet_fingerprint, state_json, state_fingerprint, completed_at
                FROM interactive_travel_sessions
                WHERE travel_id = ?
                """,
                (travel_id,),
            ).fetchone()
        if row is None:
            raise InteractiveTravelStateConflictError(travel_id)
        stored_fingerprint = str(row[2])
        if (
            expected_state_fingerprint is not None
            and expected_state_fingerprint != stored_fingerprint
        ):
            raise InteractiveTravelStateConflictError(travel_id)
        try:
            authoritative_travel = InteractiveTravelState.model_validate_json(row[1])
        except (TypeError, ValueError):
            raise InteractiveTravelStateConflictError(travel_id) from None
        if authoritative_travel.travelId != travel_id:
            raise InteractiveTravelStateConflictError(travel_id)
        if kind == "finale":
            if row[3] is None or travel is None:
                raise InteractiveTravelStateConflictError(travel_id)
            if interactive_travel_state_fingerprint(travel) != stored_fingerprint:
                raise InteractiveTravelStateConflictError(travel_id)
        elif kind in {"illustrate", "animate"}:
            if part_number is None:
                raise ValueError("part_number is required for media authorization")
            part = next(
                (item for item in authoritative_travel.parts if item.partNumber == part_number),
                None,
            )
            if part is None:
                raise InteractiveTravelStateConflictError(travel_id)
            if kind == "illustrate":
                if pet_fingerprint is None or str(row[0]) != pet_fingerprint:
                    raise InteractiveTravelPetMismatchError(travel_id)
                if (
                    destination != authoritative_travel.destination
                    or title != part.title
                    or story_text != part.storyText
                ):
                    raise InteractiveTravelStateConflictError(travel_id)
        else:
            raise ValueError(f"unsupported interactive travel side effect: {kind}")
        return stored_fingerprint

    def get(self, travel_id: str) -> InteractiveTravelSession | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT travel_id, telegram_id, pet_fingerprint, revision,
                       state_fingerprint, completed_at
                FROM interactive_travel_sessions
                WHERE travel_id = ?
                """,
                (travel_id,),
            ).fetchone()
        if row is None:
            return None
        return InteractiveTravelSession(
            travel_id=str(row[0]),
            telegram_id=int(row[1]),
            pet_fingerprint=str(row[2]),
            revision=int(row[3]),
            state_fingerprint=str(row[4]),
            completed=row[5] is not None,
        )


@lru_cache(maxsize=16)
def get_interactive_travel_session_store(
    path: str | Path = DEFAULT_INTERACTIVE_TRAVEL_STORE_PATH,
    max_records: int = DEFAULT_INTERACTIVE_TRAVEL_MAX_RECORDS,
    retention_seconds: int = int(DEFAULT_INTERACTIVE_TRAVEL_RETENTION.total_seconds()),
) -> InteractiveTravelSessionStore:
    return InteractiveTravelSessionStore(
        path,
        retention=timedelta(seconds=retention_seconds),
        max_records=max_records,
    )


__all__ = [
    "DEFAULT_INTERACTIVE_TRAVEL_MAX_RECORDS",
    "DEFAULT_INTERACTIVE_TRAVEL_RETENTION",
    "DEFAULT_INTERACTIVE_TRAVEL_STORE_PATH",
    "InteractiveTravelActiveError",
    "InteractiveTravelCommitResult",
    "InteractiveTravelContinueAttempt",
    "InteractiveTravelOwner",
    "InteractiveTravelOwnerMissingError",
    "InteractiveTravelPetMismatchError",
    "InteractiveTravelSession",
    "InteractiveTravelSessionCancelledError",
    "InteractiveTravelSessionCapacityError",
    "InteractiveTravelSessionCompletedError",
    "InteractiveTravelSessionError",
    "InteractiveTravelSessionOwnerMismatchError",
    "InteractiveTravelSessionStore",
    "InteractiveTravelStartAttempt",
    "InteractiveTravelStateConflictError",
    "fingerprint_payload",
    "get_interactive_travel_session_store",
    "interactive_travel_state_fingerprint",
]
