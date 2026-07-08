from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from app.config import get_settings
from app.schemas import (
    LiteFactExtractionRequest,
    LocalAmbientRequest,
    LocalChatDebug,
    LocalChatRequest,
    LocalChatResponse,
    LocalPetMemoryContext,
    LocalProactiveRequest,
    LocalProactiveResponse,
    LocalPushRequest,
    MemoryConsolidationRequest,
    MemoryConsolidationResponse,
    MemoryExtractionRequest,
    MemoryExtractionResponse,
)
from app.services.context_assembler import (
    MAX_CONTEXT_BRICKS,
    AssembledPetContext,
    assemble_pet_context,
)
from app.services.openai_service import (
    chat_reasoning_effort_kwargs,
    get_chat_model,
    get_openai_client,
)
from app.services.pet_reply_engine.age_message_examples import (
    categories_for_reply,
    phrases_for_categories,
)
from app.services.pet_reply_engine.models import (
    PetPersonality,
    PetRecentMessage,
    PetReplyInput,
    PetReplyPet,
    PetStats,
    PetVisualIdentity,
)
from app.services.pet_reply_engine.reply_limits import MAX_REPLY_CHARS, clamp_reply_text
from app.services.pet_reply_engine.speech_runtime import (
    age_role_hint,
    baby_examples_intro,
    character_fact_extraction_system_prompt,
    context_routing_sources,
    context_routing_system_prompt,
    dialogue_state_modifier,
    identity_prompt,
    memory_usage_rule,
    speech_template,
    state_layer_surface_flags,
    story_library_extraction_system_prompt,
    surface_prompt,
    user_memory_consolidation_system_prompt,
    user_memory_extraction_system_prompt,
    world_seed_system_prompt,
)
from app.services.prompt_debug import (
    log_ambient_reply_diagnostic,
    log_chat_completion_prompt,
    log_chat_completion_response,
)
from app.services.story_library import story_library_patch_for_new_brick

MAX_LITE_TOOL_ROUNDS = 3
MAX_LITE_BABY_EXAMPLES = 8
MAX_LITE_EXTRACTION_CONTEXT_CHARS = 12000

LITE_FACT_SPHERES = ("character", "appearance", "world", "relationship")
LITE_FACT_KINDS = (
    "character_fact",
    "appearance_fact",
    "world_fact",
    "relationship_fact",
)
USER_MEMORY_KINDS = (
    "user_fact",
    "preference",
    "event",
    "deadline",
    "relationship",
    "routine",
    "goal",
    "promise",
    "emotion",
    "boundary",
)
FACE_HINTS = ("happy", "excited", "curious", "content", "grumpy", "sleepy")
MAX_MEMORY_CONTEXT_ITEMS = 5
MAX_RECENT_AMBIENT_REPLIES = 5
AMBIENT_MEMORY_KINDS = {"preference", "relationship", "routine", "boundary"}

PhraseSurface = Literal["chat", "proactive", "ambient", "push"]


@dataclass(frozen=True)
class PhrasePlan:
    surface: PhraseSurface
    reply_limit: int
    identity_line: str
    persona_contract: str
    character_block: str | None = None
    memory_block: str | None = None
    voice_block: str | None = None
    world_block: str | None = None
    recent_ambient_block: str | None = None
    dialogue_block: str | None = None
    extra_rules: tuple[str, ...] = field(default_factory=tuple)

    def system_content(self) -> str:
        sections = [
            self.identity_line,
            self.character_block,
            self.voice_block,
            self.world_block,
            self.memory_block,
            self.recent_ambient_block,
            self.dialogue_block,
            "\n".join(self.extra_rules),
            self.persona_contract,
        ]
        return "\n\n".join(section for section in sections if section)


CONTEXT_SOURCE_IDS = ("worldContext", "characterProfile", "userMemory", "recentReplies")


@dataclass(frozen=True)
class ContextRoutingDecision:
    surface: PhraseSurface
    enabled_sources: frozenset[str] = frozenset()
    queries: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    raw: dict[str, Any] | None = None

    def enabled(self, source: str, *, default: bool = False) -> bool:
        if source not in CONTEXT_SOURCE_IDS:
            return default
        return source in self.enabled_sources

    def query(self, source: str) -> str:
        return self.queries.get(source, "")


STORY_LIBRARY_QUERY_PATTERN = re.compile(
    r"("
    r"мир|мире|существ|монстр|опас|угроз|чудовищ|твар|звер|дух|"
    r"предмет|вещ|наход|артефакт|мест|локац|лес|грот|пещер|сосед|персонаж"
    r")",
    re.IGNORECASE,
)

STORY_LIBRARY_REPLY_ENTITY_PATTERN = re.compile(
    r"("
    r"зовут|называ[ею]|встрет|наш[её]л|водится|обита|пряч|охраня|"
    r"существ|монстр|страж|чудовищ|твар|звер|дух|артефакт|грот|пещер|сосед"
    r")",
    re.IGNORECASE,
)

TECHNICAL_WORLD_TEXT_PATTERN = re.compile(
    r"("
    r"source_descriptions|Home/habitat details must be inferred|"
    r"World facts come from|No extra origin is invented|"
    r"Use source_descriptions|template_do_not_copy|source_text_do_not_copy|"
    r"безопасная среда для формы|No relationship lore is added"
    r")",
    re.IGNORECASE,
)

PET_STATE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "update_pet_name",
            "description": (
                "Update the current pet display name only when the current user clearly "
                "asks to rename this pet or assigns a new official name to you. Accept "
                "any wording and language, but do not call for questions about the "
                "current name, casual nicknames, insults, jokes, or ambiguous phrasing."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 32,
                        "description": (
                            "The new display name exactly as the user wants it shown, "
                            "without surrounding quotes or extra instruction words."
                        ),
                    }
                },
                "required": ["name"],
            },
        },
    }
]

LITE_CHARACTER_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_character_json",
            "description": (
                "Read character JSON only when the current user explicitly asks about lore, "
                "world, body, mechanics, food, home, origin, friends, fears, habits, "
                "preferences, or stable character facts. Do not use for ordinary small talk."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "characterBible",
                                "liteOverlay",
                            ],
                        },
                    }
                },
                "required": ["sections"],
            },
        },
    },
]

LITE_FACT_EXTRACTION_SCHEMA: dict[str, Any] = {
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
                    "sphere": {
                        "type": "string",
                        "enum": list(LITE_FACT_SPHERES),
                    },
                    "kind": {
                        "type": "string",
                        "enum": list(LITE_FACT_KINDS),
                    },
                    "text": {
                        "type": "string",
                        "maxLength": 500,
                    },
                    "pathHint": {
                        "type": "string",
                        "maxLength": 120,
                    },
                    "source": {
                        "type": "string",
                        "maxLength": 80,
                    },
                },
                "required": ["sphere", "kind", "text", "pathHint", "source"],
            },
        }
    },
    "required": ["facts"],
}

STORY_LIBRARY_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "bricks": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "pool": {
                        "type": "string",
                        "enum": [
                            "items",
                            "locations",
                            "neighbors",
                            "creatures",
                            "threats",
                            "events",
                        ],
                    },
                    "name": {"type": "string", "maxLength": 120},
                    "description": {"type": "string", "maxLength": 500},
                    "basedOnBrickIds": {
                        "type": "array",
                        "maxItems": 6,
                        "items": {"type": "string", "maxLength": 120},
                    },
                    "reason": {"type": "string", "maxLength": 240},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "pool",
                    "name",
                    "description",
                    "basedOnBrickIds",
                    "reason",
                    "confidence",
                ],
            },
        }
    },
    "required": ["bricks"],
}

LITE_WORLD_SEED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "worldText": {
            "type": "string",
            "maxLength": 500,
        },
    },
    "required": ["worldText"],
}

CONTEXT_ROUTING_SCHEMA: dict[str, Any] = {
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
                for source in CONTEXT_SOURCE_IDS
            },
            "required": list(CONTEXT_SOURCE_IDS),
        },
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["sources", "reason"],
}

