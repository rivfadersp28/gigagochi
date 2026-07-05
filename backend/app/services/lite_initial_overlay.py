from __future__ import annotations

import logging
import re
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.services.openai_service import chat_reasoning_effort_kwargs, get_openai_client
from app.services.prompt_debug import log_chat_completion_prompt

logger = logging.getLogger(__name__)

MAX_INITIAL_LITE_TEXT_CHARS = 700


def _compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _subject(description: str, character_bible: dict[str, Any]) -> str:
    identity = _dict(character_bible.get("identity"))
    name = _compact_spaces(str(identity.get("name") or ""))
    species = _compact_spaces(
        str(identity.get("species") or character_bible.get("species") or description)
    )
    return f"{name}, {species}" if name and species else species or description


def _lite_prompt(subject: str, user_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": f"Отвечай как {subject}."},
        {"role": "user", "content": user_prompt},
    ]


def _completion_text(
    *,
    client: Any,
    settings: Any,
    label: str,
    messages: list[dict[str, str]],
) -> str:
    timeout = getattr(
        settings,
        "openai_character_timeout_seconds",
        settings.openai_chat_timeout_seconds,
    )
    request_kwargs: dict[str, Any] = {
        "model": settings.openai_chat_model,
        "messages": messages,
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(
            getattr(settings, "openai_chat_reasoning_effort", None)
        ),
    }
    log_chat_completion_prompt(label, request_kwargs)
    completion = client.chat.completions.create(**request_kwargs)
    text = _compact_spaces(completion.choices[0].message.content or "")
    return text[:MAX_INITIAL_LITE_TEXT_CHARS]


def _fact(sphere: str, kind: str, text: str) -> dict[str, Any]:
    return {
        "sphere": sphere,
        "kind": kind,
        "text": text,
        "pathHint": f"lite_overlay.spheres.{sphere}",
        "source": "chatgpt_initial_lite_profile",
        "createdAt": _now_iso(),
    }


def _overlay_from_facts(facts: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not facts:
        return None

    spheres: dict[str, dict[str, Any]] = {}
    for fact in facts:
        sphere = str(fact.get("sphere") or "character")
        sphere_payload = spheres.setdefault(sphere, {"facts": []})
        sphere_payload["facts"].append(fact)

    return {
        "version": 1,
        "source": "chatgpt_initial_lite_profile",
        "createdAt": _now_iso(),
        "facts": facts,
        "spheres": spheres,
    }


def create_lite_initial_overlay(
    description: str,
    character_bible: dict[str, Any],
    *,
    client: Any | None = None,
    settings: Any | None = None,
) -> dict[str, Any] | None:
    settings = settings or get_settings()
    client = client or get_openai_client()
    subject = _subject(description, character_bible)
    character_text = _completion_text(
        client=client,
        settings=settings,
        label="pet_creation/lite_character_seed",
        messages=_lite_prompt(subject, "Расскажи о своем характере."),
    )
    world_text = _completion_text(
        client=client,
        settings=settings,
        label="pet_creation/lite_world_seed",
        messages=_lite_prompt(subject, "Расскажи о своем мире."),
    )

    facts: list[dict[str, Any]] = []
    if character_text:
        facts.append(_fact("character", "character_fact", character_text))
    if world_text:
        facts.append(_fact("world", "world_fact", world_text))
    return _overlay_from_facts(facts)


def attach_lite_initial_overlay(
    character_bible: dict[str, Any],
    description: str,
    *,
    client: Any | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    bible = deepcopy(character_bible)
    extensions = _dict(bible.get("extensions"))
    existing_overlay = _dict(extensions.get("lite_overlay"))
    if existing_overlay.get("facts") or existing_overlay.get("spheres"):
        return bible

    try:
        overlay = create_lite_initial_overlay(
            description,
            bible,
            client=client,
            settings=settings,
        )
    except Exception:
        logger.exception("Lite initial overlay generation failed")
        return bible

    if not overlay:
        return bible
    extensions["lite_overlay"] = overlay
    bible["extensions"] = extensions
    return bible
