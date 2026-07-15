from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import app.main as main_module
from app.config import Settings
from app.services import storage_health_service


def _settings(
    tmp_path: Path,
    *,
    generated_path: Path | None = None,
    push_path: Path | None = None,
    logs_path: Path | None = None,
    min_free_bytes: int = 0,
    min_free_percent: float = 0,
) -> SimpleNamespace:
    generated_path = generated_path or tmp_path / "generated"
    push_path = push_path or tmp_path / "push"
    logs_path = logs_path or tmp_path / "logs"
    for path in {generated_path, push_path, logs_path}:
        path.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        storage_health_generated_assets_path=str(generated_path),
        storage_health_push_data_path=str(push_path),
        storage_health_logs_path=str(logs_path),
        storage_health_min_free_bytes=min_free_bytes,
        storage_health_min_free_percent=min_free_percent,
        storage_admission_image_reserve_bytes=1,
        storage_admission_video_reserve_bytes=1,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("storage_admission_image_reserve_bytes", 25 * 1024 * 1024 - 1),
        ("storage_admission_video_reserve_bytes", 2 * 100 * 1024 * 1024 - 1),
    ],
)
def test_media_storage_reservation_covers_result_and_processing_files(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_storage_health_probes_unique_paths_and_devices_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_path = tmp_path / "shared"
    logs_path = tmp_path / "logs"
    settings = _settings(
        tmp_path,
        generated_path=shared_path,
        push_path=shared_path,
        logs_path=logs_path,
    )
    original_probe = storage_health_service._write_delete_probe
    original_disk_usage = storage_health_service.shutil.disk_usage
    probed_paths: list[Path] = []
    disk_usage_paths: list[Path] = []

    def record_probe(path: Path) -> tuple[bool, str | None]:
        probed_paths.append(path)
        return original_probe(path)

    def record_disk_usage(path: Path) -> object:
        disk_usage_paths.append(path)
        return original_disk_usage(path)

    monkeypatch.setattr(storage_health_service, "_write_delete_probe", record_probe)
    monkeypatch.setattr(storage_health_service.shutil, "disk_usage", record_disk_usage)

    runtime = storage_health_service.storage_runtime_status(settings)

    assert runtime["status"] == "ok"
    assert probed_paths == [shared_path, logs_path]
    assert disk_usage_paths == [shared_path]
    assert list(runtime["devices"]) == ["device-1"]
    assert runtime["devices"]["device-1"]["paths"] == [
        "generatedAssets",
        "pushData",
        "logs",
    ]
    assert not list(tmp_path.rglob(".storage-health-*.tmp"))
    assert str(tmp_path) not in json.dumps(runtime)


@pytest.mark.parametrize(
    ("total", "free", "expected_status"),
    [
        (100 * 1024**3, 6 * 1024**3, "ok"),
        (512 * 1024**2, 256 * 1024**2, "degraded"),
        (100 * 1024**3, 2 * 1024**3, "degraded"),
        (100 * 1024**3, 512 * 1024**2, "degraded"),
        (100_000, 4_999, "degraded"),
    ],
)
def test_storage_watermark_enforces_effective_floor_and_media_headroom(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    total: int,
    free: int,
    expected_status: str,
) -> None:
    settings = _settings(
        tmp_path,
        min_free_bytes=1024**3,
        min_free_percent=5,
    )
    monkeypatch.setattr(
        storage_health_service.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=total, used=total - free, free=free),
    )

    runtime = storage_health_service.storage_runtime_status(settings)

    assert runtime["status"] == expected_status
    assert runtime["thresholds"]["degradationPolicy"] == (
        "effectiveFloorPlusLargestMediaReservation"
    )
    device = runtime["devices"]["device-1"]
    if expected_status == "degraded":
        assert device["errorCode"] == "LOW_DISK_SPACE"
        assert runtime["failedPaths"] == ["generatedAssets", "pushData", "logs"]
    else:
        assert "errorCode" not in device