MEMORY_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "operations": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["capture_learning", "remember_user_fact"],
                    },
                    "observation": {"type": ["string", "null"], "maxLength": 500},
                    "patternKey": {"type": ["string", "null"], "maxLength": 120},
                    "kind": {"type": ["string", "null"], "enum": [*USER_MEMORY_KINDS, None]},
                    "text": {"type": ["string", "null"], "maxLength": 500},
                    "normalizedKey": {"type": ["string", "null"], "maxLength": 160},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                    "dueAt": {"type": ["string", "null"], "maxLength": 80},
                    "expiresAt": {"type": ["string", "null"], "maxLength": 80},
                    "tags": {
                        "type": "array",
                        "maxItems": 6,
                        "items": {"type": "string", "maxLength": 40},
                    },
                },
                "required": [
                    "type",
                    "observation",
                    "patternKey",
                    "kind",
                    "text",
                    "normalizedKey",
                    "confidence",
                    "importance",
                    "dueAt",
                    "expiresAt",
                    "tags",
                ],
            },
        }
    },
    "required": ["operations"],
}

MEMORY_CONSOLIDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "operations": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "promote_learning",
                            "prune_learning",
                            "rewrite_summary",
                            "rewrite_user_profile",
                        ],
                    },
                    "learningId": {"type": ["string", "null"], "maxLength": 120},
                    "reason": {"type": ["string", "null"], "maxLength": 240},
                    "content": {"type": ["string", "null"], "maxLength": 1000},
                    "memory": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "properties": {
                            "kind": {"type": "string", "enum": list(USER_MEMORY_KINDS)},
                            "text": {"type": "string", "maxLength": 500},
                            "normalizedKey": {"type": "string", "maxLength": 160},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "importance": {"type": "number", "minimum": 0, "maximum": 1},
                            "dueAt": {"type": ["string", "null"], "maxLength": 80},
                            "expiresAt": {"type": ["string", "null"], "maxLength": 80},
                            "tags": {
                                "type": "array",
                                "maxItems": 6,
                                "items": {"type": "string", "maxLength": 40},
                            },
                        },
                        "required": [
                            "kind",
                            "text",
                            "normalizedKey",
                            "confidence",
                            "importance",
                            "dueAt",
                            "expiresAt",
                            "tags",
                        ],
                    },
                },
                "required": ["type", "learningId", "reason", "content", "memory"],
            },
        }
    },
    "required": ["operations"],
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}…"


def _clean_optional_text(value: str | None, limit: int) -> str | None:
    text = _compact_spaces(value or "")
    return _truncate_text(text, limit) if text else None


def _memory_context_block(memory_context: LocalPetMemoryContext | None) -> str | None:
    if not memory_context:
        return None

    lines: list[str] = []
    user_profile = _clean_optional_text(memory_context.userProfile, 500)
    summary = _clean_optional_text(memory_context.summary, 500)
    if user_profile:
        lines.append(speech_template("memoryProfileLine", {"user_profile": user_profile}))
    if summary:
        lines.append(speech_template("memorySummaryLine", {"summary": summary}))

    memory_lines = []
    for memory in memory_context.relevantMemories[:MAX_MEMORY_CONTEXT_ITEMS]:
        text = _clean_optional_text(memory.text, 240)
        if text:
            memory_lines.append(f"- {text}")
    if memory_lines:
        lines.append(speech_template("memoryItemsHeader"))
        lines.extend(memory_lines)

    if not lines:
        return None
    return "\n".join(lines) + f"\n{memory_usage_rule()}"


def _ambient_memory_context_block(memory_context: LocalPetMemoryContext | None) -> str | None:
    if not memory_context:
        return None

    soft_memories = [
        memory
        for memory in memory_context.relevantMemories
        if memory.kind in AMBIENT_MEMORY_KINDS and not memory.dueAt
    ]
    soft_context = memory_context.model_copy(
        update={
            "summary": None,
            "relevantMemories": soft_memories,
            "proactiveCandidate": None,
        }
    )
    return _memory_context_block(soft_context)


def _recent_ambient_replies_text(replies: list[str]) -> str:
    lines: list[str] = []
    for reply in replies[-MAX_RECENT_AMBIENT_REPLIES:]:
        text = _clean_optional_text(reply, 180)
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)


def _ambient_context_prompt() -> str:
    return surface_prompt("ambient", {"recent_replies": ""})


