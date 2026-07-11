from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from app.config import get_settings
from app.schemas import LocalPetChatContext
from app.services.background_story_service import (
    generate_background_story_image_bytes,
    select_background_story_direction,
    story_direction_block,
)
from app.services.character_dossier import story_character_data
from app.services.lore_runtime import lore_prompt_block
from app.services.openai_service import (
    chat_reasoning_effort_kwargs,
    get_chat_model,
    get_openai_client,
)
from app.services.pet_reply_engine.speech_runtime import (
    background_story_reasoning_effort,
    full_story_system_prompt,
    full_story_user_prompt,
)
from app.services.prompt_debug import log_chat_completion_prompt, log_chat_completion_response

STAT_KEYS = ("hunger", "happiness", "energy")
PART_COUNT = 4
MAX_PART_IMPACT = 25
MAX_PART_TOTAL_IMPACT = 35

FULL_STORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "overallTitle": {"type": "string", "maxLength": 120},
        "arcPlan": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "goal": {"type": "string", "maxLength": 240},
                "stakes": {"type": "string", "maxLength": 240},
                "escalation": {"type": "string", "maxLength": 300},
                "finale": {"type": "string", "maxLength": 240},
            },
            "required": ["goal", "stakes", "escalation", "finale"],
        },
        "parts": {
            "type": "array",
            "minItems": PART_COUNT,
            "maxItems": PART_COUNT,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "partNumber": {"type": "integer", "minimum": 1, "maximum": PART_COUNT},
                    "title": {"type": "string", "maxLength": 120},
                    "summary": {"type": "string", "maxLength": 360},
                    "storyParagraphs": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "string", "maxLength": 260},
                    },
                    "valence": {
                        "type": "string",
                        "enum": ["positive", "negative", "mixed"],
                    },
                    "statImpacts": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 2,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "stat": {"type": "string", "enum": list(STAT_KEYS)},
                                "amount": {
                                    "type": "integer",
                                    "minimum": -MAX_PART_IMPACT,
                                    "maximum": MAX_PART_IMPACT,
                                },
                                "reason": {"type": "string", "maxLength": 280},
                            },
                            "required": ["stat", "amount", "reason"],
                        },
                    },
                },
                "required": [
                    "partNumber",
                    "title",
                    "summary",
                    "storyParagraphs",
                    "valence",
                    "statImpacts",
                ],
            },
        },
    },
    "required": ["overallTitle", "arcPlan", "parts"],
}


class FullStoryGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class FullStoryPart:
    part_number: int
    title: str
    summary: str
    story_text: str
    valence: str
    stat_impacts: tuple[dict[str, Any], ...]

    def model_dump(self) -> dict[str, Any]:
        return {
            "partNumber": self.part_number,
            "title": self.title,
            "summary": self.summary,
            "storyText": self.story_text,
            "valence": self.valence,
            "statImpacts": list(self.stat_impacts),
        }


@dataclass(frozen=True)
class FullStoryResult:
    overall_title: str
    arc_plan: dict[str, str]
    story_direction: dict[str, str]
    parts: tuple[FullStoryPart, ...]
    prompt_debug: list[dict[str, Any]]

    def model_dump(self) -> dict[str, Any]:
        return {
            "overallTitle": self.overall_title,
            "arcPlan": self.arc_plan,
            "storyDirection": self.story_direction,
            "parts": [part.model_dump() for part in self.parts],
            "promptDebug": self.prompt_debug,
        }


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].rstrip()


def _normalize_impacts(value: Any, *, valence: str) -> tuple[dict[str, Any], ...]:
    raw_items = value if isinstance(value, list) else []
    impacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        stat = raw.get("stat")
        amount = raw.get("amount")
        if stat not in STAT_KEYS or stat in seen or isinstance(amount, bool):
            continue
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            continue
        amount = max(-MAX_PART_IMPACT, min(MAX_PART_IMPACT, amount))
        if amount == 0:
            continue
        if valence == "positive" and amount < 0:
            continue
        if valence == "negative" and amount > 0:
            continue
        remaining = MAX_PART_TOTAL_IMPACT - total
        if remaining <= 0:
            break
        amount = min(amount, remaining) if amount > 0 else -min(abs(amount), remaining)
        impacts.append(
            {
                "stat": stat,
                "amount": amount,
                "reason": _text(raw.get("reason"), 280),
            }
        )
        seen.add(stat)
        total += abs(amount)
    if not impacts:
        raise FullStoryGenerationError("FULL_STORY_PART_IMPACTS_MISSING")
    signs = {1 if item["amount"] > 0 else -1 for item in impacts}
    if valence == "mixed" and signs != {-1, 1}:
        raise FullStoryGenerationError("FULL_STORY_MIXED_IMPACTS_INVALID")
    return tuple(impacts)


def _normalize_payload(
    payload: dict[str, Any],
) -> tuple[str, dict[str, str], tuple[FullStoryPart, ...]]:
    overall_title = _text(payload.get("overallTitle"), 120) or "Большое путешествие"
    raw_plan = payload.get("arcPlan") if isinstance(payload.get("arcPlan"), dict) else {}
    arc_plan = {
        key: _text(raw_plan.get(key), limit)
        for key, limit in (("goal", 240), ("stakes", 240), ("escalation", 300), ("finale", 240))
    }
    raw_parts = payload.get("parts") if isinstance(payload.get("parts"), list) else []
    if len(raw_parts) != PART_COUNT:
        raise FullStoryGenerationError("FULL_STORY_PART_COUNT_INVALID")
    parts: list[FullStoryPart] = []
    for expected_number, raw in enumerate(raw_parts, start=1):
        if not isinstance(raw, dict) or raw.get("partNumber") != expected_number:
            raise FullStoryGenerationError("FULL_STORY_PART_ORDER_INVALID")
        paragraphs = raw.get("storyParagraphs")
        if not isinstance(paragraphs, list) or len(paragraphs) != 3:
            raise FullStoryGenerationError("FULL_STORY_PARAGRAPHS_INVALID")
        valence = raw.get("valence")
        if valence not in {"positive", "negative", "mixed"}:
            raise FullStoryGenerationError("FULL_STORY_VALENCE_INVALID")
        parts.append(
            FullStoryPart(
                part_number=expected_number,
                title=_text(raw.get("title"), 120) or f"Часть {expected_number}",
                summary=_text(raw.get("summary"), 360),
                story_text="\n\n".join(_text(value, 260) for value in paragraphs),
                valence=valence,
                stat_impacts=_normalize_impacts(raw.get("statImpacts"), valence=valence),
            )
        )
    return overall_title, arc_plan, tuple(parts)


