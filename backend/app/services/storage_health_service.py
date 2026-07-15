from __future__ import annotations

import copy
import fcntl
import math
import os
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol

from app.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class _StorageHealthSettings(Protocol):
    storage_health_generated_assets_path: str
    storage_health_push_data_path: str
    storage_health_logs_path: str
    storage_health_min_free_bytes: int
    storage_health_min_free_percent: float
    storage_health_probe_cache_seconds: float


_cache_lock = Lock()
_cache_checked_at = 0.0
_cached_status: dict[str, object] | None = None
_media_reservation_lock = Lock()
DEFAULT_IMAGE_RESERVE_BYTES = 32 * 1024 * 1024
DEFAULT_VIDEO_RESERVE_BYTES = 128 * 1024 * 1024
MAX_MEDIA_RESERVE_BYTES = 2 * 1024 * 1024 * 1024
MEDIA_RESERVATION_DIRECTORY = ".private/media-storage-reservations"
MEDIA_RESERVATION_LEDGER_LOCK = "ledger.lock"
MEDIA_RESERVATION_SUFFIX = ".reserve"


class StorageCapacityError(RuntimeError):
    code = "STORAGE_CAPACITY_LOW"
    retry_after_seconds = 300

    def __init__(self, *, media_kind: str, reason: str) -> None:
        super().__init__(f"media storage admission failed for {media_kind}: {reason}")
        self.media_kind = media_kind
        self.reason = reason


def _configured_media_headroom(settings: object) -> int:
    image_bytes = int(
        getattr(
            settings,
            "storage_admission_image_reserve_bytes",
            DEFAULT_IMAGE_RESERVE_BYTES,
        )
    )
    video_bytes = int(
        getattr(
            settings,
            "storage_admission_video_reserve_bytes",
            DEFAULT_VIDEO_RESERVE_BYTES,
        )
    )
    if not 0 < image_bytes <= MAX_MEDIA_RESERVE_BYTES:
        raise ValueError("invalid image storage reservation")
    if not 0 < video_bytes <= MAX_MEDIA_RESERVE_BYTES:
        raise ValueError("invalid video storage reservation")
    return max(image_bytes, video_bytes)


def _configured_paths(settings: _StorageHealthSettings) -> dict[str, Path]:
    configured = {
        "generatedAssets": settings.storage_health_generated_assets_path,
        "pushData": settings.storage_health_push_data_path,
        "logs": settings.storage_health_logs_path,
    }
    paths: dict[str, Path] = {}
    for name, raw_path in configured.items():
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        paths[name] = path.resolve(strict=False)
    return paths


def _write_delete_probe(path: Path) -> tuple[bool, str | None]:
    # A failed delete must not create a fresh orphan on every health check and
    # eventually fill the volume. Clean the reserved probe prefix first; if the
    # filesystem still refuses deletion, report that state without creating more.
    try:
        for orphan in path.glob(".storage-health-*.tmp"):
            try:
                orphan.unlink()
            except FileNotFoundError:
                # Another worker can probe the same shared volume concurrently.
                # Its cleanup already proved that deleting this entry works.
                continue
    except OSError:
        return False, "DELETE_PROBE_FAILED"

    probe_path: Path | None = None
    write_failed = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=path,
            prefix=".storage-health-",
            suffix=".tmp",
            delete=False,
        ) as probe:
            probe_path = Path(probe.name)
            probe.write(b"storage-health\n")
            probe.flush()
            os.fsync(probe.fileno())
    except OSError:
        write_failed = True

    if probe_path is None:
        return False, "WRITE_PROBE_FAILED"
    try:
        probe_path.unlink()
    except FileNotFoundError:
        # A concurrent probe may have removed our still-open temporary file while
        # cleaning leftovers. That is a successful delete check, not degradation.
        pass
    except OSError:
        return False, "DELETE_PROBE_FAILED"
    if write_failed:
        return False, "WRITE_PROBE_FAILED"
    return True, None


def _probe_directory(path: Path) -> dict[str, object]:
    try:
        path_stat = path.stat()
    except FileNotFoundError:
        return {"writable": False, "errorCode": "NOT_FOUND", "deviceNumber": None}
    except OSError:
        return {"writable": False, "errorCode": "STAT_FAILED", "deviceNumber": None}
    if not stat.S_ISDIR(path_stat.st_mode):
        return {"writable": False, "errorCode": "NOT_DIRECTORY", "deviceNumber": None}

    writable, error_code = _write_delete_probe(path)
    return {
        "writable": writable,
        "errorCode": error_code,
        "deviceNumber": path_stat.st_dev,
    }