def _json_record_from_text(value: str) -> dict[str, Any]:
    text = (value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if _is_record(parsed) else {}


def _parse_context_routing_decision(
    *,
    surface: PhraseSurface,
    raw_content: str,
) -> ContextRoutingDecision:
    parsed = _json_record_from_text(raw_content)
    raw_sources = parsed.get("sources") if _is_record(parsed.get("sources")) else {}
    enabled: set[str] = set()
    queries: dict[str, str] = {}
    for source in CONTEXT_SOURCE_IDS:
        value = raw_sources.get(source)
        source_enabled = False
        query = ""
        if isinstance(value, bool):
            source_enabled = value
        elif _is_record(value):
            source_enabled = bool(value.get("enabled"))
            query_value = value.get("query")
            if isinstance(query_value, str):
                query = _compact_spaces(query_value)[:500].rstrip()
        if source_enabled:
            enabled.add(source)
        if query:
            queries[source] = query
    reason = parsed.get("reason")
    return ContextRoutingDecision(
        surface=surface,
        enabled_sources=frozenset(enabled),
        queries=queries,
        reason=_compact_spaces(reason)[:500].rstrip() if isinstance(reason, str) else "",
        raw=parsed or {"parseError": True, "raw": raw_content[:1000]},
    )


def _context_routing_user_payload(
    *,
    surface: PhraseSurface,
    payload: Any,
    surface_prompt_text: str,
) -> dict[str, Any]:
    memory_context = getattr(payload, "memoryContext", None)
    proactive_reason = _reason_from_payload(payload)
    return {
        "surface": surface,
        "surfacePrompt": surface_prompt_text,
        "userMessage": getattr(payload, "message", ""),
        "proactiveReason": proactive_reason,
        "pet": _lite_pet_context_payload(payload),
        "sources": context_routing_sources(),
        "memoryBrief": {
            "summary": getattr(memory_context, "summary", None) if memory_context else None,
            "userProfile": getattr(memory_context, "userProfile", None) if memory_context else None,
            "relevantMemories": [
                {
                    "kind": item.kind,
                    "text": item.text,
                    "dueAt": item.dueAt,
                }
                for item in (memory_context.relevantMemories if memory_context else [])
            ],
        },
        "recentReplies": getattr(payload, "recentAmbientReplies", []),
    }


def _reason_from_payload(payload: Any) -> str:
    raw_reason = getattr(payload, "reason", None)
    if isinstance(raw_reason, str) and raw_reason.strip():
        return _clean_optional_text(raw_reason, 240) or ""
    memory_context = getattr(payload, "memoryContext", None)
    if memory_context and getattr(memory_context, "proactiveCandidate", None):
        return _clean_optional_text(memory_context.proactiveCandidate.reason, 240) or ""
    return ""


def _surface_prompt_for_payload(surface: PhraseSurface, payload: Any) -> str:
    if surface == "ambient":
        return _ambient_context_prompt()
    if surface in ("proactive", "push"):
        return surface_prompt(surface, {"reason": _reason_from_payload(payload)})
    return surface_prompt(surface)


def _route_contexts_for_visible_reply(
    *,
    surface: PhraseSurface,
    payload: Any,
    client: Any,
    model: str,
    timeout: float,
) -> tuple[ContextRoutingDecision, dict[str, Any]]:
    surface_prompt_text = _surface_prompt_for_payload(surface, payload)
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": context_routing_system_prompt()},
            {
                "role": "user",
                "content": _safe_json_context(
                    _context_routing_user_payload(
                        surface=surface,
                        payload=payload,
                        surface_prompt_text=surface_prompt_text,
                    ),
                    6000,
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "context_routing",
                "schema": CONTEXT_ROUTING_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs("none"),
    }
    prompt_debug = log_chat_completion_prompt("pet_reply/context_routing", request_kwargs)
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("pet_reply/context_routing", completion)
    content = completion.choices[0].message.content or "{}"
    return _parse_context_routing_decision(surface=surface, raw_content=content), prompt_debug


def _routing_enabled(
    routing: ContextRoutingDecision | None,
    source: str,
    *,
    default: bool,
) -> bool:
    if routing is None:
        return default
    return routing.enabled(source)


def _character_context_block(
    pet: Any,
    routing: ContextRoutingDecision | None,
) -> str | None:
    if not _routing_enabled(routing, "characterProfile", default=False):
        return None
    bible = pet.characterBible if _is_record(pet.characterBible) else {}
    if not bible:
        return None
    extensions = bible.get("extensions") if _is_record(bible.get("extensions")) else {}
    lite_overlay = extensions.get("lite_overlay") if _is_record(extensions) else {}
    payload = {
        "characterBible": bible,
        "liteOverlay": lite_overlay if _is_record(lite_overlay) else {},
    }
    return "CHARACTER_PROFILE:\n" + _safe_json_context(payload, 3000)


def _story_context_for_routing(
    *,
    surface: PhraseSurface,
    payload: Any,
    context_routing: ContextRoutingDecision,
) -> AssembledPetContext:
    context_mode = "proactive" if surface == "push" else surface
    if surface == "chat":
        user_message = payload.message
        history = payload.history
    elif surface == "ambient":
        user_message = _ambient_context_prompt()
        history = []
    else:
        user_message = _reason_from_payload(payload) or surface_prompt(surface, {"reason": ""})
        history = []
    return assemble_pet_context(
        mode=context_mode,
        pet=payload.pet,
        user_message=user_message,
        history=history,
        memory_context=payload.memoryContext,
        force_context=context_routing.enabled("worldContext"),
        forced_query=context_routing.query("worldContext"),
        forced_reason="context_routing",
        routing_applied=True,
    )


def _extract_hidden_reaction(raw_reply: str) -> tuple[str, str | None, str | None]:
    visible_lines: list[str] = []
    inner_thought: str | None = None
    face_hint: str | None = None

    for line in (raw_reply or "").splitlines():
        stripped = line.strip()
        thought_match = re.match(r"^THOUGHT\s*:\s*(.+)$", stripped, flags=re.IGNORECASE)
        if thought_match:
            inner_thought = _truncate_text(_compact_spaces(thought_match.group(1)), 80)
            continue

        face_match = re.match(r"^FACE\s*:\s*(.+)$", stripped, flags=re.IGNORECASE)
        if face_match:
            raw_face = _compact_spaces(face_match.group(1)).casefold()
            if raw_face in FACE_HINTS:
                face_hint = raw_face
            continue

        visible_lines.append(line)

    reply = clamp_reply_text("\n".join(visible_lines).strip())
    return reply, inner_thought, face_hint


def _short_pet_description(pet: Any) -> str:
    description = _compact_spaces(pet.description)
    name = _compact_spaces(pet.name or "")
    return f"{name}, {description}" if name else description


def _short_character_description(payload: LocalChatRequest) -> str:
    return _short_pet_description(payload.pet)


def _state_role_modifier_for_pet(pet: Any, surface: PhraseSurface) -> str | None:
    stats = pet.stats
    flags = state_layer_surface_flags(surface)
    return dialogue_state_modifier(
        mood=pet.mood,
        hunger=stats.hunger,
        energy=stats.energy,
        include_mood=flags.get("mood", False),
        include_hunger=flags.get("hunger", False),
        include_energy=flags.get("energy", False),
    )


def _state_role_modifier(payload: LocalChatRequest, surface: PhraseSurface = "chat") -> str | None:
    return _state_role_modifier_for_pet(payload.pet, surface)


def _age_role_hint_for_pet(pet: Any) -> str:
    return age_role_hint(pet.stage)


def _age_role_hint(payload: LocalChatRequest) -> str:
    return _age_role_hint_for_pet(payload.pet)


def _lite_reply_input_for_examples(payload: LocalChatRequest) -> PetReplyInput:
    bible = payload.pet.characterBible if _is_record(payload.pet.characterBible) else {}
    lore = bible.get("lore") if _is_record(bible.get("lore")) else None
    return PetReplyInput(
        user_action="chat_message",
        user_text=payload.message,
        recent_messages=tuple(
            PetRecentMessage(role=item.role, text=item.text) for item in payload.history[-8:]
        ),
        pet=PetReplyPet(
            age_stage=payload.pet.stage,
            mood=payload.pet.mood,
            stats=PetStats(
                hunger=payload.pet.stats.hunger,
                happiness=payload.pet.stats.happiness,
                energy=payload.pet.stats.energy,
                cleanliness=payload.pet.stats.cleanliness,
            ),
            visual_identity=PetVisualIdentity(
                raw_description=payload.pet.description,
                species=payload.pet.description,
            ),
            personality=PetPersonality(),
            lore=lore,
            name=payload.pet.name,
            character_profile_v2=bible if bible else None,
            effective_character_bible=bible if bible else None,
        ),
    )


def _baby_phrase_examples_for_prompt(payload: LocalChatRequest) -> str | None:
    if payload.pet.stage != "baby":
        return None

    reply_input = _lite_reply_input_for_examples(payload)
    categories = categories_for_reply(reply_input)
    examples = phrases_for_categories(
        reply_input,
        categories,
        per_category=2,
        max_examples=MAX_LITE_BABY_EXAMPLES,
    )
    if not examples:
        return None

    lines = "\n".join(f"- {phrase}" for _, phrase in examples)
    return f"{baby_examples_intro()}\n{lines}"


def _lite_tools_for_routing(
    context_routing: ContextRoutingDecision,
) -> list[dict[str, Any]] | None:
    tools = list(PET_STATE_TOOLS)
    if context_routing.enabled("characterProfile"):
        tools.extend(LITE_CHARACTER_TOOLS)
    return tools


def _history_messages(payload: LocalChatRequest) -> list[dict[str, str]]:
    return [
        {
            "role": "assistant" if item.role == "pet" else "user",
            "content": item.text,
        }
        for item in payload.history[-12:]
    ]


def _identity_line_for_pet(
    *,
    surface: PhraseSurface,
    pet: Any,
    description: str,
    reply_limit: int,
) -> str:
    flags = state_layer_surface_flags(surface)
    age_hint = ""
    if flags.get("age", False):
        age_hint = _age_role_hint_for_pet(pet)
    state_modifier = _state_role_modifier_for_pet(pet, surface)
    system_content = identity_prompt(
        {
            "description": description,
            "age_hint": age_hint,
            "state_modifier": state_modifier or "",
            "reply_limit": str(reply_limit),
        },
    )
    return re.sub(r"[ \t]{2,}", " ", system_content).strip()


def _chat_identity_line(payload: LocalChatRequest, reply_limit: int) -> str:
    system_content = _identity_line_for_pet(
        surface="chat",
        pet=payload.pet,
        description=_short_character_description(payload),
        reply_limit=reply_limit,
    )
    return system_content


def _phrase_plan_for_chat(
    payload: LocalChatRequest,
    *,
    context_bundle: AssembledPetContext,
    context_routing: ContextRoutingDecision | None = None,
) -> PhrasePlan:
    reply_limit = payload.replyMaxChars or MAX_REPLY_CHARS
    return PhrasePlan(
        surface="chat",
        reply_limit=reply_limit,
        identity_line=_chat_identity_line(payload, reply_limit),
        persona_contract=surface_prompt("chat"),
        character_block=_character_context_block(payload.pet, context_routing),
        memory_block=(
            _memory_context_block(payload.memoryContext)
            if _routing_enabled(context_routing, "userMemory", default=True)
            else None
        ),
        world_block=context_bundle.prompt_block or None,
    )


def build_lite_chat_messages(
    payload: LocalChatRequest,
    *,
    context_bundle: AssembledPetContext | None = None,
    context_routing: ContextRoutingDecision | None = None,
) -> list[dict[str, str]]:
    if context_bundle is None:
        context_bundle = _story_context_for_routing(
            surface="chat",
            payload=payload,
            context_routing=context_routing or ContextRoutingDecision(surface="chat"),
        )
    plan = _phrase_plan_for_chat(
        payload,
        context_bundle=context_bundle,
        context_routing=context_routing,
    )
    system_content = plan.system_content()
    baby_examples = _baby_phrase_examples_for_prompt(payload)
    if baby_examples:
        system_content = f"{system_content}\n\n{baby_examples}"

    return [
        {
            "role": "system",
            "content": system_content,
        },
        *_history_messages(payload),
        {"role": "user", "content": payload.message},
    ]


def _lite_overlay_from(payload: LocalChatRequest) -> dict[str, Any]:
    bible = payload.pet.characterBible if _is_record(payload.pet.characterBible) else {}
    extensions = bible.get("extensions") if isinstance(bible, dict) else None
    if not _is_record(extensions):
        return {}
    overlay = extensions.get("lite_overlay")
    return dict(overlay) if _is_record(overlay) else {}


def _lite_pet_context_payload(payload: LocalChatRequest) -> dict[str, Any]:
    return {
        "name": payload.pet.name,
        "description": payload.pet.description,
        "stage": payload.pet.stage,
        "mood": payload.pet.mood,
    }


def _text_value(value: Any) -> str:
    return _compact_spaces(str(value or ""))


def _is_technical_world_text(value: str) -> bool:
    text = _text_value(value)
    return not text or text in {"-", "—"} or bool(TECHNICAL_WORLD_TEXT_PATTERN.search(text))


def _collect_clean_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        text = _text_value(value)
        return [] if _is_technical_world_text(text) else [text]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_collect_clean_strings(item))
        return result
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_collect_clean_strings(item))
        return result
    return []


