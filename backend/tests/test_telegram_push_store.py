from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.telegram_push_store import (
    JsonTelegramPushStore,
    SQLiteTelegramPushStore,
    TelegramPushRecordTooLargeError,
    TelegramPushStoreCapacityError,
    TelegramPushStoreError,
    create_telegram_push_store,
)


def _write_record_range(path_text: str, start: int, count: int) -> None:
    store = JsonTelegramPushStore(Path(path_text), version=1)
    for telegram_id in range(start, start + count):
        store.update_record(
            telegram_id,
            lambda current, telegram_id=telegram_id: {
                **(current or {}),
                "value": telegram_id,
            },
        )


def _write_sqlite_record_range(path_text: str, start: int, count: int) -> None:
    store = SQLiteTelegramPushStore(Path(path_text), version=1)
    for telegram_id in range(start, start + count):
        store.update_record(
            telegram_id,
            lambda current, telegram_id=telegram_id: {
                **(current or {}),
                "value": telegram_id,
            },
        )


def _increment_sqlite_record(path_text: str, telegram_id: int, count: int) -> None:
    store = SQLiteTelegramPushStore(Path(path_text), version=1)
    for _ in range(count):
        store.update_record(
            telegram_id,
            lambda current: {"counter": int((current or {}).get("counter", 0)) + 1},
        )


def _exit_after_uncommitted_sqlite_update(path_text: str) -> None:
    store = SQLiteTelegramPushStore(Path(path_text), version=1)
    connection = store._connect()
    connection.execute("BEGIN IMMEDIATE")
    store._update_record_in_transaction(
        connection,
        telegram_id=42,
        updater=lambda current: {**(current or {}), "value": "must-roll-back"},
    )
    os._exit(17)


def test_store_preserves_updates_from_multiple_processes(tmp_path) -> None:
    path = tmp_path / "push.json"
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_write_record_range, args=(str(path), start, 20))
        for start in (100, 200)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    records = JsonTelegramPushStore(path, version=1).read()["records"]
    assert len(records) == 40
    assert records["100"]["value"] == 100
    assert records["219"]["value"] == 219


def test_store_refuses_to_replace_corrupt_data(tmp_path) -> None:
    path = tmp_path / "push.json"
    path.write_text('{"records":', encoding="utf-8")
    store = JsonTelegramPushStore(path, version=1)

    with pytest.raises(TelegramPushStoreError, match="invalid push store JSON"):
        store.update_record(42, lambda _current: {"chatReachable": True})

    assert path.read_text(encoding="utf-8") == '{"records":'


def test_store_fsyncs_parent_directory_after_atomic_replace(monkeypatch, tmp_path) -> None:
    fsynced_modes: list[int] = []
    real_fsync = os.fsync

    def record_fsync(file_descriptor: int) -> None:
        fsynced_modes.append(os.fstat(file_descriptor).st_mode)
        real_fsync(file_descriptor)

    monkeypatch.setattr("app.services.telegram_push_store.os.fsync", record_fsync)

    JsonTelegramPushStore(tmp_path / "push.json", version=1).replace_record(
        {"telegramId": 42, "chatReachable": True}
    )

    assert any(stat.S_ISREG(mode) for mode in fsynced_modes)
    assert any(stat.S_ISDIR(mode) for mode in fsynced_modes)


def test_store_rejects_oversized_record_without_changing_file(tmp_path) -> None:
    path = tmp_path / "push.json"
    store = JsonTelegramPushStore(path, version=1, record_max_bytes=256)
    store.replace_record({"telegramId": 42, "value": "ok"})
    before = path.read_bytes()

    with pytest.raises(TelegramPushRecordTooLargeError):
        store.update_record(42, lambda current: {**(current or {}), "blob": "x" * 1_000})

    assert path.read_bytes() == before


