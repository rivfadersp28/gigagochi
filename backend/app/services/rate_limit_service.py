from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

DEFAULT_RATE_LIMIT_STORE_PATH = "data/push/rate_limits.sqlite3"
DEFAULT_RATE_LIMIT_RETENTION = timedelta(days=2)
SQLITE_BUSY_TIMEOUT_MS = 5_000


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class RateLimitReservation:
    event_id: int
    bucket: str
    user_id: int | str
    created: bool = True


class SQLiteRateLimiter:
    """Cross-process sliding-window limiter backed by one SQLite file."""

    def __init__(
        self,
        path: str | Path,
        *,
        retention: timedelta = DEFAULT_RATE_LIMIT_RETENTION,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if retention.total_seconds() <= 0:
            raise ValueError("retention must be positive")
        self.path = Path(path).expanduser().resolve()
        self.retention = retention
        self._clock = clock
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        # A lost reservation can turn a crash retry into another paid generation.
        # Prefer durability over the negligible write latency at this event volume.
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._enable_wal_with_retry(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rate_limit_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        bucket TEXT NOT NULL,
                        user_id INTEGER NOT NULL,
                        occurred_at REAL NOT NULL,
                        request_key TEXT
                    )
                    """
                )
                columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(rate_limit_events)")
                }
                if "request_key" not in columns:
                    connection.execute("ALTER TABLE rate_limit_events ADD COLUMN request_key TEXT")
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_rate_limit_key_time
                    ON rate_limit_events (bucket, user_id, occurred_at)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_rate_limit_time
                    ON rate_limit_events (occurred_at)
                    """
                )
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_rate_limit_request_key
                    ON rate_limit_events (bucket, user_id, request_key)
                    WHERE request_key IS NOT NULL
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rate_limit_counters (
                        bucket TEXT NOT NULL,
                        user_id INTEGER NOT NULL,
                        window_started_at REAL NOT NULL,
                        event_count INTEGER NOT NULL,
                        request_keys_json TEXT NOT NULL DEFAULT '[]',
                        PRIMARY KEY (bucket, user_id)
                    )
                    """
                )
                counter_columns = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(rate_limit_counters)")
                }
                if "request_keys_json" not in counter_columns:
                    connection.execute(
                        """
                        ALTER TABLE rate_limit_counters
                        ADD COLUMN request_keys_json TEXT NOT NULL DEFAULT '[]'
                        """
                    )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_rate_limit_counter_time
                    ON rate_limit_counters (window_started_at)
                    """
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _enable_wal_with_retry(connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + SQLITE_BUSY_TIMEOUT_MS / 1_000
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

    def check(
        self,
        bucket: str,
        user_id: int | str,
        limit: int,
        window: timedelta,
        request_key: str | None = None,
    ) -> RateLimitReservation | None:
        if limit <= 0:
            return None
        if not bucket or len(bucket) > 64:
            raise ValueError("bucket must contain between 1 and 64 characters")
        if request_key is not None and (not request_key or len(request_key) > 128):
            raise ValueError("request_key must contain between 1 and 128 characters")

        window_seconds = window.total_seconds()
        retention_seconds = self.retention.total_seconds()
        if window_seconds <= 0:
            raise ValueError("window must be positive")
        if window_seconds > retention_seconds:
            raise ValueError("window cannot exceed retention")

        now = self._clock()
        cutoff = now - window_seconds
        retention_cutoff = now - retention_seconds
        retry_after_seconds: int | None = None
        reservation: RateLimitReservation | None = None

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM rate_limit_events WHERE occurred_at < ?",
                    (retention_cutoff,),
                )
                if request_key is not None:
                    existing = connection.execute(
                        """
                        SELECT id FROM rate_limit_events
                        WHERE bucket = ? AND user_id = ? AND request_key = ?
                        """,
                        (bucket, user_id, request_key),
                    ).fetchone()
                    if existing is not None:
                        reservation = RateLimitReservation(
                            event_id=int(existing[0]),
                            bucket=bucket,
                            user_id=user_id,
                            created=False,
                        )
                        connection.commit()
                        return reservation
                count, oldest = connection.execute(
                    """
                    SELECT COUNT(*), MIN(occurred_at)
                    FROM rate_limit_events
                    WHERE bucket = ? AND user_id = ? AND occurred_at >= ?
                    """,
                    (bucket, user_id, cutoff),
                ).fetchone()
                if count >= limit:
                    retry_after_seconds = max(
                        1,
                        math.ceil(float(oldest) + window_seconds - now),
                    )
                else:
                    cursor = connection.execute(
                        """
                        INSERT INTO rate_limit_events (
                            bucket, user_id, occurred_at, request_key
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (bucket, user_id, now, request_key),
                    )
                    reservation = RateLimitReservation(
                        event_id=int(cursor.lastrowid),
                        bucket=bucket,
                        user_id=user_id,
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        if retry_after_seconds is not None:
            raise RateLimitExceeded(retry_after_seconds)
        return reservation

    def refund(self, reservation: RateLimitReservation) -> None:
        if not reservation.created:
            return
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    DELETE FROM rate_limit_events
                    WHERE id = ? AND bucket = ? AND user_id = ?
                    """,
                    (reservation.event_id, reservation.bucket, reservation.user_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def check_fixed_window(
        self,
        bucket: str,
        user_id: int | str,
        limit: int,
        window: timedelta,
        request_key: str | None = None,
    ) -> None:
        """Compact per-user limiter with optional bounded request-key deduplication."""

        if limit <= 0:
            return
        if not bucket or len(bucket) > 64:
            raise ValueError("bucket must contain between 1 and 64 characters")
        if request_key is not None and (not request_key or len(request_key) > 128):
            raise ValueError("request_key must contain between 1 and 128 characters")
        window_seconds = window.total_seconds()
        retention_seconds = self.retention.total_seconds()
        if window_seconds <= 0:
            raise ValueError("window must be positive")
        if window_seconds > retention_seconds:
            raise ValueError("window cannot exceed retention")

        now = self._clock()
        window_started_at = math.floor(now / window_seconds) * window_seconds
        retry_after_seconds: int | None = None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM rate_limit_counters WHERE window_started_at < ?",
                    (now - retention_seconds,),
                )
                row = connection.execute(
                    """
                    SELECT window_started_at, event_count, request_keys_json
                    FROM rate_limit_counters
                    WHERE bucket = ? AND user_id = ?
                    """,
                    (bucket, user_id),
                ).fetchone()
                same_window = row is not None and float(row[0]) == window_started_at
                current_count = int(row[1]) if same_window else 0
                request_keys: list[str] = []
                if same_window:
                    try:
                        raw_keys = json.loads(str(row[2]))
                    except (TypeError, ValueError):
                        raw_keys = []
                    if isinstance(raw_keys, list):
                        request_keys = [
                            value
                            for value in raw_keys
                            if isinstance(value, str) and 0 < len(value) <= 128
                        ][-limit:]
                    if request_key is not None and request_key in request_keys:
                        connection.commit()
                        return
                if current_count >= limit:
                    retry_after_seconds = max(
                        1,
                        math.ceil(window_started_at + window_seconds - now),
                    )
                else:
                    if request_key is not None:
                        request_keys.append(request_key)
                        request_keys = request_keys[-limit:]
                    connection.execute(
                        """
                        INSERT INTO rate_limit_counters (
                            bucket, user_id, window_started_at, event_count, request_keys_json
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(bucket, user_id) DO UPDATE SET
                            window_started_at = excluded.window_started_at,
                            event_count = excluded.event_count,
                            request_keys_json = excluded.request_keys_json
                        """,
                        (
                            bucket,
                            user_id,
                            window_started_at,
                            current_count + 1,
                            json.dumps(request_keys, separators=(",", ":")),
                        ),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        if retry_after_seconds is not None:
            raise RateLimitExceeded(retry_after_seconds)

    def clear(self, bucket: str, user_id: int | str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM rate_limit_events WHERE bucket = ? AND user_id = ?",
                    (bucket, user_id),
                )
                connection.execute(
                    "DELETE FROM rate_limit_counters WHERE bucket = ? AND user_id = ?",
                    (bucket, user_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise


@lru_cache(maxsize=16)
def _cached_rate_limiter(path: str) -> SQLiteRateLimiter:
    return SQLiteRateLimiter(path)


def get_rate_limiter(path: str | Path = DEFAULT_RATE_LIMIT_STORE_PATH) -> SQLiteRateLimiter:
    normalized_path = str(Path(path).expanduser().resolve())
    return _cached_rate_limiter(normalized_path)