def _collect_storage_runtime_status(settings: _StorageHealthSettings) -> dict[str, object]:
    paths = _configured_paths(settings)
    probes_by_path: dict[Path, dict[str, object]] = {}
    path_probes: dict[str, dict[str, object]] = {}
    device_paths: dict[int, list[str]] = {}
    device_representatives: dict[int, Path] = {}

    for name, path in paths.items():
        probe = probes_by_path.get(path)
        if probe is None:
            probe = _probe_directory(path)
            probes_by_path[path] = probe
        path_probes[name] = probe
        device_number = probe["deviceNumber"]
        if isinstance(device_number, int):
            device_paths.setdefault(device_number, []).append(name)
            device_representatives.setdefault(device_number, path)

    devices: dict[str, dict[str, object]] = {}
    device_aliases: dict[int, str] = {}
    min_free_bytes = settings.storage_health_min_free_bytes
    min_free_percent = settings.storage_health_min_free_percent
    media_headroom_bytes = _configured_media_headroom(settings)
    for index, (device_number, names) in enumerate(device_paths.items(), start=1):
        alias = f"device-{index}"
        device_aliases[device_number] = alias
        try:
            usage = shutil.disk_usage(device_representatives[device_number])
            if usage.total <= 0:
                raise ValueError("disk total must be positive")
            raw_free_percent = usage.free / usage.total * 100
            free_percent = round(raw_free_percent, 2)
            below_min_bytes = usage.free < min_free_bytes
            below_min_percent = raw_free_percent < min_free_percent
            percent_floor_bytes = math.ceil(usage.total * min_free_percent / 100)
            protected_floor_bytes = max(min_free_bytes, percent_floor_bytes)
            required_media_headroom = media_headroom_bytes if "generatedAssets" in names else 0
            required_free_bytes = protected_floor_bytes + required_media_headroom
            disk_space_ok = usage.free >= required_free_bytes
            device: dict[str, object] = {
                "status": "ok" if disk_space_ok else "degraded",
                "totalBytes": usage.total,
                "freeBytes": usage.free,
                "freePercent": free_percent,
                "belowMinimumBytes": below_min_bytes,
                "belowMinimumPercent": below_min_percent,
                "protectedFloorBytes": protected_floor_bytes,
                "mediaReservationHeadroomBytes": required_media_headroom,
                "requiredFreeBytes": required_free_bytes,
                "paths": names,
            }
            if not disk_space_ok:
                device["errorCode"] = "LOW_DISK_SPACE"
        except (OSError, ValueError):
            device = {
                "status": "degraded",
                "errorCode": "DISK_USAGE_FAILED",
                "paths": names,
            }
        devices[alias] = device

    failed_paths: list[str] = []
    path_statuses: dict[str, dict[str, object]] = {}
    for name, probe in path_probes.items():
        device_number = probe["deviceNumber"]
        device_alias = device_aliases.get(device_number) if isinstance(device_number, int) else None
        disk_space_ok = bool(device_alias is not None and devices[device_alias]["status"] == "ok")
        writable = probe["writable"] is True
        is_ok = writable and disk_space_ok
        path_status: dict[str, object] = {
            "status": "ok" if is_ok else "degraded",
            "writable": writable,
            "diskSpaceOk": disk_space_ok,
        }
        if device_alias is not None:
            path_status["device"] = device_alias
        error_code = probe["errorCode"]
        if isinstance(error_code, str):
            path_status["errorCode"] = error_code
        if not is_ok:
            failed_paths.append(name)
        path_statuses[name] = path_status

    return {
        "status": "ok" if not failed_paths else "degraded",
        "thresholds": {
            "minFreeBytes": min_free_bytes,
            "minFreePercent": min_free_percent,
            "mediaReservationHeadroomBytes": media_headroom_bytes,
            "degradationPolicy": "effectiveFloorPlusLargestMediaReservation",
        },
        "paths": path_statuses,
        "devices": devices,
        "failedPaths": failed_paths,
    }


def storage_runtime_status(settings: _StorageHealthSettings | None = None) -> dict[str, object]:
    global _cache_checked_at, _cached_status

    if settings is not None:
        return _collect_storage_runtime_status(settings)

    configured_settings = get_settings()
    cache_seconds = configured_settings.storage_health_probe_cache_seconds
    if cache_seconds <= 0:
        return _collect_storage_runtime_status(configured_settings)

    with _cache_lock:
        checked_at = time.monotonic()
        if _cached_status is not None and checked_at - _cache_checked_at < cache_seconds:
            return copy.deepcopy(_cached_status)
        collected = _collect_storage_runtime_status(configured_settings)
        _cached_status = copy.deepcopy(collected)
        _cache_checked_at = checked_at
        return collected


def _media_reserve_bytes(kind: Literal["image", "video"], settings: object) -> int:
    if kind == "image":
        return int(
            getattr(
                settings,
                "storage_admission_image_reserve_bytes",
                DEFAULT_IMAGE_RESERVE_BYTES,
            )
        )
    return int(
        getattr(
            settings,
            "storage_admission_video_reserve_bytes",
            DEFAULT_VIDEO_RESERVE_BYTES,
        )
    )


def _media_reservation_directory(settings: _StorageHealthSettings) -> Path:
    return _configured_paths(settings)["generatedAssets"] / MEDIA_RESERVATION_DIRECTORY