def test_storage_health_reports_missing_directory_without_exposing_path(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing"
    settings = _settings(tmp_path)
    settings.storage_health_logs_path = str(missing_path)

    runtime = storage_health_service.storage_runtime_status(settings)

    assert runtime["status"] == "degraded"
    assert runtime["failedPaths"] == ["logs"]
    assert runtime["paths"]["logs"] == {
        "status": "degraded",
        "writable": False,
        "diskSpaceOk": False,
        "errorCode": "NOT_FOUND",
    }
    assert str(missing_path) not in json.dumps(runtime)


def test_storage_health_degrades_when_disk_usage_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)

    def fail_disk_usage(_path: Path) -> NoReturn:
        raise OSError("synthetic statvfs failure")

    monkeypatch.setattr(storage_health_service.shutil, "disk_usage", fail_disk_usage)

    runtime = storage_health_service.storage_runtime_status(settings)

    assert runtime["status"] == "degraded"
    assert runtime["devices"]["device-1"]["errorCode"] == "DISK_USAGE_FAILED"


def test_storage_health_degrades_when_write_probe_cannot_create_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)

    def deny_probe_file(*_args: object, **_kwargs: object) -> NoReturn:
        raise PermissionError("synthetic read-only directory")

    monkeypatch.setattr(storage_health_service.tempfile, "NamedTemporaryFile", deny_probe_file)

    runtime = storage_health_service.storage_runtime_status(settings)

    assert runtime["status"] == "degraded"
    assert runtime["failedPaths"] == ["generatedAssets", "pushData", "logs"]
    assert all(path["errorCode"] == "WRITE_PROBE_FAILED" for path in runtime["paths"].values())


def test_failed_delete_probe_does_not_accumulate_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_dir = tmp_path / "generated"
    probe_dir.mkdir()
    real_unlink = Path.unlink

    def deny_probe_delete(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.startswith(".storage-health-"):
            raise PermissionError("synthetic delete denial")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_probe_delete)

    assert storage_health_service._write_delete_probe(probe_dir) == (
        False,
        "DELETE_PROBE_FAILED",
    )
    assert storage_health_service._write_delete_probe(probe_dir) == (
        False,
        "DELETE_PROBE_FAILED",
    )
    assert len(list(probe_dir.glob(".storage-health-*.tmp"))) == 1


def test_concurrent_probe_cleanup_does_not_report_false_delete_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_dir = tmp_path / "generated"
    probe_dir.mkdir()
    real_unlink = Path.unlink

    def concurrent_probe_delete(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.startswith(".storage-health-") and path.exists():
            real_unlink(path, *args, **kwargs)
            raise FileNotFoundError("synthetic concurrent probe cleanup")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", concurrent_probe_delete)

    assert storage_health_service._write_delete_probe(probe_dir) == (True, None)
    assert not list(probe_dir.glob(".storage-health-*.tmp"))


def test_default_storage_health_is_cached_and_caller_cannot_mutate_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(storage_health_probe_cache_seconds=10)
    probe_count = 0

    def collect(_settings: object) -> dict[str, object]:
        nonlocal probe_count
        probe_count += 1
        return {"status": f"probe-{probe_count}"}

    observed_times = iter([100.0, 105.0, 111.0])
    monkeypatch.setattr(storage_health_service, "_cached_status", None)
    monkeypatch.setattr(storage_health_service, "_cache_checked_at", 0.0)
    monkeypatch.setattr(storage_health_service, "get_settings", lambda: settings)
    monkeypatch.setattr(storage_health_service, "_collect_storage_runtime_status", collect)
    monkeypatch.setattr(storage_health_service.time, "monotonic", lambda: next(observed_times))

    first = storage_health_service.storage_runtime_status()
    first["status"] = "mutated"
    second = storage_health_service.storage_runtime_status()
    third = storage_health_service.storage_runtime_status()

    assert second["status"] == "probe-1"
    assert third["status"] == "probe-2"
    assert probe_count == 2


def _admission_runtime(*, total: int, free: int, writable: bool = True) -> dict[str, object]:
    return {
        "paths": {
            "generatedAssets": {
                "writable": writable,
                "device": "device-1",
            }
        },
        "devices": {
            "device-1": {
                "totalBytes": total,
                "freeBytes": free,
            }
        },
    }


def _admission_settings(tmp_path: Path) -> SimpleNamespace:
    generated_path = tmp_path / "generated"
    push_path = tmp_path / "push"
    logs_path = tmp_path / "logs"
    for path in (generated_path, push_path, logs_path):
        path.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        storage_health_generated_assets_path=str(generated_path),
        storage_health_push_data_path=str(push_path),
        storage_health_logs_path=str(logs_path),
        storage_health_min_free_bytes=100,
        storage_health_min_free_percent=10,
        storage_admission_image_reserve_bytes=70,
        storage_admission_video_reserve_bytes=120,
    )


