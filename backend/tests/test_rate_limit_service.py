from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from app.services.feature_owner import FeatureOwner
from app.services.rate_limit_service import RateLimitExceeded, SQLiteRateLimiter


def test_rate_limit_transactions_use_full_durability(tmp_path) -> None:
    limiter = SQLiteRateLimiter(tmp_path / "rate-limits.sqlite3")

    with limiter._connect() as connection:
        synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]

    assert synchronous == 2


def test_rate_limit_persists_across_limiter_restart(tmp_path) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    SQLiteRateLimiter(store_path).check(
        "generation",
        42,
        limit=1,
        window=timedelta(days=1),
    )

    restarted_limiter = SQLiteRateLimiter(store_path)

    with pytest.raises(RateLimitExceeded) as error:
        restarted_limiter.check(
            "generation",
            42,
            limit=1,
            window=timedelta(days=1),
        )
    assert error.value.retry_after_seconds > 0


def test_two_limiter_instances_enforce_concurrent_atomic_cap(tmp_path) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    limiters = [SQLiteRateLimiter(store_path), SQLiteRateLimiter(store_path)]
    barrier = Barrier(2)

    def attempt(limiter: SQLiteRateLimiter) -> bool:
        barrier.wait()
        try:
            limiter.check(
                "generation",
                42,
                limit=1,
                window=timedelta(days=1),
            )
        except RateLimitExceeded:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        accepted = list(executor.map(attempt, limiters))

    assert sorted(accepted) == [False, True]


def test_fixed_window_attempt_limit_uses_one_compact_row(tmp_path) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    limiter = SQLiteRateLimiter(store_path, clock=lambda: 1_700_000_000.0)

    limiter.check_fixed_window("push_snapshot_attempt", 42, 2, timedelta(hours=1))
    limiter.check_fixed_window("push_snapshot_attempt", 42, 2, timedelta(hours=1))
    with pytest.raises(RateLimitExceeded):
        limiter.check_fixed_window("push_snapshot_attempt", 42, 2, timedelta(hours=1))

    with sqlite3.connect(store_path) as connection:
        rows = connection.execute(
            "SELECT bucket, user_id, event_count FROM rate_limit_counters"
        ).fetchall()
        event_count = connection.execute("SELECT COUNT(*) FROM rate_limit_events").fetchone()[0]
    assert rows == [("push_snapshot_attempt", 42, 2)]
    assert event_count == 0


def test_fixed_window_request_key_replay_does_not_increment_counter(tmp_path) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    limiter = SQLiteRateLimiter(store_path, clock=lambda: 1_700_000_000.0)

    limiter.check_fixed_window(
        "push_snapshot",
        42,
        2,
        timedelta(hours=1),
        request_key="revision:1",
    )
    limiter.check_fixed_window(
        "push_snapshot",
        42,
        2,
        timedelta(hours=1),
        request_key="revision:1",
    )
    limiter.check_fixed_window(
        "push_snapshot",
        42,
        2,
        timedelta(hours=1),
        request_key="revision:2",
    )
    with pytest.raises(RateLimitExceeded):
        limiter.check_fixed_window(
            "push_snapshot",
            42,
            2,
            timedelta(hours=1),
            request_key="revision:3",
        )

    with sqlite3.connect(store_path) as connection:
        count, keys = connection.execute(
            """
            SELECT event_count, request_keys_json
            FROM rate_limit_counters
            WHERE bucket = 'push_snapshot' AND user_id = 42
            """
        ).fetchone()
        event_rows = connection.execute("SELECT COUNT(*) FROM rate_limit_events").fetchone()[0]
    assert count == 2
    assert keys == '["revision:1","revision:2"]'
    assert event_rows == 0


def test_concurrent_initialization_of_fresh_store_waits_for_wal_lock(tmp_path) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    barrier = Barrier(8)

    def initialize(_index: int) -> bool:
        barrier.wait()
        SQLiteRateLimiter(store_path)
        return True

    with ThreadPoolExecutor(max_workers=8) as executor:
        initialized = list(executor.map(initialize, range(8)))

    assert initialized == [True] * 8


