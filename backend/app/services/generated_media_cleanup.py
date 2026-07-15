from __future__ import annotations

import os
import re
import shutil
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

BACKGROUND_STORY_MEDIA_PATTERN = re.compile(r"^background-story-\d{8}T\d{12}Z\.(?:png|mp4)$")
BACKGROUND_STORY_MEDIA_URL_PREFIX = "/static/generated/"
PROCESSING_TEMP_DIRECTORY = Path(".private/processing-tmp")
PROCESSING_TEMP_PREFIXES = ("pet-ping-pong-", "generated-video-main-stream-")


@dataclass(frozen=True, slots=True)
class GeneratedMediaCleanupResult:
    removed: tuple[Path, ...]
    referenced: int
    too_young: int
    unsafe: int
    failed: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class GeneratedTempCleanupResult:
    removed: tuple[Path, ...]
    too_young: int
    unsafe: int
    failed: tuple[Path, ...]


def generated_media_cleanup_is_enabled(configured_value: Any = None) -> bool:
    if isinstance(configured_value, bool):
        return configured_value
    raw_value = os.getenv("GENERATED_MEDIA_CLEANUP_ENABLED", "true")
    return raw_value.strip().casefold() not in {"0", "false", "no", "off"}


def cleanup_owned_generated_asset_directory(
    *,
    generated_root: Path,
    asset_directory: Path,
    expected_owner_name: str,
) -> bool:
    """Delete one proven-orphan asset set without crossing its generated root."""

    root_resolved = generated_root.resolve(strict=False)
    directory = Path(asset_directory)
    if not _safe_owner_name(expected_owner_name):
        raise ValueError("unsafe generated-asset owner name")
    if directory.is_symlink():
        raise ValueError("generated-asset directory must not be a symlink")
    resolved = directory.resolve(strict=False)
    if resolved.parent != root_resolved or resolved.name != expected_owner_name:
        raise ValueError("generated-asset directory is outside its configured root")
    if not directory.exists():
        return False
    if not directory.is_dir():
        raise ValueError("generated-asset path is not a directory")
    shutil.rmtree(directory)
    return True


