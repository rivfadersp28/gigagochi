from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol


class TelegramPushStoreError(RuntimeError):
    pass


class TelegramPushRecordTooLargeError(TelegramPushStoreError):
    def __init__(self, *, actual_bytes: int, max_bytes: int) -> None:
        super().__init__(f"push record is {actual_bytes} bytes; limit is {max_bytes} bytes")
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes


class TelegramPushStoreCapacityError(TelegramPushStoreError):
    pass


DEFAULT_PUSH_RECORD_MAX_BYTES = 1_048_576
DEFAULT_PUSH_STORE_MAX_BYTES = 128 * 1024 * 1024
DEFAULT_PUSH_STORE_MAX_RECORDS = 10_000
DEFAULT_PUSH_UNREACHABLE_RETENTION_SECONDS = 90 * 24 * 60 * 60
SQLITE_BUSY_TIMEOUT_SECONDS = 30
SQLITE_PUSH_STORE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})
LEGACY_JSON_IMPORT_MIGRATION = "legacy-json-v1"
_ACTIVITY_TIMESTAMP_KEYS = (
    "lastChatSeenAt",
    "updatedAt",
    "registeredAt",
    "lastPushAttemptAt",
    "lastStoryAttemptAt",
    "lastFullStoryAt",
)


class TelegramPushStore(Protocol):
    def read(self) -> dict[str, Any]: ...

    def replace_record(self, record: dict[str, Any]) -> dict[str, Any]: ...

    def update_record(
        self,
        telegram_id: int,
        updater: Callable[[dict[str, Any] | None], dict[str, Any]],
    ) -> dict[str, Any]: ...


TelegramPushStoreBackend = Literal["auto", "json", "sqlite"]


def _validate_capacity_limits(
    *,
    record_max_bytes: int,
    store_max_bytes: int,
    store_max_records: int,
    unreachable_retention_seconds: int,
) -> None:
    if (
        record_max_bytes <= 0
        or store_max_bytes <= 0
        or store_max_records <= 0
        or unreachable_retention_seconds <= 0
    ):
        raise ValueError("push store capacity limits must be positive")


