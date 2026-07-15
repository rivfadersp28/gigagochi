from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Literal

from app.config import get_settings

TaskBankMode = Literal["easy", "hard"]
DEFAULT_TASK_BANK_MODE: TaskBankMode = "hard"
VALID_TASK_BANK_MODES = frozenset({"easy", "hard"})


def _mode_path() -> Path:
    value = getattr(
        get_settings(),
        "interactive_travel_task_bank_mode_path",
        "data/push/interactive_travel_task_bank_mode.txt",
    )
    return Path(str(value)).expanduser()


def read_task_bank_mode() -> TaskBankMode:
    try:
        value = _mode_path().read_text(encoding="utf-8").strip().lower()
    except (FileNotFoundError, OSError):
        return DEFAULT_TASK_BANK_MODE
    if value not in VALID_TASK_BANK_MODES:
        return DEFAULT_TASK_BANK_MODE
    return value  # type: ignore[return-value]


def write_task_bank_mode(mode: TaskBankMode) -> TaskBankMode:
    if mode not in VALID_TASK_BANK_MODES:
        raise ValueError("unsupported task bank mode")
    path = _mode_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(f"{mode}\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return mode
