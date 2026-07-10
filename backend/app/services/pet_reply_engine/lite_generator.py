from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from app.config import get_settings
from app.schemas import (
    HAPPINESS_DELTA_VALUES,
    LiteFactExtractionRequest,
    LocalAmbientRequest,
    LocalChatDebug,
    LocalChatRequest,
    LocalChatResponse,
    LocalPetMemoryContext,
    LocalProactiveRequest,
    LocalProactiveResponse,
    LocalPushRequest,
)
from app.services.character_dossier import build_character_capsule
from app.services.context_assembler import (
    AssembledPetContext,
    assemble_pet_context,
)
from app.services.lite_overlay import (
    LITE_FACT_KINDS,
    LITE_FACT_SPHERES,
    merge_lite_overlay_patch,
    overlay_patch_from_extracted_facts,
)
from app.services.lore_runtime import dialogue_vocabulary_block, lore_prompt_block
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
from app.services.pet_reply_engine.recent_events import (
    _format_recent_events_block,
    _recent_event_id,
    _recent_event_tokens,
    _recent_events_context_for_chat,
    _recent_story_events_from_pet,
    _select_recent_events_for_text,
)
from app.services.pet_reply_engine.reply_limits import MAX_REPLY_CHARS, clamp_reply_text
from app.services.pet_reply_engine.speech_runtime import (
    age_role_hint,
    ambient_dialogue_impulse,
    character_fact_extraction_system_prompt,
    context_routing_sources,
    context_routing_system_prompt,
    context_source_enabled,
    context_source_mode,
    context_source_modes,
    dialogue_state_modifier,
    identity_prompt,
    memory_usage_rule,
    speech_template,
    state_layer_surface_flags,
    state_param_usage_rule,
    surface_prompt,
    transient_context_rule,
    visible_reply_limit,
    visible_reply_model,
    visible_reply_reasoning_effort,
    world_seed_system_prompt,
)
from app.services.prompt_debug import (
    log_ambient_reply_diagnostic,
    log_chat_completion_prompt,
    log_chat_completion_response,
)
from app.services.tone_runtime import tone_context_payload, tone_prompt_block

MAX_LITE_TOOL_ROUNDS = 3
MAX_LITE_EXTRACTION_CONTEXT_CHARS = 12000

FACE_HINTS = ("happy", "excited", "curious", "content", "grumpy", "sleepy")
MOOD_HINTS = ("idle", "happy", "hungry", "sad")
PhraseSurface = Literal["chat", "proactive", "ambient", "push"]

VISIBLE_REPLY_FALLBACKS: dict[PhraseSurface, str] = {
    "chat": "Я рядом.",
    "ambient": "Я тут рядом.",
    "proactive": "Я рядом и хочу услышать тебя.",
    "push": "Я рядом. Загляни ко мне?",
}
MAX_MEMORY_CONTEXT_ITEMS = 5
MAX_RECENT_AMBIENT_REPLIES = 10
MAX_RECENT_HISTORY_MESSAGES = 8
AMBIENT_MEMORY_KINDS = frozenset(
    {"user_fact", "preference", "relationship", "routine", "emotion", "boundary"}
)
RECENT_EVENT_QUESTION_RE = re.compile(
    r"(?:недавно|за последнее время|что интересного было|что случилось|что произошло)",
    re.IGNORECASE,
)
RUSSIAN_REPEAT_SUFFIXES = (
    "иями",
    "ями",
    "ами",
    "ового",
    "евого",
    "овыми",
    "евыми",
    "овый",
    "евый",
    "ого",
    "ому",
    "ыми",
    "ими",
    "ая",
    "яя",
    "ое",
    "ее",
    "ые",
    "ие",
    "ой",
    "ей",
    "ах",
    "ях",
    "ом",
    "ем",
    "ую",
    "юю",
    "ы",
    "и",
    "а",
    "я",
    "у",
    "ю",
)


@dataclass(frozen=True)
class PhrasePlan:
    surface: PhraseSurface
    reply_limit: int
    identity_line: str
    persona_contract: str
    tone_block: str | None = None
    character_block: str | None = None
    memory_block: str | None = None
    voice_block: str | None = None
    recent_events_block: str | None = None
    world_block: str | None = None
    recent_ambient_block: str | None = None
    dialogue_block: str | None = None
    extra_rules: tuple[str | None, ...] = field(default_factory=tuple)

    def system_content(self) -> str:
        sections = [
            self.identity_line,
            self.tone_block,
            self.character_block,
            self.voice_block,
            self.recent_events_block,
            self.world_block,
            self.memory_block,
            self.recent_ambient_block,
            self.dialogue_block,
            "\n".join(rule for rule in self.extra_rules if rule),
            self.persona_contract,
        ]
        return "\n\n".join(section for section in sections if section)


@dataclass(frozen=True)
class VisibleReplyResult:
    reply: str
    mood_hint: str | None
    face_hint: str | None
    happiness_delta: int
    used_fallback: bool
    validation_flags: list[str]
    debug: dict[str, Any]


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

PET_RENAME_INTENT_RE = re.compile(
    r"(?:"
    r"буду\s+звать\s+тебя|зову\s+тебя|называю\s+тебя|назову\s+тебя|"
    r"пусть\s+тебя\s+зовут|тебя\s+зовут|тво[её]\s+имя|переимен|"
    r"теперь\s+ты|отныне\s+ты"
    r")",
    re.IGNORECASE,
)

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
                for source in CONTEXT_ROUTING_SOURCE_IDS
            },
            "required": list(CONTEXT_ROUTING_SOURCE_IDS),
        },
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["sources", "reason"],
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


def _visible_reply_response_format(
    reply_limit: int,
    *,
    include_happiness_delta: bool = False,
) -> dict[str, Any]:
    limit = max(1, min(reply_limit, MAX_REPLY_CHARS))
    properties: dict[str, Any] = {
        "reply": {
            "type": "string",
            "minLength": 1,
            "maxLength": limit,
        },
        "faceHint": {
            "type": ["string", "null"],
            "enum": [*FACE_HINTS, None],
        },
        "moodHint": {
            "type": ["string", "null"],
            "enum": [*MOOD_HINTS, None],
        },
    }
    required = ["reply", "faceHint", "moodHint"]
    if include_happiness_delta:
        properties["happinessDelta"] = {
            "type": "integer",
            "enum": list(HAPPINESS_DELTA_VALUES),
        }
        required.append("happinessDelta")
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "visible_pet_reply",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": required,
            },
        },
    }


def _structured_reply_contract_rule() -> str:
    return (
        "Поле reply содержит только слова, которые персонаж произносит вслух. "
        "Не пиши авторскую ремарку или описание кадра вместо реплики."
    )


def _conversation_happiness_rule() -> str:
    return (
        "Оцени, как ТЕКУЩЕЕ сообщение пользователя обращено к персонажу, и заполни "
        "happinessDelta. Игнорируй цитаты, пересказ, ролевую сцену, вопросы об угрозах и "
        "предыдущую историю: они сами по себе нейтральны. Добрые слова, забота, поддержка, "
        "благодарность или искренний комплимент: 20. Обычная нейтральная беседа: 0. "
        "Оскорбление, грубость или недоброжелательность: -20. Усиленное унижение или "
        "жестокое пожелание вреда: -40. Явная угроза причинить серьёзный вред: -60. "
        "Прямая угроза убийством, пыткой или уничтожением персонажа: -80. "
        "Выбирай только одно из значений 20, 0, -20, -40, -60, -80."
    )


