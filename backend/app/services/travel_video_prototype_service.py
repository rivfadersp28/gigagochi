from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import re
import subprocess
import uuid
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app.config import get_settings
from app.llm.compat import complete_chat, response_log_value
from app.llm.runtime import resolve_llm_model
from app.schemas import LocalPetChatContext, TravelVideoPrototypeResponse
from app.services.background_story_service import (
    BackgroundStoryResult,
    generate_background_story_image_bytes,
    generate_background_story_video_bytes,
)
from app.services.feature_owner import FeatureOwner, TelegramNotificationTarget
from app.services.generation_notification_service import send_travel_ready_video
from app.services.openai_service import chat_reasoning_effort_kwargs, get_chat_model
from app.services.prompt_debug import log_chat_completion_prompt, log_chat_completion_response

GENERATED_ROOT = Path(__file__).resolve().parents[2] / "static" / "generated"
JOB_ID_PATTERN = re.compile(r"travel-video-prototype-[a-f0-9]{32}")
JOB_FILE_NAME = "prototype-job.json"
JOB_LOCK_FILE_NAME = ".prototype-job.lock"
IMAGE_FILE_NAME = "travel-keyframe-01.png"
VIDEO_FILE_NAME = "travel-video.mp4"
TRAVEL_VIDEO_PROTOTYPE_DURATION_SECONDS = 15
TRAVEL_VIDEO_PROTOTYPE_SHOT_COUNT = 3
TRAVEL_VIDEO_PROTOTYPE_SHOT_DURATION_SECONDS = 5
TRAVEL_VIDEO_PROTOTYPE_IMAGE_SIZE = "1152x2048"
TRAVEL_VIDEO_PROTOTYPE_ASPECT_RATIO = "9:16"
TRAVEL_VIDEO_PROTOTYPE_COMPOSITION = (
    "PORTRAIT 9:16 FORMAT — COMPOSITION ONLY: compose directly for a vertical phone screen. "
    "Keep the complete character, decisive action and required story objects inside the central "
    "80% of the frame. Use the outer edges only for expendable atmosphere and scenery. Preserve "
    "the standard handcrafted stop-motion art direction, materials, palette and lighting."
)
TRAVEL_VIDEO_PROTOTYPE_VIDEO_PROMPT = (
    "Handcrafted stop-motion miniature animation with tactile clay, fabric, painted wood and "
    "paper materials. Preserve the source image's exact character identity, art direction, "
    "framing, lighting, colors, environment and object count. One continuous five-second shot "
    "with a locked camera. Continue the exact story action described below through a clear "
    "beginning, decisive movement and settled ending. Use stepped held poses with expressive "
    "body motion, restrained breathing and blinking, responsive props, soft fabric and atmospheric "
    "secondary movement. No redesign, morphing, new character, new prop, lip sync, scene cut or "
    "dramatic physics change."
)
TRAVEL_VIDEO_PROTOTYPE_RECOVERY_INTERVAL_SECONDS = 30
TRAVEL_VIDEO_PROTOTYPE_RECOVERY_BATCH_SIZE = 20
logger = logging.getLogger(__name__)


class TravelVideoPrototypeNotFoundError(RuntimeError):
    pass


class TravelVideoPrototypeIdempotencyConflictError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _job_dir(job_id: str) -> Path:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise TravelVideoPrototypeNotFoundError(job_id)
    return GENERATED_ROOT / job_id


