from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from app.services.telegram_push_store import JsonTelegramPushStore, TelegramPushStoreError


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