@pytest.mark.parametrize(("free", "expected_status"), [(220, "ok"), (219, "degraded")])
def test_storage_health_matches_largest_media_admission_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    free: int,
    expected_status: str,
) -> None:
    settings = _admission_settings(tmp_path)
    monkeypatch.setattr(
        storage_health_service.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000, used=1_000 - free, free=free),
    )

    runtime = storage_health_service.storage_runtime_status(settings)

    assert runtime["paths"]["generatedAssets"]["status"] == expected_status
    if expected_status == "ok":
        with storage_health_service.reserve_media_storage_capacity(
            "video",
            settings=settings,
        ):
            pass
    else:
        with pytest.raises(storage_health_service.StorageCapacityError):
            with storage_health_service.reserve_media_storage_capacity(
                "video",
                settings=settings,
            ):
                raise AssertionError("degraded storage must fail before provider execution")


def _hold_process_storage_reservation(settings: SimpleNamespace, acquired, release) -> None:
    storage_health_service._collect_storage_runtime_status = lambda _settings: _admission_runtime(
        total=1_000,
        free=220,
    )
    with storage_health_service.reserve_media_storage_capacity("image", settings=settings):
        acquired.set()
        release.wait(10)


def _abandon_process_storage_reservation(settings: SimpleNamespace, acquired) -> None:
    storage_health_service._collect_storage_runtime_status = lambda _settings: _admission_runtime(
        total=1_000,
        free=220,
    )
    with storage_health_service.reserve_media_storage_capacity("image", settings=settings):
        acquired.set()
        os._exit(0)


def test_media_storage_admission_preserves_configured_floor(tmp_path, monkeypatch) -> None:
    generated_path = tmp_path / "generated"
    generated_path.mkdir()
    settings = SimpleNamespace(
        storage_health_generated_assets_path=str(generated_path),
        storage_health_push_data_path=str(tmp_path / "push"),
        storage_health_logs_path=str(tmp_path / "logs"),
        storage_health_min_free_bytes=100,
        storage_health_min_free_percent=10,
        storage_admission_image_reserve_bytes=60,
        storage_admission_video_reserve_bytes=120,
    )
    monkeypatch.setattr(
        storage_health_service,
        "_collect_storage_runtime_status",
        lambda _settings: _admission_runtime(total=1_000, free=150),
    )

    with pytest.raises(storage_health_service.StorageCapacityError) as error:
        with storage_health_service.reserve_media_storage_capacity("image", settings=settings):
            raise AssertionError("low storage must fail before provider execution")

    assert error.value.code == "STORAGE_CAPACITY_LOW"
    assert error.value.reason == "LOW_DISK_SPACE"


def test_media_storage_admission_reserves_headroom_across_threads(tmp_path, monkeypatch) -> None:
    settings = _admission_settings(tmp_path)
    monkeypatch.setattr(
        storage_health_service,
        "_collect_storage_runtime_status",
        lambda _settings: _admission_runtime(total=1_000, free=220),
    )

    with storage_health_service.reserve_media_storage_capacity("image", settings=settings):
        with pytest.raises(storage_health_service.StorageCapacityError):
            with storage_health_service.reserve_media_storage_capacity(
                "image",
                settings=settings,
            ):
                raise AssertionError("second reservation must not enter")

    reservation_directory = storage_health_service._media_reservation_directory(settings)
    with storage_health_service.reserve_media_storage_capacity("image", settings=settings):
        assert len(list(reservation_directory.glob("*.reserve"))) == 1
    assert not list(reservation_directory.glob("*.reserve"))