def test_store_allows_shrinking_a_legacy_oversized_record(tmp_path) -> None:
    path = tmp_path / "push.json"
    unlimited = JsonTelegramPushStore(path, version=1, record_max_bytes=4_096)
    unlimited.replace_record({"telegramId": 42, "blob": "x" * 1_000})

    bounded = JsonTelegramPushStore(path, version=1, record_max_bytes=128)
    bounded.replace_record({"telegramId": 42, "value": "smaller"})

    assert bounded.read()["records"]["42"] == {"telegramId": 42, "value": "smaller"}


def test_store_enforces_record_count_capacity(tmp_path) -> None:
    path = tmp_path / "push.json"
    store = JsonTelegramPushStore(path, version=1, store_max_records=1)
    store.replace_record({"telegramId": 1})
    before = path.read_bytes()

    with pytest.raises(TelegramPushStoreCapacityError, match="record limit"):
        store.replace_record({"telegramId": 2})

    assert path.read_bytes() == before


def test_store_evicts_only_expired_unreachable_record_at_capacity(tmp_path) -> None:
    path = tmp_path / "push.json"
    now = datetime(2026, 7, 15, tzinfo=UTC).timestamp()
    initial = JsonTelegramPushStore(path, version=1, store_max_records=2)
    initial.replace_record(
        {
            "telegramId": 1,
            "chatReachable": False,
            "lastChatSeenAt": "2025-01-01T00:00:00Z",
        }
    )
    initial.replace_record(
        {
            "telegramId": 2,
            "chatReachable": True,
            "lastChatSeenAt": "2025-01-01T00:00:00Z",
        }
    )
    bounded = JsonTelegramPushStore(
        path,
        version=1,
        store_max_records=2,
        unreachable_retention_seconds=90 * 24 * 60 * 60,
        clock=lambda: now,
    )

    bounded.replace_record({"telegramId": 3, "chatReachable": True})

    records = bounded.read()["records"]
    assert set(records) == {"2", "3"}


def test_store_does_not_evict_unreachable_record_without_activity_timestamp(tmp_path) -> None:
    path = tmp_path / "push.json"
    store = JsonTelegramPushStore(path, version=1, store_max_records=1)
    store.replace_record({"telegramId": 1, "chatReachable": False})

    with pytest.raises(TelegramPushStoreCapacityError):
        store.replace_record({"telegramId": 2})

    assert set(store.read()["records"]) == {"1"}


def test_store_never_evicts_durable_pet_delete_fence(tmp_path) -> None:
    path = tmp_path / "push.json"
    now = datetime(2026, 7, 15, tzinfo=UTC).timestamp()
    store = JsonTelegramPushStore(
        path,
        version=1,
        store_max_records=1,
        unreachable_retention_seconds=90 * 24 * 60 * 60,
        clock=lambda: now,
    )
    store.replace_record(
        {
            "telegramId": 1,
            "chatReachable": False,
            "lastChatSeenAt": "2025-01-01T00:00:00Z",
            "petResetTombstones": [{"petId": "deleted-pet", "requestedAt": "2025-01-01T00:00:00Z"}],
        }
    )

    with pytest.raises(TelegramPushStoreCapacityError):
        store.replace_record({"telegramId": 2, "chatReachable": True})

    assert set(store.read()["records"]) == {"1"}


def test_store_enforces_total_serialized_capacity_atomically(tmp_path) -> None:
    path = tmp_path / "push.json"
    store = JsonTelegramPushStore(
        path,
        version=1,
        record_max_bytes=4_096,
        store_max_bytes=512,
    )
    store.replace_record({"telegramId": 1, "value": "ok"})
    before = path.read_bytes()

    with pytest.raises(TelegramPushStoreCapacityError, match="push store is"):
        store.replace_record({"telegramId": 2, "blob": "x" * 1_000})

    assert path.read_bytes() == before


