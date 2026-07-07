from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from app.schemas import LocalChatHistoryItem, LocalPetChatContext, LocalPetMemoryContext
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
    mode_rule = (
        "Для idle/proactive реплики можно взять одну деталь как повод для живого наблюдения."
        if mode in {"ambient", "proactive"}
        else "Используй эти детали только если они помогают ответить пользователю."
    )
    return (
        "WORLD_CONTEXT: ниже уже выбранные кирпичики мира для этой реплики. "
        "Не перечисляй их списком и не говори, что видишь контекст. "
        "Собери из 1-3 кирпичиков связанный смысл; можно умеренно фантазировать "
        "только как вариацию на эти референсы. Tone of voice меняет форму, "
        "но не факты и не смысл.\n"
        f"{mode_rule}\n"
        f"{lines}"
    )


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
    query = _query_text(
        mode=mode,
        pet=pet,
        user_message=user_message,
        history=active_history,
        memory_context=memory_context,
    )
    signal_text = _retrieval_signal_text(
        user_message=user_message,
        history=active_history,
        memory_context=memory_context,
    )
    hints = _pool_hints(signal_text)
    if not hints:
        return AssembledPetContext(
            prompt_block="",
            debug={
                "mode": mode,
                "query": query,
                "poolHints": [],
                "injectedSpheres": [],
            },
        )

    result = search_story_library(
        query=query,
        pool_hints=hints,
        limit=limit,
        character_bible=pet.characterBible,
    )
    bricks = result.get("bricks") if isinstance(result.get("bricks"), list) else []
    prompt_block = _prompt_block(bricks, mode)
    return AssembledPetContext(
        prompt_block=prompt_block,
        debug={
            "mode": mode,
            "query": query,
            "poolHints": hints,
            "injectedSpheres": bricks,
        },
    )
