from __future__ import annotations

import re
from dataclasses import dataclass
from math import log

from app.services.pet_memory.models import CanonMemoryFact, PetMemoryStateV1
from app.services.pet_reply_engine.intent import is_home_question, is_lore_question

WORD_PATTERN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{4,}")
ENTITY_PATTERN = re.compile(r"\b(?:[А-ЯЁ][а-яё]{2,}|[A-Z][A-Za-z]{2,})\b")


@dataclass(frozen=True)
class MemoryContext:
    canon_lines: tuple[str, ...] = ()
    generated_fact_lines: tuple[str, ...] = ()
    relationship_lines: tuple[str, ...] = ()
    open_thread_lines: tuple[str, ...] = ()
    reflection_lines: tuple[str, ...] = ()
    active_goal_lines: tuple[str, ...] = ()
    development_lines: tuple[str, ...] = ()
    canon_fact_ids: tuple[str, ...] = ()
    entity_lines: tuple[str, ...] = ()


def _words(text: str | None) -> set[str]:
    return {word.casefold() for word in WORD_PATTERN.findall(text or "")}


def _entities(text: str | None) -> set[str]:
    return {word.casefold() for word in ENTITY_PATTERN.findall(text or "")}


def _band(value: int) -> str:
    if value < 34:
        return "низкое"
    if value < 67:
        return "среднее"
    return "высокое"


def _canonical_type_score(fact: CanonMemoryFact, user_text: str | None) -> float:
    text = user_text or ""
    score = fact.importance + fact.confidence * 0.25 + min(fact.useCount, 6) * 0.03
    lowered = text.casefold()
    if is_home_question(text) and fact.type in ("home_fact", "world_fact", "habit_fact"):
        score += 0.45
    if is_lore_question(text) and fact.type in (
        "world_fact",
        "home_fact",
        "friend_fact",
        "family_fact",
        "origin_fact",
        "milestone",
    ):
        score += 0.25
    if any(word in lowered for word in ("друг", "друз", "приятел")) and fact.type == "friend_fact":
        score += 0.5
    if (
        any(word in lowered for word in ("семь", "родн", "брат", "сестр"))
        and fact.type == "family_fact"
    ):
        score += 0.5
    if any(word in lowered for word in ("прошл", "появ", "родил", "откуда")) and fact.type in (
        "origin_fact",
        "milestone",
    ):
        score += 0.5
    if any(
        word in lowered for word in ("люб", "нрав", "страш", "боиш", "привыч")
    ) and fact.type in (
        "preference_fact",
        "fear_fact",
        "habit_fact",
    ):
        score += 0.4
    overlap = _words(fact.text) & _words(text)
    score += min(len(overlap), 4) * 0.08
    entity_overlap = _entities(fact.text) & _entities(text)
    score += min(len(entity_overlap), 3) * 0.18
    return score


def _bm25_scores(facts: list[CanonMemoryFact], user_text: str | None) -> dict[str, float]:
    query = _words(user_text)
    if not query or not facts:
        return {}
    documents = {fact.id: _words(fact.text) for fact in facts}
    avg_len = sum(len(words) for words in documents.values()) / max(len(documents), 1)
    avg_len = max(avg_len, 1)
    df: dict[str, int] = {}
    for words in documents.values():
        for word in words:
            df[word] = df.get(word, 0) + 1
    scores: dict[str, float] = {}
    k1 = 1.2
    b = 0.75
    for fact in facts:
        words = list(documents[fact.id])
        if not words:
            continue
        score = 0.0
        for token in query:
            tf = 1 if token in documents[fact.id] else 0
            if not tf:
                continue
            idf = log(1 + (len(facts) - df.get(token, 0) + 0.5) / (df.get(token, 0) + 0.5))
            score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * len(words) / avg_len))
        scores[fact.id] = score
    return scores