def test_store_skips_atomic_rewrite_for_noop_update(monkeypatch, tmp_path) -> None:
    path = tmp_path / "push.json"
    store = JsonTelegramPushStore(path, version=1)
    store.replace_record({"telegramId": 42, "value": "unchanged"})
    before = path.read_bytes()

    def unexpected_write(*_args, **_kwargs) -> None:
        raise AssertionError("no-op update must not fsync/rewrite the store")

    monkeypatch.setattr(store, "_write_unlocked", unexpected_write)

    result = store.update_record(42, lambda current: dict(current or {}))

    assert result == {"telegramId": 42, "value": "unchanged"}
    assert path.read_bytes() == before


@pytest.mark.parametrize("suffix", [".json", ".sqlite3"])
def test_store_persists_nested_mutations_from_transactional_updater(tmp_path, suffix) -> None:
    store = create_telegram_push_store(tmp_path / f"push{suffix}", version=1)
    store.replace_record({"telegramId": 42, "nested": {"counter": 1}})

    def increment_nested(current: dict | None) -> dict:
        assert current is not None
        current["nested"]["counter"] += 1
        return current

    store.update_record(42, increment_nested)

    assert store.read()["records"]["42"]["nested"] == {"counter": 2}


def test_sqlite_store_round_trips_records_in_wal_mode(tmp_path) -> None:
    path = tmp_path / "push.sqlite3"
    store = SQLiteTelegramPushStore(path, version=3)

    store.replace_record({"telegramId": 42, "name": "Ёж", "chatReachable": True})
    updated = store.update_record(
        42,
        lambda current: {**(current or {}), "visits": 2},
    )

    assert updated == {
        "telegramId": 42,
        "name": "Ёж",
        "chatReachable": True,
        "visits": 2,
    }
    assert store.read() == {"version": 3, "records": {"42": updated}}
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    with store._connection() as connection:
        assert connection.execute("PRAGMA synchronous").fetchone() == (2,)
    for private_path in (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        tmp_path / "push.json.lock",
    ):
        assert stat.S_IMODE(private_path.stat().st_mode) == 0o600


def test_sqlite_store_fsyncs_parent_directory_after_initialization(monkeypatch, tmp_path) -> None:
    fsynced_modes: list[int] = []
    real_fsync = os.fsync

    def record_fsync(file_descriptor: int) -> None:
        fsynced_modes.append(os.fstat(file_descriptor).st_mode)
        real_fsync(file_descriptor)

    monkeypatch.setattr("app.services.telegram_push_store.os.fsync", record_fsync)

    SQLiteTelegramPushStore(tmp_path / "push.sqlite3", version=1)

    assert any(stat.S_ISDIR(mode) for mode in fsynced_modes)


def test_sqlite_store_preserves_updates_from_multiple_processes(tmp_path) -> None:
    path = tmp_path / "push.sqlite3"
    (tmp_path / "push.json").write_text(
        json.dumps({"version": 1, "records": {"1": {"telegramId": 1, "source": "legacy"}}}),
        encoding="utf-8",
    )
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_write_sqlite_record_range, args=(str(path), start, 25))
        for start in (100, 200, 300, 400)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    records = SQLiteTelegramPushStore(path, version=1).read()["records"]
    assert len(records) == 101
    assert records["1"] == {"telegramId": 1, "source": "legacy"}
    assert records["100"]["value"] == 100
    assert records["424"]["value"] == 424
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT record_count FROM push_store_meta WHERE singleton = 1"
        ).fetchone() == (101,)


def test_sqlite_store_serializes_concurrent_read_modify_write(tmp_path) -> None:
    path = tmp_path / "push.sqlite3"
    SQLiteTelegramPushStore(path, version=1).replace_record({"telegramId": 42, "counter": 0})
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_increment_sqlite_record, args=(str(path), 42, 20)) for _ in range(4)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    record = SQLiteTelegramPushStore(path, version=1).read()["records"]["42"]
    assert record["counter"] == 80


def test_sqlite_store_process_crash_rolls_back_record_and_capacity_metadata(tmp_path) -> None:
    path = tmp_path / "push.sqlite3"
    store = SQLiteTelegramPushStore(path, version=1)
    original = {"telegramId": 42, "value": "committed"}
    store.replace_record(original.copy())
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_exit_after_uncommitted_sqlite_update,
        args=(str(path),),
    )

    process.start()
    process.join(timeout=10)

    assert process.exitcode == 17
    assert store.read()["records"]["42"] == original
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
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
    assert meta == actual


