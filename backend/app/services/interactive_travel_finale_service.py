from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.llm.compat import complete_chat
from app.schemas import InteractiveTravelState
from app.services.image_service import (
    generate_video_from_image_bytes,
    strip_generated_video_auxiliary_streams,
)

GENERATED_ROOT = Path(__file__).resolve().parents[2] / "static" / "generated"
FINALE_FILENAME = "finale.json"
FINALE_VIDEO_MODEL = "bytedance/seedance-2.0"
FINALE_DURATION_SECONDS = 15
FINALE_RESOLUTION = "720p"
FINALE_ASPECT_RATIO = "9:16"

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


def _travel_dir(travel_id: str) -> Path:
    if not re.fullmatch(r"interactive-travel-[A-Za-z0-9_-]+", travel_id):
        raise ValueError("invalid interactive travel id")
    return GENERATED_ROOT / travel_id


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
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": 1,
        "savedAt": _now_iso(),
        "owner": {
            "telegramId": telegram_id,
            "username": username,
            "firstName": first_name,
        },
        "travel": travel.model_dump(mode="json"),
    }
    target = output_dir / FINALE_FILENAME
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return payload


def read_interactive_travel_finale(travel_id: str) -> dict[str, Any]:
    path = _travel_dir(travel_id) / FINALE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
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
    if not video_path.exists():
        references = [
            {"type": "video_url", "video_url": {"url": url}} for url in reference_urls
        ]
        video_bytes = generate_video_from_image_bytes(
            None,
            label="interactive_travel_finale/video",
            prompt=prompt.strip(),
            resolution=FINALE_RESOLUTION,
            aspect_ratio=FINALE_ASPECT_RATIO,
            duration=FINALE_DURATION_SECONDS,
            provider="openrouter",
            input_references=references,
            model=FINALE_VIDEO_MODEL,
        )
        video_path.write_bytes(strip_generated_video_auxiliary_streams(video_bytes))
        metadata_path.write_text(
            json.dumps(
                {
                    "id": fingerprint,
                    "createdAt": _now_iso(),
                    "prompt": prompt.strip(),
                    "model": FINALE_VIDEO_MODEL,
                    "durationSeconds": FINALE_DURATION_SECONDS,
                    "referenceUrls": reference_urls,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["videoUrl"] = (
        f"/static/generated/{travel.travelId}/finale-attempts/{fingerprint}.mp4"
    )
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
