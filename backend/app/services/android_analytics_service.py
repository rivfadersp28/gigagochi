from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import sqlite3
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings

SQLITE_BUSY_TIMEOUT_MS = 5_000
MAX_BATCH_EVENTS = 50
MAX_EVENT_AGE_SECONDS = 8 * 24 * 60 * 60
MAX_FUTURE_SKEW_SECONDS = 5 * 60
OUTBOX_RETENTION_SECONDS = 8 * 24 * 60 * 60
PERMANENT_REJECTION_STATUSES = frozenset({400, 413, 422})
logger = logging.getLogger(__name__)


class AnalyticsNotConfiguredError(RuntimeError):
    pass


class AndroidAnalyticsOutbox:
    """Durable, bounded server-to-server analytics forwarding queue."""

    def __init__(self, path: str | Path, *, max_events: int = 10_000) -> None:
        self.path = Path(path).expanduser().resolve()
        self.max_events = max_events
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
                    CREATE TABLE IF NOT EXISTS android_analytics_outbox (
                        event_id TEXT PRIMARY KEY,
                        actor_id TEXT NOT NULL,
                        event_json TEXT NOT NULL,
                        created_at REAL NOT NULL
                    ) WITHOUT ROWID
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS android_analytics_outbox_created_idx
                    ON android_analytics_outbox(created_at)
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS android_analytics_deletions (
                        actor_id TEXT PRIMARY KEY,
                        created_at REAL NOT NULL,
                        completed_at REAL
                    ) WITHOUT ROWID
                    """
                )
                deletion_columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(android_analytics_deletions)"
                    )
                }
                if "completed_at" not in deletion_columns:
                    connection.execute(
                        "ALTER TABLE android_analytics_deletions ADD COLUMN completed_at REAL"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS android_privacy_tokens (
                        token_digest BLOB PRIMARY KEY,
                        expires_at REAL NOT NULL,
                        CHECK(length(token_digest) = 32)
                    ) WITHOUT ROWID
                    """
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def enqueue_events(self, actor_id: str, events: Sequence[dict[str, Any]]) -> None:
        now = time.time()
        rows = [
            (
                str(event["eventId"]),
                actor_id,
                json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                now,
            )
            for event in events
        ]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM android_analytics_outbox WHERE created_at < ?",
                    (now - OUTBOX_RETENTION_SECONDS,),
                )
                tombstoned = connection.execute(
                    "SELECT 1 FROM android_analytics_deletions WHERE actor_id = ?",
                    (actor_id,),
                ).fetchone()
                if tombstoned is None:
                    connection.executemany(
                        """
                        INSERT OR IGNORE INTO android_analytics_outbox (
                            event_id, actor_id, event_json, created_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        rows,
                    )
                overflow = int(
                    connection.execute(
                        "SELECT MAX(0, COUNT(*) - ?) FROM android_analytics_outbox",
                        (self.max_events,),
                    ).fetchone()[0]
                )
                if overflow:
                    connection.execute(
                        """
                        DELETE FROM android_analytics_outbox
                        WHERE event_id IN (
                            SELECT event_id FROM android_analytics_outbox
                            ORDER BY created_at, event_id
                            LIMIT ?
                        )
                        """,
                        (overflow,),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def enqueue_deletion(self, actor_id: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM android_analytics_outbox WHERE actor_id = ?",
                    (actor_id,),
                )
                connection.execute(
                    """
                    INSERT INTO android_analytics_deletions(
                        actor_id, created_at, completed_at
                    ) VALUES (?, ?, NULL)
                    ON CONFLICT(actor_id) DO UPDATE SET
                        created_at = excluded.created_at,
                        completed_at = NULL
                    """,
                    (actor_id, time.time()),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def next_deletion(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT actor_id FROM android_analytics_deletions
                WHERE completed_at IS NULL
                ORDER BY created_at, actor_id
                LIMIT 1
                """
            ).fetchone()
        return str(row[0]) if row is not None else None

    def complete_deletion(self, actor_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE android_analytics_deletions
                SET completed_at = ?
                WHERE actor_id = ? AND completed_at IS NULL
                """,
                (time.time(), actor_id),
            )

    def next_batch(self) -> tuple[str, list[dict[str, Any]]] | None:
        with self._connect() as connection:
            actor_row = connection.execute(
                """
                SELECT actor_id FROM android_analytics_outbox
                ORDER BY created_at, event_id
                LIMIT 1
                """
            ).fetchone()
            if actor_row is None:
                return None
            actor_id = str(actor_row[0])
            rows = connection.execute(
                """
                SELECT event_json FROM android_analytics_outbox
                WHERE actor_id = ?
                ORDER BY created_at, event_id
                LIMIT ?
                """,
                (actor_id, MAX_BATCH_EVENTS),
            ).fetchall()
        return actor_id, [json.loads(str(row[0])) for row in rows]

    def complete_events(self, event_ids: Sequence[str]) -> None:
        if not event_ids:
            return
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.executemany(
                    "DELETE FROM android_analytics_outbox WHERE event_id = ?",
                    [(event_id,) for event_id in event_ids],
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def count(self) -> tuple[int, int]:
        with self._connect() as connection:
            events = int(
                connection.execute("SELECT COUNT(*) FROM android_analytics_outbox").fetchone()[0]
            )
            deletions = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM android_analytics_deletions
                    WHERE completed_at IS NULL
                    """
                ).fetchone()[0]
            )
        return events, deletions

    @staticmethod
    def _token_digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    def record_privacy_token(self, token: str, *, ttl_seconds: int = 24 * 60 * 60) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM android_privacy_tokens WHERE expires_at <= ?",
                    (now,),
                )
                connection.execute(
                    """
                    INSERT INTO android_privacy_tokens(token_digest, expires_at)
                    VALUES (?, ?)
                    ON CONFLICT(token_digest) DO UPDATE SET
                        expires_at = excluded.expires_at
                    """,
                    (self._token_digest(token), now + ttl_seconds),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def is_privacy_token(self, token: str) -> bool:
        now = time.time()
        digest = self._token_digest(token)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM android_privacy_tokens WHERE expires_at <= ?",
                (now,),
            )
            row = connection.execute(
                """
                SELECT 1 FROM android_privacy_tokens
                WHERE token_digest = ? AND expires_at > ?
                """,
                (digest, now),
            ).fetchone()
        return row is not None