def test_sqlite_store_single_update_writes_only_one_record_row(tmp_path) -> None:
    path = tmp_path / "push.sqlite3"
    store = SQLiteTelegramPushStore(path, version=1)
    for telegram_id in range(200):
        store.replace_record({"telegramId": telegram_id, "value": telegram_id})
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE record_write_audit (record_key TEXT NOT NULL);
            CREATE TRIGGER record_write_audit_update
            AFTER UPDATE ON push_records
            BEGIN
                INSERT INTO record_write_audit (record_key) VALUES (NEW.record_key);
            END;
            CREATE TRIGGER record_write_audit_insert
            AFTER INSERT ON push_records
            BEGIN
                INSERT INTO record_write_audit (record_key) VALUES (NEW.record_key);
            END;
            """
        )

    store.update_record(100, lambda current: {**(current or {}), "value": "changed"})

    with sqlite3.connect(path) as connection:
        writes = connection.execute("SELECT record_key FROM record_write_audit").fetchall()
        record_count = connection.execute("SELECT COUNT(*) FROM push_records").fetchone()
    assert writes == [("100",)]
    assert record_count == (200,)


def test_sqlite_store_logical_byte_accounting_matches_json_contract(tmp_path) -> None:
    path = tmp_path / "push.sqlite3"
    store = SQLiteTelegramPushStore(path, version=7)
    store.replace_record({"telegramId": 2, "nested": {"emoji": "🐾"}})
    store.replace_record({"telegramId": 10, "values": [1, 2, 3]})
    snapshot = store.read()
    expected_bytes = len(
        (json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )
    with sqlite3.connect(path) as connection:
        meta = connection.execute(
            """
            SELECT record_count, total_fragment_bytes
            FROM push_store_meta
            WHERE singleton = 1
            """
        ).fetchone()
    assert meta is not None
    assert (
        store._logical_store_size(
            version=7,
            record_count=int(meta[0]),
            total_fragment_bytes=int(meta[1]),
        )
        == expected_bytes
    )


def test_sqlite_store_enforces_record_and_total_capacity_atomically(tmp_path) -> None:
    count_path = tmp_path / "count.sqlite3"
    count_store = SQLiteTelegramPushStore(count_path, version=1, store_max_records=1)
    count_store.replace_record({"telegramId": 1})

    with pytest.raises(TelegramPushStoreCapacityError, match="record limit"):
        count_store.replace_record({"telegramId": 2})

    assert set(count_store.read()["records"]) == {"1"}

    bytes_path = tmp_path / "bytes.sqlite3"
    bytes_store = SQLiteTelegramPushStore(
        bytes_path,
        version=1,
        record_max_bytes=4_096,
        store_max_bytes=512,
    )
    bytes_store.replace_record({"telegramId": 1, "value": "ok"})

    with pytest.raises(TelegramPushStoreCapacityError, match="push store is"):
        bytes_store.replace_record({"telegramId": 2, "blob": "x" * 1_000})

    assert set(bytes_store.read()["records"]) == {"1"}


def test_sqlite_store_prunes_only_expired_unreachable_records_at_capacity(tmp_path) -> None:
    path = tmp_path / "push.sqlite3"
    now = datetime(2026, 7, 15, tzinfo=UTC).timestamp()
    initial = SQLiteTelegramPushStore(path, version=1, store_max_records=3)
    initial.replace_record(
        {
            "telegramId": 1,
            "chatReachable": False,
            "lastChatSeenAt": "2025-01-01T00:00:00Z",
        }
    )
    initial.replace_record(
        {
            "telegramId": 2,
            "chatReachable": True,
            "lastChatSeenAt": "2025-01-01T00:00:00Z",
        }
    )
    initial.replace_record({"telegramId": 3, "chatReachable": False})
    bounded = SQLiteTelegramPushStore(
        path,
        version=1,
        store_max_records=3,
        unreachable_retention_seconds=90 * 24 * 60 * 60,
        clock=lambda: now,
    )

    bounded.replace_record({"telegramId": 4, "chatReachable": True})

    assert set(bounded.read()["records"]) == {"2", "3", "4"}


def test_sqlite_store_never_evicts_durable_pet_delete_fence(tmp_path) -> None:
    path = tmp_path / "push.sqlite3"
    now = datetime(2026, 7, 15, tzinfo=UTC).timestamp()
    store = SQLiteTelegramPushStore(
        path,
        version=1,
        store_max_records=1,
        unreachable_retention_seconds=90 * 24 * 60 * 60,
        clock=lambda: now,
    )
    store.replace_record(
        {
            "telegramId": 1,
            "chatReachable": False,
            "lastChatSeenAt": "2025-01-01T00:00:00Z",
            "petResetTombstones": [{"petId": "deleted-pet", "requestedAt": "2025-01-01T00:00:00Z"}],
        }
    )

    with pytest.raises(TelegramPushStoreCapacityError):
        store.replace_record({"telegramId": 2, "chatReachable": True})

    assert set(store.read()["records"]) == {"1"}


def test_sqlite_store_rejects_oversized_record_but_allows_legacy_shrink(tmp_path) -> None:
    path = tmp_path / "push.sqlite3"
    store = SQLiteTelegramPushStore(path, version=1, record_max_bytes=256)
    store.replace_record({"telegramId": 42, "value": "ok"})

    with pytest.raises(TelegramPushRecordTooLargeError):
        store.update_record(42, lambda current: {**(current or {}), "blob": "x" * 1_000})

    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(
        json.dumps(
            {
                "version": 1,
                "records": {"7": {"telegramId": 7, "blob": "x" * 1_000}},
            }
        ),
        encoding="utf-8",
    )
    migrated = SQLiteTelegramPushStore(
        tmp_path / "migrated.sqlite3",
        version=1,
        record_max_bytes=128,
        legacy_json_path=legacy_path,
    )

    migrated.replace_record({"telegramId": 7, "value": "smaller"})

    assert migrated.read()["records"]["7"] == {"telegramId": 7, "value": "smaller"}


def test_sqlite_import_never_drops_legacy_rows_that_exceed_new_capacity(tmp_path) -> None:
    legacy_path = tmp_path / "legacy.json"
    legacy_records = {
        "1": {"telegramId": 1, "blob": "x" * 500},
        "2": {"telegramId": 2, "blob": "y" * 500},
    }
    legacy_path.write_text(
        json.dumps({"version": 1, "records": legacy_records}),
        encoding="utf-8",
    )
    store = SQLiteTelegramPushStore(
        tmp_path / "push.sqlite3",
        version=1,
        record_max_bytes=128,
        store_max_bytes=256,
        store_max_records=1,
        legacy_json_path=legacy_path,
    )

    assert store.read()["records"] == legacy_records
    with pytest.raises(TelegramPushStoreCapacityError):
        store.replace_record({"telegramId": 3})

    store.replace_record({"telegramId": 1, "value": "shrunk"})

    assert store.read()["records"] == {
        "1": {"telegramId": 1, "value": "shrunk"},
        "2": legacy_records["2"],
    }


def test_sqlite_store_imports_legacy_json_losslessly_in_one_transaction(tmp_path) -> None:
    legacy_path = tmp_path / "telegram_push_state.json"
    legacy_records = {
        "42": {"telegramId": 42, "name": "Лис", "chatReachable": True},
        "noncanonical": ["preserved", {"as": "legacy data"}],
    }
    legacy_bytes = (
        json.dumps(
            {"version": 0, "records": legacy_records},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    legacy_path.write_bytes(legacy_bytes)
    sqlite_path = tmp_path / "telegram_push_state.sqlite3"

    store = SQLiteTelegramPushStore(
        sqlite_path,
        version=1,
        legacy_json_path=legacy_path,
    )

    assert store.read() == {"version": 1, "records": legacy_records}
    with sqlite3.connect(sqlite_path) as connection:
        marker = connection.execute(
            """
            SELECT status, source_sha256, imported_records
            FROM push_store_migrations
            WHERE name = 'legacy-json-v1'
            """
        ).fetchone()
    assert marker == ("imported", hashlib.sha256(legacy_bytes).hexdigest(), 2)
    assert legacy_path.read_bytes() == legacy_bytes


def test_sqlite_store_durable_import_marker_prevents_reimport(tmp_path) -> None:
    legacy_path = tmp_path / "push.json"
    legacy_path.write_text(
        json.dumps({"version": 1, "records": {"1": {"telegramId": 1}}}),
        encoding="utf-8",
    )
    sqlite_path = tmp_path / "push.sqlite3"
    first = SQLiteTelegramPushStore(
        sqlite_path,
        version=1,
        legacy_json_path=legacy_path,
    )
    first.replace_record({"telegramId": 3, "source": "sqlite"})

    # An unchanged source is an idempotent restart.
    first._initialize()

    legacy_path.write_text(
        json.dumps({"version": 1, "records": {"2": {"telegramId": 2}}}),
        encoding="utf-8",
    )

    # A changed source indicates a stale JSON writer. Refuse it instead of
    # reimporting over newer SQLite state or silently accepting split brain.
    with pytest.raises(TelegramPushStoreError, match="changed after its SQLite import"):
        first._initialize()

    assert first.read()["records"] == {
        "1": {"telegramId": 1},
        "3": {"telegramId": 3, "source": "sqlite"},
    }


def test_sqlite_store_refuses_legacy_source_appearing_after_absent_marker(tmp_path) -> None:
    legacy_path = tmp_path / "push.json"
    store = SQLiteTelegramPushStore(
        tmp_path / "push.sqlite3",
        version=1,
        legacy_json_path=legacy_path,
    )
    store.replace_record({"telegramId": 1, "source": "sqlite"})
    legacy_path.write_text(
        json.dumps({"version": 1, "records": {"2": {"telegramId": 2}}}),
        encoding="utf-8",
    )

    with pytest.raises(TelegramPushStoreError, match="appeared after the no-source"):
        store._initialize()

    assert store.read()["records"] == {"1": {"telegramId": 1, "source": "sqlite"}}


def test_sqlite_store_required_legacy_source_is_fail_closed_and_retryable(tmp_path) -> None:
    legacy_path = tmp_path / "push.json"
    sqlite_path = tmp_path / "push.sqlite3"

    with pytest.raises(TelegramPushStoreError, match="required legacy push store is missing"):
        SQLiteTelegramPushStore(
            sqlite_path,
            version=1,
            legacy_json_path=legacy_path,
            legacy_json_required=True,
        )

    with sqlite3.connect(sqlite_path) as connection:
        tables = connection.execute(
            """
            SELECT name
            FROM sqlite_schema
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    assert tables == []

    legacy_path.write_text(
        json.dumps({"version": 1, "records": {"7": {"telegramId": 7}}}),
        encoding="utf-8",
    )
    recovered = SQLiteTelegramPushStore(
        sqlite_path,
        version=1,
        legacy_json_path=legacy_path,
        legacy_json_required=True,
    )

    assert recovered.read()["records"] == {"7": {"telegramId": 7}}