def _atomic_write(path: Path, payload: bytes) -> None:
    if not payload:
        raise ValueError("Generated prototype payload is empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _write_record(job_id: str, record: dict[str, Any]) -> None:
    _atomic_write(
        _job_dir(job_id) / JOB_FILE_NAME,
        json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"),
    )


def _read_record(job_id: str) -> dict[str, Any]:
    path = _job_dir(job_id) / JOB_FILE_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TravelVideoPrototypeNotFoundError(job_id) from exc
    if not isinstance(value, dict) or value.get("jobId") != job_id:
        raise TravelVideoPrototypeNotFoundError(job_id)
    return value


def _update_record(job_id: str, **patch: Any) -> dict[str, Any]:
    record = _read_record(job_id)
    record.update(patch)
    record["updatedAt"] = _now_iso()
    _write_record(job_id, record)
    return record


@contextmanager
def _job_lock(job_id: str, *, blocking: bool) -> Any:
    lock_path = _job_dir(job_id) / JOB_LOCK_FILE_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(lock_file.fileno(), flags)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _asset_url(job_id: str, file_name: str) -> str:
    path = _job_dir(job_id) / file_name
    return f"/static/generated/{job_id}/{file_name}?v={path.stat().st_mtime_ns}"


def _public_record(record: dict[str, Any]) -> TravelVideoPrototypeResponse:
    return TravelVideoPrototypeResponse.model_validate(
        {
            key: record.get(key)
            for key in (
                "jobId",
                "status",
                "prompt",
                "title",
                "scenario",
                "imageUrl",
                "videoUrl",
                "error",
                "createdAt",
                "updatedAt",
            )
        }
    )


def create_travel_video_prototype(
    *,
    telegram_id: int,
    prompt: str,
    request_key: str,
    pet: LocalPetChatContext,
) -> TravelVideoPrototypeResponse:
    job_digest = hashlib.sha256(f"{telegram_id}\0{request_key}".encode()).hexdigest()[:32]
    job_id = f"travel-video-prototype-{job_digest}"
    with _job_lock(job_id, blocking=True):
        try:
            record = _read_record(job_id)
        except TravelVideoPrototypeNotFoundError:
            created_at = _now_iso()
            record = {
                "jobId": job_id,
                "ownerTelegramId": telegram_id,
                "requestKey": request_key,
                "status": "queued",
                "prompt": prompt.strip(),
                "pet": pet.model_dump(mode="json"),
                "createdAt": created_at,
                "updatedAt": created_at,
            }
            _write_record(job_id, record)
        if record.get("ownerTelegramId") != telegram_id:
            raise TravelVideoPrototypeNotFoundError(job_id)
        return _public_record(record)


def create_travel_video_prototype_for_owner(
    *,
    owner: FeatureOwner,
    prompt: str,
    request_key: str,
    pet: LocalPetChatContext,
) -> TravelVideoPrototypeResponse:
    job_id = travel_video_job_id_for_owner(owner, request_key)
    with _job_lock(job_id, blocking=True):
        try:
            record = _read_record(job_id)
        except TravelVideoPrototypeNotFoundError:
            created_at = _now_iso()
            record = {
                "jobId": job_id,
                "ownerNamespace": owner.namespace,
                "ownerKey": owner.storage_key,
                "notificationChatId": (
                    owner.notification_target.chat_id if owner.notification_target else None
                ),
                "requestKey": request_key,
                "status": "queued",
                "prompt": prompt.strip(),
                "pet": pet.model_dump(mode="json"),
                "createdAt": created_at,
                "updatedAt": created_at,
            }
            _write_record(job_id, record)
        if not _record_matches_owner(record, owner):
            raise TravelVideoPrototypeNotFoundError(job_id)
        if record.get("prompt") != prompt.strip() or record.get("pet") != pet.model_dump(
            mode="json"
        ):
            raise TravelVideoPrototypeIdempotencyConflictError(request_key)
        return _public_record(record)


def travel_video_job_id_for_owner(owner: FeatureOwner, request_key: str) -> str:
    job_digest = hashlib.sha256(f"{owner.storage_key}\0{request_key}".encode()).hexdigest()[:32]
    return f"travel-video-prototype-{job_digest}"


def read_travel_video_prototype(
    job_id: str,
    *,
    telegram_id: int,
) -> TravelVideoPrototypeResponse:
    record = _read_record(job_id)
    if record.get("ownerTelegramId") != telegram_id:
        raise TravelVideoPrototypeNotFoundError(job_id)
    return _public_record(record)


def should_resume_travel_video_prototype(job_id: str, *, telegram_id: int) -> bool:
    record = _read_record(job_id)
    if record.get("ownerTelegramId") != telegram_id:
        raise TravelVideoPrototypeNotFoundError(job_id)
    record_status = record.get("status")
    return record_status not in {"failed", "ready"} or (
        record_status == "ready" and not record.get("notificationSentAt")
    )


def _record_owner(record: dict[str, Any]) -> FeatureOwner | None:
    namespace = record.get("ownerNamespace")
    owner_key = record.get("ownerKey")
    if namespace in {"telegram", "google"} and isinstance(owner_key, (int, str)):
        notification_chat_id = record.get("notificationChatId")
        target = (
            TelegramNotificationTarget(notification_chat_id)
            if namespace == "telegram"
            and isinstance(notification_chat_id, int)
            and not isinstance(notification_chat_id, bool)
            else None
        )
        try:
            return FeatureOwner(namespace, owner_key, target)
        except ValueError:
            return None
    telegram_id = record.get("ownerTelegramId")
    if isinstance(telegram_id, int) and not isinstance(telegram_id, bool):
        return FeatureOwner(
            "telegram",
            telegram_id,
            TelegramNotificationTarget(telegram_id),
        )
    return None


def _record_matches_owner(record: dict[str, Any], owner: FeatureOwner) -> bool:
    stored = _record_owner(record)
    return stored is not None and (
        stored.namespace,
        stored.storage_key,
    ) == (owner.namespace, owner.storage_key)


def read_travel_video_prototype_for_owner(
    job_id: str,
    *,
    owner: FeatureOwner,
) -> TravelVideoPrototypeResponse:
    record = _read_record(job_id)
    if not _record_matches_owner(record, owner):
        raise TravelVideoPrototypeNotFoundError(job_id)
    return _public_record(record)


def should_resume_travel_video_prototype_for_owner(
    job_id: str,
    *,
    owner: FeatureOwner,
) -> bool:
    record = _read_record(job_id)
    if not _record_matches_owner(record, owner):
        raise TravelVideoPrototypeNotFoundError(job_id)
    status_value = record.get("status")
    if status_value in {"failed", "ready"}:
        return bool(
            status_value == "ready"
            and owner.notification_target is not None
            and not record.get("notificationSentAt")
        )
    return True


def _character_context(pet: LocalPetChatContext) -> str:
    context = {
        "name": pet.name,
        "description": pet.description,
        "stage": pet.stage,
        "mood": pet.mood,
        "characterBible": pet.characterBible,
    }
    serialized = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    return serialized[:8000]


def _scenario_text(shots: Sequence[dict[str, str]]) -> str:
    portions: list[str] = []
    for index, shot in enumerate(shots):
        start = index * TRAVEL_VIDEO_PROTOTYPE_SHOT_DURATION_SECONDS
        end = start + TRAVEL_VIDEO_PROTOTYPE_SHOT_DURATION_SECONDS
        portions.append(
            f"{start}–{end} сек. {shot['setting']} {shot['action']} Переход: {shot['transition']}"
        )
    return "\n\n".join(portions)[:1600]


def _trim_text(value: Any, max_length: int) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= max_length:
        return normalized
    shortened = normalized[: max_length + 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{shortened}…"


def _generate_scenario(
    prompt: str,
    pet: LocalPetChatContext,
) -> tuple[str, str, tuple[dict[str, str], ...]]:
    settings = get_settings()
    fallback_model = get_chat_model(settings)
    model = resolve_llm_model("background_story", fallback_model)
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты сценарист коротких визуальных путешествий персонажа. Верни JSON. "
                    "История должна состоять ровно из трёх насыщенных пятисекундных сцен для "
                    "пятнадцатисекундного stop-motion ролика. У каждой сцены своё место или "
                    "заметно изменившаяся зона мира, самостоятельное действие и новый визуальный "
                    "образ. Вместе они образуют ясную мини-арку: завязка, осложнение, развязка. "
                    "Сохрани характер и внешнюю идентичность героя. Пиши по-русски."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Пожелание пользователя:\n{prompt}\n\n"
                    f"Персонаж:\n{_character_context(pet)}\n\n"
                    "Придумай название и ровно три сцены. Setting — только короткое описание "
                    "локации одним законченным предложением, без действий героя. Action — одно "
                    "законченное действие сцены. Не повторяй одно действие в разных "
                    "формулировках. Между сценами должен меняться масштаб, окружение или задача. "
                    "Пользовательское пожелание обязательно определяет путешествие и финал."
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "travel_video_prototype_scenario",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 100},
                        "shots": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 3,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "setting": {
                                        "type": "string",
                                        "minLength": 20,
                                        "maxLength": 160,
                                    },
                                    "action": {
                                        "type": "string",
                                        "minLength": 60,
                                        "maxLength": 260,
                                    },
                                    "shotType": {
                                        "type": "string",
                                        "enum": ["wide", "medium", "close-up"],
                                    },
                                    "transition": {
                                        "type": "string",
                                        "minLength": 10,
                                        "maxLength": 70,
                                    },
                                },
                                "required": ["setting", "action", "shotType", "transition"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["title", "shots"],
                    "additionalProperties": False,
                },
            },
        },
        "timeout": settings.openai_chat_timeout_seconds,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    log_chat_completion_prompt("travel_video_prototype/scenario", request_kwargs)
    completion = complete_chat("travel_video_prototype", request_kwargs)
    log_chat_completion_response(
        "travel_video_prototype/scenario",
        response_log_value(completion),
    )
    payload = json.loads(completion.content or "{}")
    title = " ".join(str(payload.get("title") or "").split())[:100]
    raw_shots = payload.get("shots")
    shots = tuple(
        {
            "setting": _trim_text(shot.get("setting"), 160),
            "action": _trim_text(shot.get("action"), 260),
            "shotType": _trim_text(shot.get("shotType"), 20),
            "transition": _trim_text(shot.get("transition"), 70),
        }
        for shot in (raw_shots if isinstance(raw_shots, list) else [])
        if isinstance(shot, dict)
    )
    if (
        not title
        or len(shots) != TRAVEL_VIDEO_PROTOTYPE_SHOT_COUNT
        or any(not all(shot.values()) for shot in shots)
    ):
        raise RuntimeError("TRAVEL_VIDEO_PROTOTYPE_SCENARIO_INVALID")
    return title, _scenario_text(shots), shots


