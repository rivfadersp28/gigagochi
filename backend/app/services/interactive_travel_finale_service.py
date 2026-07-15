from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.llm.compat import complete_chat
from app.schemas import InteractiveTravelState
from app.services.image_service import (
    reserve_video_from_image_bytes,
    strip_generated_video_auxiliary_streams,
)
from app.services.interactive_travel_media_service import (
    _assert_interactive_travel_generation_active,
    _interactive_travel_file_lock,
)

GENERATED_ROOT = Path(__file__).resolve().parents[2] / "static" / "generated"
FINALE_FILENAME = "finale.json"
FINALE_VIDEO_MODEL = "bytedance/seedance-2.0"
FINALE_DURATION_SECONDS = 15
FINALE_RESOLUTION = "720p"
FINALE_ASPECT_RATIO = "9:16"
_FINALE_ATTEMPT_LOCK_BUCKETS = tuple(Lock() for _ in range(64))

DEFAULT_DIRECTION = (
    "Create a single coherent 15-second vertical cinematic montage of this journey.\n"
    "Show four to six visually distinct beats in chronological order. Preserve the same main "
    "character, materials and world across every beat. The user's chosen actions must visibly "
    "happen on screen; do not replace them with generic walking or reaction shots. Compress "
    "dialogue into visible action, cause and consequence. End with a resolved, calm final image "
    "rather than a cliffhanger.\n"
    "No captions, subtitles, logos, split screen or spoken dialogue. Tactile handcrafted "
    "stop-motion style, clear readable staging, purposeful camera transitions, 9:16 composition."
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, content: bytes) -> None:
    if not content:
        raise ValueError("finale artifact must not be empty")
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


def _nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size > 0
    except OSError:
        return False


@contextmanager
def _finale_attempt_lock(output_dir: Path, fingerprint: str):
    lock_dir = output_dir / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{fingerprint}.lock"
    bucket = int(fingerprint[:8], 16) % len(_FINALE_ATTEMPT_LOCK_BUCKETS)
    with _FINALE_ATTEMPT_LOCK_BUCKETS[bucket], lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _travel_dir(travel_id: str) -> Path:
    if not re.fullmatch(r"interactive-travel-[A-Za-z0-9_-]+", travel_id):
        raise ValueError("invalid interactive travel id")
    root = GENERATED_ROOT.resolve(strict=False)
    output_dir = GENERATED_ROOT / travel_id
    if output_dir.is_symlink():
        raise ValueError("interactive travel directory must not be a symlink")
    resolved = output_dir.resolve(strict=False)
    if resolved.parent != root or resolved.name != travel_id:
        raise ValueError("interactive travel directory escapes generated root")
    return output_dir


