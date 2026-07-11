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
    full_story_plan_quality_system_prompt,
    full_story_plan_quality_user_prompt,
    full_story_quality_check_system_prompt,
    full_story_quality_check_user_prompt,
    full_story_render_system_prompt,
    full_story_render_user_prompt,
    full_story_system_prompt,
    full_story_user_prompt,
)
from app.services.prompt_debug import log_chat_completion_prompt, log_chat_completion_response

STAT_KEYS = ("hunger", "happiness", "energy")
PART_COUNT = 4
MAX_PART_IMPACT = 15
MAX_PART_TOTAL_IMPACT = 15
MAX_PART_STAT_IMPACTS = 1
MAX_STORY_STAT_IMPACTS = 3
FULL_STORY_MIN_TIMEOUT_SECONDS = 150.0
MAX_FULL_STORY_PLAN_ATTEMPTS = 3

STAT_IMPACT_SCHEMA: dict[str, Any] = {
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
}

FULL_STORY_PLAN_SCHEMA: dict[str, Any] = {
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
                    "narrativeFunction": {
                        "type": "string",
                        "enum": ["inciting_change", "complication", "turn", "resolution"],
                    },
                    "title": {"type": "string", "maxLength": 120},
                    "summary": {"type": "string", "maxLength": 360},
                    "eventSvo": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "subject": {"type": "string", "maxLength": 100},
                            "verb": {"type": "string", "maxLength": 80},
                            "object": {"type": "string", "maxLength": 160},
                        },
                        "required": ["subject", "verb", "object"],
                    },
                    "event": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "beforeState": {"type": "string", "maxLength": 260},
                            "trigger": {"type": "string", "maxLength": 260},
                            "protagonistGoal": {"type": "string", "maxLength": 220},
                            "oppositionGoal": {"type": "string", "maxLength": 220},
                            "opposition": {"type": "string", "maxLength": 260},
                            "decisiveAction": {"type": "string", "maxLength": 260},
                            "result": {"type": "string", "maxLength": 260},
                            "afterState": {"type": "string", "maxLength": 260},
                        },
                        "required": [
                            "beforeState",
                            "trigger",
                            "protagonistGoal",
                            "oppositionGoal",
                            "opposition",
                            "decisiveAction",
                            "result",
                            "afterState",
                        ],
                    },
                    "readerHook": {"type": "string", "maxLength": 240},
                    "carryForward": {"type": "string", "maxLength": 300},
                    "valence": {
                        "type": "string",
                        "enum": ["positive", "negative", "mixed"],
                    },
                    "statImpacts": {
                        "type": "array",
                        "minItems": 0,
                        "maxItems": MAX_PART_STAT_IMPACTS,
                        "items": STAT_IMPACT_SCHEMA,
                    },
                },
                "required": [
                    "partNumber",
                    "narrativeFunction",
                    "title",
                    "summary",
                    "eventSvo",
                    "event",
                    "readerHook",
                    "carryForward",
                    "valence",
                    "statImpacts",
                ],
            },
        },
    },
    "required": ["overallTitle", "arcPlan", "parts"],
}

FULL_STORY_RENDER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "parts": {
            "type": "array",
            "minItems": PART_COUNT,
            "maxItems": PART_COUNT,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "partNumber": {"type": "integer", "minimum": 1, "maximum": PART_COUNT},
                    "storyParagraphs": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "string", "maxLength": 320},
                    },
                },
                "required": ["partNumber", "storyParagraphs"],
            },
        }
    },
    "required": ["parts"],
}

FULL_STORY_PLAN_QUALITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "accepted": {"type": "boolean"},
        "parts": {
            "type": "array",
            "minItems": PART_COUNT,
            "maxItems": PART_COUNT,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "partNumber": {"type": "integer", "minimum": 1, "maximum": PART_COUNT},
                    "eventful": {"type": "boolean"},
                    "understandable": {"type": "boolean"},
                    "interesting": {"type": "boolean"},
                    "causal": {"type": "boolean"},
                    "distinct": {"type": "boolean"},
                    "issue": {"type": "string", "maxLength": 400},
                },
                "required": [
                    "partNumber",
                    "eventful",
                    "understandable",
                    "interesting",
                    "causal",
                    "distinct",
                    "issue",
                ],
            },
        },
        "issues": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 400},
        },
        "retryInstruction": {"type": "string", "maxLength": 1000},
    },
    "required": ["accepted", "parts", "issues", "retryInstruction"],
}

# Compatibility alias for tests and callers that inspect the story-part schema.
FULL_STORY_SCHEMA = FULL_STORY_PLAN_SCHEMA

