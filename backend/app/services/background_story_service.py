from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.schemas import LocalChatHistoryItem, LocalPetChatContext, LocalPetMemoryContext
from app.services.image_service import generate_openrouter_image_bytes
from app.services.lite_overlay import (
    LITE_FACT_KINDS,
    LITE_FACT_SPHERES,
    overlay_patch_from_extracted_facts,
)
from app.services.openai_service import (
    chat_reasoning_effort_kwargs,
    get_chat_model,
    get_openai_client,
)
from app.services.pet_reply_engine.context_plan import (
    CONTEXT_ROUTING_SOURCE_IDS,
    ContextPlan,
    ContextRoutingDecision,
    build_context_plan,
    router_sources_for_auto_modes,
)
from app.services.pet_reply_engine.speech_runtime import (
    CONTEXT_SOURCE_KEYS,
    background_story_aftermath_extraction_system_prompt,
    background_story_aftermath_extraction_user_prompt,
    background_story_default_event_type,
    background_story_max_rag_chars,
    background_story_max_story_chars,
    background_story_source_flags,
    background_story_system_prompt,
    background_story_user_prompt,
    context_routing_sources,
    context_routing_system_prompt,
    context_source_enabled,
    context_source_mode,
    state_param_labels,
    state_param_usage_rule,
)
from app.services.prompt_debug import log_chat_completion_prompt, log_chat_completion_response
from app.services.story_library import search_story_library

logger = logging.getLogger(__name__)

MAX_CHARACTER_DOSSIER_CHARS = 12000
MAX_DOSSIER_LIST_ITEMS = 12
MAX_AFTERMATH_CONTEXT_CHARS = 12000
AFTERMATH_CONFIDENCE_THRESHOLD = 0.7
BACKGROUND_ROUTING_SOURCE_IDS = CONTEXT_ROUTING_SOURCE_IDS
BACKGROUND_STORY_IMAGE_STYLE = """
Create a highly detailed Japanese fantasy manga illustration in the style of a cozy
light novel cover or classic JRPG key visual. Clean expressive ink lineart with
subtle line weight variation, soft cel shading, and vibrant but slightly muted colors.
Warm earthy palette featuring cream, ochre, honey, wood brown, olive green, muted teal,
and small red accents. Soft natural daylight with gentle ambient illumination, minimal
harsh shadows, and a welcoming atmosphere.

Characters have classic anime proportions with expressive eyes, friendly faces,
slightly oversized heads, simple readable silhouettes, and charming everyday expressions.
Clothing and equipment feature medieval fantasy designs with handcrafted details.
Materials are illustrated rather than realistic, using flat colors with simple painted
shadows instead of photorealistic textures.

The environment is rich with small storytelling details, including food, furniture,
market goods, tools, decorations, plants, and lively background characters, making every
part of the image interesting to explore. Composition resembles a Japanese fantasy book
cover: one or several large foreground characters surrounded by numerous smaller scenes
and supporting characters that create a bustling living world.

The overall mood is wholesome, cozy, adventurous, optimistic, and slice-of-life,
celebrating everyday life in a magical fantasy setting rather than epic battles.
Highly polished manga illustration, dense visual storytelling, premium print-quality
artwork, clean composition, intricate background details, timeless Japanese fantasy
aesthetic, no photorealism, no 3D rendering, no painterly brush strokes, no cinematic
realism.
""".strip()
BACKGROUND_STORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "maxLength": 120},
        "summary": {"type": "string", "maxLength": 360},
        "storyText": {"type": "string", "maxLength": 2000},
        "eventType": {"type": "string", "maxLength": 60},
        "valence": {
            "type": "string",
            "enum": ["negative", "neutral", "positive", "mixed"],
        },
        "tags": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "maxLength": 40},
        },
        "ragText": {"type": "string", "maxLength": 900},
    },
    "required": [
        "title",
        "summary",
        "storyText",
        "eventType",
        "valence",
        "tags",
        "ragText",
    ],
}
BACKGROUND_STORY_ROUTING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sources": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                source: {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "query": {"type": "string", "maxLength": 500},
                    },
                    "required": ["enabled", "query"],
                }
                for source in BACKGROUND_ROUTING_SOURCE_IDS
            },
            "required": list(BACKGROUND_ROUTING_SOURCE_IDS),
        },
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["sources", "reason"],
}
BACKGROUND_STORY_AFTERMATH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "facts": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sphere": {"type": "string", "enum": list(LITE_FACT_SPHERES)},
                    "kind": {"type": "string", "enum": list(LITE_FACT_KINDS)},
                    "text": {"type": "string", "maxLength": 500},
                    "pathHint": {"type": "string", "maxLength": 120},
                    "source": {"type": "string", "maxLength": 80},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "sphere",
                    "kind",
                    "text",
                    "pathHint",
                    "source",
                    "confidence",
                ],
            },
        },
        "recentEvent": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string", "maxLength": 500},
                "eventType": {"type": "string", "maxLength": 60},
                "participants": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": 80},
                },
                "actions": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": 80},
                },
                "objects": {
                    "type": "array",
                    "maxItems": 6,
                    "items": {"type": "string", "maxLength": 80},
                },
                "location": {"type": "string", "maxLength": 160},
                "outcome": {"type": "string", "maxLength": 260},
            },
            "required": [
                "summary",
                "eventType",
                "participants",
                "actions",
                "objects",
                "location",
                "outcome",
            ],
        },
    },
    "required": ["facts", "recentEvent"],
}