def test_sqlite_store_rolls_back_corrupt_legacy_import_and_can_retry(tmp_path) -> None:
    legacy_path = tmp_path / "push.json"
    legacy_path.write_text('{"records":', encoding="utf-8")
    sqlite_path = tmp_path / "push.sqlite3"

    with pytest.raises(TelegramPushStoreError, match="invalid legacy push store JSON"):
        SQLiteTelegramPushStore(
            sqlite_path,
            version=1,
            legacy_json_path=legacy_path,
        )

    legacy_path.write_text(
        json.dumps({"version": 1, "records": {"5": {"telegramId": 5}}}),
        encoding="utf-8",
    )
    recovered = SQLiteTelegramPushStore(
        sqlite_path,
        version=1,
        legacy_json_path=legacy_path,
    )

    assert recovered.read()["records"] == {"5": {"telegramId": 5}}


def test_sqlite_store_rolls_back_partial_legacy_insert_and_marker(monkeypatch, tmp_path) -> None:
    legacy_path = tmp_path / "push.json"
    legacy_records = {
        "1": {"telegramId": 1},
        "2": {"telegramId": 2},
    }
    legacy_path.write_text(
        json.dumps({"version": 1, "records": legacy_records}),
        encoding="utf-8",
    )
    sqlite_path = tmp_path / "push.sqlite3"
    original_encode = SQLiteTelegramPushStore._encode_record
    calls = 0

    def fail_second_record(record) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic import interruption")
        return original_encode(record)

    monkeypatch.setattr(
        SQLiteTelegramPushStore,
        "_encode_record",
        staticmethod(fail_second_record),
    )
    with pytest.raises(RuntimeError, match="synthetic import interruption"):
        SQLiteTelegramPushStore(
            sqlite_path,
            version=1,
            legacy_json_path=legacy_path,
        )

    monkeypatch.setattr(
        SQLiteTelegramPushStore,
        "_encode_record",
        staticmethod(original_encode),
    )
    recovered = SQLiteTelegramPushStore(
        sqlite_path,
        version=1,
        legacy_json_path=legacy_path,
    )

    assert recovered.read()["records"] == legacy_records