FULL_STORY_QUALITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "accepted": {"type": "boolean"},
        "issues": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "maxLength": 300},
        },
        "retryInstruction": {"type": "string", "maxLength": 800},
    },
    "required": ["accepted", "issues", "retryInstruction"],
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
    del valence
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
        if len(impacts) >= MAX_PART_STAT_IMPACTS:
            break
    return tuple(impacts)


def _normalize_payload(
    plan_payload: dict[str, Any],
    render_payload: dict[str, Any],
) -> tuple[str, dict[str, str], tuple[FullStoryPart, ...]]:
    overall_title = _text(plan_payload.get("overallTitle"), 120) or "Большое путешествие"
    raw_plan = (
        plan_payload.get("arcPlan") if isinstance(plan_payload.get("arcPlan"), dict) else {}
    )
    arc_plan = {
        key: _text(raw_plan.get(key), limit)
        for key, limit in (("goal", 240), ("stakes", 240), ("escalation", 300), ("finale", 240))
    }
    raw_plan_parts = (
        plan_payload.get("parts") if isinstance(plan_payload.get("parts"), list) else []
    )
    raw_render_parts = (
        render_payload.get("parts") if isinstance(render_payload.get("parts"), list) else []
    )
    if len(raw_plan_parts) != PART_COUNT or len(raw_render_parts) != PART_COUNT:
        raise FullStoryGenerationError("FULL_STORY_PART_COUNT_INVALID")
    parts: list[FullStoryPart] = []
    story_impact_count = 0
    for expected_number, (raw_plan_part, raw_render_part) in enumerate(
        zip(raw_plan_parts, raw_render_parts, strict=True),
        start=1,
    ):
        if (
            not isinstance(raw_plan_part, dict)
            or raw_plan_part.get("partNumber") != expected_number
            or not isinstance(raw_render_part, dict)
            or raw_render_part.get("partNumber") != expected_number
        ):
            raise FullStoryGenerationError("FULL_STORY_PART_ORDER_INVALID")
        paragraphs = raw_render_part.get("storyParagraphs")
        if not isinstance(paragraphs, list) or len(paragraphs) != 3:
            raise FullStoryGenerationError("FULL_STORY_PARAGRAPHS_INVALID")
        valence = raw_plan_part.get("valence")
        if valence not in {"positive", "negative", "mixed"}:
            raise FullStoryGenerationError("FULL_STORY_VALENCE_INVALID")
        impacts = _normalize_impacts(raw_plan_part.get("statImpacts"), valence=valence)
        impacts = impacts[: max(0, MAX_STORY_STAT_IMPACTS - story_impact_count)]
        story_impact_count += len(impacts)
        parts.append(
            FullStoryPart(
                part_number=expected_number,
                title=_text(raw_plan_part.get("title"), 120) or f"Часть {expected_number}",
                summary=_text(raw_plan_part.get("summary"), 360),
                story_text="\n\n".join(_text(value, 320) for value in paragraphs),
                valence=valence,
                stat_impacts=impacts,
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


def _completion_payload(completion: Any, *, error_code: str) -> dict[str, Any]:
    try:
        parsed = json.loads(completion.choices[0].message.content or "{}")
    except json.JSONDecodeError as exc:
        raise FullStoryGenerationError(error_code) from exc
    if not isinstance(parsed, dict):
        raise FullStoryGenerationError(error_code)
    return parsed


def _quality_issues(parsed: dict[str, Any], *, limit: int = 8) -> list[str]:
    raw_issues = parsed.get("issues") if isinstance(parsed.get("issues"), list) else []
    issues = [_text(value, 400) for value in raw_issues[:limit] if _text(value, 400)]
    raw_parts = parsed.get("parts") if isinstance(parsed.get("parts"), list) else []
    for part in raw_parts[:PART_COUNT]:
        if not isinstance(part, dict):
            continue
        issue = _text(part.get("issue"), 400)
        if issue and issue not in issues:
            issues.append(issue)
    return issues[:limit]


def _check_full_story_plan(
    *,
    story_plan: dict[str, Any],
    story_direction: dict[str, str],
    client: Any,
    model: str,
    timeout: float,
    prompt_debug: list[dict[str, Any]],
) -> tuple[bool, list[str], str]:
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": full_story_plan_quality_system_prompt()},
            {
                "role": "user",
                "content": full_story_plan_quality_user_prompt(
                    {
                        "story_direction": story_direction_block(
                            story_direction,
                            enforce_single_valence=False,
                        ),
                        "story_plan": json.dumps(
                            story_plan,
                            ensure_ascii=False,
                            indent=2,
                            default=str,
                        ),
                    }
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "full_story_plan_quality_check",
                "schema": FULL_STORY_PLAN_QUALITY_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs("low"),
    }
    prompt_debug.append(
        log_chat_completion_prompt("full_story/plan_quality_check", request_kwargs)
    )
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("full_story/plan_quality_check", completion)
    parsed = _completion_payload(completion, error_code="FULL_STORY_PLAN_QUALITY_JSON_INVALID")
    issues = _quality_issues(parsed)
    retry_instruction = _text(parsed.get("retryInstruction"), 1000)
    accepted = parsed.get("accepted") is True
    prompt_debug.append(
        {
            "event": "full_story_plan_quality_result",
            "accepted": accepted,
            "parts": parsed.get("parts"),
            "issues": issues,
            "retryInstruction": retry_instruction,
        }
    )
    return accepted, issues, retry_instruction


def _check_full_story_quality(
    *,
    story_plan: dict[str, Any],
    story_payload: dict[str, Any],
    story_direction: dict[str, str],
    client: Any,
    model: str,
    timeout: float,
    prompt_debug: list[dict[str, Any]],
) -> tuple[bool, list[str], str]:
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": full_story_quality_check_system_prompt()},
            {
                "role": "user",
                "content": full_story_quality_check_user_prompt(
                    {
                        "story_direction": story_direction_block(
                            story_direction,
                            enforce_single_valence=False,
                        ),
                        "story_plan": json.dumps(
                            story_plan,
                            ensure_ascii=False,
                            indent=2,
                            default=str,
                        ),
                        "story_payload": json.dumps(
                            story_payload,
                            ensure_ascii=False,
                            indent=2,
                            default=str,
                        ),
                    }
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "full_story_quality_check",
                "schema": FULL_STORY_QUALITY_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs("low"),
    }
    prompt_debug.append(log_chat_completion_prompt("full_story/quality_check", request_kwargs))
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("full_story/quality_check", completion)
    parsed = _completion_payload(completion, error_code="FULL_STORY_QUALITY_JSON_INVALID")
    issues = _quality_issues(parsed, limit=6)
    retry_instruction = _text(parsed.get("retryInstruction"), 800)
    accepted = parsed.get("accepted") is True
    prompt_debug.append(
        {
            "event": "full_story_quality_result",
            "accepted": accepted,
            "issues": issues,
            "retryInstruction": retry_instruction,
        }
    )
    return accepted, issues, retry_instruction


def _combined_story_payload(
    story_plan: dict[str, Any],
    rendered_story: dict[str, Any],
) -> dict[str, Any]:
    plan_parts = story_plan.get("parts") if isinstance(story_plan.get("parts"), list) else []
    render_parts = (
        rendered_story.get("parts") if isinstance(rendered_story.get("parts"), list) else []
    )
    combined_parts: list[dict[str, Any]] = []
    for plan_part, render_part in zip(plan_parts, render_parts, strict=False):
        if not isinstance(plan_part, dict) or not isinstance(render_part, dict):
            continue
        combined_parts.append({**plan_part, **render_part})
    return {
        "overallTitle": story_plan.get("overallTitle"),
        "arcPlan": story_plan.get("arcPlan"),
        "parts": combined_parts,
    }


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
    configured_timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    timeout = max(float(configured_timeout), FULL_STORY_MIN_TIMEOUT_SECONDS)
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
    plan_user_content = full_story_user_prompt(
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
    )
    plan_request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{full_story_system_prompt()}\n\n{lore_prompt_block('backgroundStory')}"
                ),
            },
            {"role": "user", "content": plan_user_content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "full_story_plan",
                "schema": FULL_STORY_PLAN_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(background_story_reasoning_effort()),
    }
    prompt_debug = [
        log_chat_completion_prompt("full_story/plan_generate", plan_request_kwargs)
    ]
    completion = openai_client.chat.completions.create(**plan_request_kwargs)
    log_chat_completion_response("full_story/plan_generate", completion)
    story_plan = _completion_payload(completion, error_code="FULL_STORY_PLAN_JSON_INVALID")
    plan_accepted, plan_issues, plan_retry_instruction = _check_full_story_plan(
        story_plan=story_plan,
        story_direction=story_direction,
        client=openai_client,
        model=model,
        timeout=timeout,
        prompt_debug=prompt_debug,
    )
    plan_attempt = 1
    while not plan_accepted and plan_attempt < MAX_FULL_STORY_PLAN_ATTEMPTS:
        plan_attempt += 1
        issue_lines = "\n".join(f"- {issue}" for issue in plan_issues)
        retry_plan_content = (
            f"{plan_user_content}\n\nPLAN_RETRY: предыдущий план отклонён. Создай полностью "
            "новый план четырёх событий, а не косметическую перестановку действий.\n"
            f"Замечания:\n{issue_lines or '- части недостаточно событийны'}\n"
            f"Указание:\n{plan_retry_instruction or 'В каждой части нужен отдельный поворот.'}\n"
            "PREVIOUS_PLAN:\n"
            + json.dumps(story_plan, ensure_ascii=False, indent=2, default=str)
        )
        retry_plan_request = {
            **plan_request_kwargs,
            "messages": [
                plan_request_kwargs["messages"][0],
                {"role": "user", "content": retry_plan_content},
            ],
        }
        prompt_debug.append(
            log_chat_completion_prompt(
                f"full_story/plan_generate_retry_{plan_attempt}",
                retry_plan_request,
            )
        )
        retry_completion = openai_client.chat.completions.create(**retry_plan_request)
        log_chat_completion_response(
            f"full_story/plan_generate_retry_{plan_attempt}",
            retry_completion,
        )
        story_plan = _completion_payload(
            retry_completion,
            error_code="FULL_STORY_PLAN_RETRY_JSON_INVALID",
        )
        plan_accepted, plan_issues, plan_retry_instruction = _check_full_story_plan(
            story_plan=story_plan,
            story_direction=story_direction,
            client=openai_client,
            model=model,
            timeout=timeout,
            prompt_debug=prompt_debug,
        )
    if not plan_accepted:
        raise FullStoryGenerationError("FULL_STORY_PLAN_QUALITY_REJECTED")

    render_user_content = full_story_render_user_prompt(
        {
            "character": character,
            "story_plan": json.dumps(
                story_plan,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
        }
    )
    render_request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": full_story_render_system_prompt()},
            {"role": "user", "content": render_user_content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "full_story_render",
                "schema": FULL_STORY_RENDER_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(background_story_reasoning_effort()),
    }
    prompt_debug.append(
        log_chat_completion_prompt("full_story/render", render_request_kwargs)
    )
    render_completion = openai_client.chat.completions.create(**render_request_kwargs)
    log_chat_completion_response("full_story/render", render_completion)
    rendered_story = _completion_payload(
        render_completion,
        error_code="FULL_STORY_RENDER_JSON_INVALID",
    )
    combined_payload = _combined_story_payload(story_plan, rendered_story)
    accepted, issues, retry_instruction = _check_full_story_quality(
        story_plan=story_plan,
        story_payload=combined_payload,
        story_direction=story_direction,
        client=openai_client,
        model=model,
        timeout=timeout,
        prompt_debug=prompt_debug,
    )
    if not accepted:
        issue_lines = "\n".join(f"- {issue}" for issue in issues)
        retry_render_content = (
            f"{render_user_content}\n\nRENDER_RETRY: предыдущая проза отклонена. "
            "Сохрани STORY_PLAN без изменений и перепиши все четыре сцены.\n"
            f"Замечания:\n{issue_lines or '- события остались за пределами сцены'}\n"
            f"Указание:\n{retry_instruction or 'Покажи центральное событие каждой части.'}\n"
            "PREVIOUS_RENDER:\n"
            + json.dumps(rendered_story, ensure_ascii=False, indent=2, default=str)
        )
        retry_render_request = {
            **render_request_kwargs,
            "messages": [
                render_request_kwargs["messages"][0],
                {"role": "user", "content": retry_render_content},
            ],
        }
        prompt_debug.append(
            log_chat_completion_prompt("full_story/render_retry", retry_render_request)
        )
        retry_completion = openai_client.chat.completions.create(**retry_render_request)
        log_chat_completion_response("full_story/render_retry", retry_completion)
        rendered_story = _completion_payload(
            retry_completion,
            error_code="FULL_STORY_RENDER_RETRY_JSON_INVALID",
        )
        combined_payload = _combined_story_payload(story_plan, rendered_story)
        retry_accepted, _, _ = _check_full_story_quality(
            story_plan=story_plan,
            story_payload=combined_payload,
            story_direction=story_direction,
            client=openai_client,
            model=model,
            timeout=timeout,
            prompt_debug=prompt_debug,
        )
        if not retry_accepted:
            raise FullStoryGenerationError("FULL_STORY_QUALITY_REJECTED")
    overall_title, arc_plan, parts = _normalize_payload(story_plan, rendered_story)
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
