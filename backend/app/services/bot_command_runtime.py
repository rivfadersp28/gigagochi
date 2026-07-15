from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore, Event, Lock, Thread
from typing import Any

BotCommandWorker = Callable[[int, dict[str, Any]], None]
SUPPORTED_BOT_COMMANDS = frozenset(
    {"/app", "/easy", "/full_story", "/hard", "/help", "/push", "/start", "/story"}
)
SQLITE_BUSY_TIMEOUT_MS = 5_000
MAX_PREPARED_RESULT_BYTES = 512 * 1024
BOT_COMMAND_LOCK_BUCKETS = 256
_BOT_COMMAND_THREAD_LOCKS = tuple(Lock() for _ in range(BOT_COMMAND_LOCK_BUCKETS))
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DurableBotCommand:
    update_id: int
    chat_id: int
    command: str
    update: dict[str, Any]


class BotCommandInboxFull(RuntimeError):
    pass


class BotCommandPreparedResultError(RuntimeError):
    pass


def _message_command(text: str) -> str:
    first = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    return first.split("@", 1)[0].lower()


def _bounded_optional_text(value: object, *, max_length: int = 256) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:max_length]


def _json_value_extends(previous: Any, current: Any) -> bool:
    if isinstance(previous, dict):
        return isinstance(current, dict) and all(
            key in current and _json_value_extends(value, current[key])
            for key, value in previous.items()
        )
    return previous == current


def _prepared_checkpoint_advances(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    if previous.get("version") != 2 or current.get("version") != 2:
        return False
    if previous.get("kind") != current.get("kind"):
        return False
    previous_revision = previous.get("checkpointRevision")
    current_revision = current.get("checkpointRevision")
    if (
        type(previous_revision) is not int
        or type(current_revision) is not int
        or current_revision != previous_revision + 1
    ):
        return False
    previous_without_revision = {
        key: value for key, value in previous.items() if key != "checkpointRevision"
    }
    current_without_revision = {
        key: value for key, value in current.items() if key != "checkpointRevision"
    }
    return _json_value_extends(previous_without_revision, current_without_revision)


def normalize_bot_command_update(update: object) -> DurableBotCommand | None:
    """Keep only fields needed to replay a supported Telegram bot command."""
    if not isinstance(update, dict):
        return None
    update_id = update.get("update_id")
    if type(update_id) is not int or update_id < 0:
        return None
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        message = callback.get("message")
        chat = message.get("chat") if isinstance(message, dict) else None
        data = callback.get("data")
        callback_id = callback.get("id")
        if (
            isinstance(chat, dict) and type(chat.get("id")) is int
            and isinstance(data, str) and data.startswith("it:")
            and isinstance(callback_id, str)
        ):
            chat_id = chat["id"]
            normalized = {"update_id": update_id, "callback_query": {
                "id": callback_id[:256], "data": data[:64],
                "message": {"chat": {"id": chat_id}},
            }}
            return DurableBotCommand(update_id, chat_id, "/interactive_story_callback", normalized)
        return None
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    text = message.get("text")
    if not isinstance(chat, dict) or not isinstance(text, str):
        return None
    chat_id = chat.get("id")
    if type(chat_id) is not int:
        return None
    command = _message_command(text)
    if command not in SUPPORTED_BOT_COMMANDS:
        return None

    sender = message.get("from")
    normalized_sender: dict[str, Any] = {}
    if isinstance(sender, dict):
        sender_id = sender.get("id")
        if type(sender_id) is int:
            normalized_sender["id"] = sender_id
        for field in ("username", "first_name", "language_code"):
            normalized = _bounded_optional_text(sender.get(field))
            if normalized is not None:
                normalized_sender[field] = normalized

    normalized_update = {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "from": normalized_sender,
            # Command arguments are not used by the current handlers.
            "text": command,
        },
    }
    return DurableBotCommand(
        update_id=update_id,
        chat_id=chat_id,
        command=command,
        update=normalized_update,
    )


