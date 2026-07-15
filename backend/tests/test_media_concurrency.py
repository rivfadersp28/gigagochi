from __future__ import annotations

import multiprocessing
import os
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.media.concurrency import FileSlotMediaAdmission


def _hold_process_slot(
    lock_dir: str,
    acquired,
    release,
    active,
    peak,
    counter_lock,
) -> None:
    admission = FileSlotMediaAdmission(
        lock_dir,
        image_slots=2,
        video_slots=1,
        poll_interval_seconds=0.01,
    )
    with admission.acquire("image"):
        with counter_lock:
            active.value += 1
            peak.value = max(peak.value, active.value)
        acquired.put(os.getpid())
        release.wait(10)
        with counter_lock:
            active.value -= 1


def test_media_concurrency_config_defaults_and_bounds() -> None:
    assert Settings.model_fields["media_image_concurrency"].default == 4
    assert Settings.model_fields["media_video_concurrency"].default == 2
    assert Settings.model_fields["generation_image_workers"].default == 4
    assert Settings.model_fields["generation_video_workers"].default == 2

    with pytest.raises(ValidationError):
        Settings(_env_file=None, media_image_concurrency=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, media_video_concurrency=33)


def test_file_slot_admission_validates_inputs_and_releases_after_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="image media concurrency"):
        FileSlotMediaAdmission(tmp_path, image_slots=0, video_slots=1)
    with pytest.raises(ValueError, match="poll interval"):
        FileSlotMediaAdmission(
            tmp_path,
            image_slots=1,
            video_slots=1,
            poll_interval_seconds=0,
        )

    admission = FileSlotMediaAdmission(tmp_path, image_slots=1, video_slots=1)
    with pytest.raises(RuntimeError, match="synthetic"):
        with admission.acquire("image"):
            raise RuntimeError("synthetic")

    with admission.acquire("image"):
        with admission.acquire("video"):
            pass


def test_file_slot_admission_bounds_threads(tmp_path: Path) -> None:
    admission = FileSlotMediaAdmission(
        tmp_path,
        image_slots=2,
        video_slots=1,
        poll_interval_seconds=0.005,
    )
    release = Event()
    capacity_reached = Event()
    counter_lock = Lock()
    active = 0
    peak = 0

    def hold_slot() -> None:
        nonlocal active, peak
        with admission.acquire("image"):
            with counter_lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    capacity_reached.set()
            assert release.wait(timeout=2)
            with counter_lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(hold_slot) for _ in range(4)]
        assert capacity_reached.wait(timeout=1)
        time.sleep(0.05)
        with counter_lock:
            assert active == 2
            assert peak == 2
        release.set()
        for future in futures:
            future.result(timeout=2)

    assert peak == 2
    assert len(list(tmp_path.glob("image-*.lock"))) <= 2


def test_file_slot_admission_bounds_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    acquired = context.Queue()
    release = context.Event()
    active = context.Value("i", 0)
    peak = context.Value("i", 0)
    counter_lock = context.Lock()
    processes = [
        context.Process(
            target=_hold_process_slot,
            args=(str(tmp_path), acquired, release, active, peak, counter_lock),
        )
        for _ in range(4)
    ]
    try:
        for process in processes:
            process.start()
        acquired.get(timeout=5)
        acquired.get(timeout=5)
        with pytest.raises(queue.Empty):
            acquired.get(timeout=0.2)
        assert peak.value == 2
    finally:
        release.set()
        for process in processes:
            process.join(timeout=5)
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)

    assert all(process.exitcode == 0 for process in processes)
    assert peak.value == 2
