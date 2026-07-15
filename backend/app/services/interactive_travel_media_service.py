from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from threading import Lock

from PIL import Image, ImageOps

from app.config import get_settings
from app.schemas import LocalPetChatContext
from app.services.background_story_service import (
    BackgroundStoryResult,
    reserve_background_story_image_bytes,
    reserve_background_story_video_bytes,
)
from app.services.image_service import generated_dir_for

INTERACTIVE_TRAVEL_BACKGROUND_SIZE = (450, 600)
INTERACTIVE_TRAVEL_VIDEO_SOURCE_SIZE = (720, 960)
INTERACTIVE_TRAVEL_VIDEO_ASPECT_RATIO = "3:4"
INTERACTIVE_TRAVEL_PROVIDER_SIZE = "768x1024"
INTERACTIVE_TRAVEL_MEDIA_VARIANT_PATTERN = re.compile(r"situation|outcome-[0-3]")
_INTERACTIVE_TRAVEL_LOCK_BUCKET_COUNT = 256
_INTERACTIVE_TRAVEL_CANCEL_RETENTION_SECONDS = 180 * 24 * 60 * 60
_INTERACTIVE_TRAVEL_FILE_LOCKS = {
    namespace: tuple(Lock() for _ in range(_INTERACTIVE_TRAVEL_LOCK_BUCKET_COUNT))
    for namespace in ("lifecycle", "media")
}

INTERACTIVE_TRAVEL_VERTICAL_COMPOSITION = """
PORTRAIT 3:4 FORMAT — COMPOSITION ONLY:
- Compose directly for a 3:4 portrait canvas, the closest video-provider format to the travel
  media container.
- Keep the complete main character, the decisive action and every required story object inside
  the central 80% of the canvas width and height.
- Use the outer edges only for expendable atmosphere and scenery; do not put required objects
  or body parts near any edge.
- Preserve the same art direction, materials, palette, lighting and character treatment as the
  standard story illustration. Change framing only.
""".strip()


def _validate_interactive_travel_id(travel_id: str) -> None:
    if not re.fullmatch(r"interactive-travel-[A-Za-z0-9_-]+", travel_id):
        raise ValueError("Invalid interactive travel id")


def _validate_interactive_travel_media_variant(variant: str) -> None:
    if not INTERACTIVE_TRAVEL_MEDIA_VARIANT_PATTERN.fullmatch(variant):
        raise ValueError("Invalid interactive travel media variant")


def _interactive_travel_lock_root(output_dir: Path) -> Path:
    return output_dir.parent / ".interactive-travel-locks"


def _interactive_travel_lock_bucket(key: str) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:2], "big") % _INTERACTIVE_TRAVEL_LOCK_BUCKET_COUNT


@contextmanager
def _interactive_travel_file_lock(
    output_dir: Path,
    namespace: str,
    key: str,
) -> Iterator[None]:
    if namespace not in _INTERACTIVE_TRAVEL_FILE_LOCKS:
        raise ValueError("Invalid interactive travel lock namespace")
    bucket = _interactive_travel_lock_bucket(key)
    lock_root = _interactive_travel_lock_root(output_dir)
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{namespace}-bucket-{bucket:03d}.lock"
    with _INTERACTIVE_TRAVEL_FILE_LOCKS[namespace][bucket], lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _interactive_travel_cancel_marker(output_dir: Path, travel_id: str) -> Path:
    return _interactive_travel_lock_root(output_dir) / f"{travel_id}.cancelled"


def _prune_expired_interactive_travel_cancel_markers(
    output_dir: Path,
    *,
    keep_travel_id: str,
) -> None:
    retention_seconds = getattr(
        get_settings(),
        "interactive_travel_owner_retention_seconds",
        _INTERACTIVE_TRAVEL_CANCEL_RETENTION_SECONDS,
    )
    cutoff = time.time() - retention_seconds
    lock_root = _interactive_travel_lock_root(output_dir)
    for marker in lock_root.glob("interactive-travel-*.cancelled"):
        if marker.name == f"{keep_travel_id}.cancelled":
            continue
        try:
            if marker.stat().st_mtime < cutoff:
                marker.unlink(missing_ok=True)
        except OSError:
            continue


