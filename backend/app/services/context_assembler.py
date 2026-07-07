from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from app.schemas import LocalChatHistoryItem, LocalPetChatContext, LocalPetMemoryContext
from app.services.pet_reply_engine.speech_runtime import (
    format_world_context_block,
    world_context_mode_rule,
)
from app.services.story_library import search_story_library

ContextMode = Literal["chat", "proactive", "ambient"]

MAX_CONTEXT_BRICKS = 5
WORD_PATTERN = re.compile(r"[0-9A-Za-zА-Яа-яЁё-]+")

GENERIC_DIALOGUE_PATTERNS: tuple[str, ...] = (
    "привет",
    "как дела",
    "что делаешь",
    "скуча",
    "я рядом",
    "обними",
)

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
        "крош",
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


def _has_strong_story_signal(text: str, hints: list[str]) -> bool:
    if not hints:
        return False
    lowered = text.casefold()
    if any(pattern in lowered for pattern in GENERIC_DIALOGUE_PATTERNS):
        non_generic_words = _tokens(lowered) - _tokens(" ".join(GENERIC_DIALOGUE_PATTERNS))
        return bool(non_generic_words & _tokens(" ".join(sum(POOL_KEYWORDS.values(), ()))))
    return True


def _retrieval_signal_text(
    *,
    user_message: str,
    history: list[LocalChatHistoryItem],
    memory_context: LocalPetMemoryContext | None,
) -> str:
    return _text_value(
        " ".join(
            [
                user_message,
                _history_text(history),
                _memory_text(memory_context),
            ]
        ),
        limit=1500,
    )


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
    if mode == "ambient":
        return "короткая фоновая реплика питомца о его мире, находке, месте или существе"
    if mode == "proactive":
        return "личная проактивная реплика питомца с конкретной деталью мира"
    return "короткая реплика питомца"


def _retrieval_plan(
    *,
    mode: ContextMode,
    pet: LocalPetChatContext,
    user_message: str,
    history: list[LocalChatHistoryItem],
    memory_context: LocalPetMemoryContext | None,
) -> StoryRetrievalPlan:
    query = _query_text(
        mode=mode,
        pet=pet,
        user_message=user_message,
        history=history,
        memory_context=memory_context,
    )
    signal_text = _retrieval_signal_text(
        user_message=user_message,
        history=history,
        memory_context=memory_context,
    )
    hints = _pool_hints(signal_text)
    if not _has_strong_story_signal(signal_text, hints):
        return StoryRetrievalPlan(
            needs_context=False,
            query=query,
            signal_text=signal_text,
            pool_hints=[],
            reason="no_story_signal",
        )
    return StoryRetrievalPlan(
        needs_context=True,
        query=query,
        signal_text=signal_text,
        pool_hints=hints,
        reason="matched_story_spheres",
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
    return format_world_context_block(mode_rule=world_context_mode_rule(mode), lines=lines)


def assemble_pet_context(
    *,
    mode: ContextMode,
    pet: LocalPetChatContext,
    user_message: str = "",
    history: list[LocalChatHistoryItem] | None = None,
    memory_context: LocalPetMemoryContext | None = None,
    limit: int = MAX_CONTEXT_BRICKS,
) -> AssembledPetContext:
    active_history = history or []
    plan = _retrieval_plan(
        mode=mode,
        pet=pet,
        user_message=user_message,
        history=active_history,
        memory_context=memory_context,
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