def _sanitize_technical_world_text(value: Any) -> Any:
    if isinstance(value, str):
        return "" if _is_technical_world_text(value) else value
    if isinstance(value, list):
        cleaned = [_sanitize_technical_world_text(item) for item in value]
        return [item for item in cleaned if item not in ("", None, [], {})]
    if isinstance(value, dict):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _sanitize_technical_world_text(item)) not in ("", None, [], {})
        }
    return value


def _existing_world_texts(payload: LocalChatRequest) -> list[str]:
    texts: list[str] = []
    overlay = _lite_overlay_from(payload)
    texts.extend(_collect_clean_strings(overlay.get("spheres", {}).get("world", {})))
    texts.extend(
        _collect_clean_strings(
            [
                fact
                for fact in overlay.get("facts", [])
                if isinstance(fact, dict)
                and (
                    fact.get("sphere") == "world" or fact.get("kind") in {"world_fact", "lore_fact"}
                )
            ]
        )
    )

    bible = payload.pet.characterBible if _is_record(payload.pet.characterBible) else {}
    lore = bible.get("lore") if _is_record(bible.get("lore")) else {}
    profile_world = bible.get("world") if _is_record(bible.get("world")) else {}
    texts.extend(_collect_clean_strings(lore.get("world") if _is_record(lore) else {}))
    texts.extend(_collect_clean_strings(lore.get("home") if _is_record(lore) else {}))
    texts.extend(_collect_clean_strings(profile_world))
    return [text for text in texts if len(text) >= 12]


def _world_seed_messages(payload: LocalChatRequest) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": world_seed_system_prompt(),
        },
        {
            "role": "user",
            "content": speech_template(
                "worldSeedUserMessage",
                {
                    "pet_name": payload.pet.name or speech_template("unnamedPet"),
                    "description": payload.pet.description,
                    "age_hint": _age_role_hint(payload),
                    "state": _state_role_modifier(payload) or payload.pet.mood,
                    "user_message": payload.message,
                },
            ),
        },
    ]


