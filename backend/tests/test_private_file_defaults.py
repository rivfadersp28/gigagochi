from __future__ import annotations

import stat

from app.services.generation_job_store import GenerationJobStore
from app.services.interactive_travel_session_store import InteractiveTravelSessionStore
from app.services.jsonl_log import append_bounded_jsonl
from app.services.provider_task_receipt_store import ProviderTaskReceiptStore
from app.services.rate_limit_service import SQLiteRateLimiter


def _permission_bits(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_new_sqlite_runtime_stores_are_private(tmp_path) -> None:
    paths = [
        tmp_path / "generation.sqlite3",
        tmp_path / "owners.sqlite3",
        tmp_path / "rate.sqlite3",
        tmp_path / "provider-receipts.sqlite3",
    ]

    GenerationJobStore(paths[0])
    InteractiveTravelSessionStore(paths[1])
    SQLiteRateLimiter(paths[2])
    ProviderTaskReceiptStore(paths[3])

    for path in paths:
        assert _permission_bits(path) == 0o600


def test_new_jsonl_logs_and_locks_are_private(tmp_path) -> None:
    path = tmp_path / "ai-prompts.jsonl"

    append_bounded_jsonl(
        path,
        {"event": "synthetic", "message": "private"},
        max_bytes=65_536,
        backup_count=1,
    )

    assert _permission_bits(path) == 0o600
    assert _permission_bits(tmp_path / ".ai-prompts.jsonl.lock") == 0o600
