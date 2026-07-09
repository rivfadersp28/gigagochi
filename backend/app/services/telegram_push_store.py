from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class TelegramPushStoreError(RuntimeError):
    pass


class JsonTelegramPushStore:
    def __init__(self, path: Path, *, version: int) -> None:
        self.path = path
        self.version = version
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
            next_record = updater(current.copy() if isinstance(current, dict) else None)
            if not isinstance(next_record, dict):
                raise TelegramPushStoreError("record updater must return an object")
            next_record["telegramId"] = telegram_id
            records[str(telegram_id)] = next_record
            self._write_unlocked(store)
            return next_record

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

    def _write_unlocked(self, store: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                json.dump(store, temp_file, ensure_ascii=False, indent=2, sort_keys=True)
                temp_file.write("\n")
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, self.path)
            temp_path = None
        except OSError as exc:
            raise TelegramPushStoreError(f"cannot write push store: {exc}") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
