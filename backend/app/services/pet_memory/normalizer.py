from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.services.pet_memory.models import (
    ActiveGoal,
    CanonMemoryFact,
    ConversationThread,
    GeneratedFactCandidate,
    PetMemoryStateV1,
    ReflectionMemory,
    RelationshipEvent,
    RelationshipMemory,
    UserFact,
)

MAX_CANON_FACTS = 60
MAX_GENERATED_FACTS = 50
MAX_RELATIONSHIP_EVENTS = 30
MAX_USER_FACTS = 30
MAX_THREADS = 12
MAX_OPEN_THREADS = 6
MAX_REFLECTIONS = 20
MAX_ACTIVE_GOALS = 8
MAX_LIVE_ACTIVE_GOALS = 5
MAX_EVENTS = 100
MAX_REJECTED = 30
MAX_TEXT = 500

_SPACE_PATTERN = re.compile(r"\s+")


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def make_memory_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def clamp_float(value: float, low: float = 0, high: float = 1) -> float:
    return max(low, min(high, value))


def clamp_int(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, value))


def normalize_text(value: str, limit: int = MAX_TEXT) -> str:
    return _SPACE_PATTERN.sub(" ", value.strip())[:limit]


def normalized_key(value: str) -> str:
    return normalize_text(value).casefold().removeprefix("лор:").strip()


def _is_iso_like(value: str | None) -> bool:
    if not value:
        return False
    return not isinstance(value, str) or not value.strip() or not _date_parse_failed(value)


