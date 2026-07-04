from __future__ import annotations

from datetime import datetime, timedelta

from app.services.pet_memory.models import (
    ActiveGoal,
    PetEvent,
    PetMemoryPatch,
    PetMemoryStateV1,
    ReflectionMemory,
)
from app.services.pet_memory.normalizer import make_memory_id


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _reflection_text(events: list[PetEvent]) -> str:
    combined = " ".join(event.text for event in events).casefold()
    if "дом" in combined or "полк" in combined or "мир" in combined:
        return "Питомец стал спокойнее возвращаться к теме своего дома рядом с пользователем."
    if "друг" in combined or "кап" in combined:
        return "Пользователь интересуется друзьями питомца, и питомцу легче продолжать эту тему."
    if "имя" in combined or "зовут" in combined:
        return "Питомец внимательнее относится к тому, как обращаться к пользователю."
    return "Питомец чуть больше доверяет пользователю после нескольких спокойных сообщений."


def _existing_reflection_sources(memory: PetMemoryStateV1) -> set[tuple[str, ...]]:
    return {tuple(item.sourceEventIds) for item in memory.reflections}


def _expired_goal_updates(memory: PetMemoryStateV1, now: str) -> list[ActiveGoal]:
    now_dt = _parse_iso(now)
    updates: list[ActiveGoal] = []
    if not now_dt:
        return updates
    for goal in memory.activeGoals:
        if goal.status != "active" or not goal.expiresAt:
            continue
        expires_at = _parse_iso(goal.expiresAt)
        if expires_at and expires_at < now_dt:
            updates.append(goal.model_copy(update={"status": "expired", "updatedAt": now}))
    return updates


def compact_memory(memory: PetMemoryStateV1, *, now: str) -> PetMemoryPatch:
    patch = PetMemoryPatch()
    patch.activeGoalUpserts.extend(_expired_goal_updates(memory, now))

    significant_events = [
        event
        for event in memory.events[-12:]
        if event.kind in ("user_message", "pet_reply", "memory_accepted", "relationship", "thread")
    ]
    if len(significant_events) < 2:
        return patch

    should_reflect = len(memory.events) >= 8 or len(memory.events) > 95
    if not should_reflect:
        return patch

    source_events = significant_events[-4:]
    source_ids = tuple(event.id for event in source_events)
    if source_ids in _existing_reflection_sources(memory):
        return patch

    now_dt = _parse_iso(now)
    if now_dt:
        recent_reflections = [
            item
            for item in memory.reflections
            if (created := _parse_iso(item.createdAt)) and created > now_dt - timedelta(hours=1)
        ]
        if recent_reflections:
            return patch

    patch.reflectionUpserts.append(
        ReflectionMemory(
            id=make_memory_id("reflection"),
            text=_reflection_text(source_events),
            scope="relationship",
            sourceEventIds=list(source_ids),
            confidence=0.65,
            importance=0.55,
            createdAt=now,
            updatedAt=now,
        )
    )
    patch.eventAppends.append(
        PetEvent(
            id=make_memory_id("event"),
            kind="reflection",
            text="Питомец сделал короткий внутренний вывод из нескольких событий.",
            importance=0.45,
            createdAt=now,
        )
    )
    return patch
