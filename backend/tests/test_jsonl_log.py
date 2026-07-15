import json
from concurrent.futures import ThreadPoolExecutor

from app.services.jsonl_log import append_bounded_jsonl


def _append(path, value: str) -> None:
    append_bounded_jsonl(
        path,
        {"value": value},
        max_bytes=35,
        backup_count=2,
    )


def test_append_bounded_jsonl_rotates_and_bounds_backups(tmp_path) -> None:
    path = tmp_path / "events.jsonl"

    _append(path, "first")
    _append(path, "second")
    _append(path, "third")
    _append(path, "fourth")

    assert json.loads(path.read_text(encoding="utf-8"))["value"] == "fourth"
    assert json.loads((tmp_path / "events.jsonl.1").read_text(encoding="utf-8"))["value"] == "third"
    second_backup = json.loads((tmp_path / "events.jsonl.2").read_text(encoding="utf-8"))
    assert second_backup["value"] == "second"
    assert not (tmp_path / "events.jsonl.3").exists()


def test_append_bounded_jsonl_can_drop_old_content_without_backups(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    append_bounded_jsonl(path, {"value": "first"}, max_bytes=20, backup_count=0)
    append_bounded_jsonl(path, {"value": "second"}, max_bytes=20, backup_count=0)

    assert json.loads(path.read_text(encoding="utf-8"))["value"] == "second"
    assert not (tmp_path / "events.jsonl.1").exists()


def test_append_bounded_jsonl_replaces_oversized_entry_with_marker(tmp_path) -> None:
    path = tmp_path / "events.jsonl"

    append_bounded_jsonl(
        path,
        {"event": "large", "value": "x" * 10_000},
        max_bytes=512,
        backup_count=2,
    )

    assert path.stat().st_size <= 512
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["event"] == "large"
    assert payload["logEntryTruncated"] is True
    assert payload["originalBytes"] > 10_000
    assert len(payload["sha256"]) == 64


def test_append_bounded_jsonl_serializes_concurrent_writers(tmp_path) -> None:
    path = tmp_path / "events.jsonl"

    def append(index: int) -> None:
        append_bounded_jsonl(
            path,
            {"index": index, "value": "x" * 100},
            max_bytes=1_000_000,
            backup_count=2,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append, range(100)))

    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert sorted(entry["index"] for entry in entries) == list(range(100))