@dataclass(frozen=True)
class BackgroundStoryResult:
    title: str
    summary: str
    story_text: str
    event_type: str
    valence: str
    tags: tuple[str, ...]
    rag_text: str
    story_library_patch: dict[str, Any] | None
    lite_overlay_patch: dict[str, Any] | None
    recent_story_event: dict[str, Any] | None
    prompt_debug: list[dict[str, Any]]

    def model_dump(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "storyText": self.story_text,
            "eventType": self.event_type,
            "valence": self.valence,
            "tags": list(self.tags),
            "ragText": self.rag_text,
            "storyLibraryPatch": self.story_library_patch,
            "liteOverlayPatch": self.lite_overlay_patch,
            "recentStoryEvent": self.recent_story_event,
            "promptDebug": self.prompt_debug,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _text_value(value: Any, *, limit: int = 500) -> str:
    if value is None:
        return ""
    text = _compact_spaces(str(value))
    return text[:limit].rstrip()


def _truncate_text(value: str, limit: int) -> str:
    text = _compact_spaces(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _string_list(value: Any, *, limit: int = MAX_DOSSIER_LIST_ITEMS) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text_value(item, limit=220)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _compact_json(value: Any, *, limit: int = 1400) -> str:
    if value in (None, "", [], {}):
        return ""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return _truncate_text(text, limit)


def _select_record(value: Any, keys: tuple[str, ...], *, limit: int = 800) -> dict[str, Any]:
    if not _is_record(value):
        return {}
    result: dict[str, Any] = {}
    for key in keys:
        item = value.get(key)
        if item in (None, "", [], {}):
            continue
        if isinstance(item, str):
            text = _text_value(item, limit=limit)
            if text:
                result[key] = text
        elif isinstance(item, list):
            values = _string_list(item)
            if values:
                result[key] = values
        elif isinstance(item, dict):
            nested = _clean_context_value(item)
            if nested not in (None, "", [], {}):
                result[key] = nested
        else:
            result[key] = item
    return result


def _background_story_image_identity(pet: LocalPetChatContext) -> str:
    bible = pet.characterBible if _is_record(pet.characterBible) else {}
    identity = _select_record(
        bible.get("identity"),
        ("name", "nickname", "one_liner", "role", "species"),
    )
    visual = _select_record(
        bible.get("visual"),
        ("anchors", "colors", "features", "growth_forms", "materials", "proportions"),
    )
    legacy_visual = {
        key: bible.get(key)
        for key in (
            "species",
            "signature",
            "main_colors",
            "signature_features",
            "materials",
            "proportions",
        )
        if bible.get(key) not in (None, "", [], {})
    }
    name = _text_value(pet.name) or _text_value(identity.get("name")) or "unnamed pet"
    lines = [
        f"Current name: {name}",
        f"User description: {_text_value(pet.description)}",
        f"Current growth stage: {pet.stage}",
        f"Current mood: {pet.mood}",
    ]
    identity_text = _compact_json(identity)
    visual_text = _compact_json(visual or legacy_visual)
    if identity_text:
        lines.append(f"Identity: {identity_text}")
    if visual_text:
        lines.append(f"Visual anchors: {visual_text}")
    return "\n".join(lines)


def _current_asset_image_url(pet: LocalPetChatContext) -> str:
    asset_images = pet.assetImages
    if not isinstance(asset_images, dict):
        return ""
    stage_images = asset_images.get(pet.stage)
    if not isinstance(stage_images, dict):
        return ""
    return _text_value(stage_images.get(pet.mood), limit=1000)


def _asset_input_references_for_background_story(
    pet: LocalPetChatContext,
) -> list[dict[str, Any]]:
    image_url = _current_asset_image_url(pet)
    if not image_url.startswith(("http://", "https://", "data:image/")):
        return []
    return [{"type": "image_url", "image_url": {"url": image_url}}]


def build_background_story_image_prompt(
    *,
    pet: LocalPetChatContext,
    story: BackgroundStoryResult,
) -> str:
    reference_url = _current_asset_image_url(pet)
    reference_text = (
        f"Current sprite reference URL: {reference_url}"
        if reference_url
        else "No current sprite reference URL was provided. Follow the text identity exactly."
    )
    tags = ", ".join(story.tags)
    return f"""
Create one illustration for a generated background story about the pet.
The image is sent to the owner in Telegram with the story text, so it must show
the central moment clearly in one frame.

STORY TITLE:
{story.title}

STORY SUMMARY:
{story.summary}

STORY TEXT:
{story.story_text}

EVENT TYPE:
{story.event_type}

VALENCE:
{story.valence}

TAGS:
{tags or "none"}

CHARACTER APPEARANCE TO PRESERVE:
{_background_story_image_identity(pet)}

CHARACTER REFERENCE:
{reference_text}

SHARED ART STYLE:
{BACKGROUND_STORY_IMAGE_STYLE}

Composition rules:
- One complete illustration, not a storyboard, card, UI, split panel or collage.
- Keep the pet clearly visible as the main character.
- Preserve the pet species, silhouette, colors, face placement, materials and signature features.
- Apply anime human proportions only to humanoid/background characters.
- Do not redesign the pet into a human unless the original pet identity is humanoid.
- Show the story environment and the main action without adding unrelated lore.
- No text, captions, speech bubbles, watermarks, logos or interface elements inside the image.
- No graphic violence, blood, weapons, horror, adult themes or copyrighted characters.
""".strip()


def generate_background_story_image_bytes(
    *,
    pet: LocalPetChatContext,
    story: BackgroundStoryResult,
) -> bytes:
    return generate_openrouter_image_bytes(
        build_background_story_image_prompt(pet=pet, story=story),
        label="background_story/image",
        input_references=_asset_input_references_for_background_story(pet),
    )


def _clean_context_value(value: Any) -> Any:
    if isinstance(value, str):
        return _text_value(value, limit=500)
    if isinstance(value, list):
        cleaned = [_clean_context_value(item) for item in value[:MAX_DOSSIER_LIST_ITEMS]]
        return [item for item in cleaned if item not in (None, "", [], {})]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"source_urls", "provenance", "dialogue_moves"}:
                continue
            cleaned = _clean_context_value(item)
            if cleaned not in (None, "", [], {}):
                result[str(key)] = cleaned
        return result
    return value


def _lite_overlay_facts(extensions: dict[str, Any]) -> list[str]:
    overlay = extensions.get("lite_overlay") if _is_record(extensions.get("lite_overlay")) else {}
    facts = overlay.get("facts") if _is_record(overlay) else []
    if not isinstance(facts, list):
        return []
    result: list[str] = []
    for fact in facts[-MAX_DOSSIER_LIST_ITEMS:]:
        if not _is_record(fact):
            continue
        text = _text_value(fact.get("text"), limit=360)
        if text:
            result.append(text)
    return result


def _global_story_briefs(
    *,
    pet: LocalPetChatContext,
    query: str | None = None,
) -> list[dict[str, str]]:
    query_text = _compact_spaces(
        query or " ".join([pet.name or "", pet.description, pet.stage, pet.mood])
    )
    result = search_story_library(
        query=query_text,
        pool_hints=[],
        limit=5,
        character_bible=pet.characterBible,
        include_global=True,
        include_overlay=False,
        include_patch=False,
        diverse_pools=True,
    )
    bricks = result.get("bricks") if isinstance(result.get("bricks"), list) else []
    briefs: list[dict[str, str]] = []
    for brick in bricks:
        if not _is_record(brick):
            continue
        name = _text_value(brick.get("name"), limit=120)
        text = _text_value(brick.get("text"), limit=360)
        if name or text:
            briefs.append({"name": name, "text": text})
    return briefs


def _memory_brief(memory_context: LocalPetMemoryContext | None) -> dict[str, Any] | None:
    if not memory_context:
        return None
    memories = [
        {
            "kind": item.kind,
            "text": _text_value(item.text, limit=260),
            "dueAt": item.dueAt,
        }
        for item in memory_context.relevantMemories[:5]
        if _text_value(item.text, limit=260)
    ]
    result = {
        "summary": _text_value(memory_context.summary, limit=600),
        "userProfile": _text_value(memory_context.userProfile, limit=600),
        "relevantMemories": memories,
    }
    return {key: value for key, value in result.items() if value not in ("", [], None)}


def _history_brief(history: list[LocalChatHistoryItem] | None) -> list[dict[str, str]]:
    if not history:
        return []
    return [
        {
            "role": item.role,
            "text": _text_value(item.text, limit=500),
        }
        for item in history[-6:]
        if _text_value(item.text, limit=500)
    ]


def _recent_replies_brief(recent_replies: list[str] | None) -> list[str]:
    if not recent_replies:
        return []
    return [text for text in (_text_value(item, limit=500) for item in recent_replies[-6:]) if text]


def _story_event_briefs(recent_story_events: list[dict[str, Any]] | None) -> list[str]:
    if not recent_story_events:
        return []
    briefs: list[str] = []
    for item in recent_story_events[-8:]:
        if not _is_record(item):
            continue
        parts: list[str] = []
        summary = _text_value(item.get("summary"), limit=360)
        if summary:
            parts.append(summary)
        event_type = _text_value(item.get("eventType"), limit=60)
        if event_type:
            parts.append(f"тип: {event_type}")
        participants = _string_list(item.get("participants"), limit=4)
        if participants:
            parts.append(f"участники: {', '.join(participants)}")
        actions = _string_list(item.get("actions"), limit=4)
        if actions:
            parts.append(f"действия: {', '.join(actions)}")
        objects = _string_list(item.get("objects"), limit=4)
        if objects:
            parts.append(f"предметы: {', '.join(objects)}")
        location = _text_value(item.get("location"), limit=120)
        if location:
            parts.append(f"место: {location}")
        outcome = _text_value(item.get("outcome"), limit=180)
        if outcome:
            parts.append(f"исход: {outcome}")
        brief = "; ".join(parts)
        if brief:
            briefs.append(brief)
    return briefs


def _anti_repeat_block(recent_story_events: list[dict[str, Any]] | None) -> str:
    briefs = _story_event_briefs(recent_story_events)
    if not briefs:
        return ""
    lines = "\n".join(f"- {brief}" for brief in briefs)
    return (
        "ANTI_REPEAT: эти события уже происходили. "
        "Используй список только как запрет на повтор, "
        "не как источник новых деталей сюжета. "
        "Не повторяй по сути то же сочетание участник + "
        "действие/случайность + предмет + место "
        f"+ исход.\n{lines}"
    )


def _state_params_brief(pet: LocalPetChatContext) -> dict[str, Any]:
    labels = state_param_labels(
        hunger=pet.stats.hunger,
        happiness=pet.stats.happiness,
        energy=pet.stats.energy,
    )
    return {
        "usageRule": state_param_usage_rule(),
        "голод": labels["hunger"],
        "счастье": labels["happiness"],
        "энергия": labels["energy"],
    }


def _background_context_modes() -> dict[str, str]:
    modes = {
        source: context_source_mode("backgroundStory", source) for source in CONTEXT_SOURCE_KEYS
    }
    # Previous generated pet stories are conversation memory only. Feeding them
    # back into /story makes the story generator repeat its own past outputs.
    modes["storyOverlay"] = "disabled"
    return modes


def _background_context_source_enabled(
    surface: str,
    source: str,
    *,
    router_enabled: bool | None = None,
    auto_default: bool = False,
) -> bool:
    if surface == "backgroundStory" and source == "storyOverlay":
        return False
    return context_source_enabled(
        surface,
        source,
        router_enabled=router_enabled,
        auto_default=auto_default,
    )


def _background_context_plan_from_routing(
    *,
    modes: dict[str, str] | None = None,
    routing: ContextRoutingDecision | None,
) -> ContextPlan:
    return build_context_plan(
        surface="backgroundStory",
        modes=modes or _background_context_modes(),
        routing=routing,
        source_enabled=_background_context_source_enabled,
    )


def _background_routing_payload(
    *,
    pet: LocalPetChatContext,
    memory_context: LocalPetMemoryContext | None,
    history: list[LocalChatHistoryItem] | None,
    recent_replies: list[str] | None,
    now_iso: str | None,
    timezone: str | None,
) -> dict[str, Any]:
    pet_payload: dict[str, Any] = {
        "name": pet.name,
        "stage": pet.stage,
    }
    if context_source_enabled("backgroundStory", "stateParams", auto_default=True):
        pet_payload["params"] = _state_params_brief(pet)
    return {
        "surface": "backgroundStory",
        "task": "generate_background_story",
        "eventType": background_story_default_event_type(),
        "now": now_iso or _now_iso(),
        "timezone": timezone,
        "pet": pet_payload,
        "sources": context_routing_sources(),
        "memoryBrief": _memory_brief(memory_context) or {},
        "recentChatHistory": _history_brief(history),
        "recentReplies": _recent_replies_brief(recent_replies),
    }


def _parse_background_routing_payload(value: str) -> ContextRoutingDecision:
    parsed = _json_record_from_text(value)
    sources = parsed.get("sources") if _is_record(parsed.get("sources")) else {}
    enabled: set[str] = set()
    queries: dict[str, str] = {}
    for source in BACKGROUND_ROUTING_SOURCE_IDS:
        item = sources.get(source)
        source_enabled = False
        query = ""
        if isinstance(item, bool):
            source_enabled = item
        elif _is_record(item):
            source_enabled = bool(item.get("enabled"))
            query = _text_value(item.get("query"), limit=500)
        if source_enabled:
            enabled.add(source)
        if query:
            queries[source] = query
    reason = parsed.get("reason")
    return ContextRoutingDecision(
        surface="backgroundStory",
        enabled_sources=frozenset(enabled),
        queries=queries,
        reason=_text_value(reason) if isinstance(reason, str) else "",
        raw=parsed or {"parseError": True, "raw": value[:1000]},
    )


def _plan_background_story_context(
    *,
    pet: LocalPetChatContext,
    memory_context: LocalPetMemoryContext | None,
    history: list[LocalChatHistoryItem] | None,
    recent_replies: list[str] | None,
    now_iso: str | None,
    timezone: str | None,
    client: Any,
    model: str,
    timeout: float,
) -> tuple[ContextPlan, dict[str, Any] | None]:
    modes = _background_context_modes()
    if not router_sources_for_auto_modes(modes):
        return (
            _background_context_plan_from_routing(
                modes=modes,
                routing=ContextRoutingDecision(
                    surface="backgroundStory",
                    reason="no_auto_context_sources",
                    raw={"skipped": True, "sourceModes": modes},
                ),
            ),
            None,
        )
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": context_routing_system_prompt()},
            {
                "role": "user",
                "content": json.dumps(
                    _background_routing_payload(
                        pet=pet,
                        memory_context=memory_context,
                        history=history,
                        recent_replies=recent_replies,
                        now_iso=now_iso,
                        timezone=timezone,
                    ),
                    ensure_ascii=False,
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "background_story_context_routing",
                "schema": BACKGROUND_STORY_ROUTING_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs("none"),
    }
    prompt_debug = log_chat_completion_prompt("background_story/context_routing", request_kwargs)
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("background_story/context_routing", completion)
    return (
        _background_context_plan_from_routing(
            modes=modes,
            routing=_parse_background_routing_payload(
                completion.choices[0].message.content or "{}"
            ),
        ),
        prompt_debug,
    )


def character_dossier_for_background_story(
    *,
    pet: LocalPetChatContext,
    memory_context: LocalPetMemoryContext | None = None,
    history: list[LocalChatHistoryItem] | None = None,
    recent_replies: list[str] | None = None,
    now_iso: str | None = None,
    timezone: str | None = None,
    context_plan: ContextPlan | None = None,
    source_flags: dict[str, bool] | None = None,
    include_story_library: bool | None = None,
    story_library_query: str | None = None,
) -> str:
    bible = pet.characterBible if _is_record(pet.characterBible) else {}
    extensions = bible.get("extensions") if _is_record(bible.get("extensions")) else {}
    lore = bible.get("lore") if _is_record(bible.get("lore")) else {}
    if context_plan is not None:
        sources = {source: context_plan.includes(source) for source in CONTEXT_SOURCE_KEYS}
        if include_story_library is None:
            include_story_library = context_plan.includes("storyLibrary")
        if story_library_query is None:
            story_library_query = context_plan.query("worldContext")
    else:
        sources = source_flags if source_flags is not None else background_story_source_flags()

    def enabled(source: str) -> bool:
        return sources.get(source, True)

    current_state: dict[str, Any] = {
        "name": pet.name,
        "stage": pet.stage,
    }
    if enabled("stateParams"):
        current_state["params"] = _state_params_brief(pet)
    dossier: dict[str, Any] = {
        "now": now_iso or _now_iso(),
        "timezone": timezone,
        "currentState": current_state,
    }

    if enabled("characterProfile"):
        dossier.update(
            {
                "description": pet.description,
                "identity": _select_record(
                    bible.get("identity"),
                    ("name", "nickname", "one_liner", "role", "species"),
                ),
                "signature": _text_value(bible.get("signature"), limit=300),
                "species": _text_value(bible.get("species"), limit=200),
                "visual": _select_record(
                    bible.get("visual"),
                    (
                        "anchors",
                        "colors",
                        "features",
                        "growth_forms",
                        "materials",
                        "proportions",
                    ),
                ),
                "innerState": _select_record(
                    bible.get("inner_state"),
                    (
                        "core_want",
                        "inner_conflict",
                        "fears",
                        "comfort_actions",
                        "drives",
                    ),
                ),
                "lore": _select_record(
                    lore,
                    (
                        "origin",
                        "home",
                        "world",
                        "relationships",
                        "inner_life",
                        "story_seeds",
                        "growth_arc",
                    ),
                ),
                "world": _select_record(
                    bible.get("world"),
                    (
                        "habitat",
                        "home",
                        "objects",
                        "relationships",
                        "routines",
                        "story_seeds",
                    ),
                ),
            }
        )
    if enabled("liteOverlay"):
        dossier["liteFacts"] = _lite_overlay_facts(extensions)
    if include_story_library is None:
        include_story_library = context_source_enabled(
            "backgroundStory",
            "storyLibrary",
            auto_default=False,
        )
    if include_story_library:
        dossier["globalStoryBricks"] = _global_story_briefs(
            pet=pet,
            query=story_library_query,
        )
    memory = _memory_brief(memory_context) if enabled("userMemory") else None
    if memory:
        dossier["userMemory"] = memory
    recent_history = _history_brief(history) if enabled("chatHistory") else []
    if recent_history:
        dossier["recentChatHistory"] = recent_history
    recent_reply_brief = _recent_replies_brief(recent_replies) if enabled("recentReplies") else []
    if recent_reply_brief:
        dossier["recentReplies"] = recent_reply_brief

    compact = {key: value for key, value in dossier.items() if value not in (None, "", [], {})}
    return _truncate_text(
        json.dumps(compact, ensure_ascii=False, indent=2, default=str),
        MAX_CHARACTER_DOSSIER_CHARS,
    )


def _json_record_from_text(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(value[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _clean_tags(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text_value(item, limit=40)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= 8:
            break
    return tuple(result)


def _normalize_story_payload(payload: dict[str, Any]) -> BackgroundStoryResult:
    max_story_chars = max(200, background_story_max_story_chars())
    max_rag_chars = max(120, background_story_max_rag_chars())
    fallback_event_type = background_story_default_event_type()

    title = _text_value(payload.get("title"), limit=120) or "Фоновое событие"
    summary = _text_value(payload.get("summary"), limit=360)
    story_text = _truncate_text(_text_value(payload.get("storyText"), limit=2200), max_story_chars)
    event_type = _text_value(payload.get("eventType"), limit=60) or fallback_event_type
    valence = _text_value(payload.get("valence"), limit=20) or "mixed"
    if valence not in {"negative", "neutral", "positive", "mixed"}:
        valence = "mixed"
    tags = _clean_tags(payload.get("tags"))
    rag_text = _truncate_text(_text_value(payload.get("ragText"), limit=1000), max_rag_chars)

    if not summary:
        summary = _truncate_text(story_text, 260) if story_text else title
    if not story_text:
        story_text = summary
    if not rag_text:
        rag_text = summary

    return BackgroundStoryResult(
        title=title,
        summary=summary,
        story_text=story_text,
        event_type=event_type,
        valence=valence,
        tags=tags,
        rag_text=rag_text,
        story_library_patch=None,
        lite_overlay_patch=None,
        recent_story_event=None,
        prompt_debug=[],
    )


def _clamp_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _aftermath_character_context(pet: LocalPetChatContext) -> str:
    bible = pet.characterBible if _is_record(pet.characterBible) else {}
    extensions = bible.get("extensions") if _is_record(bible.get("extensions")) else {}
    lore = bible.get("lore") if _is_record(bible.get("lore")) else {}
    payload = {
        "name": pet.name,
        "description": pet.description,
        "stage": pet.stage,
        "currentState": {
            "name": pet.name,
            "stage": pet.stage,
            "params": _state_params_brief(pet),
        },
        "identity": _select_record(
            bible.get("identity"),
            ("name", "nickname", "one_liner", "role", "species"),
        ),
        "signature": _text_value(bible.get("signature"), limit=300),
        "species": _text_value(bible.get("species"), limit=200),
        "visual": _select_record(
            bible.get("visual"),
            ("anchors", "colors", "features", "growth_forms", "materials", "proportions"),
        ),
        "innerState": _select_record(
            bible.get("inner_state"),
            ("core_want", "inner_conflict", "fears", "comfort_actions", "drives"),
        ),
        "lore": _select_record(
            lore,
            ("origin", "home", "world", "relationships", "inner_life", "growth_arc"),
        ),
        "world": _select_record(
            bible.get("world"),
            ("habitat", "home", "objects", "relationships", "routines"),
        ),
        "liteOverlay": _clean_context_value(extensions.get("lite_overlay"))
        if _is_record(extensions.get("lite_overlay"))
        else {},
    }
    compact = {key: value for key, value in payload.items() if value not in (None, "", [], {})}
    return _truncate_text(
        json.dumps(compact, ensure_ascii=False, indent=2, default=str),
        MAX_AFTERMATH_CONTEXT_CHARS,
    )


def _aftermath_story_payload(result: BackgroundStoryResult) -> str:
    return _truncate_text(
        json.dumps(
            {
                "title": result.title,
                "summary": result.summary,
                "storyText": result.story_text,
                "eventType": result.event_type,
                "valence": result.valence,
                "tags": list(result.tags),
                "ragText": result.rag_text,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        MAX_AFTERMATH_CONTEXT_CHARS,
    )


def _normalize_recent_story_event(
    value: Any,
    *,
    story: BackgroundStoryResult,
) -> dict[str, Any]:
    item = value if _is_record(value) else {}
    event = {
        "title": story.title,
        "summary": _text_value(item.get("summary"), limit=500) or story.summary,
        "eventType": _text_value(item.get("eventType"), limit=60) or story.event_type,
        "participants": _string_list(item.get("participants"), limit=6),
        "actions": _string_list(item.get("actions"), limit=6),
        "objects": _string_list(item.get("objects"), limit=6),
        "location": _text_value(item.get("location"), limit=160),
        "outcome": _text_value(item.get("outcome"), limit=260),
        "tags": list(story.tags),
        "createdAt": _now_iso(),
        "source": "background_story",
    }
    if not event["summary"]:
        event["summary"] = _truncate_text(story.story_text, 500)
    return {
        key: item_value for key, item_value in event.items() if item_value not in (None, "", [], {})
    }


def _parse_aftermath_extraction_payload(
    raw_content: str,
    *,
    story: BackgroundStoryResult,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    parsed = _json_record_from_text(raw_content)
    raw_facts = parsed.get("facts")

    facts: list[dict[str, Any]] = []
    if isinstance(raw_facts, list):
        for raw_fact in raw_facts:
            if not _is_record(raw_fact):
                continue
            if _clamp_float(raw_fact.get("confidence"), 0.0) < AFTERMATH_CONFIDENCE_THRESHOLD:
                continue
            fact = dict(raw_fact)
            fact["source"] = "background_story_aftermath"
            facts.append(fact)
    patch = overlay_patch_from_extracted_facts(
        facts,
        default_source="background_story_aftermath",
    )
    recent_event = _normalize_recent_story_event(parsed.get("recentEvent"), story=story)
    return patch, recent_event


def _extract_background_story_aftermath_patch(
    *,
    pet: LocalPetChatContext,
    story: BackgroundStoryResult,
    client: Any,
    model: str,
    timeout: float,
    prompt_debug: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": background_story_aftermath_extraction_system_prompt(),
            },
            {
                "role": "user",
                "content": background_story_aftermath_extraction_user_prompt(
                    {
                        "character_context": _aftermath_character_context(pet),
                        "story_payload": _aftermath_story_payload(story),
                    }
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "background_story_aftermath_extraction",
                "schema": BACKGROUND_STORY_AFTERMATH_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(get_settings().openai_chat_reasoning_effort),
    }
    prompt_debug.append(
        log_chat_completion_prompt("background_story/aftermath_extraction", request_kwargs)
    )
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("background_story/aftermath_extraction", completion)
    return _parse_aftermath_extraction_payload(
        completion.choices[0].message.content or "{}",
        story=story,
    )


def generate_background_story(
    *,
    pet: LocalPetChatContext,
    memory_context: LocalPetMemoryContext | None = None,
    history: list[LocalChatHistoryItem] | None = None,
    recent_replies: list[str] | None = None,
    recent_story_events: list[dict[str, Any]] | None = None,
    now_iso: str | None = None,
    timezone: str | None = None,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> BackgroundStoryResult:
    settings = get_settings()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    context_plan, routing_debug = _plan_background_story_context(
        pet=pet,
        memory_context=memory_context,
        history=history,
        recent_replies=recent_replies,
        now_iso=now_iso,
        timezone=timezone,
        client=openai_client,
        model=model,
        timeout=timeout,
    )
    character = character_dossier_for_background_story(
        pet=pet,
        memory_context=memory_context,
        history=history,
        recent_replies=recent_replies,
        now_iso=now_iso,
        timezone=timezone,
        context_plan=context_plan,
    )
    user_content = background_story_user_prompt(
        {
            "character": character,
            "event_type": background_story_default_event_type(),
        }
    )
    anti_repeat = _anti_repeat_block(recent_story_events)
    if anti_repeat:
        user_content = f"{user_content}\n\n{anti_repeat}"
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": background_story_system_prompt()},
            {"role": "user", "content": user_content},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "background_story",
                "schema": BACKGROUND_STORY_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    prompt_debug = [item for item in (routing_debug,) if item is not None]
    prompt_debug.append(log_chat_completion_prompt("background_story/generate", request_kwargs))
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("background_story/generate", completion)
    content = completion.choices[0].message.content or "{}"
    result = _normalize_story_payload(_json_record_from_text(content))
    lite_overlay_patch: dict[str, Any] | None = None
    recent_story_event: dict[str, Any] | None = None
    try:
        lite_overlay_patch, recent_story_event = _extract_background_story_aftermath_patch(
            pet=pet,
            story=result,
            client=openai_client,
            model=model,
            timeout=timeout,
            prompt_debug=prompt_debug,
        )
    except Exception:
        logger.exception("background_story_after_extraction failed")
        recent_story_event = _normalize_recent_story_event(None, story=result)
    return BackgroundStoryResult(
        title=result.title,
        summary=result.summary,
        story_text=result.story_text,
        event_type=result.event_type,
        valence=result.valence,
        tags=result.tags,
        rag_text=result.rag_text,
        story_library_patch=result.story_library_patch,
        lite_overlay_patch=lite_overlay_patch,
        recent_story_event=recent_story_event,
        prompt_debug=prompt_debug,
    )