def _serialize(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")


def _serialize_store(store: dict[str, Any]) -> bytes:
    return _serialize(store) + b"\n"


def _record_activity_timestamp(record: dict[str, Any]) -> float | None:
    parsed: list[float] = []
    values = [record.get(key) for key in _ACTIVITY_TIMESTAMP_KEYS]
    reset_request = record.get("petResetRequest")
    if isinstance(reset_request, dict):
        values.append(reset_request.get("requestedAt"))
    reset_tombstones = record.get("petResetTombstones")
    if isinstance(reset_tombstones, list):
        values.extend(
            item.get("requestedAt") for item in reset_tombstones if isinstance(item, dict)
        )
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        parsed.append(timestamp.timestamp())
    return max(parsed) if parsed else None


def _record_has_reset_fence(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    reset_request = record.get("petResetRequest")
    if isinstance(reset_request, dict) and reset_request.get("petId"):
        return True
    tombstones = record.get("petResetTombstones")
    return isinstance(tombstones, list) and any(
        isinstance(item, dict) and item.get("petId") for item in tombstones
    )


class JsonTelegramPushStore:
    def __init__(
        self,
        path: Path,
        *,
        version: int,
        record_max_bytes: int = DEFAULT_PUSH_RECORD_MAX_BYTES,
        store_max_bytes: int = DEFAULT_PUSH_STORE_MAX_BYTES,
        store_max_records: int = DEFAULT_PUSH_STORE_MAX_RECORDS,
        unreachable_retention_seconds: int = DEFAULT_PUSH_UNREACHABLE_RETENTION_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        _validate_capacity_limits(
            record_max_bytes=record_max_bytes,
            store_max_bytes=store_max_bytes,
            store_max_records=store_max_records,
            unreachable_retention_seconds=unreachable_retention_seconds,
        )
        self.path = path
        self.version = version
        self.record_max_bytes = record_max_bytes
        self.store_max_bytes = store_max_bytes
        self.store_max_records = store_max_records
        self.unreachable_retention_seconds = unreachable_retention_seconds
        self._clock = clock
        self.lock_path = path.with_suffix(f"{path.suffix}.lock")

    def empty(self) -> dict[str, Any]:
        return {"version": self.version, "records": {}}

    @contextmanager
    def _lock(self, *, exclusive: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def read(self) -> dict[str, Any]:
        with self._lock(exclusive=False):
            return self._read_unlocked()

    def replace_record(self, record: dict[str, Any]) -> dict[str, Any]:
        telegram_id = record.get("telegramId")
        if not isinstance(telegram_id, int):
            raise TelegramPushStoreError("record.telegramId must be an integer")
        return self.update_record(telegram_id, lambda _current: record)

    def update_record(
        self,
        telegram_id: int,
        updater: Callable[[dict[str, Any] | None], dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock(exclusive=True):
            store = self._read_unlocked()
            records = store.setdefault("records", {})
            current = records.get(str(telegram_id))
            current_record_size = len(self._serialize(current)) if isinstance(current, dict) else 0
            current_store_size = len(self._serialize_store(store))
            next_record = updater(deepcopy(current) if isinstance(current, dict) else None)
            if not isinstance(next_record, dict):
                raise TelegramPushStoreError("record updater must return an object")
            next_record["telegramId"] = telegram_id
            if isinstance(current, dict) and next_record == current:
                return next_record
            next_record_size = len(self._serialize(next_record))
            if next_record_size > self.record_max_bytes and next_record_size >= current_record_size:
                raise TelegramPushRecordTooLargeError(
                    actual_bytes=next_record_size,
                    max_bytes=self.record_max_bytes,
                )
            if current is None and len(records) >= self.store_max_records:
                self._prune_expired_unreachable(records, exclude_key=str(telegram_id))
            if current is None and len(records) >= self.store_max_records:
                raise TelegramPushStoreCapacityError(
                    f"push store record limit reached ({self.store_max_records})"
                )
            records[str(telegram_id)] = next_record
            serialized_store = self._serialize_store(store)
            if len(serialized_store) > self.store_max_bytes:
                self._prune_expired_unreachable(records, exclude_key=str(telegram_id))
                serialized_store = self._serialize_store(store)
            if (
                len(serialized_store) > self.store_max_bytes
                and len(serialized_store) >= current_store_size
            ):
                raise TelegramPushStoreCapacityError(
                    f"push store is {len(serialized_store)} bytes; "
                    f"limit is {self.store_max_bytes} bytes"
                )
            self._write_unlocked(store, serialized=serialized_store)
            return next_record

    def _prune_expired_unreachable(
        self,
        records: dict[str, Any],
        *,
        exclude_key: str,
    ) -> None:
        cutoff = self._clock() - self.unreachable_retention_seconds
        expired: list[tuple[float, str]] = []
        for record_key, record in records.items():
            if record_key == exclude_key or not isinstance(record, dict):
                continue
            if record.get("chatReachable") is not False:
                continue
            if _record_has_reset_fence(record):
                continue
            activity = self._record_activity_timestamp(record)
            if activity is not None and activity < cutoff:
                expired.append((activity, record_key))
        for _activity, record_key in sorted(expired):
            records.pop(record_key, None)

    @staticmethod
    def _record_activity_timestamp(record: dict[str, Any]) -> float | None:
        return _record_activity_timestamp(record)

    @staticmethod
    def _serialize(value: Any) -> bytes:
        return _serialize(value)

    def _serialize_store(self, store: dict[str, Any]) -> bytes:
        return _serialize_store(store)

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.empty()
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise TelegramPushStoreError(f"cannot read push store: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise TelegramPushStoreError(
                f"invalid push store JSON at line {exc.lineno}, column {exc.colno}"
            ) from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("records"), dict):
            raise TelegramPushStoreError("push store must contain a records object")
        parsed["version"] = self.version
        return parsed

    def _write_unlocked(
        self,
        store: dict[str, Any],
        *,
        serialized: bytes | None = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                output = serialized if serialized is not None else self._serialize_store(store)
                temp_file.write(output)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, self.path)
            temp_path = None
            # fsyncing the temporary file does not make the directory entry created
            # by rename durable across a host power loss.
            directory_fd = os.open(
                self.path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise TelegramPushStoreError(f"cannot write push store: {exc}") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)


_SQLITE_INITIALIZATION_LOCK = Lock()
_SQLITE_INITIALIZED_IDENTITIES: dict[Path, tuple[int, int]] = {}


class SQLiteTelegramPushStore:
    """Cross-process Telegram push registry with one durable row per owner."""

    def __init__(
        self,
        path: Path,
        *,
        version: int,
        record_max_bytes: int = DEFAULT_PUSH_RECORD_MAX_BYTES,
        store_max_bytes: int = DEFAULT_PUSH_STORE_MAX_BYTES,
        store_max_records: int = DEFAULT_PUSH_STORE_MAX_RECORDS,
        unreachable_retention_seconds: int = DEFAULT_PUSH_UNREACHABLE_RETENTION_SECONDS,
        clock: Callable[[], float] = time.time,
        legacy_json_path: Path | None = None,
        legacy_json_required: bool = False,
    ) -> None:
        _validate_capacity_limits(
            record_max_bytes=record_max_bytes,
            store_max_bytes=store_max_bytes,
            store_max_records=store_max_records,
            unreachable_retention_seconds=unreachable_retention_seconds,
        )
        self.path = path.expanduser().resolve()
        self.version = version
        self.record_max_bytes = record_max_bytes
        self.store_max_bytes = store_max_bytes
        self.store_max_records = store_max_records
        self.unreachable_retention_seconds = unreachable_retention_seconds
        self._clock = clock
        self.legacy_json_path = (
            legacy_json_path.expanduser().resolve()
            if legacy_json_path is not None
            else self.path.with_suffix(".json")
        )
        self.legacy_json_required = legacy_json_required
        if self.legacy_json_path == self.path:
            raise ValueError("legacy JSON path must differ from the SQLite push store path")
        self._ensure_initialized()

    def empty(self) -> dict[str, Any]:
        return {"version": self.version, "records": {}}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=SQLITE_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        try:
            connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_SECONDS * 1_000}")
            # A lost update can replay user-visible events and paid story work. Favor
            # power-loss durability over write latency at this registry's volume.
            connection.execute("PRAGMA synchronous=FULL")
            return connection
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int] | None:
        try:
            stat_result = path.stat()
        except FileNotFoundError:
            return None
        return (stat_result.st_dev, stat_result.st_ino)

    def _ensure_initialized(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _SQLITE_INITIALIZATION_LOCK:
            identity = self._file_identity(self.path)
            if identity is not None and _SQLITE_INITIALIZED_IDENTITIES.get(self.path) == identity:
                return
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_RDWR,
                    0o600,
                )
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            self._initialize()
            initialized_identity = self._file_identity(self.path)
            if initialized_identity is None:
                raise TelegramPushStoreError("SQLite push store was not created")
            _SQLITE_INITIALIZED_IDENTITIES[self.path] = initialized_identity

    @contextmanager
    def _legacy_json_lock(self):
        lock_path = self.legacy_json_path.with_suffix(f"{self.legacy_json_path.suffix}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.chmod(lock_path, 0o600)
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _initialize(self) -> None:
        try:
            # Lock ordering is legacy JSON first, SQLite second. This gives the
            # import a stable source snapshot while old and new binaries are
            # stopped and restarted together during a deployment.
            with self._legacy_json_lock():
                with self._connection() as connection:
                    self._validate_database_identity(connection)
                    for database_file in (
                        self.path,
                        Path(f"{self.path}-wal"),
                        Path(f"{self.path}-shm"),
                    ):
                        if database_file.exists():
                            os.chmod(database_file, 0o600)
                    self._enable_wal_with_retry(connection)
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        self._create_schema(connection)
                        self._validate_metadata(connection)
                        self._import_legacy_json_once(connection)
                        connection.execute(
                            "UPDATE push_store_meta SET version = ? WHERE singleton = 1",
                            (self.version,),
                        )
                        connection.commit()
                    except BaseException:
                        connection.rollback()
                        raise
                for database_file in (
                    self.path,
                    Path(f"{self.path}-wal"),
                    Path(f"{self.path}-shm"),
                ):
                    if database_file.exists():
                        os.chmod(database_file, 0o600)
                # Do not release the legacy lock until the new database name and
                # WAL sidecars are durable in the parent directory.
                self._fsync_parent_directory()
        except TelegramPushStoreError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise TelegramPushStoreError(f"cannot initialize SQLite push store: {exc}") from exc

    @staticmethod
    def _enable_wal_with_retry(connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + SQLITE_BUSY_TIMEOUT_SECONDS
        delay = 0.01
        while True:
            try:
                mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
                if mode is None or str(mode[0]).casefold() != "wal":
                    raise TelegramPushStoreError("SQLite push store could not enable WAL mode")
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
    def _validate_database_identity(connection: sqlite3.Connection) -> None:
        existing_tables = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_schema
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }
        required_tables = {
            "push_records",
            "push_store_meta",
            "push_store_migrations",
        }
        if existing_tables and not required_tables <= existing_tables:
            raise TelegramPushStoreError(
                "refusing to initialize Telegram push storage in an unrelated SQLite database"
            )

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS push_records (
                record_key TEXT PRIMARY KEY,
                record_json TEXT NOT NULL,
                fragment_bytes INTEGER NOT NULL CHECK (fragment_bytes >= 0),
                chat_reachable INTEGER,
                activity_at REAL
            ) WITHOUT ROWID
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS push_records_unreachable_activity_idx
            ON push_records (activity_at, record_key)
            WHERE chat_reachable = 0 AND activity_at IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS push_store_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                version INTEGER NOT NULL,
                record_count INTEGER NOT NULL CHECK (record_count >= 0),
                total_fragment_bytes INTEGER NOT NULL CHECK (total_fragment_bytes >= 0)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO push_store_meta (
                singleton, version, record_count, total_fragment_bytes
            ) VALUES (1, 0, 0, 0)
            ON CONFLICT(singleton) DO NOTHING
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS push_store_migrations (
                name TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_sha256 TEXT,
                imported_records INTEGER NOT NULL,
                completed_at TEXT NOT NULL
            ) WITHOUT ROWID
            """
        )

    @staticmethod
    def _validate_metadata(connection: sqlite3.Connection) -> None:
        meta = connection.execute(
            """
            SELECT record_count, total_fragment_bytes
            FROM push_store_meta
            WHERE singleton = 1
            """
        ).fetchone()
        actual = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(fragment_bytes), 0) FROM push_records"
        ).fetchone()
        if (
            meta is None
            or actual is None
            or (int(meta[0]), int(meta[1]))
            != (
                int(actual[0]),
                int(actual[1]),
            )
        ):
            raise TelegramPushStoreError("SQLite push store capacity metadata is inconsistent")

    def _import_legacy_json_once(self, connection: sqlite3.Connection) -> None:
        marker = connection.execute(
            """
            SELECT status, source_sha256
            FROM push_store_migrations
            WHERE name = ?
            """,
            (LEGACY_JSON_IMPORT_MIGRATION,),
        ).fetchone()
        if marker is not None:
            self._assert_legacy_source_matches_marker(
                status=str(marker[0]),
                source_sha256=str(marker[1]) if marker[1] is not None else None,
            )
            return

        count_row = connection.execute(
            "SELECT record_count FROM push_store_meta WHERE singleton = 1"
        ).fetchone()
        if count_row is None:
            raise TelegramPushStoreError("SQLite push store metadata is missing")
        current_count = int(count_row[0])
        if current_count != 0:
            raise TelegramPushStoreError(
                "refusing legacy JSON import into a non-empty unmarked SQLite push store"
            )

        source_sha256: str | None = None
        status = "source-absent"
        imported_records = 0
        total_fragment_bytes = 0
        source_bytes = self._read_legacy_source_bytes()
        if source_bytes is None and self.legacy_json_required:
            raise TelegramPushStoreError(
                "required legacy push store is missing; refusing an empty SQLite migration"
            )
        if source_bytes is not None:
            try:
                parsed = json.loads(source_bytes.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise TelegramPushStoreError("legacy push store is not valid UTF-8") from exc
            except json.JSONDecodeError as exc:
                raise TelegramPushStoreError(
                    f"invalid legacy push store JSON at line {exc.lineno}, column {exc.colno}"
                ) from exc
            if not isinstance(parsed, dict) or not isinstance(parsed.get("records"), dict):
                raise TelegramPushStoreError("legacy push store must contain a records object")
            source_sha256 = hashlib.sha256(source_bytes).hexdigest()
            for record_key, record in parsed["records"].items():
                key = str(record_key)
                fragment_bytes = self._record_fragment_size(key, record)
                chat_reachable, activity_at = self._record_index_values(record)
                connection.execute(
                    """
                    INSERT INTO push_records (
                        record_key, record_json, fragment_bytes, chat_reachable, activity_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        self._encode_record(record),
                        fragment_bytes,
                        chat_reachable,
                        activity_at,
                    ),
                )
                imported_records += 1
                total_fragment_bytes += fragment_bytes
            connection.execute(
                """
                UPDATE push_store_meta
                SET record_count = ?, total_fragment_bytes = ?
                WHERE singleton = 1
                """,
                (imported_records, total_fragment_bytes),
            )
            status = "imported"

        connection.execute(
            """
            INSERT INTO push_store_migrations (
                name, status, source_path, source_sha256, imported_records, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                LEGACY_JSON_IMPORT_MIGRATION,
                status,
                str(self.legacy_json_path),
                source_sha256,
                imported_records,
                datetime.now(UTC).isoformat(),
            ),
        )

    def _read_legacy_source_bytes(self) -> bytes | None:
        try:
            return self.legacy_json_path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise TelegramPushStoreError(f"cannot read legacy push store: {exc}") from exc

    def _assert_legacy_source_matches_marker(
        self,
        *,
        status: str,
        source_sha256: str | None,
    ) -> None:
        source_bytes = self._read_legacy_source_bytes()
        if status == "source-absent":
            if source_bytes is not None:
                raise TelegramPushStoreError(
                    "legacy push store appeared after the no-source migration marker"
                )
            return
        if status != "imported" or source_sha256 is None:
            raise TelegramPushStoreError("SQLite push store has an invalid legacy import marker")
        # Removing an already verified backup is safe. If it is still present,
        # however, any byte change can be a stale old process writing split-brain state.
        if source_bytes is not None and hashlib.sha256(source_bytes).hexdigest() != source_sha256:
            raise TelegramPushStoreError("legacy push store changed after its SQLite import")

    def _fsync_parent_directory(self) -> None:
        descriptor = os.open(
            self.path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _encode_record(record: Any) -> str:
        return json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _decode_record(record_json: str) -> Any:
        try:
            return json.loads(record_json)
        except json.JSONDecodeError as exc:
            raise TelegramPushStoreError("SQLite push store contains invalid record JSON") from exc

    @staticmethod
    def _record_fragment_size(record_key: str, record: Any) -> int:
        serialized_key = json.dumps(record_key, ensure_ascii=False)
        value_lines = _serialize(record).decode("utf-8").splitlines()
        if len(value_lines) == 1:
            fragment = f"    {serialized_key}: {value_lines[0]}"
        else:
            fragment_lines = [f"    {serialized_key}: {value_lines[0]}"]
            fragment_lines.extend(f"    {line}" for line in value_lines[1:])
            fragment = "\n".join(fragment_lines)
        return len(fragment.encode("utf-8"))

    @staticmethod
    def _logical_store_size(
        *,
        version: int,
        record_count: int,
        total_fragment_bytes: int,
    ) -> int:
        if record_count == 0:
            return len(_serialize_store({"version": version, "records": {}}))
        prefix = b'{\n  "records": {\n'
        suffix = b'\n  },\n  "version": ' + json.dumps(version).encode("utf-8") + b"\n}\n"
        return len(prefix) + total_fragment_bytes + (record_count - 1) * len(b",\n") + len(suffix)

    @staticmethod
    def _record_index_values(record: Any) -> tuple[int | None, float | None]:
        if not isinstance(record, dict):
            return None, None
        reachable_value = record.get("chatReachable")
        if _record_has_reset_fence(record):
            # The SQLite index is used only for destructive retention pruning.
            # A delete fence must outlive unreachable-chat retention so an old
            # client cannot recreate removed pet data.
            chat_reachable = 1
        elif reachable_value is False:
            chat_reachable = 0
        elif reachable_value is True:
            chat_reachable = 1
        else:
            chat_reachable = None
        return chat_reachable, _record_activity_timestamp(record)

    def read(self) -> dict[str, Any]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT record_key, record_json FROM push_records ORDER BY record_key"
                ).fetchall()
            return {
                "version": self.version,
                "records": {
                    str(record_key): self._decode_record(str(record_json))
                    for record_key, record_json in rows
                },
            }
        except TelegramPushStoreError:
            raise
        except sqlite3.Error as exc:
            raise TelegramPushStoreError(f"cannot read SQLite push store: {exc}") from exc

    def replace_record(self, record: dict[str, Any]) -> dict[str, Any]:
        telegram_id = record.get("telegramId")
        if not isinstance(telegram_id, int):
            raise TelegramPushStoreError("record.telegramId must be an integer")
        return self.update_record(telegram_id, lambda _current: record)

    def update_record(
        self,
        telegram_id: int,
        updater: Callable[[dict[str, Any] | None], dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    result = self._update_record_in_transaction(
                        connection,
                        telegram_id=telegram_id,
                        updater=updater,
                    )
                    connection.commit()
                    return result
                except BaseException:
                    connection.rollback()
                    raise
        except TelegramPushStoreError:
            raise
        except sqlite3.Error as exc:
            raise TelegramPushStoreError(f"cannot update SQLite push store: {exc}") from exc

    def _update_record_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        telegram_id: int,
        updater: Callable[[dict[str, Any] | None], dict[str, Any]],
    ) -> dict[str, Any]:
        record_key = str(telegram_id)
        current_row = connection.execute(
            """
            SELECT record_json, fragment_bytes
            FROM push_records
            WHERE record_key = ?
            """,
            (record_key,),
        ).fetchone()
        current_value = (
            self._decode_record(str(current_row[0])) if current_row is not None else None
        )
        current = current_value if isinstance(current_value, dict) else None
        current_record_size = len(_serialize(current_value)) if current_row is not None else 0
        current_fragment_bytes = int(current_row[1]) if current_row is not None else 0
        next_record = updater(deepcopy(current) if current is not None else None)
        if not isinstance(next_record, dict):
            raise TelegramPushStoreError("record updater must return an object")
        next_record["telegramId"] = telegram_id
        if current is not None and next_record == current:
            return next_record

        next_record_size = len(_serialize(next_record))
        if next_record_size > self.record_max_bytes and next_record_size >= current_record_size:
            raise TelegramPushRecordTooLargeError(
                actual_bytes=next_record_size,
                max_bytes=self.record_max_bytes,
            )

        meta = self._read_meta(connection)
        original_store_size = self._logical_store_size(
            version=self.version,
            record_count=meta[0],
            total_fragment_bytes=meta[1],
        )
        is_new = current_row is None
        if is_new and meta[0] >= self.store_max_records:
            self._prune_expired_unreachable(connection, exclude_key=record_key)
            meta = self._read_meta(connection)
        if is_new and meta[0] >= self.store_max_records:
            raise TelegramPushStoreCapacityError(
                f"push store record limit reached ({self.store_max_records})"
            )

        next_fragment_bytes = self._record_fragment_size(record_key, next_record)
        next_count = meta[0] + (1 if is_new else 0)
        next_total_fragment_bytes = meta[1] - current_fragment_bytes + next_fragment_bytes
        next_store_size = self._logical_store_size(
            version=self.version,
            record_count=next_count,
            total_fragment_bytes=next_total_fragment_bytes,
        )
        if next_store_size > self.store_max_bytes:
            self._prune_expired_unreachable(connection, exclude_key=record_key)
            meta = self._read_meta(connection)
            next_count = meta[0] + (1 if is_new else 0)
            next_total_fragment_bytes = meta[1] - current_fragment_bytes + next_fragment_bytes
            next_store_size = self._logical_store_size(
                version=self.version,
                record_count=next_count,
                total_fragment_bytes=next_total_fragment_bytes,
            )
        if next_store_size > self.store_max_bytes and next_store_size >= original_store_size:
            raise TelegramPushStoreCapacityError(
                f"push store is {next_store_size} bytes; limit is {self.store_max_bytes} bytes"
            )

        chat_reachable, activity_at = self._record_index_values(next_record)
        connection.execute(
            """
            INSERT INTO push_records (
                record_key, record_json, fragment_bytes, chat_reachable, activity_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(record_key) DO UPDATE SET
                record_json = excluded.record_json,
                fragment_bytes = excluded.fragment_bytes,
                chat_reachable = excluded.chat_reachable,
                activity_at = excluded.activity_at
            """,
            (
                record_key,
                self._encode_record(next_record),
                next_fragment_bytes,
                chat_reachable,
                activity_at,
            ),
        )
        connection.execute(
            """
            UPDATE push_store_meta
            SET record_count = ?, total_fragment_bytes = ?
            WHERE singleton = 1
            """,
            (next_count, next_total_fragment_bytes),
        )
        return next_record

    @staticmethod
    def _read_meta(connection: sqlite3.Connection) -> tuple[int, int]:
        row = connection.execute(
            """
            SELECT record_count, total_fragment_bytes
            FROM push_store_meta
            WHERE singleton = 1
            """
        ).fetchone()
        if row is None:
            raise TelegramPushStoreError("SQLite push store metadata is missing")
        return int(row[0]), int(row[1])

    def _prune_expired_unreachable(
        self,
        connection: sqlite3.Connection,
        *,
        exclude_key: str,
    ) -> None:
        cutoff = self._clock() - self.unreachable_retention_seconds
        removed = connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(fragment_bytes), 0)
            FROM push_records
            WHERE chat_reachable = 0
              AND activity_at IS NOT NULL
              AND activity_at < ?
              AND record_key != ?
            """,
            (cutoff, exclude_key),
        ).fetchone()
        if removed is None or int(removed[0]) == 0:
            return
        connection.execute(
            """
            DELETE FROM push_records
            WHERE chat_reachable = 0
              AND activity_at IS NOT NULL
              AND activity_at < ?
              AND record_key != ?
            """,
            (cutoff, exclude_key),
        )
        connection.execute(
            """
            UPDATE push_store_meta
            SET record_count = record_count - ?,
                total_fragment_bytes = total_fragment_bytes - ?
            WHERE singleton = 1
            """,
            (int(removed[0]), int(removed[1])),
        )


def create_telegram_push_store(
    path: Path,
    *,
    version: int,
    backend: TelegramPushStoreBackend = "auto",
    record_max_bytes: int = DEFAULT_PUSH_RECORD_MAX_BYTES,
    store_max_bytes: int = DEFAULT_PUSH_STORE_MAX_BYTES,
    store_max_records: int = DEFAULT_PUSH_STORE_MAX_RECORDS,
    unreachable_retention_seconds: int = DEFAULT_PUSH_UNREACHABLE_RETENTION_SECONDS,
    legacy_json_path: Path | None = None,
    legacy_json_required: bool = False,
) -> TelegramPushStore:
    normalized_backend = backend.casefold()
    if normalized_backend == "auto":
        suffix = path.suffix.casefold()
        if suffix == ".json":
            normalized_backend = "json"
        elif suffix in SQLITE_PUSH_STORE_SUFFIXES:
            normalized_backend = "sqlite"
        else:
            raise TelegramPushStoreError(
                "cannot infer Telegram push store backend from path suffix; "
                "use .json/.sqlite3 or configure the backend explicitly"
            )
    if normalized_backend == "json":
        return JsonTelegramPushStore(
            path,
            version=version,
            record_max_bytes=record_max_bytes,
            store_max_bytes=store_max_bytes,
            store_max_records=store_max_records,
            unreachable_retention_seconds=unreachable_retention_seconds,
        )
    if normalized_backend == "sqlite":
        return SQLiteTelegramPushStore(
            path,
            version=version,
            record_max_bytes=record_max_bytes,
            store_max_bytes=store_max_bytes,
            store_max_records=store_max_records,
            unreachable_retention_seconds=unreachable_retention_seconds,
            legacy_json_path=legacy_json_path,
            legacy_json_required=legacy_json_required,
        )
    raise TelegramPushStoreError(f"unsupported Telegram push store backend: {backend}")