def test_rate_limit_prunes_events_outside_bounded_retention(tmp_path) -> None:
    now = [1_700_000_000.0]
    store_path = tmp_path / "rate-limits.sqlite3"
    limiter = SQLiteRateLimiter(store_path, clock=lambda: now[0])
    limiter.check("generation", 1, limit=1, window=timedelta(days=1))
    now[0] += timedelta(days=3).total_seconds()

    limiter.check("generation", 2, limit=1, window=timedelta(days=1))

    with sqlite3.connect(store_path) as connection:
        rows = connection.execute(
            "SELECT user_id FROM rate_limit_events ORDER BY user_id"
        ).fetchall()
    assert rows == [(2,)]


def test_request_key_replay_does_not_consume_another_event_or_refund_original(
    tmp_path,
) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    limiter = SQLiteRateLimiter(store_path)

    created = limiter.check(
        "generation",
        42,
        limit=2,
        window=timedelta(days=1),
        request_key="telegram-update:100",
    )
    replayed = limiter.check(
        "generation",
        42,
        limit=2,
        window=timedelta(days=1),
        request_key="telegram-update:100",
    )

    assert created is not None and created.created is True
    assert replayed is not None and replayed.created is False
    assert replayed.event_id == created.event_id
    limiter.refund(replayed)
    with sqlite3.connect(store_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM rate_limit_events WHERE bucket = 'generation'"
        ).fetchone()[0]
    assert count == 1


def test_google_and_telegram_rate_limit_namespaces_do_not_share_quota(tmp_path) -> None:
    limiter = SQLiteRateLimiter(tmp_path / "rate-limits.sqlite3")
    google_owner = FeatureOwner("google", "google:" + "a" * 64)

    limiter.check(
        "generation",
        42,
        limit=1,
        window=timedelta(days=1),
        request_key="telegram:shared",
    )
    limiter.check(
        "generation",
        google_owner.storage_key,
        limit=1,
        window=timedelta(days=1),
        request_key="android:create:shared",
    )

    with pytest.raises(RateLimitExceeded):
        limiter.check("generation", 42, limit=1, window=timedelta(days=1))
    with pytest.raises(RateLimitExceeded):
        limiter.check(
            "generation",
            google_owner.storage_key,
            limit=1,
            window=timedelta(days=1),
        )


def test_concurrent_same_request_key_creates_one_event(tmp_path) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    limiters = [SQLiteRateLimiter(store_path), SQLiteRateLimiter(store_path)]
    barrier = Barrier(2)

    def attempt(limiter: SQLiteRateLimiter):
        barrier.wait()
        return limiter.check(
            "generation",
            42,
            limit=2,
            window=timedelta(days=1),
            request_key="telegram-update:100",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        reservations = list(executor.map(attempt, limiters))

    assert all(reservation is not None for reservation in reservations)
    assert sorted(reservation.created for reservation in reservations if reservation) == [
        False,
        True,
    ]
    assert len({reservation.event_id for reservation in reservations if reservation}) == 1
    with sqlite3.connect(store_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM rate_limit_events").fetchone()[0]
    assert count == 1


def test_request_key_migrates_existing_store(tmp_path) -> None:
    store_path = tmp_path / "rate-limits.sqlite3"
    with sqlite3.connect(store_path) as connection:
        connection.execute(
            """
            CREATE TABLE rate_limit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bucket TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                occurred_at REAL NOT NULL
            )
            """
        )

    limiter = SQLiteRateLimiter(store_path)
    reservation = limiter.check(
        "generation",
        42,
        limit=1,
        window=timedelta(days=1),
        request_key="telegram-update:100",
    )

    assert reservation is not None
    with sqlite3.connect(store_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(rate_limit_events)")}
    assert "request_key" in columns