def test_sqlite_store_refuses_unmarked_import_over_existing_records(tmp_path) -> None:
    legacy_path = tmp_path / "push.json"
    sqlite_path = tmp_path / "push.sqlite3"
    store = SQLiteTelegramPushStore(
        sqlite_path,
        version=1,
        legacy_json_path=legacy_path,
    )
    store.replace_record({"telegramId": 1, "source": "sqlite"})
    with sqlite3.connect(sqlite_path) as connection:
        connection.execute("DELETE FROM push_store_migrations")
    legacy_path.write_text(
        json.dumps({"version": 1, "records": {"1": {"telegramId": 1, "source": "json"}}}),
        encoding="utf-8",
    )

    with pytest.raises(TelegramPushStoreError, match="non-empty unmarked"):
        store._initialize()

    assert store.read()["records"] == {"1": {"telegramId": 1, "source": "sqlite"}}


def test_sqlite_store_refuses_unrelated_database_without_modifying_it(tmp_path) -> None:
    path = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated_state (value TEXT NOT NULL)")
        connection.execute("INSERT INTO unrelated_state VALUES ('preserve-me')")
        original_journal_mode = connection.execute("PRAGMA journal_mode").fetchone()

    with pytest.raises(TelegramPushStoreError, match="unrelated SQLite database"):
        SQLiteTelegramPushStore(path, version=1)

    with sqlite3.connect(path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")
        }
        value = connection.execute("SELECT value FROM unrelated_state").fetchone()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
    assert tables == {"unrelated_state"}
    assert value == ("preserve-me",)
    assert journal_mode == original_journal_mode


def test_push_store_factory_selects_backend_by_suffix_or_explicit_config(tmp_path) -> None:
    json_store = create_telegram_push_store(tmp_path / "push.json", version=1)
    sqlite_store = create_telegram_push_store(tmp_path / "push.sqlite3", version=1)
    explicit_store = create_telegram_push_store(
        tmp_path / "custom.state",
        version=1,
        backend="sqlite",
    )

    assert isinstance(json_store, JsonTelegramPushStore)
    assert isinstance(sqlite_store, SQLiteTelegramPushStore)
    assert isinstance(explicit_store, SQLiteTelegramPushStore)
    with pytest.raises(TelegramPushStoreError, match="cannot infer"):
        create_telegram_push_store(tmp_path / "push.unknown", version=1)
