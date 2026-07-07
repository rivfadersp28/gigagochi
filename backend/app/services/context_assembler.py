from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from app.schemas import LocalChatHistoryItem, LocalPetChatContext, LocalPetMemoryContext
from app.services.pet_reply_engine.speech_runtime import (
    format_world_context_block,
    story_context_default_query,
)
from app.services.story_library import search_story_library

ContextMode = Literal["chat", "proactive", "ambient"]

MAX_CONTEXT_BRICKS = 5
WORD_PATTERN = re.compile(r"[0-9A-Za-zА-Яа-яЁё-]+")

POOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "items": (
        "предмет",
        "вещ",
        "наход",
        "артефакт",
        "инвентар",
        "подар",
        "сокров",
        "камень",
        "ключ",
        "лента",
    ),
    "locations": (
        "мир",
        "мест",
        "дом",
        "где",
        "лес",
        "пещер",
        "грот",
        "озер",
        "гора",
        "луг",
        "парк",
        "тропа",
        "локац",
    ),
    "threats": (
        "монстр",
        "опас",
        "страш",
        "угроз",
        "враг",
        "твар",
        "чудовищ",
        "темн",
        "ноч",
        "пуга",
        "боиш",
        "бой",
    ),
    "neighbors": (
        "сосед",
        "друг",
        "персонаж",
        "кто",
        "рядом",
        "встрет",
        "помог",
        "нпс",
        "npc",
    ),
    "creatures": (
        "существ",
        "живот",
        "звер",
        "птиц",
        "жук",
        "улит",
        "светля",
        "бабоч",
        "дух",
    ),
}

ALL_POOL_HINTS: tuple[str, ...] = tuple(POOL_KEYWORDS.keys())


@dataclass(frozen=True)
class AssembledPetContext:
    prompt_block: str
    debug: dict[str, Any] | None


@dataclass(frozen=True)
class StoryRetrievalPlan:
    needs_context: bool
    query: str
    signal_text: str
    pool_hints: list[str]
    reason: str


def _compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _text_value(value: Any, *, limit: int = 1200) -> str:
    if not isinstance(value, str):
        return ""
    return _compact_spaces(value)[:limit].rstrip()


def _tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in WORD_PATTERN.findall(value.casefold()):
        if len(token) <= 2:
            continue
        tokens.add(token)
    return tokens


def _history_text(history: list[LocalChatHistoryItem], *, limit: int = 4) -> str:
    parts = [item.text for item in history[-limit:]]
    return _text_value(" ".join(parts), limit=1200)


def _memory_text(memory_context: LocalPetMemoryContext | None) -> str:
    if not memory_context:
        return ""
    parts: list[str] = []
    if memory_context.summary:
        parts.append(memory_context.summary)
    if memory_context.userProfile:
        parts.append(memory_context.userProfile)
    parts.extend(item.text for item in memory_context.relevantMemories[:3])
    if memory_context.proactiveCandidate:
        parts.append(memory_context.proactiveCandidate.reason)
    return _text_value(" ".join(parts), limit=1200)


def _pool_hints(text: str) -> list[str]:
    text_tokens = _tokens(text)
    hints: list[str] = []
    for pool, keywords in POOL_KEYWORDS.items():
        if any(keyword in text_tokens or keyword in text.casefold() for keyword in keywords):
            hints.append(pool)

    result: list[str] = []
    seen: set[str] = set()
    for hint in hints:
        if hint in seen:
            continue
        seen.add(hint)
        result.append(hint)
    return result[:5]


def _query_text(
    *,
    mode: ContextMode,
    pet: LocalPetChatContext,
    user_message: str,
    history: list[LocalChatHistoryItem],
    memory_context: LocalPetMemoryContext | None,
) -> str:
    parts = [
        user_message,
        _history_text(history),
        _memory_text(memory_context),
        pet.name or "",
        pet.description,
        pet.stage,
        pet.mood,
    ]
    query = _text_value(" ".join(parts), limit=1500)
    if query:
        return query
    return story_context_default_query(mode)