@contextmanager
def _locked_media_reservation_ledger(
    settings: _StorageHealthSettings,
    *,
    media_kind: str,
) -> Iterator[Path]:
    """Serialize short ledger mutations across threads and processes."""

    with _media_reservation_lock:
        ledger_directory = _media_reservation_directory(settings)
        descriptor: int | None = None
        try:
            ledger_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(
                ledger_directory / MEDIA_RESERVATION_LEDGER_LOCK,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield ledger_directory
        except StorageCapacityError:
            raise
        except OSError as exc:
            raise StorageCapacityError(
                media_kind=media_kind,
                reason="RESERVATION_LEDGER_UNAVAILABLE",
            ) from exc
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)


def _read_media_reservation_bytes(descriptor: int, *, media_kind: str) -> int:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        payload = os.read(descriptor, 64).decode("ascii").strip()
        reserve_bytes = int(payload)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise StorageCapacityError(
            media_kind=media_kind,
            reason="RESERVATION_LEDGER_CORRUPT",
        ) from exc
    if not 0 < reserve_bytes <= MAX_MEDIA_RESERVE_BYTES:
        raise StorageCapacityError(
            media_kind=media_kind,
            reason="RESERVATION_LEDGER_CORRUPT",
        )
    return reserve_bytes


def _active_media_reserved_bytes(ledger_directory: Path, *, media_kind: str) -> int:
    reserved_bytes = 0
    open_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    for path in ledger_directory.glob(f"*{MEDIA_RESERVATION_SUFFIX}"):
        try:
            descriptor = os.open(path, open_flags)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise StorageCapacityError(
                media_kind=media_kind,
                reason="RESERVATION_LEDGER_UNAVAILABLE",
            ) from exc
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                reserved_bytes += _read_media_reservation_bytes(
                    descriptor,
                    media_kind=media_kind,
                )
            else:
                # The owning process disappeared or completed before unlinking.
                # Kernel-released flock is the crash-safe stale marker.
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    return reserved_bytes


def _create_media_reservation(
    ledger_directory: Path,
    *,
    reserve_bytes: int,
    media_kind: str,
) -> tuple[Path, int]:
    path = ledger_directory / f"{uuid.uuid4().hex}{MEDIA_RESERVATION_SUFFIX}"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_CREAT
            | os.O_EXCL
            | os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        payload = f"{reserve_bytes}\n".encode("ascii")
        if os.write(descriptor, payload) != len(payload):
            raise OSError("short media reservation ledger write")
        os.fsync(descriptor)
        return path, descriptor
    except OSError as exc:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        path.unlink(missing_ok=True)
        raise StorageCapacityError(
            media_kind=media_kind,
            reason="RESERVATION_LEDGER_UNAVAILABLE",
        ) from exc


def _release_media_reservation(path: Path, descriptor: int) -> None:
    try:
        path.unlink(missing_ok=True)
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def reserve_media_storage_capacity(
    kind: Literal["image", "video"],
    *,
    settings: _StorageHealthSettings | None = None,
) -> Iterator[None]:
    """Reserve output headroom across processes until the caller commits it."""

    configured_settings = settings or get_settings()
    reserve_bytes = _media_reserve_bytes(kind, configured_settings)
    if not 0 < reserve_bytes <= MAX_MEDIA_RESERVE_BYTES:
        raise StorageCapacityError(media_kind=kind, reason="INVALID_RESERVATION")

    reservation_path: Path | None = None
    reservation_descriptor: int | None = None
    with _locked_media_reservation_ledger(configured_settings, media_kind=kind) as ledger_directory:
        runtime = _collect_storage_runtime_status(configured_settings)
        paths = runtime.get("paths")
        generated = paths.get("generatedAssets") if isinstance(paths, dict) else None
        if not isinstance(generated, dict) or generated.get("writable") is not True:
            raise StorageCapacityError(media_kind=kind, reason="GENERATED_ASSETS_NOT_WRITABLE")
        device_alias = generated.get("device")
        devices = runtime.get("devices")
        device = devices.get(device_alias) if isinstance(devices, dict) else None
        if not isinstance(device, dict):
            raise StorageCapacityError(media_kind=kind, reason="DISK_USAGE_UNAVAILABLE")
        total_bytes = device.get("totalBytes")
        free_bytes = device.get("freeBytes")
        if not isinstance(total_bytes, int) or not isinstance(free_bytes, int) or total_bytes <= 0:
            raise StorageCapacityError(media_kind=kind, reason="DISK_USAGE_UNAVAILABLE")

        min_free_bytes = int(configured_settings.storage_health_min_free_bytes)
        min_free_percent = float(configured_settings.storage_health_min_free_percent)
        percent_floor = math.ceil(total_bytes * min_free_percent / 100)
        protected_floor = max(min_free_bytes, percent_floor)
        active_reserved_bytes = _active_media_reserved_bytes(
            ledger_directory,
            media_kind=kind,
        )
        free_after_reservations = free_bytes - active_reserved_bytes - reserve_bytes
        if free_after_reservations < protected_floor:
            raise StorageCapacityError(media_kind=kind, reason="LOW_DISK_SPACE")
        reservation_path, reservation_descriptor = _create_media_reservation(
            ledger_directory,
            reserve_bytes=reserve_bytes,
            media_kind=kind,
        )

    try:
        yield
    finally:
        if reservation_path is not None and reservation_descriptor is not None:
            _release_media_reservation(reservation_path, reservation_descriptor)