def test_media_storage_admission_reserves_headroom_across_processes(
    tmp_path,
    monkeypatch,
) -> None:
    settings = _admission_settings(tmp_path)
    monkeypatch.setattr(
        storage_health_service,
        "_collect_storage_runtime_status",
        lambda _settings: _admission_runtime(total=1_000, free=220),
    )
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_process_storage_reservation,
        args=(settings, acquired, release),
    )
    try:
        process.start()
        assert acquired.wait(timeout=5)
        with pytest.raises(storage_health_service.StorageCapacityError):
            with storage_health_service.reserve_media_storage_capacity(
                "image",
                settings=settings,
            ):
                raise AssertionError("cross-process reservation must be counted")
    finally:
        release.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)

    assert process.exitcode == 0
    with storage_health_service.reserve_media_storage_capacity("image", settings=settings):
        pass


def test_media_storage_admission_reclaims_crashed_process_reservation(
    tmp_path,
    monkeypatch,
) -> None:
    settings = _admission_settings(tmp_path)
    monkeypatch.setattr(
        storage_health_service,
        "_collect_storage_runtime_status",
        lambda _settings: _admission_runtime(total=1_000, free=220),
    )
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    process = context.Process(
        target=_abandon_process_storage_reservation,
        args=(settings, acquired),
    )
    process.start()
    assert acquired.wait(timeout=5)
    process.join(timeout=5)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
    assert process.exitcode == 0

    reservation_directory = storage_health_service._media_reservation_directory(settings)
    assert len(list(reservation_directory.glob("*.reserve"))) == 1
    with storage_health_service.reserve_media_storage_capacity("image", settings=settings):
        assert len(list(reservation_directory.glob("*.reserve"))) == 1
    assert not list(reservation_directory.glob("*.reserve"))


def test_media_storage_admission_rejects_unwritable_generated_volume(tmp_path, monkeypatch) -> None:
    generated_path = tmp_path / "generated"
    generated_path.mkdir()
    settings = SimpleNamespace(
        storage_health_generated_assets_path=str(generated_path),
        storage_health_push_data_path=str(tmp_path / "push"),
        storage_health_logs_path=str(tmp_path / "logs"),
        storage_health_min_free_bytes=0,
        storage_health_min_free_percent=0,
        storage_admission_image_reserve_bytes=1,
        storage_admission_video_reserve_bytes=1,
    )
    monkeypatch.setattr(
        storage_health_service,
        "_collect_storage_runtime_status",
        lambda _settings: _admission_runtime(total=1_000, free=1_000, writable=False),
    )

    with pytest.raises(storage_health_service.StorageCapacityError) as error:
        with storage_health_service.reserve_media_storage_capacity("video", settings=settings):
            raise AssertionError("unwritable storage must not enter")

    assert error.value.reason == "GENERATED_ASSETS_NOT_WRITABLE"


def test_health_returns_503_and_ops_alert_for_storage_degradation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = {
        "status": "degraded",
        "thresholds": {
            "minFreeBytes": 1024**3,
            "minFreePercent": 5,
            "degradationPolicy": "bothBelowMinimum",
        },
        "paths": {},
        "devices": {},
        "failedPaths": ["logs"],
    }
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(main_module, "scheduler_runtime_status", lambda: {})
    monkeypatch.setattr(main_module.tma, "generation_job_runtime_status", lambda: {"stuck": 0})
    monkeypatch.setattr(main_module, "llm_runtime_status", lambda: {"status": "ok"})
    monkeypatch.setattr(main_module, "media_runtime_status", lambda: {"status": "ok"})
    monkeypatch.setattr(main_module, "storage_runtime_status", lambda: storage)
    monkeypatch.setattr(main_module, "notify_ops", lambda key, text: alerts.append((key, text)))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(scheduler_tasks={})),
    )

    response = asyncio.run(main_module.health(request))

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload == {"status": "degraded"}
    assert alerts == [("health:storage", "Health degraded: storage (logs)")]
