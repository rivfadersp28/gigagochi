from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def append_bounded_jsonl(
    path: Path,
    payload: Mapping[str, Any],
    *,
    max_bytes: int,
    backup_count: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded_line = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
    if len(encoded_line) > max_bytes:
        encoded_line = _oversized_entry_marker(
            payload,
            original=encoded_line,
            max_bytes=max_bytes,
        )
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            current_size = path.stat().st_size if path.exists() else 0
            if current_size and current_size + len(encoded_line) > max_bytes:
                _rotate(path, backup_count=backup_count)
            with path.open("ab") as log_file:
                log_file.write(encoded_line)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _oversized_entry_marker(
    payload: Mapping[str, Any],
    *,
    original: bytes,
    max_bytes: int,
) -> bytes:
    context = {
        key: payload[key] for key in ("timestamp", "event", "promptType", "label") if key in payload
    }
    digest = hashlib.sha256(original).hexdigest()
    candidates = (
        {
            **context,
            "logEntryTruncated": True,
            "originalBytes": len(original),
            "sha256": digest,
        },
        {
            "logEntryTruncated": True,
            "originalBytes": len(original),
            "sha256": digest,
        },
        {"logEntryTruncated": True},
    )
    for candidate in candidates:
        encoded = (
            json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        )
        if len(encoded) <= max_bytes:
            return encoded
    raise ValueError("max_bytes is too small for a valid JSONL truncation marker")


def _rotate(path: Path, *, backup_count: int) -> None:
    if backup_count <= 0:
        path.unlink(missing_ok=True)
        return

    oldest = path.with_name(f"{path.name}.{backup_count}")
    oldest.unlink(missing_ok=True)
    for index in range(backup_count - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
    if path.exists():
        os.replace(path, path.with_name(f"{path.name}.1"))