def _retrieval_plan(
    *,
    mode: ContextMode,
    pet: LocalPetChatContext,
    user_message: str,
    history: list[LocalChatHistoryItem],
    memory_context: LocalPetMemoryContext | None,
    force_context: bool = False,
    forced_query: str | None = None,
    forced_pool_hints: list[str] | None = None,
    forced_reason: str = "context_routing",
    routing_applied: bool = False,
) -> StoryRetrievalPlan:
    if force_context:
        query = _text_value(forced_query or user_message, limit=1500)
        if not query:
            query = _query_text(
                mode=mode,
                pet=pet,
                user_message=user_message,
                history=history,
                memory_context=memory_context,
            )
        return StoryRetrievalPlan(
            needs_context=True,
            query=query,
            signal_text=_text_value(user_message, limit=1500),
            pool_hints=forced_pool_hints or _pool_hints(query) or list(ALL_POOL_HINTS),
            reason=forced_reason,
        )

    if routing_applied:
        query = _query_text(
            mode=mode,
            pet=pet,
            user_message=user_message,
            history=history,
            memory_context=memory_context,
        )
        return StoryRetrievalPlan(
            needs_context=False,
            query=query,
            signal_text=_text_value(user_message, limit=1500),
            pool_hints=[],
            reason="disabled_by_context_routing",
        )

    signal_text = _text_value(user_message, limit=1500)
    query = _query_text(
        mode=mode,
        pet=pet,
        user_message=signal_text,
        history=[] if mode == "ambient" else history,
        memory_context=None if mode == "ambient" else memory_context,
    )
    return StoryRetrievalPlan(
        needs_context=False,
        query=query,
        signal_text=signal_text,
        pool_hints=[],
        reason="no_context_routing_request",
    )


def _render_brick(brick: dict[str, Any]) -> str:
    pool = _text_value(brick.get("poolLabel") or brick.get("pool"), limit=80)
    name = _text_value(brick.get("name"), limit=120)
    text = _text_value(brick.get("text"), limit=260)
    if not name:
        return ""
    if text and text.casefold() != name.casefold():
        return f"- [{pool}] {name}: {text}"
    return f"- [{pool}] {name}"


def _prompt_block(bricks: list[dict[str, Any]], mode: ContextMode) -> str:
    if not bricks:
        return ""
    lines = "\n".join(line for brick in bricks if (line := _render_brick(brick)))
    if not lines:
        return ""
    return format_world_context_block(lines=lines)


def assemble_pet_context(
    *,
    mode: ContextMode,
    pet: LocalPetChatContext,
    user_message: str = "",
    history: list[LocalChatHistoryItem] | None = None,
    memory_context: LocalPetMemoryContext | None = None,
    limit: int = MAX_CONTEXT_BRICKS,
    force_context: bool = False,
    forced_query: str | None = None,
    forced_pool_hints: list[str] | None = None,
    forced_reason: str = "context_routing",
    routing_applied: bool = False,
) -> AssembledPetContext:
    active_history = [] if mode == "ambient" else history or []
    plan = _retrieval_plan(
        mode=mode,
        pet=pet,
        user_message=user_message,
        history=active_history,
        memory_context=memory_context,
        force_context=force_context,
        forced_query=forced_query,
        forced_pool_hints=forced_pool_hints,
        forced_reason=forced_reason,
        routing_applied=routing_applied,
    )
    if not plan.needs_context:
        return AssembledPetContext(
            prompt_block="",
            debug={
                "mode": mode,
                "query": plan.query,
                "signalText": plan.signal_text,
                "poolHints": [],
                "injectedSpheres": [],
                "needsStoryContext": False,
                "reason": plan.reason,
            },
        )

    result = search_story_library(
        query=plan.query,
        pool_hints=plan.pool_hints,
        limit=limit,
        character_bible=pet.characterBible,
        diverse_pools=mode == "ambient",
    )
    bricks = result.get("bricks") if isinstance(result.get("bricks"), list) else []
    prompt_block = _prompt_block(bricks, mode)
    return AssembledPetContext(
        prompt_block=prompt_block,
        debug={
            "mode": mode,
            "query": plan.query,
            "signalText": plan.signal_text,
            "poolHints": plan.pool_hints,
            "injectedSpheres": bricks,
            "needsStoryContext": True,
            "reason": plan.reason,
        },
    )