def _parse_world_seed_text(raw_content: str) -> str | None:
    try:
        parsed = json.loads(raw_content or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    text = _text_value(parsed.get("worldText"))
    return _truncate_text(text, 500) if text else None


def _world_seed_overlay_patch(
    payload: LocalChatRequest,
    *,
    client: Any,
    model: str,
    timeout: float,
    prompt_debug: list[dict[str, Any]],
) -> dict[str, Any] | None:
    settings = get_settings()
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": _world_seed_messages(payload),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "lite_world_seed",
                "schema": LITE_WORLD_SEED_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    prompt_debug.append(log_chat_completion_prompt("pet_reply/lite_world_seed", request_kwargs))
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("pet_reply/lite_world_seed", completion)
    world_text = _parse_world_seed_text(completion.choices[0].message.content or "")
    if not world_text:
        return None

    raw_fact = {
        "sphere": "world",
        "kind": "world_fact",
        "text": world_text,
        "pathHint": "lite_overlay.spheres.world",
        "source": "chatgpt_world_seed",
    }
    patch = _overlay_patch_from_extracted_facts([raw_fact])
    if not patch:
        return None
    patch["worldSeed"] = {
        "source": "chatgpt",
        "createdAt": _now_iso(),
    }
    return patch


def _merge_lite_overlay_patch(target: dict[str, Any], patch: dict[str, Any] | None) -> None:
    if not patch:
        return

    existing_keys = {
        _lite_fact_key(fact) for fact in target.get("facts", []) if isinstance(fact, dict)
    }
    facts = target.setdefault("facts", [])
    if not isinstance(facts, list):
        facts = []
        target["facts"] = facts
    for fact in patch.get("facts", []):
        if not isinstance(fact, dict):
            continue
        key = _lite_fact_key(fact)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        facts.append(fact)

    spheres = target.setdefault("spheres", {})
    if not isinstance(spheres, dict):
        spheres = {}
        target["spheres"] = spheres
    patch_spheres = patch.get("spheres")
    if isinstance(patch_spheres, dict):
        for sphere, patch_sphere in patch_spheres.items():
            if not isinstance(patch_sphere, dict):
                continue
            target_sphere = spheres.setdefault(sphere, {})
            if not isinstance(target_sphere, dict):
                target_sphere = {}
                spheres[sphere] = target_sphere
            _merge_lite_overlay_patch(target_sphere, patch_sphere)

    if isinstance(patch.get("worldSeed"), dict):
        target["worldSeed"] = patch["worldSeed"]


def _lite_character_bible_for_read(
    payload: LocalChatRequest,
    world_seed_patch: dict[str, Any] | None,
) -> dict[str, Any]:
    bible = deepcopy(payload.pet.characterBible) if _is_record(payload.pet.characterBible) else {}
    bible = _sanitize_technical_world_text(bible)
    if world_seed_patch:
        world_facts = world_seed_patch.get("facts") if isinstance(world_seed_patch, dict) else []
        world_text = ""
        if isinstance(world_facts, list) and world_facts and isinstance(world_facts[0], dict):
            world_text = _text_value(world_facts[0].get("text"))
        if world_text:
            lore = bible.setdefault("lore", {})
            if isinstance(lore, dict):
                lore["world"] = {
                    **(lore.get("world") if isinstance(lore.get("world"), dict) else {}),
                    "story": world_text,
                    "environment": world_text,
                }
                lore["home"] = {
                    **(lore.get("home") if isinstance(lore.get("home"), dict) else {}),
                    "story": world_text,
                }
            profile_world = bible.setdefault("world", {})
            if isinstance(profile_world, dict):
                profile_world.setdefault("habitat", world_text)
                profile_world.setdefault("home", world_text)
    return bible


def _read_character_json(
    payload: LocalChatRequest,
    arguments: dict[str, Any],
    overlay_patch: dict[str, Any] | None = None,
    *,
    context_routing: ContextRoutingDecision | None = None,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
    prompt_debug: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw_sections = arguments.get("sections")
    sections = set(raw_sections if isinstance(raw_sections, list) else [])
    if not sections:
        sections = {"characterBible", "liteOverlay"}

    should_seed_world = bool(
        context_routing and context_routing.enabled("worldContext")
    ) and not _existing_world_texts(payload)
    world_seed_patch = (
        _world_seed_overlay_patch(
            payload,
            client=client,
            model=model,
            timeout=timeout,
            prompt_debug=prompt_debug,
        )
        if should_seed_world and client and model and timeout and prompt_debug is not None
        else None
    )
    if world_seed_patch and overlay_patch is not None:
        _merge_lite_overlay_patch(overlay_patch, world_seed_patch)

    result: dict[str, Any] = {
        "description": payload.pet.description,
        "name": payload.pet.name,
    }
    if "characterBible" in sections:
        result["characterBible"] = _lite_character_bible_for_read(payload, world_seed_patch)
    if "liteOverlay" in sections:
        overlay = _lite_overlay_from(payload)
        if world_seed_patch:
            overlay = dict(overlay)
            _merge_lite_overlay_patch(overlay, world_seed_patch)
        result["liteOverlay"] = overlay
    if world_seed_patch:
        result["worldInfo"] = {
            "createdByChatGPT": True,
            "patch": world_seed_patch,
        }
    return result


def _normalized_pet_name(value: Any) -> str | None:
    name = _compact_spaces(str(value or ""))
    name = name.strip("\"'«»“”„")
    if not name or not re.search(r"[0-9A-Za-zА-Яа-яЁё]", name):
        return None
    return _truncate_text(name, 32)


def _update_pet_name_patch(
    pet_patch: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    name = _normalized_pet_name(arguments.get("name"))
    if not name:
        return {"saved": False, "reason": "empty_name"}
    pet_patch["name"] = name
    return {"saved": True, "petPatch": {"name": name}}


def _lite_fact_path_hint(sphere: str) -> str:
    return f"lite_overlay.spheres.{sphere}"


def _default_kind_for_sphere(sphere: str) -> str:
    if sphere == "appearance":
        return "appearance_fact"
    if sphere == "world":
        return "world_fact"
    if sphere == "relationship":
        return "relationship_fact"
    return "character_fact"


def _normalized_extracted_fact(value: Any) -> dict[str, Any] | None:
    if not _is_record(value):
        return None

    text = _compact_spaces(str(value.get("text") or ""))
    if not text:
        return None

    sphere = str(value.get("sphere") or "character").strip()
    if sphere not in LITE_FACT_SPHERES:
        sphere = "character"

    kind = str(value.get("kind") or "").strip()
    if kind not in LITE_FACT_KINDS:
        kind = _default_kind_for_sphere(sphere)

    path_hint = _compact_spaces(str(value.get("pathHint") or "")) or _lite_fact_path_hint(sphere)
    source = _compact_spaces(str(value.get("source") or "")) or "lite_post_reply_extractor"

    return {
        "sphere": sphere,
        "kind": kind,
        "text": text,
        "pathHint": path_hint,
        "source": source,
        "createdAt": _now_iso(),
    }


def _lite_fact_key(fact: dict[str, Any]) -> str:
    return f"{fact.get('sphere', 'character')}:{fact.get('text', '')}".casefold()


def _overlay_patch_from_extracted_facts(raw_facts: Any) -> dict[str, Any] | None:
    if not isinstance(raw_facts, list):
        return None

    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_fact in raw_facts:
        fact = _normalized_extracted_fact(raw_fact)
        if not fact:
            continue
        key = _lite_fact_key(fact)
        if key in seen:
            continue
        seen.add(key)
        facts.append(fact)

    if not facts:
        return None

    spheres: dict[str, dict[str, Any]] = {}
    for sphere in LITE_FACT_SPHERES:
        sphere_facts = [fact for fact in facts if fact["sphere"] == sphere]
        if sphere_facts:
            spheres[sphere] = {"facts": sphere_facts}

    return {
        "facts": facts,
        "spheres": spheres,
    }


def _parse_lite_fact_extraction_payload(raw_content: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw_content or "{}")
    except json.JSONDecodeError:
        return None
    if not _is_record(parsed):
        return None
    return _overlay_patch_from_extracted_facts(parsed.get("facts"))


def _lite_extraction_context(payload: LiteFactExtractionRequest) -> str:
    character_context = _read_character_json(
        LocalChatRequest(
            message=payload.message,
            pet=payload.pet,
            history=payload.history,
        ),
        {"sections": ["characterBible", "liteOverlay"]},
    )
    return _truncate_text(
        json.dumps(character_context, ensure_ascii=False, default=str),
        MAX_LITE_EXTRACTION_CONTEXT_CHARS,
    )


def build_lite_fact_extraction_messages(
    payload: LiteFactExtractionRequest,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": character_fact_extraction_system_prompt(),
        },
        {
            "role": "user",
            "content": speech_template(
                "liteFactExtractionUserMessage",
                {
                    "character_context": _lite_extraction_context(payload),
                    "message": payload.message,
                    "reply": payload.reply,
                },
            ),
        },
    ]


def _tool_call_id(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("id") or "")
    return str(getattr(tool_call, "id", "") or "")


def _tool_call_function(tool_call: Any) -> tuple[str, str]:
    if isinstance(tool_call, dict):
        function = tool_call.get("function") or {}
        return str(function.get("name") or ""), str(function.get("arguments") or "{}")
    function = getattr(tool_call, "function", None)
    return (
        str(getattr(function, "name", "") or ""),
        str(getattr(function, "arguments", "{}") or "{}"),
    )


def _parse_arguments(raw_arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _assistant_tool_call_message(message: Any, tool_calls: list[Any]) -> dict[str, Any]:
    serialized_calls: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        name, arguments = _tool_call_function(tool_call)
        serialized_calls.append(
            {
                "id": _tool_call_id(tool_call),
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return {
        "role": "assistant",
        "content": getattr(message, "content", None) or "",
        "tool_calls": serialized_calls,
    }


def _tool_response_message(tool_call_id: str, payload: dict[str, Any]) -> dict[str, str]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(payload, ensure_ascii=False),
    }


def _handle_tool_call(
    payload: LocalChatRequest,
    tool_call: Any,
    overlay_patch: dict[str, Any],
    pet_patch: dict[str, Any],
    *,
    context_routing: ContextRoutingDecision,
    client: Any,
    model: str,
    timeout: float,
    prompt_debug: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    name, raw_arguments = _tool_call_function(tool_call)
    arguments = _parse_arguments(raw_arguments)
    debug = {"name": name, "arguments": arguments}

    if name == "read_character_json":
        result = _read_character_json(
            payload,
            arguments,
            overlay_patch,
            context_routing=context_routing,
            client=client,
            model=model,
            timeout=timeout,
            prompt_debug=prompt_debug,
        )
    elif name == "update_pet_name":
        result = _update_pet_name_patch(pet_patch, arguments)
    else:
        result = {"error": f"unknown_tool:{name}"}
    debug["result"] = result
    return result, debug


def _story_context_debug(
    context_bundle: AssembledPetContext | None,
) -> dict[str, Any] | None:
    if context_bundle and context_bundle.debug:
        return context_bundle.debug
    return None


def _story_context_brief(context_bundle: AssembledPetContext | None) -> list[dict[str, Any]]:
    debug = _story_context_debug(context_bundle) or {}
    bricks = debug.get("injectedSpheres")
    if not isinstance(bricks, list):
        return []
    result: list[dict[str, Any]] = []
    for brick in bricks[:MAX_CONTEXT_BRICKS]:
        if not isinstance(brick, dict):
            continue
        result.append(
            {
                "id": brick.get("id"),
                "pool": brick.get("pool"),
                "name": brick.get("name"),
                "text": brick.get("text"),
            }
        )
    return result


def _should_extract_story_library_patch(
    payload: LocalChatRequest,
    reply: str,
    context_bundle: AssembledPetContext | None,
) -> bool:
    if not reply.strip():
        return False
    if not STORY_LIBRARY_REPLY_ENTITY_PATTERN.search(reply):
        return False
    return bool(
        (context_bundle and context_bundle.prompt_block)
        or STORY_LIBRARY_QUERY_PATTERN.search(payload.message)
    )


def build_story_library_extraction_messages(
    payload: LocalChatRequest,
    reply: str,
    context_bundle: AssembledPetContext | None,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": story_library_extraction_system_prompt(),
        },
        {
            "role": "user",
            "content": speech_template(
                "storyExtractionUserMessage",
                {
                    "pet_context": _safe_json_context(_lite_pet_context_payload(payload), 1600),
                    "user_message": payload.message,
                    "reply": reply,
                    "story_context": _safe_json_context(
                        _story_context_brief(context_bundle),
                        3000,
                    ),
                },
            ),
        },
    ]


def _parse_story_library_extraction_payload(raw_content: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw_content or "{}")
    except json.JSONDecodeError:
        return None
    if not _is_record(parsed) or not isinstance(parsed.get("bricks"), list):
        return None

    bricks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_brick in parsed["bricks"]:
        if not _is_record(raw_brick):
            continue
        confidence = _clamp_float(raw_brick.get("confidence"), 0.0)
        if confidence < 0.6:
            continue
        result = story_library_patch_for_new_brick(raw_brick)
        brick = result.get("brick") if _is_record(result) else None
        if not _is_record(brick):
            continue
        brick_id = str(brick.get("id") or "")
        if not brick_id or brick_id in seen:
            continue
        seen.add(brick_id)
        bricks.append(brick)
    if not bricks:
        return None
    return {"version": 1, "bricks": bricks}


def extract_story_library_patch_from_reply(
    payload: LocalChatRequest,
    reply: str,
    context_bundle: AssembledPetContext | None,
    *,
    client: Any,
    model: str,
    timeout: float,
    prompt_debug: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not _should_extract_story_library_patch(payload, reply, context_bundle):
        return None

    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_story_library_extraction_messages(payload, reply, context_bundle),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "story_library_extraction",
                "schema": STORY_LIBRARY_EXTRACTION_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(get_settings().openai_chat_reasoning_effort),
    }
    prompt_debug.append(
        log_chat_completion_prompt("pet_reply/story_library_extraction", request_kwargs)
    )
    completion = client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("pet_reply/story_library_extraction", completion)
    return _parse_story_library_extraction_payload(completion.choices[0].message.content or "{}")


def generate_lite_pet_reply(
    payload: LocalChatRequest,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> LocalChatResponse:
    settings = get_settings()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    context_routing, context_routing_prompt_debug = _route_contexts_for_visible_reply(
        surface="chat",
        payload=payload,
        client=openai_client,
        model=model,
        timeout=timeout,
    )
    context_bundle = _story_context_for_routing(
        surface="chat",
        payload=payload,
        context_routing=context_routing,
    )
    messages: list[dict[str, Any]] = build_lite_chat_messages(
        payload,
        context_bundle=context_bundle,
        context_routing=context_routing,
    )
    tools = _lite_tools_for_routing(context_routing)
    overlay_patch: dict[str, Any] = {}
    pet_patch: dict[str, Any] = {}
    tool_debug: list[dict[str, Any]] = []
    prompt_debug: list[dict[str, Any]] = [context_routing_prompt_debug]
    reply = ""
    inner_thought: str | None = None
    face_hint: str | None = None

    for round_index in range(MAX_LITE_TOOL_ROUNDS + 1):
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "timeout": timeout,
        }
        if tools:
            request_kwargs["tools"] = tools
            request_kwargs["tool_choice"] = "auto"
        else:
            request_kwargs.update(
                chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort)
            )

        prompt_debug.append(
            log_chat_completion_prompt(f"pet_reply/lite round {round_index + 1}", request_kwargs)
        )
        completion = openai_client.chat.completions.create(**request_kwargs)
        log_chat_completion_response(f"pet_reply/lite round {round_index + 1}", completion)
        message = completion.choices[0].message
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        if not tools or not tool_calls:
            reply, inner_thought, face_hint = _extract_hidden_reaction(
                getattr(message, "content", None) or ""
            )
            break

        messages.append(_assistant_tool_call_message(message, tool_calls))
        for tool_call in tool_calls:
            result, debug = _handle_tool_call(
                payload,
                tool_call,
                overlay_patch,
                pet_patch,
                context_routing=context_routing,
                client=openai_client,
                model=model,
                timeout=timeout,
                prompt_debug=prompt_debug,
            )
            tool_debug.append(debug)
            messages.append(_tool_response_message(_tool_call_id(tool_call), result))

    story_library_patch: dict[str, Any] | None = None
    if reply:
        try:
            story_library_patch = extract_story_library_patch_from_reply(
                payload,
                reply,
                context_bundle,
                client=openai_client,
                model=model,
                timeout=timeout,
                prompt_debug=prompt_debug,
            )
        except Exception:
            story_library_patch = None

    debug = LocalChatDebug(
        usedFallback=False,
        validationFlags=[],
        promptDebug=prompt_debug,
        liteToolCalls=tool_debug,
        liteOverlayPatch=overlay_patch or None,
        storyLibraryPatch=story_library_patch,
        storyLibraryDebug=_story_context_debug(context_bundle),
        contextRoutingDebug={
            "surface": context_routing.surface,
            "enabledSources": sorted(context_routing.enabled_sources),
            "queries": context_routing.queries,
            "reason": context_routing.reason,
            "raw": context_routing.raw,
        },
    )
    return LocalChatResponse(
        reply=reply,
        moodHint=None,
        innerThought=inner_thought,
        faceHint=face_hint,
        petPatch=pet_patch or None,
        debug=debug,
    )


def extract_lite_overlay_patch_from_reply(
    payload: LiteFactExtractionRequest,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> tuple[dict[str, Any] | None, LocalChatDebug | None]:
    settings = get_settings()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    prompt_debug: list[dict[str, Any]] = []

    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_lite_fact_extraction_messages(payload),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "lite_fact_extraction",
                "schema": LITE_FACT_EXTRACTION_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    prompt_debug.append(
        log_chat_completion_prompt("pet_reply/lite_fact_extraction", request_kwargs)
    )
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("pet_reply/lite_fact_extraction", completion)
    patch = _parse_lite_fact_extraction_payload(completion.choices[0].message.content or "{}")
    debug = None
    if payload.includeDebug or prompt_debug:
        debug = LocalChatDebug(
            usedFallback=False,
            validationFlags=[],
            promptDebug=prompt_debug,
            liteOverlayPatch=patch,
        )
    return patch, debug


def _safe_json_context(value: Any, limit: int = 6000) -> str:
    return _truncate_text(json.dumps(value, ensure_ascii=False, default=str), limit)


def _clamp_float(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _memory_key_from_text(text: str) -> str:
    words = re.findall(r"[\wа-яё]+", text.casefold(), flags=re.IGNORECASE)
    return "-".join(words[:12])[:160] or "memory"


def _optional_iso_text(value: Any) -> str | None:
    text = _compact_spaces(str(value or ""))
    return text[:80] if text else None


def _normalized_memory_operation(value: Any) -> dict[str, Any] | None:
    if not _is_record(value):
        return None
    operation_type = str(value.get("type") or "").strip()
    kind = str(value.get("kind") or "user_fact").strip()
    if kind not in USER_MEMORY_KINDS:
        kind = "user_fact"

    if operation_type == "capture_learning":
        observation = _compact_spaces(str(value.get("observation") or ""))
        if not observation:
            return None
        operation: dict[str, Any] = {
            "type": "capture_learning",
            "observation": _truncate_text(observation, 500),
            "confidence": _clamp_float(value.get("confidence"), 0.6),
            "importance": _clamp_float(value.get("importance"), 0.5),
        }
        pattern_key = _compact_spaces(str(value.get("patternKey") or ""))
        if pattern_key:
            operation["patternKey"] = _truncate_text(pattern_key, 120)
        operation["kind"] = kind
        due_at = _optional_iso_text(value.get("dueAt"))
        if due_at:
            operation["dueAt"] = due_at
        return operation

    if operation_type == "remember_user_fact":
        text = _compact_spaces(str(value.get("text") or ""))
        if not text:
            return None
        normalized_key = _compact_spaces(str(value.get("normalizedKey") or ""))
        tags = value.get("tags") if isinstance(value.get("tags"), list) else []
        operation = {
            "type": "remember_user_fact",
            "kind": kind,
            "text": _truncate_text(text, 500),
            "normalizedKey": _truncate_text(normalized_key or _memory_key_from_text(text), 160),
            "confidence": _clamp_float(value.get("confidence"), 0.75),
            "importance": _clamp_float(value.get("importance"), 0.7),
            "tags": [
                _truncate_text(_compact_spaces(str(tag)), 40)
                for tag in tags[:6]
                if _compact_spaces(str(tag))
            ],
        }
        due_at = _optional_iso_text(value.get("dueAt"))
        expires_at = _optional_iso_text(value.get("expiresAt"))
        if due_at:
            operation["dueAt"] = due_at
        if expires_at:
            operation["expiresAt"] = expires_at
        return operation

    return None


def _parse_memory_extraction_payload(raw_content: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(raw_content or "{}")
    except json.JSONDecodeError:
        return []
    if not _is_record(parsed) or not isinstance(parsed.get("operations"), list):
        return []
    operations: list[dict[str, Any]] = []
    for raw_operation in parsed["operations"]:
        operation = _normalized_memory_operation(raw_operation)
        if operation:
            operations.append(operation)
    return operations


def build_memory_extraction_messages(payload: MemoryExtractionRequest) -> list[dict[str, str]]:
    memory_context = payload.memoryContext.model_dump(mode="json") if payload.memoryContext else {}
    history_context = [item.model_dump(mode="json") for item in payload.history[-8:]]
    return [
        {
            "role": "system",
            "content": user_memory_extraction_system_prompt(),
        },
        {
            "role": "user",
            "content": speech_template(
                "userMemoryExtractionUserMessage",
                {
                    "now_iso": payload.nowIso or _now_iso(),
                    "timezone": payload.timezone or "Europe/Moscow",
                    "existing_memory": payload.existingMemoryBrief or speech_template("emptyValue"),
                    "memory_context": _safe_json_context(memory_context, 3000),
                    "history_context": _safe_json_context(history_context, 3000),
                    "message": payload.message,
                    "reply": payload.reply,
                },
            ),
        },
    ]


def extract_user_memory_operations(
    payload: MemoryExtractionRequest,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> MemoryExtractionResponse:
    settings = get_settings()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    prompt_debug: list[dict[str, Any]] = []

    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_memory_extraction_messages(payload),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "user_memory_extraction",
                "schema": MEMORY_EXTRACTION_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    prompt_debug.append(log_chat_completion_prompt("pet_reply/memory_extraction", request_kwargs))
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("pet_reply/memory_extraction", completion)
    operations = _parse_memory_extraction_payload(completion.choices[0].message.content or "{}")
    debug = None
    if payload.includeDebug or prompt_debug:
        debug = LocalChatDebug(
            usedFallback=False,
            validationFlags=[],
            promptDebug=prompt_debug,
            memoryDebug={"extractionOperations": operations},
        )
    return MemoryExtractionResponse(operations=operations, debug=debug)


def _normalized_consolidation_operation(value: Any) -> dict[str, Any] | None:
    if not _is_record(value):
        return None
    operation_type = str(value.get("type") or "").strip()
    learning_id = _compact_spaces(str(value.get("learningId") or ""))

    if operation_type == "promote_learning":
        memory = _normalized_memory_operation(
            {
                "type": "remember_user_fact",
                **(value.get("memory") if _is_record(value.get("memory")) else {}),
            }
        )
        if not learning_id or not memory:
            return None
        return {
            "type": "promote_learning",
            "learningId": _truncate_text(learning_id, 120),
            "memory": {key: val for key, val in memory.items() if key != "type"},
        }

    if operation_type == "prune_learning":
        if not learning_id:
            return None
        reason = _compact_spaces(str(value.get("reason") or ""))
        return {
            "type": "prune_learning",
            "learningId": _truncate_text(learning_id, 120),
            **({"reason": _truncate_text(reason, 240)} if reason else {}),
        }

    if operation_type in {"rewrite_summary", "rewrite_user_profile"}:
        content = _compact_spaces(str(value.get("content") or ""))
        if not content:
            return None
        return {
            "type": operation_type,
            "content": _truncate_text(content, 1000),
        }

    return None


def _parse_consolidation_payload(raw_content: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(raw_content or "{}")
    except json.JSONDecodeError:
        return []
    if not _is_record(parsed) or not isinstance(parsed.get("operations"), list):
        return []
    operations: list[dict[str, Any]] = []
    for raw_operation in parsed["operations"]:
        operation = _normalized_consolidation_operation(raw_operation)
        if operation:
            operations.append(operation)
    return operations


def build_memory_consolidation_messages(
    payload: MemoryConsolidationRequest,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": user_memory_consolidation_system_prompt(),
        },
        {
            "role": "user",
            "content": speech_template(
                "userMemoryConsolidationUserMessage",
                {
                    "now_iso": payload.nowIso or _now_iso(),
                    "timezone": payload.timezone or "Europe/Moscow",
                    "pending_learnings": _safe_json_context(payload.pendingLearnings, 9000),
                    "existing_memories": _safe_json_context(payload.existingMemories, 9000),
                    "summary": payload.summary or speech_template("emptyValue"),
                    "user_profile": payload.userProfile or speech_template("emptyValue"),
                },
            ),
        },
    ]


def consolidate_user_memory(
    payload: MemoryConsolidationRequest,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> MemoryConsolidationResponse:
    settings = get_settings()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    prompt_debug: list[dict[str, Any]] = []

    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_memory_consolidation_messages(payload),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "user_memory_consolidation",
                "schema": MEMORY_CONSOLIDATION_SCHEMA,
                "strict": True,
            },
        },
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    prompt_debug.append(
        log_chat_completion_prompt("pet_reply/memory_consolidation", request_kwargs)
    )
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("pet_reply/memory_consolidation", completion)
    operations = _parse_consolidation_payload(completion.choices[0].message.content or "{}")
    debug = None
    if payload.includeDebug or prompt_debug:
        debug = LocalChatDebug(
            usedFallback=False,
            validationFlags=[],
            promptDebug=prompt_debug,
            memoryDebug={"consolidationOperations": operations},
        )
    return MemoryConsolidationResponse(operations=operations, debug=debug)


def build_proactive_messages(
    payload: LocalProactiveRequest,
    *,
    context_bundle: AssembledPetContext | None = None,
    context_routing: ContextRoutingDecision | None = None,
) -> list[dict[str, str]]:
    memory_block = (
        _memory_context_block(payload.memoryContext)
        if _routing_enabled(context_routing, "userMemory", default=True)
        else None
    )
    if context_bundle is None:
        context_bundle = _story_context_for_routing(
            surface="proactive",
            payload=payload,
            context_routing=context_routing or ContextRoutingDecision(surface="proactive"),
        )
    reason = _reason_from_payload(payload)
    plan = PhrasePlan(
        surface="proactive",
        reply_limit=MAX_REPLY_CHARS,
        identity_line=_identity_line_for_pet(
            surface="proactive",
            pet=payload.pet,
            description=_short_pet_description(payload.pet),
            reply_limit=MAX_REPLY_CHARS,
        ),
        persona_contract=surface_prompt("proactive", {"reason": reason}),
        world_block=context_bundle.prompt_block or None,
        character_block=_character_context_block(payload.pet, context_routing),
        memory_block=memory_block,
    )
    return [
        {
            "role": "system",
            "content": plan.system_content(),
        }
    ]


def build_push_messages(
    payload: LocalPushRequest,
    *,
    context_bundle: AssembledPetContext | None = None,
    context_routing: ContextRoutingDecision | None = None,
) -> list[dict[str, str]]:
    memory_block = (
        _memory_context_block(payload.memoryContext)
        if _routing_enabled(context_routing, "userMemory", default=True)
        else None
    )
    if context_bundle is None:
        context_bundle = _story_context_for_routing(
            surface="push",
            payload=payload,
            context_routing=context_routing or ContextRoutingDecision(surface="push"),
        )
    plan = PhrasePlan(
        surface="push",
        reply_limit=180,
        identity_line=_identity_line_for_pet(
            surface="push",
            pet=payload.pet,
            description=_short_pet_description(payload.pet),
            reply_limit=180,
        ),
        persona_contract=surface_prompt("push", {"reason": _reason_from_payload(payload)}),
        world_block=context_bundle.prompt_block or None,
        character_block=_character_context_block(payload.pet, context_routing),
        memory_block=memory_block,
    )
    return [
        {
            "role": "system",
            "content": plan.system_content(),
        }
    ]


def generate_proactive_pet_message(
    payload: LocalProactiveRequest,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> LocalProactiveResponse:
    settings = get_settings()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    prompt_debug: list[dict[str, Any]] = []
    context_routing, context_routing_prompt_debug = _route_contexts_for_visible_reply(
        surface="proactive",
        payload=payload,
        client=openai_client,
        model=model,
        timeout=timeout,
    )
    prompt_debug.append(context_routing_prompt_debug)
    context_bundle = _story_context_for_routing(
        surface="proactive",
        payload=payload,
        context_routing=context_routing,
    )

    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_proactive_messages(
            payload,
            context_bundle=context_bundle,
            context_routing=context_routing,
        ),
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    prompt_debug.append(log_chat_completion_prompt("pet_reply/proactive", request_kwargs))
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("pet_reply/proactive", completion)
    reply, inner_thought, face_hint = _extract_hidden_reaction(
        completion.choices[0].message.content or ""
    )
    debug = None
    if payload.includeDebug or prompt_debug:
        debug = LocalChatDebug(
            usedFallback=False,
            validationFlags=[],
            promptDebug=prompt_debug,
            memoryDebug={
                "proactiveReason": (
                    payload.memoryContext.proactiveCandidate.reason
                    if payload.memoryContext.proactiveCandidate
                    else None
                ),
                "selectedMemoryIds": (
                    payload.memoryContext.proactiveCandidate.memoryIds
                    if payload.memoryContext.proactiveCandidate
                    else []
                ),
            },
            storyLibraryDebug=_story_context_debug(context_bundle),
            contextRoutingDebug={
                "surface": context_routing.surface,
                "enabledSources": sorted(context_routing.enabled_sources),
                "queries": context_routing.queries,
                "reason": context_routing.reason,
                "raw": context_routing.raw,
            },
        )
    return LocalProactiveResponse(
        reply=reply,
        moodHint=None,
        innerThought=inner_thought,
        faceHint=face_hint,
        debug=debug,
    )


def generate_push_pet_message(
    payload: LocalPushRequest,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> LocalProactiveResponse:
    settings = get_settings()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    prompt_debug: list[dict[str, Any]] = []
    context_routing, context_routing_prompt_debug = _route_contexts_for_visible_reply(
        surface="push",
        payload=payload,
        client=openai_client,
        model=model,
        timeout=timeout,
    )
    prompt_debug.append(context_routing_prompt_debug)
    context_bundle = _story_context_for_routing(
        surface="push",
        payload=payload,
        context_routing=context_routing,
    )

    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_push_messages(
            payload,
            context_bundle=context_bundle,
            context_routing=context_routing,
        ),
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    prompt_debug.append(log_chat_completion_prompt("pet_reply/push", request_kwargs))
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("pet_reply/push", completion)
    reply, inner_thought, face_hint = _extract_hidden_reaction(
        completion.choices[0].message.content or ""
    )
    debug = None
    if payload.includeDebug or prompt_debug:
        debug = LocalChatDebug(
            usedFallback=False,
            validationFlags=[],
            promptDebug=prompt_debug,
            memoryDebug={"pushReason": _reason_from_payload(payload)},
            storyLibraryDebug=_story_context_debug(context_bundle),
            contextRoutingDebug={
                "surface": context_routing.surface,
                "enabledSources": sorted(context_routing.enabled_sources),
                "queries": context_routing.queries,
                "reason": context_routing.reason,
                "raw": context_routing.raw,
            },
        )
    return LocalProactiveResponse(
        reply=clamp_reply_text(reply, 180),
        moodHint=None,
        innerThought=inner_thought,
        faceHint=face_hint,
        debug=debug,
    )


def build_ambient_messages(
    payload: LocalAmbientRequest,
    *,
    context_bundle: AssembledPetContext | None = None,
    context_routing: ContextRoutingDecision | None = None,
) -> list[dict[str, str]]:
    reply_limit = payload.replyMaxChars or 160
    memory_block = (
        _ambient_memory_context_block(payload.memoryContext)
        if _routing_enabled(context_routing, "userMemory", default=True)
        else None
    )
    if context_bundle is None:
        context_bundle = _story_context_for_routing(
            surface="ambient",
            payload=payload,
            context_routing=context_routing or ContextRoutingDecision(surface="ambient"),
        )
    recent_ambient_replies = (
        _recent_ambient_replies_text(payload.recentAmbientReplies)
        if _routing_enabled(context_routing, "recentReplies", default=True)
        else ""
    )
    plan = PhrasePlan(
        surface="ambient",
        reply_limit=reply_limit,
        identity_line=_identity_line_for_pet(
            surface="ambient",
            pet=payload.pet,
            description=_short_pet_description(payload.pet),
            reply_limit=reply_limit,
        ),
        persona_contract=surface_prompt("ambient", {"recent_replies": recent_ambient_replies}),
        world_block=context_bundle.prompt_block or None,
        character_block=_character_context_block(payload.pet, context_routing),
        memory_block=memory_block,
    )
    return [{"role": "system", "content": plan.system_content()}]


def generate_ambient_pet_message(
    payload: LocalAmbientRequest,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> LocalChatResponse:
    settings = get_settings()
    model = model or get_chat_model(settings)
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    prompt_debug: list[dict[str, Any]] = []
    context_routing, context_routing_prompt_debug = _route_contexts_for_visible_reply(
        surface="ambient",
        payload=payload,
        client=openai_client,
        model=model,
        timeout=timeout,
    )
    prompt_debug.append(context_routing_prompt_debug)
    context_bundle = _story_context_for_routing(
        surface="ambient",
        payload=payload,
        context_routing=context_routing,
    )

    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_ambient_messages(
            payload,
            context_bundle=context_bundle,
            context_routing=context_routing,
        ),
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(settings.openai_chat_reasoning_effort),
    }
    prompt_debug.append(log_chat_completion_prompt("pet_reply/ambient", request_kwargs))
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("pet_reply/ambient", completion)
    raw_reply = completion.choices[0].message.content or ""
    reply, inner_thought, face_hint = _extract_hidden_reaction(raw_reply)
    log_ambient_reply_diagnostic(
        "pet_reply/ambient",
        request_kwargs,
        raw_reply=raw_reply,
        visible_reply=reply,
    )
    debug = None
    if payload.includeDebug or prompt_debug:
        debug = LocalChatDebug(
            usedFallback=False,
            validationFlags=[],
            promptDebug=prompt_debug,
            storyLibraryDebug=_story_context_debug(context_bundle),
            contextRoutingDebug={
                "surface": context_routing.surface,
                "enabledSources": sorted(context_routing.enabled_sources),
                "queries": context_routing.queries,
                "reason": context_routing.reason,
                "raw": context_routing.raw,
            },
        )
    return LocalChatResponse(
        reply=clamp_reply_text(reply, payload.replyMaxChars or 160),
        moodHint=None,
        innerThought=inner_thought,
        faceHint=face_hint,
        debug=debug,
    )