def _normalize_structured_hint(value: Any, allowed: tuple[str, ...]) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _compact_spaces(value).casefold()
    return normalized if normalized in allowed else None


def _normalized_visible_reply(value: Any, reply_limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = _compact_spaces(value)
    if not text:
        return None
    upper_text = text.upper()
    if (
        text.startswith(("{", "[", "```"))
        or "THOUGHT:" in upper_text
        or "FACE:" in upper_text
        or "MOOD:" in upper_text
    ):
        return None
    return clamp_reply_text(text, reply_limit)


def _fallback_visible_reply_result(
    *,
    surface: PhraseSurface,
    reply_limit: int,
    raw_reply: str,
    validation_flags: list[str],
    parsed: dict[str, Any] | None = None,
) -> VisibleReplyResult:
    fallback = clamp_reply_text(VISIBLE_REPLY_FALLBACKS[surface], reply_limit)
    debug = {
        "rawResponse": _truncate_text(raw_reply or "", 1000),
        "parsedResponse": parsed,
        "normalizedResponse": {
            "reply": fallback,
            "faceHint": None,
            "moodHint": None,
            "happinessDelta": 0,
        },
        "usedFallback": True,
        "validationFlags": validation_flags,
    }
    return VisibleReplyResult(
        reply=fallback,
        mood_hint=None,
        face_hint=None,
        happiness_delta=0,
        used_fallback=True,
        validation_flags=validation_flags,
        debug=debug,
    )


def _parse_visible_reply_response(
    raw_reply: str,
    *,
    surface: PhraseSurface,
    reply_limit: int,
) -> VisibleReplyResult:
    validation_flags: list[str] = []
    parsed = _json_record_from_text(raw_reply)
    if not parsed:
        return _fallback_visible_reply_result(
            surface=surface,
            reply_limit=reply_limit,
            raw_reply=raw_reply,
            validation_flags=["structured_reply_invalid_json"],
        )

    reply = _normalized_visible_reply(parsed.get("reply"), reply_limit)
    if not reply:
        validation_flags.append("structured_reply_invalid_reply")

    face_hint = _normalize_structured_hint(parsed.get("faceHint"), FACE_HINTS)
    if parsed.get("faceHint") is not None and face_hint is None:
        validation_flags.append("structured_reply_invalid_face_hint")

    mood_hint = _normalize_structured_hint(parsed.get("moodHint"), MOOD_HINTS)
    if parsed.get("moodHint") is not None and mood_hint is None:
        validation_flags.append("structured_reply_invalid_mood_hint")

    happiness_delta = parsed.get("happinessDelta", 0)
    if happiness_delta not in HAPPINESS_DELTA_VALUES:
        happiness_delta = 0
        validation_flags.append("structured_reply_invalid_happiness_delta")

    if not reply:
        return _fallback_visible_reply_result(
            surface=surface,
            reply_limit=reply_limit,
            raw_reply=raw_reply,
            validation_flags=validation_flags,
            parsed=parsed,
        )

    debug = {
        "rawResponse": _truncate_text(raw_reply or "", 1000),
        "parsedResponse": parsed,
        "normalizedResponse": {
            "reply": reply,
            "faceHint": face_hint,
            "moodHint": mood_hint,
            "happinessDelta": happiness_delta,
        },
        "usedFallback": False,
        "validationFlags": validation_flags,
    }
    return VisibleReplyResult(
        reply=reply,
        mood_hint=mood_hint,
        face_hint=face_hint,
        happiness_delta=happiness_delta,
        used_fallback=False,
        validation_flags=validation_flags,
        debug=debug,
    )


_CASUAL_CHARACTER_SMALL_TALK_RE = re.compile(
    r"(^|\b)(как дела|как ты|ты как|как сам|как сама|как жизнь|как настроение|"
    r"что нового|чем занимаешься|что делаешь)(\b|[?!.,;:]*$)",
    re.IGNORECASE,
)
_CHARACTER_DETAIL_REQUEST_RE = re.compile(
    r"(кто ты|ты кто|что ты такое|како[йеая] ты|расскажи о себе|"
    r"внешн|выгляд|тело|уш|глаз|лап|хвост|крыл|рог|зуб|шерст|чешу|"
    r"привыч|любим|боишь|умеешь|способност|характер|дом|жив[её]шь|"
    r"прошл|истори|пита|\bешь\b|ед[ауеы]|батар|нюх|вывес)",
    re.IGNORECASE,
)
_CASUAL_CHARACTER_SUPPRESSED_SOURCES = frozenset({"characterProfile", "liteOverlay", "chatHistory"})


def _is_casual_character_small_talk(message: str) -> bool:
    text = _compact_spaces(message).casefold()
    if not text or len(text) > 120:
        return False
    if _CHARACTER_DETAIL_REQUEST_RE.search(text):
        return False
    return bool(_CASUAL_CHARACTER_SMALL_TALK_RE.search(text))


def _context_plan_without_sources(
    plan: ContextPlan,
    sources: frozenset[str],
    *,
    reason: str,
) -> ContextPlan:
    suppressed = sorted(plan.included_sources.intersection(sources))
    if not suppressed:
        return plan
    included_sources = frozenset(
        source for source in plan.included_sources if source not in sources
    )
    queries = {
        key: value
        for key, value in plan.queries.items()
        if key not in _CASUAL_CHARACTER_SUPPRESSED_SOURCES
    }
    debug = deepcopy(plan.debug)
    debug["includedSources"] = sorted(included_sources)
    debug["suppressedSources"] = sorted(set(debug.get("suppressedSources", [])) | set(suppressed))
    debug["suppressionReason"] = reason
    return ContextPlan(
        surface=plan.surface,
        modes=plan.modes,
        router_decision=plan.router_decision,
        included_sources=included_sources,
        queries=queries,
        debug=debug,
    )


def _apply_chat_casual_context_guard(
    payload: LocalChatRequest,
    plan: ContextPlan,
) -> ContextPlan:
    if not _is_casual_character_small_talk(payload.message):
        return plan
    return _context_plan_without_sources(
        plan,
        _CASUAL_CHARACTER_SUPPRESSED_SOURCES,
        reason="generic_chat_small_talk_does_not_need_character_profile",
    )


def _memory_context_block(memory_context: LocalPetMemoryContext | None) -> str | None:
    if not memory_context:
        return None

    lines: list[str] = []
    user_profile = _clean_optional_text(memory_context.userProfile, 500)
    if user_profile:
        lines.append(f"Профиль владельца: {user_profile}")
    summary = _clean_optional_text(memory_context.summary, 500)
    if summary:
        lines.append(f"Краткая память общения: {summary}")
    memory_lines: list[str] = []
    for item in memory_context.relevantMemories[:MAX_MEMORY_CONTEXT_ITEMS]:
        text = _clean_optional_text(item.text, 300)
        if text:
            memory_lines.append(f"- [{item.kind}] {text}")
    if memory_lines:
        lines.append("Выбранные факты памяти:")
        lines.extend(memory_lines)

    for episode_index, episode in enumerate(memory_context.episodes[:MAX_MEMORY_CONTEXT_ITEMS], 1):
        episode_lines: list[str] = []
        for message in episode.messages:
            text = _clean_optional_text(message.text, 500)
            if text:
                role = "персонаж" if message.role == "pet" else "владелец"
                episode_lines.append(f"{role}: {text}")
        if episode_lines:
            lines.append(f"Память диалога {episode_index}:")
            lines.extend(episode_lines)

    if not lines:
        return None
    return "\n".join(lines) + f"\n{memory_usage_rule()}"


def _visible_context_block(payload: LocalChatRequest) -> str | None:
    visible_context = payload.visibleContext
    if not visible_context:
        return None
    last_pet_line = _clean_optional_text(visible_context.lastPetLine, 500)
    if not last_pet_line:
        return None
    return (
        "Последняя видимая реплика персонажа:\n"
        f"{last_pet_line}\n"
        "Это только ближайший видимый контекст для текущего ответа."
    )


def _ambient_memory_context_block(memory_context: LocalPetMemoryContext | None) -> str | None:
    if not memory_context:
        return None
    lines = [
        f"- [{item.kind}] {text}"
        for item in memory_context.relevantMemories[:MAX_MEMORY_CONTEXT_ITEMS]
        if item.kind in AMBIENT_MEMORY_KINDS and (text := _clean_optional_text(item.text, 300))
    ]
    if not lines:
        return None
    return "Мягкая память о собеседнике:\n" + "\n".join(lines) + f"\n{memory_usage_rule()}"


def _ambient_recent_conversation_block(history: list[Any]) -> str | None:
    lines: list[str] = []
    for message in history[-4:]:
        text = _clean_optional_text(getattr(message, "text", None), 300)
        if not text:
            continue
        role = "персонаж" if getattr(message, "role", None) == "pet" else "владелец"
        lines.append(f"{role}: {text}")
    return "Недавний разговор:\n" + "\n".join(lines) if lines else None


def _recent_reply_lines(replies: list[str], *, limit: int, item_limit: int) -> list[str]:
    lines: list[str] = []
    for reply in replies[-limit:]:
        text = _clean_optional_text(reply, item_limit)
        if text:
            lines.append(text)
    return lines


def _anti_repeat_block(lines: list[str]) -> str | None:
    if not lines:
        return None
    token_counts: dict[str, int] = {}
    ignored = {
        "который",
        "которая",
        "которые",
        "сейчас",
        "только",
        "очень",
        "тебя",
        "тебе",
        "меня",
        "рядом",
    }
    for line in lines:
        for token in re.findall(r"[А-Яа-яЁё-]{5,}", line.casefold()):
            if token in ignored:
                continue
            token = next(
                (
                    token[: -len(suffix)]
                    for suffix in RUSSIAN_REPEAT_SUFFIXES
                    if token.endswith(suffix) and len(token) - len(suffix) >= 4
                ),
                token,
            )
            token_counts[token] = token_counts.get(token, 0) + 1
    repeated_markers = [
        token
        for token, count in sorted(token_counts.items(), key=lambda item: (-item[1], item[0]))
        if count >= 2
    ][:10]
    marker_rule = (
        "\nПовторяющиеся смысловые маркеры: "
        + ", ".join(repeated_markers)
        + ". Не строй новую реплику вокруг них."
        if repeated_markers
        else ""
    )
    return (
        "Недавние реплики персонажа, уже показанные владельцу:\n"
        + "\n".join(f"- {line}" for line in lines)
        + "\nИзбегай не только дословного повтора, но и той же стартовой конструкции, "
        "действия, предмета, метафоры и повода заговорить. Это не источник фактов." + marker_rule
    )


def _recent_ambient_replies_block(replies: list[str]) -> str | None:
    lines = _recent_reply_lines(
        replies,
        limit=MAX_RECENT_AMBIENT_REPLIES,
        item_limit=180,
    )
    return _anti_repeat_block(lines)


def _recent_ambient_reply_debug_lines(replies: list[str]) -> list[str]:
    return _recent_reply_lines(
        replies,
        limit=MAX_RECENT_AMBIENT_REPLIES,
        item_limit=180,
    )


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
    for source in CONTEXT_ROUTING_SOURCE_IDS:
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
        "toneProfile": tone_context_payload("contextRouting"),
        "userMessage": getattr(payload, "message", ""),
        "lastVisiblePetLine": (
            payload.visibleContext.lastPetLine
            if isinstance(payload, LocalChatRequest) and payload.visibleContext
            else None
        ),
        "proactiveReason": proactive_reason,
        "pet": _lite_pet_context_payload(payload),
        "sources": context_routing_sources(),
        "memoryBrief": {
            "episodes": [
                {
                    "id": episode.id,
                    "messages": [
                        {
                            "role": message.role,
                            "text": message.text,
                        }
                        for message in episode.messages
                    ],
                }
                for episode in (memory_context.episodes if memory_context else [])
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


def _visible_context_plan_auto_defaults(surface: PhraseSurface) -> frozenset[str]:
    if surface == "chat":
        return frozenset({"userMemory", "chatHistory"})
    if surface == "proactive":
        return frozenset({"userMemory"})
    if surface == "ambient":
        return frozenset({"userMemory", "chatHistory", "recentReplies"})
    return frozenset()


def _context_plan_from_routing(
    *,
    surface: PhraseSurface,
    routing: ContextRoutingDecision | None,
) -> ContextPlan:
    return build_context_plan(
        surface=surface,
        modes=context_source_modes(surface),
        routing=routing,
        source_enabled=context_source_enabled,
        auto_default_sources=_visible_context_plan_auto_defaults(surface),
    )


def _plan_contexts_for_visible_reply(
    *,
    surface: PhraseSurface,
    payload: Any,
    client: Any,
    model: str,
    timeout: float,
) -> tuple[ContextPlan, dict[str, Any] | None]:
    modes = context_source_modes(surface)
    auto_router_sources = router_sources_for_auto_modes(modes)
    if not auto_router_sources:
        return (
            build_context_plan(
                surface=surface,
                modes=modes,
                routing=ContextRoutingDecision(
                    surface=surface,
                    reason="no_auto_context_sources",
                    raw={"skipped": True, "sourceModes": modes},
                ),
                source_enabled=context_source_enabled,
                auto_default_sources=_visible_context_plan_auto_defaults(surface),
            ),
            None,
        )

    deterministic_sources = {"userMemory", "chatHistory", "recentReplies"}
    if auto_router_sources.issubset(deterministic_sources):
        enabled_sources: set[str] = set()
        memory_context = getattr(payload, "memoryContext", None)
        if (
            "userMemory" in auto_router_sources
            and memory_context
            and any(
                (
                    memory_context.summary,
                    memory_context.userProfile,
                    memory_context.relevantMemories,
                    memory_context.episodes,
                    memory_context.proactiveCandidate,
                )
            )
        ):
            enabled_sources.add("userMemory")
        if "chatHistory" in auto_router_sources and getattr(payload, "history", None):
            enabled_sources.add("chatHistory")
        if "recentReplies" in auto_router_sources and getattr(
            payload, "recentAmbientReplies", None
        ):
            enabled_sources.add("recentReplies")
        routing = ContextRoutingDecision(
            surface=surface,
            enabled_sources=frozenset(enabled_sources),
            reason="deterministic_context_routing",
            raw={
                "skipped": True,
                "autoSources": sorted(auto_router_sources),
                "enabledSources": sorted(enabled_sources),
            },
        )
        return (
            build_context_plan(
                surface=surface,
                modes=modes,
                routing=routing,
                source_enabled=context_source_enabled,
                auto_default_sources=_visible_context_plan_auto_defaults(surface),
            ),
            None,
        )

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
    routing = _parse_context_routing_decision(surface=surface, raw_content=content)
    context_plan = build_context_plan(
        surface=surface,
        modes=modes,
        routing=routing,
        source_enabled=context_source_enabled,
        auto_default_sources=_visible_context_plan_auto_defaults(surface),
    )
    if surface == "chat" and isinstance(payload, LocalChatRequest):
        context_plan = _apply_chat_casual_context_guard(payload, context_plan)
    return (context_plan, prompt_debug)


def _source_enabled(
    surface: PhraseSurface,
    source: str,
    routing: ContextRoutingDecision | ContextPlan | None,
    *,
    router_source: str | None = None,
    auto_default: bool = False,
) -> bool:
    if isinstance(routing, ContextPlan):
        return routing.includes(source)
    router_enabled = None
    if router_source and routing is not None:
        router_enabled = routing.enabled(router_source)
    return context_source_enabled(
        surface,
        source,
        router_enabled=router_enabled,
        auto_default=auto_default,
    )


def _character_context_block(
    pet: Any,
    surface: PhraseSurface,
    routing: ContextRoutingDecision | ContextPlan | None,
) -> str | None:
    include_profile = _source_enabled(
        surface,
        "characterProfile",
        routing,
        router_source="characterProfile",
    )
    if not include_profile:
        return None
    bible = _sanitized_character_bible(pet.characterBible)
    if not bible:
        return None
    return "CHARACTER_PROFILE:\n" + _safe_json_context({"characterBible": bible}, 3000)


def _character_block_for_surface(
    pet: Any,
    surface: PhraseSurface,
    routing: ContextRoutingDecision | ContextPlan | None,
) -> str | None:
    return _combine_character_blocks(
        _character_capsule_block(
            pet,
            include_durable_facts=surface != "chat",
        ),
        _character_context_block(pet, surface, routing),
    )


def _relevant_lite_overlay_block(pet: Any, query: str, *, limit: int = 3) -> str | None:
    bible = pet.characterBible if _is_record(getattr(pet, "characterBible", None)) else {}
    extensions = bible.get("extensions") if _is_record(bible.get("extensions")) else {}
    overlay = extensions.get("lite_overlay") if _is_record(extensions) else {}
    if not _is_record(overlay):
        return None

    raw_facts: list[Any] = []
    if isinstance(overlay.get("facts"), list):
        raw_facts.extend(overlay["facts"])
    spheres = overlay.get("spheres") if _is_record(overlay.get("spheres")) else {}
    for sphere in spheres.values():
        if _is_record(sphere) and isinstance(sphere.get("facts"), list):
            raw_facts.extend(sphere["facts"])

    query_tokens = _recent_event_tokens(query)
    if not query_tokens:
        return None
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for raw_fact in raw_facts:
        if not _is_record(raw_fact):
            continue
        text = _clean_optional_text(raw_fact.get("text"), 360)
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        fact_tokens = _recent_event_tokens(text)
        overlap = len(query_tokens & fact_tokens)
        if not overlap:
            overlap = sum(
                1
                for query_token in query_tokens
                if len(query_token) >= 5
                and any(
                    fact_token.startswith(query_token[:5]) or query_token.startswith(fact_token[:5])
                    for fact_token in fact_tokens
                    if len(fact_token) >= 5
                )
            )
        if overlap:
            scored.append((overlap, text))
    if not scored:
        return None
    selected = [text for _score, text in sorted(scored, reverse=True)[:limit]]
    return "Релевантные устойчивые факты персонажа:\n" + "\n".join(f"- {text}" for text in selected)


def _record_at(value: Any, key: str) -> dict[str, Any]:
    if not _is_record(value):
        return {}
    child = value.get(key)
    return child if _is_record(child) else {}


def _text_list(value: Any, *, limit: int = 6, item_limit: int = 180) -> list[str]:
    if isinstance(value, str):
        text = _clean_optional_text(value, item_limit)
        return [text] if text else []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _clean_optional_text(str(item), item_limit) if item is not None else None
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _join_labeled_line(label: str, *values: Any, limit: int = 320) -> str | None:
    parts: list[str] = []
    for value in values:
        text = _clean_optional_text(str(value), limit) if value is not None else None
        if text and text not in parts:
            parts.append(text)
    if not parts:
        return None
    return f"{label}: {'; '.join(parts)}"


def _character_capsule_block(
    pet: Any,
    *,
    include_durable_facts: bool = True,
) -> str | None:
    capsule = build_character_capsule(
        pet,
        include_durable_facts=include_durable_facts,
    )
    if not capsule:
        return None
    return capsule


def _combine_character_blocks(*blocks: str | None) -> str | None:
    values = [block for block in blocks if block]
    return "\n\n".join(values) if values else None


def _visible_world_block(context_bundle: AssembledPetContext) -> str:
    values = [dialogue_vocabulary_block(), context_bundle.prompt_block]
    return "\n\n".join(value for value in values if value)


def _story_context_for_routing(
    *,
    surface: PhraseSurface,
    payload: Any,
    context_routing: ContextRoutingDecision | ContextPlan,
) -> AssembledPetContext:
    context_mode = "proactive" if surface == "push" else surface
    include_story_library = _source_enabled(
        surface,
        "storyLibrary",
        context_routing,
        router_source="worldContext",
    )
    include_story_overlay = False
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
        force_context=include_story_library,
        forced_query=context_routing.query("worldContext"),
        forced_reason="context_routing",
        routing_applied=True,
        include_story_library=include_story_library,
        include_story_overlay=include_story_overlay,
    )


def _reply_identity_label(pet: Any) -> str:
    name = _compact_spaces(pet.name or "")
    description = _compact_spaces(getattr(pet, "description", "") or "")
    return name or description or speech_template("unnamedPet")


def _short_character_description(payload: LocalChatRequest) -> str:
    return _reply_identity_label(payload.pet)


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


def _lite_tools_for_payload(
    payload: LocalChatRequest,
    context_routing: ContextRoutingDecision | ContextPlan,
) -> list[dict[str, Any]] | None:
    tools = list(PET_STATE_TOOLS) if PET_RENAME_INTENT_RE.search(payload.message) else []
    if _source_enabled(
        "chat",
        "characterProfile",
        context_routing,
        router_source="characterProfile",
    ):
        tools.extend(LITE_CHARACTER_TOOLS)
    return tools or None


def _history_items_for_prompt(payload: LocalChatRequest) -> list[Any]:
    return payload.history[-MAX_RECENT_HISTORY_MESSAGES:]


def _history_messages(payload: LocalChatRequest) -> list[dict[str, str]]:
    return [
        {
            "role": "assistant" if item.role == "pet" else "user",
            "content": item.text,
        }
        for item in _history_items_for_prompt(payload)
    ]


BABY_DESCRIPTION_PREFIX_RE = re.compile(
    r"^\s*(маленьк\w*|малыш\w*|дет[её]ныш\w*)\b",
    re.IGNORECASE,
)


def _baby_identity_description(description: str) -> str:
    text = _compact_spaces(description)
    if not text or BABY_DESCRIPTION_PREFIX_RE.search(text):
        return text
    first_word_match = re.match(r"[\wёЁ-]+", text)
    first_word = first_word_match.group(0).casefold() if first_word_match else ""
    if first_word.endswith(("а", "я")):
        prefix = "маленькая"
    elif first_word.endswith(("о", "е")):
        prefix = "маленькое"
    else:
        prefix = "маленький"
    return f"{prefix} {text}"


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
        if pet.stage == "baby":
            description = _baby_identity_description(description)
        else:
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
    context_routing: ContextRoutingDecision | ContextPlan | None = None,
    recent_events_block: str | None = None,
) -> PhrasePlan:
    reply_limit = visible_reply_limit(payload.replyMaxChars)
    return PhrasePlan(
        surface="chat",
        reply_limit=reply_limit,
        identity_line=_chat_identity_line(payload, reply_limit),
        persona_contract=surface_prompt("chat"),
        tone_block=None,
        character_block=_combine_character_blocks(
            _character_block_for_surface(payload.pet, "chat", context_routing),
            (
                _relevant_lite_overlay_block(payload.pet, payload.message)
                if _source_enabled(
                    "chat",
                    "liteOverlay",
                    context_routing,
                    router_source="characterProfile",
                )
                else None
            ),
        ),
        dialogue_block=_visible_context_block(payload),
        memory_block=(
            _memory_context_block(payload.memoryContext)
            if _source_enabled(
                "chat",
                "userMemory",
                context_routing,
                router_source="userMemory",
                auto_default=True,
            )
            else None
        ),
        recent_events_block=recent_events_block,
        world_block=_visible_world_block(context_bundle),
        recent_ambient_block=None,
        extra_rules=(
            state_param_usage_rule(),
            _recent_event_truth_rule(payload, recent_events_block),
            transient_context_rule(),
            _structured_reply_contract_rule(),
            _conversation_happiness_rule(),
        ),
    )


def build_lite_chat_messages(
    payload: LocalChatRequest,
    *,
    context_bundle: AssembledPetContext | None = None,
    context_plan: ContextPlan | None = None,
    context_routing: ContextRoutingDecision | ContextPlan | None = None,
    recent_events_block: str | None = None,
) -> list[dict[str, str]]:
    context_plan = context_plan or _context_plan_from_routing(
        surface="chat",
        routing=context_routing,
    )
    context_plan = _apply_chat_casual_context_guard(payload, context_plan)
    if context_bundle is None:
        context_bundle = _story_context_for_routing(
            surface="chat",
            payload=payload,
            context_routing=context_plan,
        )
    if recent_events_block is None:
        recent_events_block, _recent_events_debug = _recent_events_context_for_chat(payload)
    plan = _phrase_plan_for_chat(
        payload,
        context_bundle=context_bundle,
        context_routing=context_plan,
        recent_events_block=recent_events_block,
    )
    system_content = plan.system_content()
    history_messages = _history_messages(payload) if context_plan.includes("chatHistory") else []

    return [
        {
            "role": "system",
            "content": system_content,
        },
        *history_messages,
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


def _sanitized_character_bible(value: Any) -> dict[str, Any]:
    bible = deepcopy(value) if _is_record(value) else {}
    bible = _sanitize_technical_world_text(bible)
    extensions = bible.get("extensions") if _is_record(bible.get("extensions")) else None
    if isinstance(extensions, dict):
        extensions.pop("story_library_overlay", None)
        extensions.pop("recent_story_events", None)
        if not extensions:
            bible.pop("extensions", None)
    return bible


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
            "content": (
                f"{world_seed_system_prompt()}\n\n"
                f"{lore_prompt_block('worldSeed')}\n\n"
                f"{tone_prompt_block('worldContext')}"
            ),
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
    patch = overlay_patch_from_extracted_facts([raw_fact])
    if not patch:
        return None
    patch["worldSeed"] = {
        "source": "chatgpt",
        "createdAt": _now_iso(),
    }
    return patch


def _lite_character_bible_for_read(
    payload: LocalChatRequest,
    world_seed_patch: dict[str, Any] | None,
) -> dict[str, Any]:
    bible = _sanitized_character_bible(payload.pet.characterBible)
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
    context_routing: ContextRoutingDecision | ContextPlan | None = None,
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
        context_routing
        and _source_enabled(
            "chat",
            "storyLibrary",
            context_routing,
            router_source="worldContext",
        )
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
        merge_lite_overlay_patch(overlay_patch, world_seed_patch)

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
            merge_lite_overlay_patch(overlay, world_seed_patch)
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


def _parse_lite_fact_extraction_payload(raw_content: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw_content or "{}")
    except json.JSONDecodeError:
        return None
    if not _is_record(parsed):
        return None
    raw_facts = parsed.get("facts") if isinstance(parsed.get("facts"), list) else []
    confirmed_facts = [
        fact
        for fact in raw_facts
        if _is_record(fact) and str(fact.get("source") or "").strip() == "user_confirmed"
    ]
    return overlay_patch_from_extracted_facts(confirmed_facts)


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


def _recent_events_context_for_lite_extraction(
    payload: LiteFactExtractionRequest,
) -> tuple[str | None, dict[str, Any]]:
    events = _recent_story_events_from_pet(payload.pet)
    selected, debug = _select_recent_events_for_text(
        events=events,
        text=f"{payload.message}\n{payload.reply}",
        mode=context_source_mode("chat", "recentEvents"),
    )
    return _format_recent_events_block(selected), debug


def build_lite_fact_extraction_messages(
    payload: LiteFactExtractionRequest,
) -> list[dict[str, str]]:
    recent_events_block, _recent_events_debug = _recent_events_context_for_lite_extraction(payload)
    system_content = (
        character_fact_extraction_system_prompt()
        + "\n\nRecent event canonical facts have priority over the assistant reply. "
        "Treat one visible assistant reply as weak evidence, not automatic canon."
    )
    user_content = speech_template(
        "liteFactExtractionUserMessage",
        {
            "character_context": _lite_extraction_context(payload),
            "message": payload.message,
            "reply": payload.reply,
        },
    )
    if recent_events_block:
        user_content = f"{user_content}\n\n{recent_events_block}"
    return [
        {
            "role": "system",
            "content": system_content,
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]


LITE_FACT_RECOVERY_RE = re.compile(
    r"\b("
    r"защитил\w*|защищ[ае]\w*|вернул\w*|верну\w*|наш[её]л\w*|"
    r"нашл\w*|сохранил\w*|спас\w*)\b",
    re.IGNORECASE,
)
LITE_FACT_UNRESOLVED_RE = re.compile(
    r"\b(не\s+смог\w*\s+верну\w*|не\s+вернул\w*|не\s+защитил\w*|"
    r"потерял\w*|потерян\w*|украл\w*|украден\w*|утащил\w*|lost)\b",
    re.IGNORECASE,
)
LITE_FACT_NEW_CANON_RE = re.compile(
    r"\b("
    r"уме[ею]т\w*|умею|может|могу|способн\w*|зна[ею]т\w*|знаю|"
    r"владе[ею]т\w*|владею|маг\w*|ритуал\w*|заклин\w*|"
    r"призыва\w*|созда[её]т\w*|лечит\w*|видит\w*|предсказыва\w*|"
    r"учил\w*|школ\w*|гильди\w*|титул\w*|професси\w*|мастер\w*"
    r")\b",
    re.IGNORECASE,
)


def _event_object_tokens(event: dict[str, Any]) -> set[str]:
    parts: list[str] = []
    for key in ("objects", "participants"):
        value = event.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if isinstance(item, (str, int, float)))
    for key in ("summary", "compactText", "outcome"):
        value = event.get(key)
        if isinstance(value, str):
            parts.append(value)
    canonical_facts = event.get("canonicalFacts")
    if isinstance(canonical_facts, list):
        parts.extend(str(item) for item in canonical_facts if isinstance(item, str))
    status_changes = event.get("statusChanges")
    if isinstance(status_changes, list):
        for item in status_changes:
            if _is_record(item):
                parts.append(str(item.get("entity") or ""))
    return set().union(*(_recent_event_tokens(part) for part in parts)) if parts else set()


def _event_canonical_text(event: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("summary", "compactText", "outcome"):
        value = event.get(key)
        if isinstance(value, str):
            parts.append(value)
    canonical_facts = event.get("canonicalFacts")
    if isinstance(canonical_facts, list):
        parts.extend(str(item) for item in canonical_facts if isinstance(item, str))
    status_changes = event.get("statusChanges")
    if isinstance(status_changes, list):
        for item in status_changes:
            if _is_record(item):
                parts.extend(str(item.get(key) or "") for key in ("entity", "state", "owner"))
    return "\n".join(parts)


def _lite_fact_conflict_reason(
    fact: dict[str, Any],
    event: dict[str, Any],
) -> str | None:
    fact_text = _text_value(fact.get("text"))
    if not fact_text:
        return None
    fact_tokens = _recent_event_tokens(fact_text)
    event_tokens = _event_object_tokens(event)
    if event_tokens and not (fact_tokens & event_tokens):
        return None
    canonical_text = _event_canonical_text(event)
    if not LITE_FACT_UNRESOLVED_RE.search(canonical_text):
        return None
    if LITE_FACT_RECOVERY_RE.search(fact_text) and not LITE_FACT_UNRESOLVED_RE.search(fact_text):
        return "recovery_fact_contradicts_unresolved_recent_event"
    return None


def _recent_event_truth_rule(
    payload: LocalChatRequest,
    recent_events_block: str | None,
) -> str | None:
    if not RECENT_EVENT_QUESTION_RE.search(payload.message):
        return None
    if recent_events_block:
        return (
            "На вопрос о недавнем опирайся только на блок недавних событий; "
            "не добавляй ещё одно свершившееся событие."
        )
    return (
        "В доступной памяти нет подтверждённого недавнего события. Не выдумывай "
        "находку, встречу или приключение как уже случившийся факт; честно опиши "
        "текущее состояние или маленький момент прямо сейчас."
    )


def _filter_lite_overlay_patch_against_recent_events(
    patch: dict[str, Any] | None,
    events: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    if not patch or not isinstance(patch.get("facts"), list) or not events:
        return patch, []
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for fact in patch["facts"]:
        if not _is_record(fact):
            continue
        conflict: dict[str, str] | None = None
        for index, event in enumerate(events):
            reason = _lite_fact_conflict_reason(fact, event)
            if reason:
                conflict = {
                    "factText": _text_value(fact.get("text")),
                    "conflictReason": reason,
                    "conflictingEventId": _recent_event_id(event, index),
                }
                break
        if conflict:
            skipped.append(conflict)
            continue
        kept.append(fact)
    return overlay_patch_from_extracted_facts(kept), skipped


def _character_capsule_support_tokens(pet: Any) -> set[str]:
    bible = _sanitized_character_bible(getattr(pet, "characterBible", None))
    identity = _record_at(bible, "identity")
    genesis = _record_at(bible, "genesis")
    roleplay_contract = _record_at(bible, "roleplay_contract")
    support_parts: list[str] = [
        str(getattr(pet, "description", "") or ""),
        *[str(identity.get(key) or "") for key in ("species", "role", "one_liner")],
        *[
            str(genesis.get(key) or "")
            for key in (
                "core_reading",
                "description",
                "central_trait",
                "character_trait",
                "inner_conflict",
                "conflict",
                "appetite",
                "safe_adaptation",
                "pet_safe_adaptation",
                "story_engine",
                "daily_life_hook",
                "daily_care_hook",
            )
        ],
        *[str(item) for item in genesis.get("likes", []) if isinstance(item, str)],
        *[str(item) for item in genesis.get("does", []) if isinstance(item, str)],
        *[str(item) for item in genesis.get("causal_spine", []) if isinstance(item, str)],
        *[
            str(roleplay_contract.get(key) or "")
            for key in (
                "self_intro",
                "how_to_answer_who_are_you",
                "how_to_answer_what_do_you_eat",
                "how_to_answer_where_do_you_live",
            )
        ],
    ]
    return set().union(*(_recent_event_tokens(part) for part in support_parts if part))


def _filter_lite_overlay_patch_against_character_capsule(
    patch: dict[str, Any] | None,
    pet: Any,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    if not patch or not isinstance(patch.get("facts"), list):
        return patch, []
    support_tokens = _character_capsule_support_tokens(pet)
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for fact in patch["facts"]:
        if not _is_record(fact):
            continue
        fact_text = _text_value(fact.get("text"))
        sphere = _text_value(fact.get("sphere"))
        if (
            sphere in {"character", "world"}
            and LITE_FACT_NEW_CANON_RE.search(fact_text)
            and len(_recent_event_tokens(fact_text) & support_tokens) < 2
        ):
            skipped.append(
                {
                    "factText": fact_text,
                    "conflictReason": "new_canon_not_supported_by_character_capsule",
                }
            )
            continue
        kept.append(fact)
    return overlay_patch_from_extracted_facts(kept), skipped


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
    context_routing: ContextRoutingDecision | ContextPlan,
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


def generate_lite_pet_reply(
    payload: LocalChatRequest,
    *,
    client: Any | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> LocalChatResponse:
    settings = get_settings()
    model = model or visible_reply_model()
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    context_plan, context_routing_prompt_debug = _plan_contexts_for_visible_reply(
        surface="chat",
        payload=payload,
        client=openai_client,
        model=model,
        timeout=timeout,
    )
    context_bundle = _story_context_for_routing(
        surface="chat",
        payload=payload,
        context_routing=context_plan,
    )
    recent_events_block, recent_events_debug = _recent_events_context_for_chat(payload)
    messages: list[dict[str, Any]] = build_lite_chat_messages(
        payload,
        context_bundle=context_bundle,
        context_plan=context_plan,
        recent_events_block=recent_events_block,
    )
    tools = _lite_tools_for_payload(payload, context_plan)
    overlay_patch: dict[str, Any] = {}
    pet_patch: dict[str, Any] = {}
    tool_debug: list[dict[str, Any]] = []
    prompt_debug: list[dict[str, Any]] = [
        item for item in (context_routing_prompt_debug,) if item is not None
    ]
    reply = ""
    mood_hint: str | None = None
    face_hint: str | None = None
    happiness_delta = 0
    structured_reply_debug: dict[str, Any] | None = None
    structured_reply_used_fallback = False
    structured_reply_validation_flags: list[str] = []
    reply_limit = visible_reply_limit(payload.replyMaxChars)

    for round_index in range(MAX_LITE_TOOL_ROUNDS + 1):
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "response_format": _visible_reply_response_format(
                reply_limit,
                include_happiness_delta=True,
            ),
            "timeout": timeout,
            **chat_reasoning_effort_kwargs("none" if tools else visible_reply_reasoning_effort()),
        }
        if tools:
            request_kwargs["tools"] = tools
            request_kwargs["tool_choice"] = "auto"

        prompt_debug.append(
            log_chat_completion_prompt(f"pet_reply/lite round {round_index + 1}", request_kwargs)
        )
        completion = openai_client.chat.completions.create(**request_kwargs)
        log_chat_completion_response(f"pet_reply/lite round {round_index + 1}", completion)
        message = completion.choices[0].message
        tool_calls = list(getattr(message, "tool_calls", None) or [])
        if not tools or not tool_calls:
            structured_reply = _parse_visible_reply_response(
                getattr(message, "content", None) or "",
                surface="chat",
                reply_limit=reply_limit,
            )
            reply = structured_reply.reply
            mood_hint = structured_reply.mood_hint
            face_hint = structured_reply.face_hint
            happiness_delta = structured_reply.happiness_delta
            structured_reply_debug = structured_reply.debug
            structured_reply_used_fallback = structured_reply.used_fallback
            structured_reply_validation_flags = structured_reply.validation_flags
            break

        messages.append(_assistant_tool_call_message(message, tool_calls))
        for tool_call in tool_calls:
            result, debug = _handle_tool_call(
                payload,
                tool_call,
                overlay_patch,
                pet_patch,
                context_routing=context_plan,
                client=openai_client,
                model=model,
                timeout=timeout,
                prompt_debug=prompt_debug,
            )
            tool_debug.append(debug)
            messages.append(_tool_response_message(_tool_call_id(tool_call), result))

    if structured_reply_debug is None:
        structured_reply = _fallback_visible_reply_result(
            surface="chat",
            reply_limit=reply_limit,
            raw_reply="",
            validation_flags=["structured_reply_missing_final_response"],
        )
        reply = structured_reply.reply
        mood_hint = structured_reply.mood_hint
        face_hint = structured_reply.face_hint
        happiness_delta = structured_reply.happiness_delta
        structured_reply_debug = structured_reply.debug
        structured_reply_used_fallback = structured_reply.used_fallback
        structured_reply_validation_flags = structured_reply.validation_flags

    included_sources = set(context_plan.debug.get("includedSources", []))
    if recent_events_debug.get("includedEventIds"):
        included_sources.add("recentEvents")
    context_debug = {
        **context_plan.debug,
        "includedSources": sorted(included_sources),
        "recentEvents": recent_events_debug,
        "antiRepeatLines": [],
    }
    debug = None
    if payload.includeDebug:
        debug = LocalChatDebug(
            usedFallback=structured_reply_used_fallback,
            validationFlags=structured_reply_validation_flags,
            promptDebug=prompt_debug,
            structuredReplyDebug=structured_reply_debug,
            liteToolCalls=tool_debug,
            liteOverlayPatch=overlay_patch or None,
            storyLibraryDebug=_story_context_debug(context_bundle),
            contextRoutingDebug=context_debug,
        )
    return LocalChatResponse(
        reply=reply,
        moodHint=mood_hint,
        happinessDelta=happiness_delta,
        innerThought=None,
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
    selected_events, recent_events_debug = _select_recent_events_for_text(
        events=_recent_story_events_from_pet(payload.pet),
        text=f"{payload.message}\n{payload.reply}",
        mode=context_source_mode("chat", "recentEvents"),
    )
    patch, conflict_skips = _filter_lite_overlay_patch_against_recent_events(
        patch,
        selected_events,
    )
    patch, capsule_skips = _filter_lite_overlay_patch_against_character_capsule(
        patch,
        payload.pet,
    )
    debug = None
    if payload.includeDebug:
        debug = LocalChatDebug(
            usedFallback=False,
            validationFlags=[],
            promptDebug=prompt_debug,
            liteOverlayPatch=patch,
            memoryDebug={
                "recentEvents": recent_events_debug,
                "liteFactConflictSkips": [*conflict_skips, *capsule_skips],
            },
        )
    return patch, debug


def _safe_json_context(value: Any, limit: int = 6000) -> str:
    return _truncate_text(json.dumps(value, ensure_ascii=False, default=str), limit)


def build_proactive_messages(
    payload: LocalProactiveRequest,
    *,
    context_bundle: AssembledPetContext | None = None,
    context_plan: ContextPlan | None = None,
    context_routing: ContextRoutingDecision | None = None,
) -> list[dict[str, str]]:
    context_plan = context_plan or _context_plan_from_routing(
        surface="proactive",
        routing=context_routing,
    )
    memory_block = (
        _memory_context_block(payload.memoryContext)
        if _source_enabled(
            "proactive",
            "userMemory",
            context_plan,
            router_source="userMemory",
            auto_default=True,
        )
        else None
    )
    if context_bundle is None:
        context_bundle = _story_context_for_routing(
            surface="proactive",
            payload=payload,
            context_routing=context_plan,
        )
    reason = _reason_from_payload(payload)
    reply_limit = visible_reply_limit()
    plan = PhrasePlan(
        surface="proactive",
        reply_limit=reply_limit,
        identity_line=_identity_line_for_pet(
            surface="proactive",
            pet=payload.pet,
            description=_reply_identity_label(payload.pet),
            reply_limit=reply_limit,
        ),
        persona_contract=surface_prompt("proactive", {"reason": reason}),
        tone_block=None,
        world_block=_visible_world_block(context_bundle),
        character_block=_character_block_for_surface(payload.pet, "proactive", context_plan),
        memory_block=memory_block,
        extra_rules=(
            state_param_usage_rule(),
            transient_context_rule(),
            _structured_reply_contract_rule(),
        ),
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
    context_plan: ContextPlan | None = None,
    context_routing: ContextRoutingDecision | None = None,
) -> list[dict[str, str]]:
    context_plan = context_plan or _context_plan_from_routing(
        surface="push",
        routing=context_routing,
    )
    memory_block = (
        _memory_context_block(payload.memoryContext)
        if _source_enabled(
            "push",
            "userMemory",
            context_plan,
            router_source="userMemory",
            auto_default=True,
        )
        else None
    )
    if context_bundle is None:
        context_bundle = _story_context_for_routing(
            surface="push",
            payload=payload,
            context_routing=context_plan,
        )
    reply_limit = visible_reply_limit()
    plan = PhrasePlan(
        surface="push",
        reply_limit=reply_limit,
        identity_line=_identity_line_for_pet(
            surface="push",
            pet=payload.pet,
            description=_reply_identity_label(payload.pet),
            reply_limit=reply_limit,
        ),
        persona_contract=surface_prompt("push", {"reason": _reason_from_payload(payload)}),
        tone_block=None,
        world_block=_visible_world_block(context_bundle),
        character_block=_character_block_for_surface(payload.pet, "push", context_plan),
        memory_block=memory_block,
        extra_rules=(
            state_param_usage_rule(),
            transient_context_rule(),
            _structured_reply_contract_rule(),
        ),
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
    model = model or visible_reply_model()
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    prompt_debug: list[dict[str, Any]] = []
    context_plan, context_routing_prompt_debug = _plan_contexts_for_visible_reply(
        surface="proactive",
        payload=payload,
        client=openai_client,
        model=model,
        timeout=timeout,
    )
    if context_routing_prompt_debug is not None:
        prompt_debug.append(context_routing_prompt_debug)
    context_bundle = _story_context_for_routing(
        surface="proactive",
        payload=payload,
        context_routing=context_plan,
    )

    reply_limit = visible_reply_limit()
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_proactive_messages(
            payload,
            context_bundle=context_bundle,
            context_plan=context_plan,
        ),
        "response_format": _visible_reply_response_format(reply_limit),
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(visible_reply_reasoning_effort()),
    }
    prompt_debug.append(log_chat_completion_prompt("pet_reply/proactive", request_kwargs))
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("pet_reply/proactive", completion)
    structured_reply = _parse_visible_reply_response(
        completion.choices[0].message.content or "",
        surface="proactive",
        reply_limit=reply_limit,
    )
    proactive_candidate = (
        payload.memoryContext.proactiveCandidate if payload.memoryContext else None
    )
    debug = None
    if payload.includeDebug:
        debug = LocalChatDebug(
            usedFallback=structured_reply.used_fallback,
            validationFlags=structured_reply.validation_flags,
            promptDebug=prompt_debug,
            structuredReplyDebug=structured_reply.debug,
            memoryDebug={
                "proactiveReason": proactive_candidate.reason if proactive_candidate else None,
                "selectedMemoryIds": proactive_candidate.memoryIds if proactive_candidate else [],
            },
            storyLibraryDebug=_story_context_debug(context_bundle),
            contextRoutingDebug=context_plan.debug,
        )
    return LocalProactiveResponse(
        reply=structured_reply.reply,
        moodHint=structured_reply.mood_hint,
        innerThought=None,
        faceHint=structured_reply.face_hint,
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
    model = model or visible_reply_model()
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    prompt_debug: list[dict[str, Any]] = []
    context_plan, context_routing_prompt_debug = _plan_contexts_for_visible_reply(
        surface="push",
        payload=payload,
        client=openai_client,
        model=model,
        timeout=timeout,
    )
    if context_routing_prompt_debug is not None:
        prompt_debug.append(context_routing_prompt_debug)
    context_bundle = _story_context_for_routing(
        surface="push",
        payload=payload,
        context_routing=context_plan,
    )

    reply_limit = visible_reply_limit()
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_push_messages(
            payload,
            context_bundle=context_bundle,
            context_plan=context_plan,
        ),
        "response_format": _visible_reply_response_format(reply_limit),
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(visible_reply_reasoning_effort()),
    }
    prompt_debug.append(log_chat_completion_prompt("pet_reply/push", request_kwargs))
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("pet_reply/push", completion)
    structured_reply = _parse_visible_reply_response(
        completion.choices[0].message.content or "",
        surface="push",
        reply_limit=reply_limit,
    )
    debug = None
    if payload.includeDebug:
        debug = LocalChatDebug(
            usedFallback=structured_reply.used_fallback,
            validationFlags=structured_reply.validation_flags,
            promptDebug=prompt_debug,
            structuredReplyDebug=structured_reply.debug,
            memoryDebug={"pushReason": _reason_from_payload(payload)},
            storyLibraryDebug=_story_context_debug(context_bundle),
            contextRoutingDebug=context_plan.debug,
        )
    return LocalProactiveResponse(
        reply=structured_reply.reply,
        moodHint=structured_reply.mood_hint,
        innerThought=None,
        faceHint=structured_reply.face_hint,
        debug=debug,
    )


def build_ambient_messages(
    payload: LocalAmbientRequest,
    *,
    context_bundle: AssembledPetContext | None = None,
    context_plan: ContextPlan | None = None,
    context_routing: ContextRoutingDecision | None = None,
) -> list[dict[str, str]]:
    context_plan = context_plan or _context_plan_from_routing(
        surface="ambient",
        routing=context_routing,
    )
    reply_limit = visible_reply_limit(payload.replyMaxChars)
    memory_block = (
        _ambient_memory_context_block(payload.memoryContext)
        if _source_enabled(
            "ambient",
            "userMemory",
            context_plan,
            router_source="userMemory",
            auto_default=True,
        )
        else None
    )
    if context_bundle is None:
        context_bundle = _story_context_for_routing(
            surface="ambient",
            payload=payload,
            context_routing=context_plan,
        )
    recent_ambient_block = (
        _recent_ambient_replies_block(payload.recentAmbientReplies)
        if _source_enabled(
            "ambient",
            "recentReplies",
            context_plan,
            router_source="recentReplies",
            auto_default=True,
        )
        else None
    )
    recent_conversation_block = (
        _ambient_recent_conversation_block(payload.history)
        if context_plan.includes("chatHistory")
        else None
    )
    plan = PhrasePlan(
        surface="ambient",
        reply_limit=reply_limit,
        identity_line=_identity_line_for_pet(
            surface="ambient",
            pet=payload.pet,
            description=_reply_identity_label(payload.pet),
            reply_limit=reply_limit,
        ),
        persona_contract=surface_prompt("ambient", {"recent_replies": ""}),
        tone_block=None,
        world_block=_visible_world_block(context_bundle),
        character_block=_character_block_for_surface(payload.pet, "ambient", context_plan),
        memory_block=memory_block,
        recent_ambient_block=recent_ambient_block,
        dialogue_block=recent_conversation_block,
        extra_rules=(
            state_param_usage_rule(),
            transient_context_rule(),
            _structured_reply_contract_rule(),
            f"Разговорный импульс этой реплики: {ambient_dialogue_impulse()}.",
        ),
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
    model = model or visible_reply_model()
    timeout = timeout if timeout is not None else settings.openai_chat_timeout_seconds
    openai_client = client or get_openai_client()
    prompt_debug: list[dict[str, Any]] = []
    context_plan, context_routing_prompt_debug = _plan_contexts_for_visible_reply(
        surface="ambient",
        payload=payload,
        client=openai_client,
        model=model,
        timeout=timeout,
    )
    if context_routing_prompt_debug is not None:
        prompt_debug.append(context_routing_prompt_debug)
    context_bundle = _story_context_for_routing(
        surface="ambient",
        payload=payload,
        context_routing=context_plan,
    )

    reply_limit = visible_reply_limit(payload.replyMaxChars)
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": build_ambient_messages(
            payload,
            context_bundle=context_bundle,
            context_plan=context_plan,
        ),
        "response_format": _visible_reply_response_format(reply_limit),
        "timeout": timeout,
        **chat_reasoning_effort_kwargs(visible_reply_reasoning_effort()),
    }
    prompt_debug.append(log_chat_completion_prompt("pet_reply/ambient", request_kwargs))
    completion = openai_client.chat.completions.create(**request_kwargs)
    log_chat_completion_response("pet_reply/ambient", completion)
    raw_reply = completion.choices[0].message.content or ""
    structured_reply = _parse_visible_reply_response(
        raw_reply,
        surface="ambient",
        reply_limit=reply_limit,
    )
    log_ambient_reply_diagnostic(
        "pet_reply/ambient",
        request_kwargs,
        raw_reply=raw_reply,
        visible_reply=structured_reply.reply,
    )
    debug = None
    if payload.includeDebug:
        debug = LocalChatDebug(
            usedFallback=structured_reply.used_fallback,
            validationFlags=structured_reply.validation_flags,
            promptDebug=prompt_debug,
            structuredReplyDebug=structured_reply.debug,
            storyLibraryDebug=_story_context_debug(context_bundle),
            contextRoutingDebug={
                **context_plan.debug,
                "antiRepeatLines": (
                    _recent_ambient_reply_debug_lines(payload.recentAmbientReplies)
                    if context_plan.includes("recentReplies")
                    else []
                ),
            },
        )
    return LocalChatResponse(
        reply=structured_reply.reply,
        moodHint=structured_reply.mood_hint,
        innerThought=None,
        faceHint=structured_reply.face_hint,
        debug=debug,
    )