class AndroidAnalyticsForwarder:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = str(settings.gigagochi_stats_base_url or "").rstrip("/")
        self.ingest_token = (
            settings.gigagochi_stats_ingest_token.get_secret_value().strip()
            if settings.gigagochi_stats_ingest_token is not None
            else ""
        )
        self.actor_secret = (
            settings.gigagochi_stats_actor_secret.get_secret_value().strip()
            if settings.gigagochi_stats_actor_secret is not None
            else ""
        )
        self.outbox = AndroidAnalyticsOutbox(
            settings.gigagochi_stats_outbox_path,
            max_events=settings.gigagochi_stats_outbox_max_events,
        )
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.ingest_token and len(self.actor_secret) >= 32)

    def require_configured(self) -> None:
        if not self.configured:
            raise AnalyticsNotConfiguredError("analytics forwarding is not configured")

    def actor_id(self, account_id: str) -> str:
        self.require_configured()
        return hmac.new(
            self.actor_secret.encode("utf-8"),
            account_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def accept(self, account_id: str, events: Sequence[dict[str, Any]]) -> None:
        self.outbox.enqueue_events(self.actor_id(account_id), events)

    def request_deletion(self, account_id: str) -> str:
        actor_id = self.actor_id(account_id)
        self.outbox.enqueue_deletion(actor_id)
        return actor_id

    def flush_once(self) -> bool:
        self.require_configured()
        deletion = self.outbox.next_deletion()
        if deletion is not None:
            status_code = self._post("/delete", {"actorId": deletion})
            if 200 <= status_code < 300:
                self.outbox.complete_deletion(deletion)
                return True
            return False

        batch = self.outbox.next_batch()
        if batch is None:
            return False
        actor_id, events = batch
        event_ids = [str(event["eventId"]) for event in events]
        status_code = self._post(
            "/events",
            {"schemaVersion": 1, "actorId": actor_id, "events": events},
        )
        if 200 <= status_code < 300 or status_code in PERMANENT_REJECTION_STATUSES:
            self.outbox.complete_events(event_ids)
            return True
        return False

    def _post(self, path: str, body: dict[str, Any]) -> int:
        headers = {
            "Content-Type": "application/json",
            "X-Ingest-Token": self.ingest_token,
        }
        if self._client is not None:
            response = self._client.post(
                f"{self.base_url}{path}",
                headers=headers,
                json=body,
            )
            return response.status_code
        try:
            with httpx.Client(timeout=5, follow_redirects=False) as client:
                response = client.post(
                    f"{self.base_url}{path}",
                    headers=headers,
                    json=body,
                )
            return response.status_code
        except httpx.HTTPError:
            logger.warning("android_analytics_forward_failed errorCode=transport")
            return 599


async def analytics_flush_loop(
    forwarder: AndroidAnalyticsForwarder,
    *,
    interval_seconds: int,
) -> None:
    while True:
        try:
            progressed = await asyncio.to_thread(forwarder.flush_once)
        except AnalyticsNotConfiguredError:
            return
        except Exception:
            logger.exception("android_analytics_flush_failed")
            progressed = False
        await asyncio.sleep(0 if progressed else interval_seconds)