class SQLiteBotCommandInbox:
    """Durable, leased command queue keyed by Telegram update_id."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_pending: int = 500,
        max_pending_per_chat: int = 8,
        max_completed: int = 10_000,
        completed_retention_seconds: int = 7 * 24 * 60 * 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be positive")
        if max_pending_per_chat < 1:
            raise ValueError("max_pending_per_chat must be positive")
        if max_completed < 1:
            raise ValueError("max_completed must be positive")
        if completed_retention_seconds < 1:
            raise ValueError("completed_retention_seconds must be positive")
        self.path = Path(path).expanduser().resolve()
        self.max_pending = max_pending
        self.max_pending_per_chat = max_pending_per_chat
        self.max_completed = max_completed
        self.completed_retention_seconds = completed_retention_seconds
        self._clock = clock
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def command_lock(self, update_id: int) -> Iterator[None]:
        """Keep one update callback active across processes for its whole lifetime."""
        bucket = (
            int.from_bytes(
                hashlib.blake2s(str(update_id).encode("ascii"), digest_size=4).digest(),
                "big",
            )
            % BOT_COMMAND_LOCK_BUCKETS
        )
        lock_dir = self.path.parent / f".{self.path.name}.command-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"bucket-{bucket:03d}.lock"
        with _BOT_COMMAND_THREAD_LOCKS[bucket]:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._enable_wal_with_retry(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bot_command_inbox (
                        update_id INTEGER PRIMARY KEY,
                        chat_id INTEGER NOT NULL,
                        command TEXT NOT NULL,
                        update_json TEXT NOT NULL,
                        prepared_json TEXT,
                        status TEXT NOT NULL,
                        lease_owner TEXT,
                        lease_until REAL,
                        created_at REAL NOT NULL,
                        completed_at REAL
                    )
                    """
                )
                columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(bot_command_inbox)")
                }
                if "prepared_json" not in columns:
                    connection.execute(
                        "ALTER TABLE bot_command_inbox ADD COLUMN prepared_json TEXT"
                    )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_bot_command_inbox_ready
                    ON bot_command_inbox (status, lease_until, update_id)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_bot_command_inbox_completed
                    ON bot_command_inbox (status, completed_at)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_bot_command_inbox_chat_active
                    ON bot_command_inbox (chat_id, status, update_id, lease_until)
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
                error_code = getattr(exc, "sqlite_errorcode", None)
                primary_code = error_code & 0xFF if isinstance(error_code, int) else None
                if primary_code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 0.1)

    def enqueue(self, command: DurableBotCommand) -> bool:
        now = self._clock()
        payload = json.dumps(command.update, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._prune_completed(connection, now)
                existing = connection.execute(
                    "SELECT 1 FROM bot_command_inbox WHERE update_id = ?",
                    (command.update_id,),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return False
                chat_active_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM bot_command_inbox
                        WHERE chat_id = ? AND status IN ('pending', 'processing')
                        """,
                        (command.chat_id,),
                    ).fetchone()[0]
                )
                if chat_active_count >= self.max_pending_per_chat:
                    # Keep a bounded tombstone so a concurrent poller observes the
                    # same durable decision instead of enqueueing the dropped burst
                    # after this chat's backlog starts draining.
                    connection.execute(
                        """
                        INSERT INTO bot_command_inbox (
                            update_id, chat_id, command, update_json, status,
                            lease_owner, lease_until, created_at, completed_at
                        ) VALUES (?, ?, ?, '{}', 'completed', NULL, NULL, ?, ?)
                        """,
                        (
                            command.update_id,
                            command.chat_id,
                            command.command,
                            now,
                            now,
                        ),
                    )
                    self._prune_completed(connection, now)
                    connection.commit()
                    logger.warning(
                        "Dropping durable Telegram command because per-chat backlog is full "
                        "update_id=%s",
                        command.update_id,
                    )
                    return False
                active_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM bot_command_inbox
                        WHERE status IN ('pending', 'processing')
                        """
                    ).fetchone()[0]
                )
                if active_count >= self.max_pending:
                    raise BotCommandInboxFull("bot command inbox is full")
                connection.execute(
                    """
                    INSERT INTO bot_command_inbox (
                        update_id, chat_id, command, update_json, status,
                        lease_owner, lease_until, created_at, completed_at
                    ) VALUES (?, ?, ?, ?, 'pending', NULL, NULL, ?, NULL)
                    """,
                    (
                        command.update_id,
                        command.chat_id,
                        command.command,
                        payload,
                        now,
                    ),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return True

    def list_ready(self, *, limit: int) -> list[DurableBotCommand]:
        if limit < 1:
            return []
        now = self._clock()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT candidate.update_id, candidate.chat_id,
                       candidate.command, candidate.update_json
                FROM bot_command_inbox AS candidate
                WHERE (
                    candidate.status = 'pending'
                    OR (
                        candidate.status = 'processing'
                        AND COALESCE(candidate.lease_until, 0) <= ?
                    )
                )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM bot_command_inbox AS earlier
                    WHERE earlier.chat_id = candidate.chat_id
                      AND earlier.update_id < candidate.update_id
                      AND earlier.status IN ('pending', 'processing')
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM bot_command_inbox AS running
                    WHERE running.chat_id = candidate.chat_id
                      AND running.update_id != candidate.update_id
                      AND running.status = 'processing'
                      AND COALESCE(running.lease_until, 0) > ?
                  )
                ORDER BY candidate.update_id
                LIMIT ?
                """,
                (now, now, limit),
            ).fetchall()

        commands: list[DurableBotCommand] = []
        for row in rows:
            try:
                payload = json.loads(row["update_json"])
            except (TypeError, ValueError):
                logger.error(
                    "Dropping corrupt durable Telegram command update_id=%s",
                    row["update_id"],
                )
                self.discard(int(row["update_id"]))
                continue
            normalized = normalize_bot_command_update(payload)
            if (
                normalized is None
                or normalized.update_id != row["update_id"]
                or normalized.chat_id != row["chat_id"]
                or normalized.command != row["command"]
            ):
                logger.error(
                    "Dropping invalid durable Telegram command update_id=%s",
                    row["update_id"],
                )
                self.discard(int(row["update_id"]))
                continue
            commands.append(normalized)
        return commands

    def claim(self, update_id: int, *, owner: str, lease_seconds: int) -> bool:
        if not owner or lease_seconds < 1:
            raise ValueError("owner and positive lease_seconds are required")
        now = self._clock()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE bot_command_inbox AS candidate
                    SET status = 'processing', lease_owner = ?, lease_until = ?
                    WHERE candidate.update_id = ?
                      AND (
                        candidate.status = 'pending'
                        OR (
                            candidate.status = 'processing'
                            AND COALESCE(candidate.lease_until, 0) <= ?
                        )
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM bot_command_inbox AS earlier
                        WHERE earlier.chat_id = candidate.chat_id
                          AND earlier.update_id < candidate.update_id
                          AND earlier.status IN ('pending', 'processing')
                      )
                      AND NOT EXISTS (
                        SELECT 1
                        FROM bot_command_inbox AS running
                        WHERE running.chat_id = candidate.chat_id
                          AND running.update_id != candidate.update_id
                          AND running.status = 'processing'
                          AND COALESCE(running.lease_until, 0) > ?
                      )
                    """,
                    (owner, now + lease_seconds, update_id, now, now),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return cursor.rowcount == 1

    def renew_claims(
        self,
        *,
        owner: str,
        update_ids: set[int],
        lease_seconds: int,
    ) -> set[int]:
        if not update_ids:
            return set()
        if not owner or lease_seconds < 1:
            raise ValueError("owner and positive lease_seconds are required")
        now = self._clock()
        placeholders = ",".join("?" for _ in update_ids)
        parameters: list[object] = [now + lease_seconds, owner, *sorted(update_ids)]
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                UPDATE bot_command_inbox
                SET lease_until = ?
                WHERE status = 'processing' AND lease_owner = ?
                  AND update_id IN ({placeholders})
                RETURNING update_id
                """,
                parameters,
            ).fetchall()
        return {int(row["update_id"]) for row in rows}

    def load_prepared(self, update_id: int, *, owner: str) -> dict[str, Any] | None:
        """Load an already committed paid result without releasing its claim."""
        if not owner:
            raise ValueError("owner is required")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT prepared_json FROM bot_command_inbox
                WHERE update_id = ? AND status = 'processing' AND lease_owner = ?
                """,
                (update_id, owner),
            ).fetchone()
        if row is None:
            raise BotCommandPreparedResultError("durable command claim was lost")
        payload = row["prepared_json"]
        if payload is None:
            return None
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_PREPARED_RESULT_BYTES:
            raise BotCommandPreparedResultError("prepared result is invalid or oversized")
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise BotCommandPreparedResultError("prepared result is corrupt") from exc
        if not isinstance(decoded, dict):
            raise BotCommandPreparedResultError("prepared result must be a JSON object")
        return decoded

    def checkpoint_prepared(
        self,
        update_id: int,
        *,
        owner: str,
        payload: dict[str, Any],
    ) -> bool:
        """Persist the paid result before Telegram delivery; never overwrite it."""
        if not owner:
            raise ValueError("owner is required")
        try:
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise BotCommandPreparedResultError("prepared result is not JSON serializable") from exc
        if len(serialized.encode("utf-8")) > MAX_PREPARED_RESULT_BYTES:
            raise BotCommandPreparedResultError("prepared result is oversized")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT prepared_json FROM bot_command_inbox
                    WHERE update_id = ? AND status = 'processing' AND lease_owner = ?
                    """,
                    (update_id, owner),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return False
                existing = row["prepared_json"]
                if existing is not None:
                    try:
                        existing_payload = json.loads(existing)
                    except (TypeError, ValueError) as exc:
                        raise BotCommandPreparedResultError(
                            "existing prepared result is corrupt"
                        ) from exc
                    if existing_payload == payload:
                        connection.commit()
                        return True
                    if not isinstance(existing_payload, dict) or not _prepared_checkpoint_advances(
                        existing_payload,
                        payload,
                    ):
                        raise BotCommandPreparedResultError(
                            "prepared result checkpoint must advance monotonically"
                        )
                    cursor = connection.execute(
                        """
                        UPDATE bot_command_inbox SET prepared_json = ?
                        WHERE update_id = ? AND status = 'processing'
                          AND lease_owner = ? AND prepared_json = ?
                        """,
                        (serialized, update_id, owner, existing),
                    )
                else:
                    if payload.get("version") == 2 and (
                        payload.get("checkpointRevision") != 1
                        or not isinstance(payload.get("progress"), dict)
                    ):
                        raise BotCommandPreparedResultError(
                            "initial staged prepared result must start at revision 1"
                        )
                    cursor = connection.execute(
                        """
                        UPDATE bot_command_inbox SET prepared_json = ?
                        WHERE update_id = ? AND status = 'processing'
                          AND lease_owner = ? AND prepared_json IS NULL
                        """,
                        (serialized, update_id, owner),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return cursor.rowcount == 1

    def complete(self, update_id: int, *, owner: str) -> bool:
        now = self._clock()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE bot_command_inbox
                    SET status = 'completed', update_json = '{}', prepared_json = NULL,
                        lease_owner = NULL, lease_until = NULL, completed_at = ?
                    WHERE update_id = ? AND status = 'processing' AND lease_owner = ?
                    """,
                    (now, update_id, owner),
                )
                self._prune_completed(connection, now)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return cursor.rowcount == 1

    def release(self, update_id: int, *, owner: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE bot_command_inbox
                SET status = 'pending', lease_owner = NULL, lease_until = NULL
                WHERE update_id = ? AND status = 'processing' AND lease_owner = ?
                """,
                (update_id, owner),
            )
        return cursor.rowcount == 1

    def discard(self, update_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM bot_command_inbox WHERE update_id = ?",
                (update_id,),
            )

    def status(self, update_id: int) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM bot_command_inbox WHERE update_id = ?",
                (update_id,),
            ).fetchone()
        return str(row["status"]) if row is not None else None

    def _prune_completed(self, connection: sqlite3.Connection, now: float) -> None:
        connection.execute(
            """
            DELETE FROM bot_command_inbox
            WHERE status = 'completed' AND completed_at < ?
            """,
            (now - self.completed_retention_seconds,),
        )
        connection.execute(
            """
            DELETE FROM bot_command_inbox
            WHERE update_id IN (
                SELECT update_id FROM bot_command_inbox
                WHERE status = 'completed'
                ORDER BY completed_at DESC, update_id DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (self.max_completed,),
        )


class BotCommandLeaseKeeper:
    def __init__(
        self,
        inbox: SQLiteBotCommandInbox,
        *,
        owner: str,
        lease_seconds: int,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        if lease_seconds < 3:
            raise ValueError("lease_seconds must be at least 3")
        if heartbeat_interval_seconds is not None and heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        self._inbox = inbox
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._interval_seconds = heartbeat_interval_seconds or max(
            1.0,
            min(30.0, lease_seconds / 3),
        )
        self._lock = Lock()
        self._active_update_ids: set[int] = set()
        self._stop = Event()
        self._thread = Thread(
            target=self._run,
            name="telegram-command-lease-heartbeat",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def add(self, update_id: int) -> None:
        with self._lock:
            self._active_update_ids.add(update_id)

    def remove(self, update_id: int) -> None:
        with self._lock:
            self._active_update_ids.discard(update_id)

    def snapshot(self) -> set[int]:
        with self._lock:
            return set(self._active_update_ids)

    def ensure_owned(self, update_id: int) -> bool:
        with self._lock:
            if update_id not in self._active_update_ids:
                return False
        return update_id in self._renew_claims({update_id}, source="stage")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval_seconds + 1)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            update_ids = self.snapshot()
            if not update_ids:
                continue
            self._renew_claims(update_ids, source="heartbeat")

    def _renew_claims(self, update_ids: set[int], *, source: str) -> set[int]:
        try:
            renewed = self._inbox.renew_claims(
                owner=self._owner,
                update_ids=update_ids,
                lease_seconds=self._lease_seconds,
            )
        except Exception:
            with self._lock:
                self._active_update_ids.difference_update(update_ids)
            logger.exception(
                "Failed to renew durable Telegram command leases; fencing workers source=%s",
                source,
            )
            return set()
        lost = update_ids - renewed
        if lost:
            with self._lock:
                self._active_update_ids.difference_update(lost)
            logger.warning(
                "Lost durable Telegram command leases updateIds=%s owner=%s source=%s",
                sorted(lost),
                self._owner,
                source,
            )
        return renewed


class BoundedBotCommandDispatcher:
    def __init__(self, *, max_workers: int, max_queued_commands: int) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="telegram-command",
        )
        self._slots = BoundedSemaphore(max_workers + max_queued_commands)
        self._lock = Lock()
        self._active_chat_ids: set[int] = set()

    def can_submit(self, chat_id: int) -> bool:
        with self._lock:
            if chat_id in self._active_chat_ids or not self._slots.acquire(blocking=False):
                return False
            self._slots.release()
            return True

    def submit(
        self,
        worker: BotCommandWorker,
        chat_id: int,
        keyboard: dict[str, Any],
    ) -> bool:
        with self._lock:
            if chat_id in self._active_chat_ids or not self._slots.acquire(blocking=False):
                return False
            self._active_chat_ids.add(chat_id)
        try:
            self._executor.submit(self._run, worker, chat_id, keyboard)
        except Exception:
            self._release(chat_id)
            raise
        return True

    def _run(
        self,
        worker: BotCommandWorker,
        chat_id: int,
        keyboard: dict[str, Any],
    ) -> None:
        try:
            worker(chat_id, keyboard)
        finally:
            self._release(chat_id)

    def _release(self, chat_id: int) -> None:
        with self._lock:
            self._active_chat_ids.discard(chat_id)
            self._slots.release()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def __enter__(self) -> BoundedBotCommandDispatcher:
        return self

    def __exit__(self, *_args: object) -> None:
        self.shutdown()


def load_bot_update_offset(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None
    offset = payload.get("offset") if isinstance(payload, dict) else None
    return offset if type(offset) is int and offset >= 0 else None


def save_bot_update_offset(path: Path, offset: int) -> int:
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        current_offset = load_bot_update_offset(path)
        actual_offset = max(offset, current_offset) if current_offset is not None else offset
        if current_offset == actual_offset:
            _fsync_directory(path.parent)
            return actual_offset

        temporary_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary_path.open("x", encoding="utf-8") as handle:
                json.dump({"offset": actual_offset}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            _fsync_directory(path.parent)
        finally:
            temporary_path.unlink(missing_ok=True)
        return actual_offset


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