def _assert_interactive_travel_generation_active(
    travel_id: str,
    output_dir: Path,
) -> None:
    if _interactive_travel_cancel_marker(output_dir, travel_id).is_file():
        raise RuntimeError("INTERACTIVE_TRAVEL_GENERATION_CANCELLED")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_nonempty(path: Path, content: bytes) -> None:
    if not content:
        raise ValueError("Generated media is empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _is_nonempty_regular_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size > 0
    except OSError:
        return False


def _interactive_travel_media_url(travel_id: str, path: Path) -> str:
    return f"/static/generated/{travel_id}/{path.name}?v={path.stat().st_mtime_ns}"


def _has_completed_interactive_travel_finale(output_dir: Path, travel_id: str) -> bool:
    finale_path = output_dir / "finale.json"
    if not _is_nonempty_regular_file(finale_path):
        return False
    try:
        payload = json.loads(finale_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    travel = payload.get("travel") if isinstance(payload, dict) else None
    return bool(
        isinstance(travel, dict)
        and travel.get("travelId") == travel_id
        and travel.get("completed") is True
    )


def _cancel_interactive_travel_generation(
    travel_id: str,
    *,
    preserve_finale: bool,
) -> bool:
    _validate_interactive_travel_id(travel_id)
    output_dir = generated_dir_for(travel_id)
    with _interactive_travel_file_lock(output_dir, "lifecycle", travel_id):
        _atomic_write_nonempty(
            _interactive_travel_cancel_marker(output_dir, travel_id),
            b"cancelled\n",
        )
        _prune_expired_interactive_travel_cancel_markers(
            output_dir,
            keep_travel_id=travel_id,
        )
        if (
            preserve_finale
            and not output_dir.is_symlink()
            and _has_completed_interactive_travel_finale(output_dir, travel_id)
        ):
            return True
        if output_dir.is_symlink():
            output_dir.unlink(missing_ok=True)
            return False
        try:
            shutil.rmtree(output_dir)
        except FileNotFoundError:
            pass
        return False


def cancel_interactive_travel_generation(travel_id: str) -> bool:
    """Fence new media commits and keep an existing finale archive intact."""

    return _cancel_interactive_travel_generation(travel_id, preserve_finale=True)


def reset_interactive_travel_generation(travel_id: str) -> None:
    _cancel_interactive_travel_generation(travel_id, preserve_finale=False)


def _normalize_interactive_travel_background_image(image_bytes: bytes) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        fitted = ImageOps.fit(
            normalized,
            INTERACTIVE_TRAVEL_BACKGROUND_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        buffer = BytesIO()
        fitted.save(buffer, format="PNG")
        return buffer.getvalue()


def _normalize_interactive_travel_video_source(image_bytes: bytes) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        normalized = ImageOps.exif_transpose(image).convert("RGB")
        fitted = ImageOps.fit(
            normalized,
            INTERACTIVE_TRAVEL_VIDEO_SOURCE_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        buffer = BytesIO()
        fitted.save(buffer, format="PNG")
        return buffer.getvalue()


def generate_interactive_travel_part_image(
    *,
    pet: LocalPetChatContext,
    travel_id: str,
    destination: str,
    part_number: int,
    title: str,
    story_text: str,
    variant: str = "situation",
) -> str:
    _validate_interactive_travel_id(travel_id)
    if not 1 <= part_number <= 7:
        raise ValueError("Invalid interactive travel part number")
    output_dir = generated_dir_for(travel_id)
    _validate_interactive_travel_media_variant(variant)
    suffix = "" if variant == "situation" else f"-{variant}"
    path = output_dir / f"interactive-travel-part-{part_number:02d}{suffix}.png"
    video_source_path = output_dir / (
        f"interactive-travel-part-{part_number:02d}{suffix}-video-source.png"
    )
    media_lock_key = f"image:{travel_id}:{part_number}:{variant}"
    lifecycle_lock_key = travel_id
    with _interactive_travel_file_lock(output_dir, "media", media_lock_key):
        with _interactive_travel_file_lock(output_dir, "lifecycle", lifecycle_lock_key):
            _assert_interactive_travel_generation_active(travel_id, output_dir)
            if _is_nonempty_regular_file(path):
                return _interactive_travel_media_url(travel_id, path)

        image_story = BackgroundStoryResult(
            title=title,
            summary=f"Путешествие в место «{destination}». Часть {part_number}.",
            story_text=story_text,
            event_type="interactive_travel_part",
            valence="mixed",
            tags=(destination,),
            rag_text=story_text,
            story_library_patch=None,
            lite_overlay_patch=None,
            recent_story_event=None,
            prompt_debug=[],
        )
        with reserve_background_story_image_bytes(
            pet=pet,
            story=image_story,
            image_size=INTERACTIVE_TRAVEL_PROVIDER_SIZE,
            composition_direction=INTERACTIVE_TRAVEL_VERTICAL_COMPOSITION,
        ) as raw_image_bytes:
            image_bytes = _normalize_interactive_travel_background_image(raw_image_bytes)
            video_source_bytes = _normalize_interactive_travel_video_source(raw_image_bytes)
            with _interactive_travel_file_lock(output_dir, "lifecycle", lifecycle_lock_key):
                _assert_interactive_travel_generation_active(travel_id, output_dir)
                # The poster is the commit marker. A lost response can safely reuse it; if the
                # auxiliary source is missing, video generation reconstructs it from the poster.
                _atomic_write_nonempty(video_source_path, video_source_bytes)
                _atomic_write_nonempty(path, image_bytes)
                return _interactive_travel_media_url(travel_id, path)


def generate_interactive_travel_part_video(
    *,
    travel_id: str,
    part_number: int,
    variant: str = "situation",
) -> str:
    _validate_interactive_travel_id(travel_id)
    if not 1 <= part_number <= 7:
        raise ValueError("Invalid interactive travel part number")
    output_dir = generated_dir_for(travel_id)
    _validate_interactive_travel_media_variant(variant)
    suffix = "" if variant == "situation" else f"-{variant}"
    path = output_dir / f"interactive-travel-part-{part_number:02d}{suffix}.mp4"
    source_path = output_dir / f"interactive-travel-part-{part_number:02d}{suffix}-video-source.png"
    poster_path = output_dir / f"interactive-travel-part-{part_number:02d}{suffix}.png"
    media_lock_key = f"video:{travel_id}:{part_number}:{variant}"
    lifecycle_lock_key = travel_id
    with _interactive_travel_file_lock(output_dir, "media", media_lock_key):
        with _interactive_travel_file_lock(output_dir, "lifecycle", lifecycle_lock_key):
            _assert_interactive_travel_generation_active(travel_id, output_dir)
            if _is_nonempty_regular_file(path):
                return _interactive_travel_media_url(travel_id, path)
            if _is_nonempty_regular_file(source_path):
                source_bytes = source_path.read_bytes()
            elif _is_nonempty_regular_file(poster_path):
                source_bytes = _normalize_interactive_travel_video_source(poster_path.read_bytes())
            else:
                raise FileNotFoundError("Interactive travel poster is missing")

        with reserve_background_story_video_bytes(
            source_bytes,
            aspect_ratio=INTERACTIVE_TRAVEL_VIDEO_ASPECT_RATIO,
        ) as video_bytes:
            with _interactive_travel_file_lock(output_dir, "lifecycle", lifecycle_lock_key):
                _assert_interactive_travel_generation_active(travel_id, output_dir)
                _atomic_write_nonempty(path, video_bytes)
                return _interactive_travel_media_url(travel_id, path)