def cleanup_stale_generated_processing_temp_directories(
    *,
    generated_root: Path,
    now: datetime | None = None,
    minimum_age: timedelta = timedelta(days=1),
) -> GeneratedTempCleanupResult:
    """Remove only abandoned, known-prefix FFmpeg scratch directories."""

    if minimum_age.total_seconds() < 0:
        raise ValueError("minimum_age must not be negative")
    root = Path(generated_root)
    root_resolved = root.resolve(strict=False)
    temp_root = root / PROCESSING_TEMP_DIRECTORY
    if temp_root.is_symlink() or temp_root.resolve(strict=False) != (
        root_resolved / PROCESSING_TEMP_DIRECTORY
    ):
        return GeneratedTempCleanupResult(removed=(), too_young=0, unsafe=1, failed=())
    try:
        candidates = list(temp_root.iterdir())
    except FileNotFoundError:
        return GeneratedTempCleanupResult(removed=(), too_young=0, unsafe=0, failed=())
    except OSError:
        return GeneratedTempCleanupResult(
            removed=(),
            too_young=0,
            unsafe=0,
            failed=(temp_root,),
        )

    effective_now = now or datetime.now(UTC)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=UTC)
    cutoff_timestamp = effective_now.timestamp() - minimum_age.total_seconds()
    removed: list[Path] = []
    failed: list[Path] = []
    too_young = 0
    unsafe = 0
    for candidate in candidates:
        if not candidate.name.startswith(PROCESSING_TEMP_PREFIXES):
            continue
        try:
            metadata = os.stat(candidate, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            failed.append(candidate)
            continue
        if candidate.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            unsafe += 1
            continue
        if metadata.st_mtime > cutoff_timestamp:
            too_young += 1
            continue
        try:
            shutil.rmtree(candidate)
        except FileNotFoundError:
            continue
        except OSError:
            failed.append(candidate)
        else:
            removed.append(candidate)

    return GeneratedTempCleanupResult(
        removed=tuple(removed),
        too_young=too_young,
        unsafe=unsafe,
        failed=tuple(failed),
    )


def _safe_owner_name(value: str) -> bool:
    return bool(
        0 < len(value) <= 120
        and all(character.isalnum() or character in {"-", "_"} for character in value)
    )


def _background_story_reference(value: str) -> tuple[str, str] | None:
    try:
        path = urlsplit(value).path
    except ValueError:
        return None
    if not path.startswith(BACKGROUND_STORY_MEDIA_URL_PREFIX):
        return None
    relative = path.removeprefix(BACKGROUND_STORY_MEDIA_URL_PREFIX)
    parts = relative.split("/")
    if len(parts) != 2:
        return None
    owner_name, filename = parts
    if not _safe_owner_name(owner_name) or not BACKGROUND_STORY_MEDIA_PATTERN.fullmatch(filename):
        return None
    return owner_name, filename


def collect_background_story_media_references(values: Iterable[Any]) -> set[tuple[str, str]]:
    """Collect exact public media references from bounded durable JSON-like values."""

    references: set[tuple[str, str]] = set()
    pending = list(values)
    visited: set[int] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            reference = _background_story_reference(value)
            if reference is not None:
                references.add(reference)
            continue
        if not isinstance(value, (dict, list, tuple)):
            continue
        identity = id(value)
        if identity in visited:
            continue
        visited.add(identity)
        pending.extend(value.values() if isinstance(value, dict) else value)
    return references


def _safe_owner_directories(
    generated_root: Path,
    owner_directories: Mapping[str, Path] | None,
) -> tuple[dict[str, Path], int]:
    safe: dict[str, Path] = {}
    unsafe = 0
    if owner_directories is None:
        try:
            candidates = {
                path.name: path
                for path in generated_root.iterdir()
                if not path.name.startswith(".") and _safe_owner_name(path.name)
            }
        except FileNotFoundError:
            return {}, 0
        except OSError:
            return {}, 1
    else:
        candidates = owner_directories

    root_resolved = generated_root.resolve(strict=False)
    for owner_name, directory in candidates.items():
        path = Path(directory)
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            unsafe += 1
            continue
        if (
            not _safe_owner_name(owner_name)
            or path.is_symlink()
            or resolved.parent != root_resolved
            or resolved.name != owner_name
        ):
            unsafe += 1
            continue
        safe[owner_name] = path
    return safe, unsafe


def cleanup_unreferenced_background_story_media(
    *,
    generated_root: Path,
    saved_values: Iterable[Any],
    owner_directories: Mapping[str, Path] | None = None,
    now: datetime | None = None,
    minimum_age: timedelta = timedelta(days=8),
) -> GeneratedMediaCleanupResult:
    """Remove old, unreferenced Telegram story media and no other generated assets.

    The caller must supply every durable value that can own one of these URLs. If a
    durable source cannot be read, the caller should fail closed and skip this function.
    """

    if minimum_age.total_seconds() < 0:
        raise ValueError("minimum_age must not be negative")
    effective_now = now or datetime.now(UTC)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=UTC)
    cutoff_timestamp = effective_now.timestamp() - minimum_age.total_seconds()
    references = collect_background_story_media_references(saved_values)
    safe_directories, unsafe = _safe_owner_directories(generated_root, owner_directories)
    removed: list[Path] = []
    failed: list[Path] = []
    referenced = 0
    too_young = 0

    for owner_name, directory in safe_directories.items():
        try:
            candidates = list(directory.iterdir())
        except FileNotFoundError:
            continue
        except OSError:
            unsafe += 1
            continue
        for candidate in candidates:
            if not BACKGROUND_STORY_MEDIA_PATTERN.fullmatch(candidate.name):
                continue
            try:
                metadata = os.stat(candidate, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError:
                failed.append(candidate)
                continue
            if candidate.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                unsafe += 1
                continue
            if (owner_name, candidate.name) in references:
                referenced += 1
                continue
            if metadata.st_mtime > cutoff_timestamp:
                too_young += 1
                continue
            try:
                candidate.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                failed.append(candidate)
            else:
                removed.append(candidate)

    return GeneratedMediaCleanupResult(
        removed=tuple(removed),
        referenced=referenced,
        too_young=too_young,
        unsafe=unsafe,
        failed=tuple(failed),
    )