def _date_parse_failed(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return False
    except ValueError:
        return True


def _safe_date(value: str | None, fallback: str) -> str:
    return value if isinstance(value, str) and _is_iso_like(value) else fallback


def _clean_canon_item(item: CanonMemoryFact, now: str) -> CanonMemoryFact | None:
    text = normalize_text(item.text)
    if not text:
        return None
    return item.model_copy(
        update={
            "text": text,
            "confidence": clamp_float(item.confidence),
            "importance": clamp_float(item.importance),
            "decayScore": clamp_float(item.decayScore),
            "createdAt": _safe_date(item.createdAt, now),
            "updatedAt": _safe_date(item.updatedAt, now),
            "lastUsedAt": _safe_date(item.lastUsedAt, now) if item.lastUsedAt else None,
            "lastReinforcedAt": (
                _safe_date(item.lastReinforcedAt, now) if item.lastReinforcedAt else None
            ),
        }
    )


def _dedupe_canon(items: list[CanonMemoryFact], now: str) -> list[CanonMemoryFact]:
    by_key: dict[str, CanonMemoryFact] = {}
    for item in items:
        clean = _clean_canon_item(item, now)
        if not clean:
            continue
        key = normalized_key(clean.text)
        existing = by_key.get(key)
        if not existing:
            by_key[key] = clean
            continue
        by_key[key] = existing.model_copy(
            update={
                "importance": max(existing.importance, clean.importance),
                "confidence": max(existing.confidence, clean.confidence),
                "useCount": max(existing.useCount, clean.useCount),
                "decayScore": min(existing.decayScore, clean.decayScore),
                "updatedAt": max(existing.updatedAt, clean.updatedAt),
                "pinned": existing.pinned or clean.pinned,
            }
        )
    return sorted(
        by_key.values(),
        key=lambda fact: (fact.pinned, fact.type == "milestone", fact.importance, fact.updatedAt),
        reverse=True,
    )[:MAX_CANON_FACTS]


def _clean_generated_fact(
    item: GeneratedFactCandidate, now: str
) -> GeneratedFactCandidate | None:
    text = normalize_text(item.text)
    if not text:
        return None
    return item.model_copy(
        update={
            "text": text,
            "sourceSpan": normalize_text(item.sourceSpan, 240) if item.sourceSpan else None,
            "confidence": clamp_float(item.confidence),
            "importance": clamp_float(item.importance),
            "promotionPolicy": normalize_text(item.promotionPolicy, 80)
            or "needs_reinforcement",
            "conflictReasons": [
                normalize_text(reason, 120)
                for reason in item.conflictReasons[:6]
                if normalize_text(reason, 120)
            ],
            "createdAt": _safe_date(item.createdAt, now),
            "updatedAt": _safe_date(item.updatedAt, now),
        }
    )


def _dedupe_generated_facts(
    items: list[GeneratedFactCandidate], now: str
) -> list[GeneratedFactCandidate]:
    by_key: dict[str, GeneratedFactCandidate] = {}
    for item in items:
        clean = _clean_generated_fact(item, now)
        if not clean:
            continue
        key = f"{clean.scope}:{normalized_key(clean.text)}"
        existing = by_key.get(key)
        if not existing:
            by_key[key] = clean
            continue
        status_rank = {
            "rejected": 0,
            "draft": 1,
            "needs_user_confirmation": 2,
            "accepted_soft": 3,
            "canon": 4,
        }
        preferred = clean if status_rank[clean.status] > status_rank[existing.status] else existing
        by_key[key] = preferred.model_copy(
            update={
                "confidence": max(existing.confidence, clean.confidence),
                "importance": max(existing.importance, clean.importance),
                "reinforcementCount": max(existing.reinforcementCount, clean.reinforcementCount),
                "updatedAt": max(existing.updatedAt, clean.updatedAt),
                "conflictReasons": list(
                    dict.fromkeys([*existing.conflictReasons, *clean.conflictReasons])
                )[:6],
            }
        )
    return sorted(
        by_key.values(),
        key=lambda fact: (
            fact.status in ("accepted_soft", "needs_user_confirmation"),
            fact.importance,
            fact.updatedAt,
        ),
        reverse=True,
    )[:MAX_GENERATED_FACTS]


def _clean_relationship(memory: RelationshipMemory, now: str) -> RelationshipMemory:
    user_facts = _dedupe_user_facts(memory.userFacts, now)
    shared_events = _dedupe_relationship_events(memory.sharedEvents, now)
    boundaries: list[str] = []
    seen = set()
    for item in memory.boundaries:
        text = normalize_text(item, 160)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        boundaries.append(text)
    return memory.model_copy(
        update={
            "userName": normalize_text(memory.userName, 80) if memory.userName else None,
            "preferredAddress": (
                normalize_text(memory.preferredAddress, 80) if memory.preferredAddress else None
            ),
            "trust": clamp_int(memory.trust),
            "attachment": clamp_int(memory.attachment),
            "familiarity": clamp_int(memory.familiarity),
            "sharedEvents": shared_events[:MAX_RELATIONSHIP_EVENTS],
            "userFacts": user_facts[:MAX_USER_FACTS],
            "boundaries": boundaries[:20],
            "lastWarmMomentAt": (
                _safe_date(memory.lastWarmMomentAt, now) if memory.lastWarmMomentAt else None
            ),
        }
    )


def _dedupe_user_facts(items: list[UserFact], now: str) -> list[UserFact]:
    by_key: dict[str, UserFact] = {}
    for item in items:
        text = normalize_text(item.text)
        if not text:
            continue
        clean = item.model_copy(
            update={
                "text": text,
                "createdAt": _safe_date(item.createdAt, now),
                "updatedAt": _safe_date(item.updatedAt, now),
                "lastUsedAt": _safe_date(item.lastUsedAt, now) if item.lastUsedAt else None,
            }
        )
        key = normalized_key(text)
        existing = by_key.get(key)
        if existing:
            by_key[key] = existing.model_copy(
                update={
                    "confidence": max(existing.confidence, clean.confidence),
                    "importance": max(existing.importance, clean.importance),
                    "updatedAt": max(existing.updatedAt, clean.updatedAt),
                }
            )
        else:
            by_key[key] = clean
    return sorted(by_key.values(), key=lambda item: (item.importance, item.updatedAt), reverse=True)


def _dedupe_relationship_events(
    items: list[RelationshipEvent], now: str
) -> list[RelationshipEvent]:
    by_key: dict[str, RelationshipEvent] = {}
    for item in items:
        text = normalize_text(item.text)
        if not text:
            continue
        clean = item.model_copy(
            update={
                "text": text,
                "createdAt": _safe_date(item.createdAt, now),
                "updatedAt": _safe_date(item.updatedAt, now),
            }
        )
        key = normalized_key(text)
        existing = by_key.get(key)
        by_key[key] = (
            existing.model_copy(
                update={
                    "importance": max(existing.importance, clean.importance),
                    "updatedAt": max(existing.updatedAt, clean.updatedAt),
                }
            )
            if existing
            else clean
        )
    return sorted(by_key.values(), key=lambda item: (item.importance, item.updatedAt), reverse=True)


def _clean_threads(items: list[ConversationThread], now: str) -> list[ConversationThread]:
    cleaned: list[ConversationThread] = []
    for item in items:
        topic = normalize_text(item.topic, 160)
        summary = normalize_text(item.summary)
        if not topic or not summary:
            continue
        cleaned.append(
            item.model_copy(
                update={
                    "topic": topic,
                    "summary": summary,
                    "priority": clamp_float(item.priority),
                    "createdAt": _safe_date(item.createdAt, now),
                    "updatedAt": _safe_date(item.updatedAt, now),
                    "lastMentionedAt": (
                        _safe_date(item.lastMentionedAt, now) if item.lastMentionedAt else None
                    ),
                    "suggestedFollowUp": (
                        normalize_text(item.suggestedFollowUp, 240)
                        if item.suggestedFollowUp
                        else None
                    ),
                    "lastQuestionAskedAt": (
                        _safe_date(item.lastQuestionAskedAt, now)
                        if item.lastQuestionAskedAt
                        else None
                    ),
                }
            )
        )
    open_count = 0
    limited: list[ConversationThread] = []
    for item in sorted(
        cleaned, key=lambda thread: (thread.priority, thread.updatedAt), reverse=True
    ):
        if item.status == "open":
            open_count += 1
            if open_count > MAX_OPEN_THREADS:
                item = item.model_copy(update={"status": "paused", "updatedAt": now})
        limited.append(item)
    return limited[:MAX_THREADS]


def _clean_reflections(items: list[ReflectionMemory], now: str) -> list[ReflectionMemory]:
    cleaned: list[ReflectionMemory] = []
    for item in items:
        text = normalize_text(item.text)
        if not text or len(item.sourceEventIds) < 2:
            continue
        cleaned.append(
            item.model_copy(
                update={
                    "text": text,
                    "confidence": clamp_float(item.confidence),
                    "importance": clamp_float(item.importance),
                    "createdAt": _safe_date(item.createdAt, now),
                    "updatedAt": _safe_date(item.updatedAt, now),
                    "lastUsedAt": _safe_date(item.lastUsedAt, now) if item.lastUsedAt else None,
                    "sourceEventIds": item.sourceEventIds[:12],
                }
            )
        )
    return sorted(cleaned, key=lambda item: (item.importance, item.updatedAt), reverse=True)[
        :MAX_REFLECTIONS
    ]


def _clean_goals(items: list[ActiveGoal], now: str) -> list[ActiveGoal]:
    cleaned: list[ActiveGoal] = []
    for item in items:
        text = normalize_text(item.text, 300)
        if not text:
            continue
        cleaned.append(
            item.model_copy(
                update={
                    "text": text,
                    "priority": clamp_float(item.priority),
                    "createdAt": _safe_date(item.createdAt, now),
                    "updatedAt": _safe_date(item.updatedAt, now),
                    "expiresAt": _safe_date(item.expiresAt, now) if item.expiresAt else None,
                }
            )
        )
    active_count = 0
    limited: list[ActiveGoal] = []
    for item in sorted(cleaned, key=lambda goal: (goal.priority, goal.updatedAt), reverse=True):
        if item.status == "active":
            active_count += 1
            if active_count > MAX_LIVE_ACTIVE_GOALS:
                item = item.model_copy(update={"status": "paused", "updatedAt": now})
        limited.append(item)
    return limited[:MAX_ACTIVE_GOALS]


def _legacy_lore_to_canon(
    lore_memories: list[str], existing: list[CanonMemoryFact], now: str
) -> list[CanonMemoryFact]:
    seen = {normalized_key(item.text) for item in existing}
    migrated: list[CanonMemoryFact] = []
    for memory in lore_memories:
        text = normalize_text(str(memory).removeprefix("ЛОР:").removeprefix("LORE:").strip())
        if not text:
            continue
        key = normalized_key(text)
        if key in seen:
            continue
        seen.add(key)
        migrated.append(
            CanonMemoryFact(
                id=make_memory_id("canon"),
                type="world_fact",
                text=text,
                source="model",
                confidence=0.7,
                importance=0.55,
                useCount=0,
                decayScore=0.05,
                createdAt=now,
                updatedAt=now,
            )
        )
    return migrated


def normalize_memory(
    value: PetMemoryStateV1 | dict[str, Any] | None,
    *,
    lore_memories: list[str] | tuple[str, ...] | None = None,
    now: str | None = None,
) -> PetMemoryStateV1:
    now_value = now or now_iso()
    if isinstance(value, PetMemoryStateV1):
        memory = value
    elif isinstance(value, dict):
        try:
            memory = PetMemoryStateV1.model_validate(value)
        except ValidationError:
            memory = PetMemoryStateV1()
    else:
        memory = PetMemoryStateV1()

    canon = _dedupe_canon(
        [
            *memory.canon,
            *_legacy_lore_to_canon(list(lore_memories or ()), memory.canon, now_value),
        ],
        now_value,
    )
    generated_facts = _dedupe_generated_facts(memory.generatedFacts, now_value)
    relationship = _clean_relationship(memory.relationship, now_value)
    threads = _clean_threads(memory.threads, now_value)
    reflections = _clean_reflections(memory.reflections, now_value)
    goals = _clean_goals(memory.activeGoals, now_value)
    events = [
        event.model_copy(
            update={
                "text": normalize_text(event.text),
                "createdAt": _safe_date(event.createdAt, now_value),
            }
        )
        for event in memory.events
        if normalize_text(event.text)
    ][-MAX_EVENTS:]
    rejected = [
        item.model_copy(
            update={
                "text": normalize_text(item.text),
                "reason": normalize_text(item.reason, 160),
                "createdAt": _safe_date(item.createdAt, now_value),
            }
        )
        for item in memory.rejectedCandidates
        if normalize_text(item.text)
    ][-MAX_REJECTED:]

    return PetMemoryStateV1(
        schemaVersion=1,
        canon=canon,
        generatedFacts=generated_facts,
        relationship=relationship,
        threads=threads,
        reflections=reflections,
        activeGoals=goals,
        development=memory.development.model_copy(
            update={
                "trust": clamp_int(memory.development.trust),
                "attachment": clamp_int(memory.development.attachment),
                "curiosity": clamp_int(memory.development.curiosity),
                "confidence": clamp_int(memory.development.confidence),
                "loneliness": clamp_int(memory.development.loneliness),
                "playfulness": clamp_int(memory.development.playfulness),
                "lastDevelopmentReason": (
                    normalize_text(memory.development.lastDevelopmentReason, 300)
                    if memory.development.lastDevelopmentReason
                    else None
                ),
            }
        ),
        events=events,
        rejectedCandidates=rejected,
    )
