from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Literal

MediaKind = Literal["image", "video"]
MAX_MEDIA_CONCURRENCY_SLOTS = 32


class FileSlotMediaAdmission:
    """A blocking image/video semaphore shared by every process on one volume."""

    def __init__(
        self,
        lock_dir: str | Path,
        *,
        image_slots: int,
        video_slots: int,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        self._lock_dir = Path(lock_dir).expanduser().resolve(strict=False)
        self._slot_counts = {
            "image": self._validate_slot_count("image", image_slots),
            "video": self._validate_slot_count("video", video_slots),
        }
        if poll_interval_seconds <= 0:
            raise ValueError("media slot poll interval must be positive")
        self._poll_interval_seconds = poll_interval_seconds
        self._cursor_lock = Lock()
        self._next_slot = {"image": 0, "video": 0}
        self._lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _validate_slot_count(kind: MediaKind, value: int) -> int:
        if type(value) is not int or not 1 <= value <= MAX_MEDIA_CONCURRENCY_SLOTS:
            raise ValueError(
                f"{kind} media concurrency must be between 1 and {MAX_MEDIA_CONCURRENCY_SLOTS}"
            )
        return value

    @property
    def lock_dir(self) -> Path:
        return self._lock_dir

    def slot_count(self, kind: MediaKind) -> int:
        try:
            return self._slot_counts[kind]
        except KeyError as exc:
            raise ValueError(f"unsupported media kind: {kind}") from exc

    def _slot_order(self, kind: MediaKind) -> range | tuple[int, ...]:
        slot_count = self.slot_count(kind)
        with self._cursor_lock:
            first = self._next_slot[kind]
            self._next_slot[kind] = (first + 1) % slot_count
        if first == 0:
            return range(slot_count)
        return (*range(first, slot_count), *range(first))

    def _try_acquire(self, kind: MediaKind) -> int | None:
        for slot in self._slot_order(kind):
            path = self._lock_dir / f"{kind}-{slot:02d}.lock"
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(descriptor)
                continue
            except BaseException:
                os.close(descriptor)
                raise
            return descriptor
        return None

    @contextmanager
    def acquire(self, kind: MediaKind) -> Iterator[None]:
        descriptor: int | None = None
        while descriptor is None:
            descriptor = self._try_acquire(kind)
            if descriptor is None:
                time.sleep(self._poll_interval_seconds)
        try:
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