def _read_finale_payload(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("interactive travel finale must not be a symlink")
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise ValueError("interactive travel finale must be a regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("interactive travel finale must contain an object")
    return payload


def _merge_saved_finale_media(
    travel: InteractiveTravelState,
    target: Path,
) -> tuple[InteractiveTravelState, str | None]:
    if not target.exists() and not target.is_symlink():
        return travel, None
    existing_payload = _read_finale_payload(target)
    existing = InteractiveTravelState.model_validate(existing_payload.get("travel"))
    if existing.travelId != travel.travelId:
        raise ValueError("interactive travel finale id mismatch")
    existing_parts = {part.partNumber: part for part in existing.parts}
    incoming_payload = travel.model_dump(mode="json")
    for part in incoming_payload["parts"]:
        saved_part = existing_parts.get(part["partNumber"])
        if saved_part is None:
            continue
        if not part.get("backgroundImageUrl") and saved_part.backgroundImageUrl:
            part["backgroundImageUrl"] = saved_part.backgroundImageUrl
        if not part.get("backgroundVideoUrl") and saved_part.backgroundVideoUrl:
            part["backgroundVideoUrl"] = saved_part.backgroundVideoUrl
    media_updated_at = existing_payload.get("mediaUpdatedAt")
    return (
        InteractiveTravelState.model_validate(incoming_payload),
        media_updated_at if isinstance(media_updated_at, str) else None,
    )


def save_interactive_travel_finale(
    travel: InteractiveTravelState,
    *,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> dict[str, Any]:
    if not travel.completed:
        raise ValueError("interactive travel finale must be completed")
    output_dir = _travel_dir(travel.travelId)
    target = output_dir / FINALE_FILENAME
    with _interactive_travel_file_lock(output_dir, "lifecycle", travel.travelId):
        _assert_interactive_travel_generation_active(travel.travelId, output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        archived_travel, media_updated_at = _merge_saved_finale_media(travel, target)
        payload = {
            "schemaVersion": 1,
            "savedAt": _now_iso(),
            "owner": {
                "telegramId": telegram_id,
                "username": username,
                "firstName": first_name,
            },
            "travel": archived_travel.model_dump(mode="json"),
        }
        if media_updated_at is not None:
            payload["mediaUpdatedAt"] = media_updated_at
        _atomic_write(
            target,
            (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
    return payload


def patch_interactive_travel_finale_media(
    travel_id: str,
    *,
    part_number: int,
    image_url: str | None = None,
    video_url: str | None = None,
) -> bool:
    """Atomically add late media URLs to an already-saved finale archive."""

    if not 1 <= part_number <= 7:
        raise ValueError("invalid interactive travel part number")
    if image_url is None and video_url is None:
        raise ValueError("at least one interactive travel media URL is required")
    output_dir = _travel_dir(travel_id)
    target = output_dir / FINALE_FILENAME
    with _interactive_travel_file_lock(output_dir, "lifecycle", travel_id):
        if target.is_symlink():
            raise ValueError("interactive travel finale must not be a symlink")
        if not target.exists():
            return False
        payload = _read_finale_payload(target)
        travel = InteractiveTravelState.model_validate(payload.get("travel"))
        if travel.travelId != travel_id:
            raise ValueError("interactive travel finale id mismatch")
        travel_payload = travel.model_dump(mode="json")
        part = next(
            (item for item in travel_payload["parts"] if item["partNumber"] == part_number),
            None,
        )
        if part is None:
            raise ValueError("interactive travel finale part is missing")
        updates = {
            key: value
            for key, value in (
                ("backgroundImageUrl", image_url),
                ("backgroundVideoUrl", video_url),
            )
            if value is not None
        }
        if all(part.get(key) == value for key, value in updates.items()):
            return True
        part.update(updates)
        payload["travel"] = InteractiveTravelState.model_validate(travel_payload).model_dump(
            mode="json"
        )
        payload["mediaUpdatedAt"] = _now_iso()
        _atomic_write(
            target,
            (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        return True


def read_interactive_travel_finale(travel_id: str) -> dict[str, Any]:
    path = _travel_dir(travel_id) / FINALE_FILENAME
    payload = _read_finale_payload(path)
    payload["travel"] = InteractiveTravelState.model_validate(payload["travel"]).model_dump(
        mode="json"
    )
    return payload


def list_interactive_travel_finales() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in GENERATED_ROOT.glob(f"interactive-travel-*/{FINALE_FILENAME}"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            travel = InteractiveTravelState.model_validate(payload.get("travel"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        result.append(
            {
                "travelId": travel.travelId,
                "title": travel.overallTitle,
                "destination": travel.destination,
                "savedAt": payload.get("savedAt"),
                "owner": payload.get("owner") or {},
                "partCount": len(travel.parts),
                "videoCount": sum(bool(part.backgroundVideoUrl) for part in travel.parts),
            }
        )
    return sorted(result, key=lambda item: str(item.get("savedAt") or ""), reverse=True)


def build_interactive_travel_story(travel: InteractiveTravelState) -> str:
    lines = [
        f"TITLE: {travel.overallTitle}",
        f"DESTINATION: {travel.destination}",
    ]
    if travel.introReaction:
        lines.append(f"OPENING REACTION: {travel.introReaction.text}")
    for part in travel.parts:
        lines.extend(
            [
                "",
                f"BEAT {part.partNumber}: {part.title}",
                f"SITUATION: {part.storyText}",
                f"OBSTACLE: {part.challenge}",
            ]
        )
        if part.answer:
            lines.append(f"USER-CHOSEN ACTION: {part.answer}")
        if part.result:
            lines.extend(
                [
                    f"CHARACTER REACTION: {part.result.reaction}",
                    f"VISIBLE ACTION: {part.result.text}",
                    f"CONSEQUENCE: {part.result.consequence}",
                ]
            )
        if part.transition:
            lines.append(f"TIME TRANSITION: {part.transition.summary}")
    lines.extend(
        [
            "",
            f"FINAL OUTCOME: {travel.outcomeValence or 'resolved'}",
        ]
    )
    return "\n".join(lines)


def compile_interactive_travel_video_prompt(
    travel: InteractiveTravelState,
    *,
    direction: str = DEFAULT_DIRECTION,
) -> str:
    story = build_interactive_travel_story(travel)
    completion = complete_chat(
        "full_story",
        {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a film director writing one production-ready prompt for a video "
                        "generation model. Return only the final English video prompt. Preserve "
                        "the supplied chronology and literal user-chosen actions."
                    ),
                },
                {
                    "role": "user",
                    "content": f"DIRECTION:\n{direction.strip()}\n\nJOURNEY:\n{story}",
                },
            ],
            "temperature": 0.5,
            "max_completion_tokens": 1200,
        },
    )
    prompt = str(completion.content or "").strip()
    if not prompt:
        raise RuntimeError("finale prompt generation returned empty content")
    return prompt


def _reference_url(value: str, base_url: str) -> str:
    absolute = urljoin(f"{base_url.rstrip('/')}/", value)
    parsed = urlsplit(absolute)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("finale video references must resolve to public HTTPS URLs")
    return absolute


def generate_interactive_travel_finale_video(
    travel: InteractiveTravelState,
    *,
    prompt: str,
    reference_base_url: str,
) -> dict[str, Any]:
    reference_urls = [
        _reference_url(part.backgroundVideoUrl, reference_base_url)
        for part in travel.parts
        if part.backgroundVideoUrl
    ]
    if not reference_urls:
        raise ValueError("completed travel contains no video references")
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "travelId": travel.travelId,
                "prompt": prompt.strip(),
                "references": reference_urls,
                "model": FINALE_VIDEO_MODEL,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    output_dir = _travel_dir(travel.travelId) / "finale-attempts"
    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = output_dir / f"{fingerprint}.mp4"
    metadata_path = output_dir / f"{fingerprint}.json"
    with _finale_attempt_lock(output_dir, fingerprint):
        if not _nonempty_file(video_path):
            references = [
                {"type": "video_url", "video_url": {"url": url}} for url in reference_urls
            ]
            with reserve_video_from_image_bytes(
                None,
                label="interactive_travel_finale/video",
                prompt=prompt.strip(),
                resolution=FINALE_RESOLUTION,
                aspect_ratio=FINALE_ASPECT_RATIO,
                duration=FINALE_DURATION_SECONDS,
                provider="openrouter",
                input_references=references,
                model=FINALE_VIDEO_MODEL,
            ) as video_bytes:
                _atomic_write(video_path, strip_generated_video_auxiliary_streams(video_bytes))

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict) or metadata.get("id") != fingerprint:
                raise ValueError("invalid finale metadata")
        except (OSError, ValueError, json.JSONDecodeError):
            created_at = datetime.fromtimestamp(video_path.stat().st_mtime, tz=UTC).isoformat()
            metadata = {
                "id": fingerprint,
                "createdAt": created_at.replace("+00:00", "Z"),
                "prompt": prompt.strip(),
                "model": FINALE_VIDEO_MODEL,
                "durationSeconds": FINALE_DURATION_SECONDS,
                "referenceUrls": reference_urls,
            }
            _atomic_write(
                metadata_path,
                (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
    metadata["videoUrl"] = f"/static/generated/{travel.travelId}/finale-attempts/{fingerprint}.mp4"
    return metadata


def list_interactive_travel_finale_attempts(travel_id: str) -> list[dict[str, Any]]:
    output_dir = _travel_dir(travel_id) / "finale-attempts"
    attempts: list[dict[str, Any]] = []
    for path in output_dir.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        item["videoUrl"] = f"/static/generated/{travel_id}/finale-attempts/{path.stem}.mp4"
        attempts.append(item)
    return sorted(attempts, key=lambda item: str(item.get("createdAt") or ""), reverse=True)
