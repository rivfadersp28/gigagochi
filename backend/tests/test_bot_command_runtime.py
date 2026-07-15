from __future__ import annotations

import sqlite3
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Thread

import pytest

from app.services import bot_command_runtime
from app.services.bot_command_runtime import (
    BotCommandInboxFull,
    BotCommandLeaseKeeper,
    BotCommandPreparedResultError,
    BoundedBotCommandDispatcher,
    DurableBotCommand,
    SQLiteBotCommandInbox,
    load_bot_update_offset,
    normalize_bot_command_update,
    save_bot_update_offset,
)


def _durable_command(update_id: int, *, chat_id: int = 1001) -> DurableBotCommand:
    command = normalize_bot_command_update(
        {
            "update_id": update_id,
            "message": {
                "chat": {"id": chat_id},
                "from": {"id": chat_id, "first_name": "Test"},
                "text": "/story",
            },
        }
    )
    assert command is not None
    return command


def test_dispatcher_coalesces_per_chat_and_bounds_pending_commands() -> None:
    started = Event()
    release = Event()
    calls: list[int] = []

    def worker(chat_id: int, _keyboard: dict) -> None:
        calls.append(chat_id)
        started.set()
        release.wait(timeout=2)

    dispatcher = BoundedBotCommandDispatcher(max_workers=1, max_queued_commands=1)
    try:
        assert dispatcher.submit(worker, 1, {}) is True
        assert started.wait(timeout=1)
        assert all(dispatcher.submit(worker, 1, {}) is False for _ in range(100))
        assert dispatcher.submit(worker, 2, {}) is True
        assert dispatcher.submit(worker, 3, {}) is False
    finally:
        release.set()
        dispatcher.shutdown()

    assert calls == [1, 2]


def test_dispatcher_releases_chat_after_completion() -> None:
    calls: list[int] = []
    dispatcher = BoundedBotCommandDispatcher(max_workers=1, max_queued_commands=0)

    def worker(chat_id: int, _keyboard: dict) -> None:
        calls.append(chat_id)

    try:
        assert dispatcher.submit(worker, 1, {}) is True
        deadline = time.monotonic() + 1
        while not dispatcher.submit(worker, 1, {}):
            if time.monotonic() >= deadline:
                raise AssertionError("completed chat was not released")
            time.sleep(0.01)
    finally:
        dispatcher.shutdown()

    assert calls == [1, 1]


def test_bot_update_offset_is_atomic_and_rejects_corrupt_state(tmp_path: Path) -> None:
    path = tmp_path / "push" / "bot_update_offset.json"

    assert load_bot_update_offset(path) is None
    assert save_bot_update_offset(path, 43) == 43
    assert load_bot_update_offset(path) == 43

    path.write_text('{"offset": "bad"}', encoding="utf-8")
    assert load_bot_update_offset(path) is None


def test_bot_update_offset_fsyncs_file_and_parent_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "push" / "bot_update_offset.json"
    real_fsync = bot_command_runtime.os.fsync
    fsynced_types: list[str] = []

    def record_fsync(file_descriptor: int) -> None:
        mode = bot_command_runtime.os.fstat(file_descriptor).st_mode
        fsynced_types.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(file_descriptor)

    monkeypatch.setattr(bot_command_runtime.os, "fsync", record_fsync)

    assert save_bot_update_offset(path, 43) == 43
    assert fsynced_types == ["file", "directory"]


def test_inbox_rejects_zero_completed_marker_capacity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_completed must be positive"):
        SQLiteBotCommandInbox(tmp_path / "inbox.sqlite3", max_completed=0)


def test_pending_command_survives_offset_advance_and_process_restart(tmp_path: Path) -> None:
    inbox_path = tmp_path / "push" / "bot_command_inbox.sqlite3"
    offset_path = tmp_path / "push" / "bot_update_offset.json"
    update = {
        "update_id": 42,
        "message": {
            "chat": {"id": 1001},
            "from": {"id": 1001, "first_name": "Test"},
            "text": "/story",
        },
    }
    command = normalize_bot_command_update(update)
    assert command is not None

    first_process = SQLiteBotCommandInbox(inbox_path)
    assert first_process.enqueue(command) is True
    save_bot_update_offset(offset_path, 43)

    assert load_bot_update_offset(offset_path) == 43
    restarted_process = SQLiteBotCommandInbox(inbox_path)
    pending = restarted_process.list_ready(limit=10)
    assert [item.update_id for item in pending] == [42]