def _image_file_name(shot_number: int) -> str:
    return f"travel-keyframe-{shot_number:02d}.png"


def _clip_file_name(shot_number: int) -> str:
    return f"travel-clip-{shot_number:02d}.mp4"


def _video_prompt_for_shot(shot: dict[str, str]) -> str:
    return (
        f"{TRAVEL_VIDEO_PROTOTYPE_VIDEO_PROMPT}\n\n"
        f"SHOT TYPE: {shot['shotType']}\n"
        f"SETTING: {shot['setting']}\n"
        f"ACTION: {shot['action']}"
    )


def _concat_video_segments(segment_paths: Sequence[Path]) -> bytes:
    if len(segment_paths) != TRAVEL_VIDEO_PROTOTYPE_SHOT_COUNT:
        raise ValueError("Travel prototype requires exactly three video segments")
    with TemporaryDirectory(prefix="travel-prototype-concat-") as temp_dir_value:
        output_path = Path(temp_dir_value) / VIDEO_FILE_NAME
        command = ["ffmpeg", "-v", "error", "-y"]
        for path in segment_paths:
            command.extend(["-f", "mov", "-protocol_whitelist", "file", "-i", str(path)])
        filter_graph = (
            "[0:v]fps=24,format=yuv420p,setpts=PTS-STARTPTS[v0];"
            "[1:v]fps=24,format=yuv420p,setpts=PTS-STARTPTS[v1];"
            "[2:v]fps=24,format=yuv420p,setpts=PTS-STARTPTS[v2];"
            "[v0][v1][v2]concat=n=3:v=1:a=0[out]"
        )
        command.extend(
            [
                "-filter_complex",
                filter_graph,
                "-map",
                "[out]",
                "-t",
                str(TRAVEL_VIDEO_PROTOTYPE_DURATION_SECONDS),
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
        )
        payload = output_path.read_bytes()
        if not payload:
            raise RuntimeError("TRAVEL_VIDEO_PROTOTYPE_CONCAT_EMPTY")
        return payload


def _send_ready_video_best_effort(
    *,
    job_id: str,
    telegram_id: int,
    video: bytes,
) -> bool:
    try:
        return send_travel_ready_video(telegram_id, video) is not False
    except Exception:
        logger.exception(
            "travel video Telegram delivery failed",
            extra={"job_id": job_id, "telegram_id": telegram_id},
        )
        return False


def _saved_shots(record: dict[str, Any]) -> tuple[dict[str, str], ...] | None:
    raw_shots = record.get("shots")
    if not isinstance(raw_shots, list) or len(raw_shots) != TRAVEL_VIDEO_PROTOTYPE_SHOT_COUNT:
        return None
    shots = tuple(
        {
            "setting": str(shot.get("setting") or ""),
            "action": str(shot.get("action") or ""),
            "shotType": str(shot.get("shotType") or ""),
            "transition": str(shot.get("transition") or ""),
        }
        for shot in raw_shots
        if isinstance(shot, dict)
    )
    if len(shots) != TRAVEL_VIDEO_PROTOTYPE_SHOT_COUNT or any(
        not all(shot.values()) for shot in shots
    ):
        return None
    return shots


def _read_nonempty(path: Path) -> bytes | None:
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    return payload or None


def generate_travel_video_prototype(
    *,
    job_id: str,
    telegram_id: int,
) -> None:
    generate_travel_video_prototype_for_owner(
        job_id=job_id,
        owner=FeatureOwner(
            "telegram",
            telegram_id,
            TelegramNotificationTarget(telegram_id),
        ),
    )


def generate_travel_video_prototype_for_owner(
    *,
    job_id: str,
    owner: FeatureOwner,
) -> None:
    with _job_lock(job_id, blocking=False) as acquired:
        if not acquired:
            return
        record = _read_record(job_id)
        if not _record_matches_owner(record, owner):
            raise TravelVideoPrototypeNotFoundError(job_id)
        if record.get("status") == "failed":
            return
        try:
            pet = LocalPetChatContext.model_validate(record["pet"])
            video_path = _job_dir(job_id) / VIDEO_FILE_NAME
            video_bytes = _read_nonempty(video_path)
            if record.get("status") == "ready" and video_bytes:
                if (
                    owner.notification_target is not None
                    and not record.get("notificationSentAt")
                    and _send_ready_video_best_effort(
                        job_id=job_id,
                        telegram_id=owner.notification_target.chat_id,
                        video=video_bytes,
                    )
                ):
                    _update_record(job_id, notificationSentAt=_now_iso())
                return

            shots = _saved_shots(record)
            title = str(record.get("title") or "")
            scenario = str(record.get("scenario") or "")
            if shots is None or not title or not scenario:
                _update_record(job_id, status="writing", error=None)
                title, scenario, shots = _generate_scenario(record["prompt"], pet)
                record = _update_record(
                    job_id,
                    status="illustrating",
                    title=title,
                    scenario=scenario,
                    shots=list(shots),
                )
            else:
                _update_record(job_id, status="illustrating", error=None)

            image_payloads: list[bytes] = []
            for shot_number, shot in enumerate(shots, 1):
                image_path = _job_dir(job_id) / _image_file_name(shot_number)
                image_bytes = _read_nonempty(image_path)
                if image_bytes is None:
                    shot_story_text = f"{shot['setting']} {shot['action']}"
                    story = BackgroundStoryResult(
                        title=f"{title} — сцена {shot_number}",
                        summary=shot["setting"],
                        story_text=shot_story_text,
                        event_type="travel_video_prototype_shot",
                        valence="positive",
                        tags=(record["prompt"][:120], f"scene-{shot_number}"),
                        rag_text=shot_story_text,
                        story_library_patch=None,
                        lite_overlay_patch=None,
                        recent_story_event=None,
                        prompt_debug=[],
                    )
                    image_bytes = generate_background_story_image_bytes(
                        pet=pet,
                        story=story,
                        image_size=TRAVEL_VIDEO_PROTOTYPE_IMAGE_SIZE,
                        composition_direction=TRAVEL_VIDEO_PROTOTYPE_COMPOSITION,
                    )
                    _atomic_write(image_path, image_bytes)
                image_payloads.append(image_bytes)
            _update_record(
                job_id,
                status="animating",
                imageUrl=_asset_url(job_id, IMAGE_FILE_NAME),
            )

            clip_paths: list[Path] = []
            for shot_number, (shot, image_bytes) in enumerate(
                zip(shots, image_payloads, strict=True),
                1,
            ):
                clip_path = _job_dir(job_id) / _clip_file_name(shot_number)
                if _read_nonempty(clip_path) is None:
                    clip_bytes = generate_background_story_video_bytes(
                        image_bytes,
                        aspect_ratio=TRAVEL_VIDEO_PROTOTYPE_ASPECT_RATIO,
                        duration_seconds=TRAVEL_VIDEO_PROTOTYPE_SHOT_DURATION_SECONDS,
                        prompt=_video_prompt_for_shot(shot),
                    )
                    _atomic_write(clip_path, clip_bytes)
                clip_paths.append(clip_path)
            video_bytes = _read_nonempty(video_path)
            if video_bytes is None:
                video_bytes = _concat_video_segments(clip_paths)
                _atomic_write(video_path, video_bytes)
            _update_record(
                job_id,
                status="ready",
                videoUrl=_asset_url(job_id, VIDEO_FILE_NAME),
            )
            if owner.notification_target is not None and _send_ready_video_best_effort(
                job_id=job_id,
                telegram_id=owner.notification_target.chat_id,
                video=video_bytes,
            ):
                _update_record(job_id, notificationSentAt=_now_iso())
        except Exception:
            logger.exception("travel video prototype generation failed", extra={"job_id": job_id})
            _update_record(
                job_id,
                status="failed",
                error="Не удалось собрать видео. Попробуйте ещё раз.",
            )


def resume_pending_travel_video_prototypes(
    *,
    limit: int = TRAVEL_VIDEO_PROTOTYPE_RECOVERY_BATCH_SIZE,
) -> int:
    """Resume interrupted jobs and retry delivery for ready, undelivered videos."""

    if limit < 1:
        raise ValueError("travel video recovery limit must be positive")
    try:
        directories = sorted(
            (
                path
                for path in GENERATED_ROOT.iterdir()
                if path.is_dir() and JOB_ID_PATTERN.fullmatch(path.name)
            ),
            key=lambda path: path.stat().st_mtime,
        )
    except FileNotFoundError:
        return 0
    resumed = 0
    for directory in directories:
        if resumed >= limit:
            break
        try:
            record = _read_record(directory.name)
            status = record.get("status")
            if status == "failed" or (status == "ready" and record.get("notificationSentAt")):
                continue
            owner = _record_owner(record)
            if owner is None:
                continue
            if status == "ready" and owner.notification_target is None:
                continue
            generate_travel_video_prototype_for_owner(
                job_id=directory.name,
                owner=owner,
            )
            resumed += 1
        except (OSError, ValueError, TravelVideoPrototypeNotFoundError):
            logger.warning(
                "travel video recovery skipped invalid job",
                extra={"job_id": directory.name},
                exc_info=True,
            )
    return resumed


async def _travel_video_prototype_recovery_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(resume_pending_travel_video_prototypes)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("travel video recovery batch failed")
        await asyncio.sleep(TRAVEL_VIDEO_PROTOTYPE_RECOVERY_INTERVAL_SECONDS)


def start_travel_video_prototype_recovery_scheduler() -> asyncio.Task[None]:
    return asyncio.create_task(
        _travel_video_prototype_recovery_loop(),
        name="travel-video-prototype-recovery",
    )