def _canon_lines(
    memory: PetMemoryStateV1, user_text: str | None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    bm25 = _bm25_scores(memory.canon, user_text)
    scored = sorted(
        memory.canon,
        key=lambda fact: _canonical_type_score(fact, user_text) + bm25.get(fact.id, 0),
        reverse=True,
    )
    selected = scored[:8]
    return tuple(fact.text for fact in selected), tuple(fact.id for fact in selected)


def _relationship_lines(memory: PetMemoryStateV1, user_text: str | None) -> tuple[str, ...]:
    relationship = memory.relationship
    lines: list[str] = []
    lowered = (user_text or "").casefold()
    if relationship.userName:
        lines.append(f"имя пользователя: {relationship.userName}")
    if relationship.preferredAddress:
        lines.append(f"как обращаться: {relationship.preferredAddress}")
    if relationship.boundaries:
        lines.append("границы пользователя: " + "; ".join(relationship.boundaries[:4]))
    if "помни" in lowered or "зовут" in lowered or "обо мне" in lowered:
        user_facts = relationship.userFacts[:8]
    else:
        user_facts = relationship.userFacts[:4]
    for fact in user_facts:
        lines.append(fact.text)
    lines.append(
        "отношения: "
        f"доверие {_band(relationship.trust)}, "
        f"привязанность {_band(relationship.attachment)}, "
        f"знакомство {_band(relationship.familiarity)}"
    )
    return tuple(lines[:10])


def _generated_fact_lines(memory: PetMemoryStateV1, user_text: str | None) -> tuple[str, ...]:
    user_words = _words(user_text)
    candidates = [
        fact
        for fact in memory.generatedFacts
        if fact.status == "accepted_soft" and fact.confidence >= 0.5
    ]
    scored = sorted(
        candidates,
        key=lambda fact: (
            bool(_words(fact.text) & user_words),
            fact.importance,
            fact.updatedAt,
        ),
        reverse=True,
    )
    return tuple(f"{fact.scope}: {fact.text}" for fact in scored[:4])


def _open_thread_lines(memory: PetMemoryStateV1) -> tuple[str, ...]:
    threads = sorted(
        (item for item in memory.threads if item.status == "open"),
        key=lambda thread: (thread.priority, thread.updatedAt),
        reverse=True,
    )
    lines = []
    for thread in threads[:3]:
        follow = f" follow-up: {thread.suggestedFollowUp}" if thread.suggestedFollowUp else ""
        lines.append(f"{thread.topic}: {thread.summary}{follow}")
    return tuple(lines)


def _reflection_lines(memory: PetMemoryStateV1, user_text: str | None) -> tuple[str, ...]:
    user_words = _words(user_text)
    reflections = sorted(
        memory.reflections,
        key=lambda item: (
            bool(_words(item.text) & user_words),
            item.importance,
            item.updatedAt,
        ),
        reverse=True,
    )
    return tuple(item.text for item in reflections[:3])


def _active_goal_lines(memory: PetMemoryStateV1) -> tuple[str, ...]:
    goals = sorted(
        (item for item in memory.activeGoals if item.status == "active"),
        key=lambda goal: (goal.priority, goal.updatedAt),
        reverse=True,
    )
    return tuple(f"{goal.kind}: {goal.text}" for goal in goals[:2])


def _development_lines(memory: PetMemoryStateV1) -> tuple[str, ...]:
    development = memory.development
    lines = (
        f"доверие: {_band(development.trust)}",
        f"привязанность: {_band(development.attachment)}",
        f"любопытство: {_band(development.curiosity)}",
        f"уверенность: {_band(development.confidence)}",
        f"одиночество: {_band(development.loneliness)}",
        f"игривость: {_band(development.playfulness)}",
    )
    if development.lastDevelopmentReason:
        return (*lines, f"последнее изменение: {development.lastDevelopmentReason}")
    return lines


def build_memory_context(memory: PetMemoryStateV1, user_text: str | None) -> MemoryContext:
    canon_lines, canon_fact_ids = _canon_lines(memory, user_text)
    entities = sorted(_entities(user_text))[:8]
    return MemoryContext(
        canon_lines=canon_lines,
        generated_fact_lines=_generated_fact_lines(memory, user_text),
        relationship_lines=_relationship_lines(memory, user_text),
        open_thread_lines=_open_thread_lines(memory),
        reflection_lines=_reflection_lines(memory, user_text),
        active_goal_lines=_active_goal_lines(memory),
        development_lines=_development_lines(memory),
        canon_fact_ids=canon_fact_ids,
        entity_lines=tuple(entities),
    )