def _full_story_anti_repeat(history: list[dict[str, Any]] | None) -> str:
    lines: list[str] = []
    for item in (history or [])[-8:]:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("overallTitle") or item.get("title"), 120)
        raw_plan = item.get("arcPlan") if isinstance(item.get("arcPlan"), dict) else {}
        goal = _text(item.get("goal") or raw_plan.get("goal"), 240)
        direction = (
            item.get("storyDirection") if isinstance(item.get("storyDirection"), dict) else item
        )
        structure = ", ".join(
            value
            for key in ("plotMode", "incidentClass", "settingClass", "resolutionMode")
            if (value := _text(direction.get(key), 80))
        )
        parts = [value for value in (title, goal, structure) if value]
        if parts:
            lines.append(" — ".join(parts))
    if not lines:
        return "ANTI_REPEAT: предыдущих полных историй пока нет."
    return (
        "ANTI_REPEAT: предыдущие полные истории перечислены только как запрет на повтор. "
        "Не продолжай их и не заимствуй участников, предметы или места. Не повторяй главную "
        "потребность, тип осложнения и способ развязки, заменив только декорации.\n- "
        + "\n- ".join(lines)
    )


def generate_full_story(
    *,
    pet: LocalPetChatContext,
    recent_full_stories: list[dict[str, Any]] | None = None,
    day_context: dict[str, Any] | None = None,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> FullStoryResult:
    settings = get_settings()
    openai_client = client or get_openai_client()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    character = json.dumps(story_character_data(pet), ensure_ascii=False, indent=2)
    current_state = json.dumps(
        {
            "stage": pet.stage,
            "stats": pet.stats.model_dump(mode="json"),
            "scale": "0–100; больше — лучше",
        },
        ensure_ascii=False,
        indent=2,
    )
    story_direction = select_background_story_direction(
        recent_full_stories,
        current_stats=pet.stats.model_dump(mode="json"),
    )
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{full_story_system_prompt()}\n\n{lore_prompt_block('backgroundStory')}"
                ),
            },
            {
                "role": "user",
                "content": full_story_user_prompt(
                    {
                        "character": character,
                        "current_state": current_state,
                        "story_direction": story_direction_block(
                            story_direction,
                            enforce_single_valence=False,
                        ),
                        "anti_repeat": _full_story_anti_repeat(recent_full_stories),
                        "day_context": json.dumps(
                            day_context
                            or {
                                "mode": "manual",
                                "rule": "Плановое локальное время частей не задано.",
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    }
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "full_story",
                "schema": FULL_STORY_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(background_story_reasoning_effort()),
    }
    prompt_debug = [log_chat_completion_prompt("full_story/generate", request_kwargs)]
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("full_story/generate", completion)
    content = completion.choices[0].message.content or "{}"
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise FullStoryGenerationError("FULL_STORY_JSON_INVALID") from exc
    if not isinstance(payload, dict):
        raise FullStoryGenerationError("FULL_STORY_PAYLOAD_INVALID")
    overall_title, arc_plan, parts = _normalize_payload(payload)
    return FullStoryResult(
        overall_title=overall_title,
        arc_plan=arc_plan,
        story_direction=story_direction,
        parts=parts,
        prompt_debug=prompt_debug,
    )


def generate_full_story_part_image_bytes(
    *,
    pet: LocalPetChatContext,
    overall_title: str,
    part: FullStoryPart | dict[str, Any],
    prompt_debug: list[dict[str, Any]] | None = None,
    recent_story_events: list[dict[str, Any]] | None = None,
    direction_output: dict[str, str] | None = None,
) -> bytes:
    if isinstance(part, FullStoryPart):
        title = part.title
        summary = part.summary
        story_text = part.story_text
        valence = part.valence
        delivery_context = ""
    else:
        title = _text(part.get("title"), 120)
        summary = _text(part.get("summary"), 360)
        story_text = str(part.get("storyText") or "").strip()
        valence = _text(part.get("valence"), 40) or "mixed"
        local_time = _text(part.get("scheduledLocalTime"), 20)
        day_period = _text(part.get("dayPeriod"), 40)
        delivery_context = (
            f" Контекст доставки: {day_period}, локальное время {local_time}. "
            "Если кадр на улице, согласуй естественный свет с этим временем; "
            "для интерьера не добавляй внешнее время искусственно."
            if local_time or day_period
            else ""
        )
    image_story = SimpleNamespace(
        title=f"{overall_title}: {title}",
        summary=f"{summary}{delivery_context}",
        story_text=story_text,
        event_type="full_story_part",
        valence=valence,
        tags=(),
        prompt_debug=prompt_debug if prompt_debug is not None else [],
    )
    return generate_background_story_image_bytes(
        pet=pet,
        story=image_story,
        recent_story_events=recent_story_events,
        direction_output=direction_output,
    )