def test_prepared_result_survives_expired_lease_and_clears_on_complete(
    tmp_path: Path,
) -> None:
    now = [1_000.0]
    path = tmp_path / "inbox.sqlite3"
    first = SQLiteBotCommandInbox(path, clock=lambda: now[0])
    assert first.enqueue(_durable_command(1)) is True
    assert first.claim(1, owner="first", lease_seconds=30) is True
    prepared = {
        "version": 1,
        "kind": "story",
        "petId": "pet-1",
        "story": {"title": "Сохранённая история"},
    }
    assert first.checkpoint_prepared(1, owner="first", payload=prepared) is True

    now[0] += 31
    restarted = SQLiteBotCommandInbox(path, clock=lambda: now[0])
    assert restarted.claim(1, owner="second", lease_seconds=30) is True
    assert restarted.load_prepared(1, owner="second") == prepared
    assert restarted.complete(1, owner="second") is True

    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            "SELECT update_json, prepared_json FROM bot_command_inbox WHERE update_id = 1"
        ).fetchone()
    assert stored == ("{}", None)


def test_existing_inbox_schema_is_migrated_for_prepared_results(tmp_path: Path) -> None:
    path = tmp_path / "legacy-inbox.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE bot_command_inbox (
                update_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                command TEXT NOT NULL,
                update_json TEXT NOT NULL,
                status TEXT NOT NULL,
                lease_owner TEXT,
                lease_until REAL,
                created_at REAL NOT NULL,
                completed_at REAL
            )
            """
        )

    SQLiteBotCommandInbox(path)

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(bot_command_inbox)")}
    assert "prepared_json" in columns


def test_prepared_progress_advances_by_append_only_revisions(tmp_path: Path) -> None:
    inbox = SQLiteBotCommandInbox(tmp_path / "inbox.sqlite3")
    assert inbox.enqueue(_durable_command(1)) is True
    assert inbox.claim(1, owner="worker", lease_seconds=30) is True
    text = {
        "version": 2,
        "kind": "full_story",
        "checkpointRevision": 1,
        "progress": {"text": {"parts": [1, 2, 3, 4]}},
    }
    image = {
        **text,
        "checkpointRevision": 2,
        "progress": {
            **text["progress"],
            "part:1:image": {"url": "/static/generated/pet/image.png"},
        },
    }
    video = {
        **image,
        "checkpointRevision": 3,
        "progress": {
            **image["progress"],
            "part:1:video": {"url": "/static/generated/pet/video.mp4"},
        },
    }

    assert inbox.checkpoint_prepared(1, owner="worker", payload=text) is True
    assert inbox.checkpoint_prepared(1, owner="worker", payload=image) is True
    assert inbox.checkpoint_prepared(1, owner="worker", payload=video) is True
    assert inbox.load_prepared(1, owner="worker") == video

    changed_list = {
        **video,
        "checkpointRevision": 4,
        "progress": {
            **video["progress"],
            "text": {"parts": [1, 2, 3, 4, 5]},
        },
    }
    with pytest.raises(BotCommandPreparedResultError, match="monotonically"):
        inbox.checkpoint_prepared(1, owner="worker", payload=changed_list)

    skipped_revision = {
        **video,
        "checkpointRevision": 5,
        "progress": {**video["progress"], "preparedResult": {"story": {}}},
    }
    with pytest.raises(BotCommandPreparedResultError, match="monotonically"):
        inbox.checkpoint_prepared(1, owner="worker", payload=skipped_revision)


def test_normalizer_keeps_only_bounded_supported_command_fields() -> None:
    command = normalize_bot_command_update(
        {
            "update_id": 7,
            "ignored": "x" * 100_000,
            "message": {
                "chat": {"id": 9, "title": "private"},
                "from": {
                    "id": 8,
                    "first_name": "x" * 1_000,
                    "ignored": "secret",
                },
                "text": "/story@GigagochiBot ignored arguments",
                "photo": ["large-payload"],
            },
        }
    )

    assert command is not None
    assert command.command == "/story"
    assert command.update == {
        "update_id": 7,
        "message": {
            "chat": {"id": 9},
            "from": {"id": 8, "first_name": "x" * 256},
            "text": "/story",
        },
    }
    assert (
        normalize_bot_command_update(
            {"update_id": 8, "message": {"chat": {"id": 9}, "text": "hello"}}
        )
        is None
    )
    assert (
        normalize_bot_command_update(
            {"update_id": 9, "message": {"chat": {"id": 9}, "text": "/unknown"}}
        )
        is None
    )
    assert (
        normalize_bot_command_update({"update_id": 10, "message": {"chat": {}, "text": "/story"}})
        is None
    )


def test_inbox_stops_growing_when_pending_capacity_is_full(tmp_path: Path) -> None:
    inbox = SQLiteBotCommandInbox(tmp_path / "inbox.sqlite3", max_pending=1)
    assert inbox.enqueue(_durable_command(1)) is True

    with pytest.raises(BotCommandInboxFull):
        inbox.enqueue(_durable_command(2))

    assert [item.update_id for item in inbox.list_ready(limit=10)] == [1]


def test_per_chat_backlog_is_tombstoned_without_blocking_other_chats(
    tmp_path: Path,
) -> None:
    inbox = SQLiteBotCommandInbox(
        tmp_path / "inbox.sqlite3",
        max_pending=10,
        max_pending_per_chat=2,
    )
    assert inbox.enqueue(_durable_command(1, chat_id=1001)) is True
    assert inbox.enqueue(_durable_command(2, chat_id=1001)) is True

    assert inbox.enqueue(_durable_command(3, chat_id=1001)) is False
    assert inbox.status(3) == "completed"
    assert inbox.enqueue(_durable_command(4, chat_id=2002)) is True
    assert [item.update_id for item in inbox.list_ready(limit=10)] == [1, 4]


def test_completed_markers_are_bounded_by_count_and_retention(tmp_path: Path) -> None:
    now = [1_000.0]
    inbox = SQLiteBotCommandInbox(
        tmp_path / "inbox.sqlite3",
        max_completed=2,
        completed_retention_seconds=10,
        clock=lambda: now[0],
    )
    for update_id in (1, 2, 3):
        assert inbox.enqueue(_durable_command(update_id)) is True
        assert inbox.claim(update_id, owner="owner", lease_seconds=30) is True
        assert inbox.complete(update_id, owner="owner") is True
        now[0] += 1

    assert inbox.status(1) is None
    assert inbox.status(2) == "completed"
    assert inbox.status(3) == "completed"

    now[0] += 20
    assert inbox.enqueue(_durable_command(4)) is True
    assert inbox.status(2) is None
    assert inbox.status(3) is None


def test_only_one_process_claims_the_same_update(tmp_path: Path) -> None:
    path = tmp_path / "inbox.sqlite3"
    first = SQLiteBotCommandInbox(path)
    second = SQLiteBotCommandInbox(path)
    first.enqueue(_durable_command(1))
    barrier = Barrier(2)

    def claim(inbox: SQLiteBotCommandInbox, owner: str) -> bool:
        barrier.wait()
        return inbox.claim(1, owner=owner, lease_seconds=30)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda pair: claim(*pair),
                ((first, "first"), (second, "second")),
            )
        )

    assert sorted(results) == [False, True]


def test_only_oldest_command_per_chat_is_ready_and_claimable_across_processes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inbox.sqlite3"
    first = SQLiteBotCommandInbox(path)
    second = SQLiteBotCommandInbox(path)
    first.enqueue(_durable_command(1, chat_id=1001))
    first.enqueue(_durable_command(2, chat_id=1001))
    first.enqueue(_durable_command(3, chat_id=2002))

    assert [item.update_id for item in first.list_ready(limit=10)] == [1, 3]
    barrier = Barrier(2)

    def claim(inbox: SQLiteBotCommandInbox, update_id: int, owner: str) -> bool:
        barrier.wait()
        return inbox.claim(update_id, owner=owner, lease_seconds=30)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda arguments: claim(*arguments),
                ((first, 1, "first"), (second, 2, "second")),
            )
        )

    assert results == [True, False]
    assert [item.update_id for item in second.list_ready(limit=10)] == [3]

    assert first.complete(1, owner="first") is True
    assert [item.update_id for item in second.list_ready(limit=10)] == [2, 3]


def test_concurrent_offset_writers_keep_monotonic_maximum(tmp_path: Path) -> None:
    path = tmp_path / "push" / "bot-update-offset.json"
    barrier = Barrier(2)

    def save(offset: int) -> int:
        barrier.wait()
        return save_bot_update_offset(path, offset)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, (100, 200)))

    assert max(results) == 200
    assert load_bot_update_offset(path) == 200
    assert save_bot_update_offset(path, 150) == 200
    assert load_bot_update_offset(path) == 200


def test_expired_process_claim_is_recovered_after_restart(tmp_path: Path) -> None:
    now = [1_000.0]
    path = tmp_path / "inbox.sqlite3"
    first = SQLiteBotCommandInbox(path, clock=lambda: now[0])
    first.enqueue(_durable_command(1))
    assert first.claim(1, owner="crashed", lease_seconds=30) is True
    assert first.list_ready(limit=10) == []

    now[0] += 31
    restarted = SQLiteBotCommandInbox(path, clock=lambda: now[0])
    assert [item.update_id for item in restarted.list_ready(limit=10)] == [1]
    assert restarted.claim(1, owner="restarted", lease_seconds=30) is True


def test_lease_keeper_prevents_live_worker_from_being_recovered(tmp_path: Path) -> None:
    now = [1_000.0]
    inbox = SQLiteBotCommandInbox(tmp_path / "inbox.sqlite3", clock=lambda: now[0])
    inbox.enqueue(_durable_command(1))
    assert inbox.claim(1, owner="live", lease_seconds=3) is True
    keeper = BotCommandLeaseKeeper(
        inbox,
        owner="live",
        lease_seconds=3,
        heartbeat_interval_seconds=0.01,
    )
    keeper.add(1)
    keeper.start()
    try:
        now[0] += 2.9
        time.sleep(0.05)
        now[0] += 0.2
        assert inbox.list_ready(limit=10) == []
    finally:
        keeper.stop()


def test_lease_keeper_fences_exact_claim_lost_to_takeover(tmp_path: Path) -> None:
    now = [1_000.0]
    path = tmp_path / "inbox.sqlite3"
    inbox = SQLiteBotCommandInbox(path, clock=lambda: now[0])
    inbox.enqueue(_durable_command(1, chat_id=1001))
    inbox.enqueue(_durable_command(2, chat_id=1002))
    assert inbox.claim(1, owner="first", lease_seconds=3) is True
    assert inbox.claim(2, owner="first", lease_seconds=30) is True
    keeper = BotCommandLeaseKeeper(inbox, owner="first", lease_seconds=30)
    keeper.add(1)
    keeper.add(2)

    now[0] += 4
    takeover = SQLiteBotCommandInbox(path, clock=lambda: now[0])
    assert takeover.claim(1, owner="takeover", lease_seconds=30) is True

    renewed = keeper._renew_claims({1, 2}, source="test")

    assert renewed == {2}
    assert keeper.snapshot() == {2}
    assert keeper.ensure_owned(1) is False
    assert keeper.ensure_owned(2) is True


def test_command_lock_serializes_same_update_across_inbox_instances(tmp_path: Path) -> None:
    path = tmp_path / "inbox.sqlite3"
    first = SQLiteBotCommandInbox(path)
    second = SQLiteBotCommandInbox(path)
    first_acquired = Event()
    release_first = Event()
    second_acquired = Event()

    def hold_first() -> None:
        with first.command_lock(42):
            first_acquired.set()
            assert release_first.wait(timeout=2)

    def acquire_second() -> None:
        with second.command_lock(42):
            second_acquired.set()

    first_thread = Thread(target=hold_first)
    second_thread = Thread(target=acquire_second)
    first_thread.start()
    assert first_acquired.wait(timeout=1)
    second_thread.start()
    try:
        assert not second_acquired.wait(timeout=0.05)
    finally:
        release_first.set()
        first_thread.join(timeout=2)
        second_thread.join(timeout=2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert second_acquired.is_set()


def test_concurrent_fresh_inbox_initialization_retries_wal_lock(tmp_path: Path) -> None:
    path = tmp_path / "fresh.sqlite3"
    workers = 8
    barrier = Barrier(workers)

    def initialize(_index: int) -> str:
        barrier.wait()
        return str(SQLiteBotCommandInbox(path).path)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        paths = list(executor.map(initialize, range(workers)))

    assert paths == [str(path.resolve())] * workers
