from __future__ import annotations

from app.services.pet_memory.models import CanonMemoryFact, PetMemoryStateV1
from app.services.pet_memory.normalizer import MAX_CANON_FACTS, clamp_float


def _decay_delta(fact: CanonMemoryFact) -> float:
    if fact.pinned or fact.type == "milestone" or fact.importance >= 0.8:
        return 0
    if fact.confidence < 0.5 and fact.useCount == 0:
        return 0.08
    if fact.importance < 0.35:
        return 0.05
    return 0.02


def apply_memory_decay(
    memory: PetMemoryStateV1,
    *,
    used_canon_fact_ids: tuple[str, ...] = (),
    confirmed_canon_fact_ids: tuple[str, ...] = (),
    now: str,
) -> tuple[list[CanonMemoryFact], list[str]]:
    used_ids = set(used_canon_fact_ids)
    confirmed_ids = set(confirmed_canon_fact_ids)
    upserts: list[CanonMemoryFact] = []

    for fact in memory.canon:
        if fact.id in used_ids or fact.id in confirmed_ids:
            reinforced = fact.model_copy(
                update={
                    "useCount": fact.useCount + 1,
                    "lastUsedAt": now,
                    "lastReinforcedAt": now if fact.id in confirmed_ids else fact.lastReinforcedAt,
                    "confidence": clamp_float(
                        fact.confidence + (0.08 if fact.id in confirmed_ids else 0.02)
                    ),
                    "importance": clamp_float(
                        fact.importance + (0.06 if fact.id in confirmed_ids else 0.015)
                    ),
                    "decayScore": clamp_float(
                        fact.decayScore - (0.18 if fact.id in confirmed_ids else 0.06)
                    ),
                    "updatedAt": now,
                }
            )
            if reinforced != fact:
                upserts.append(reinforced)
            continue

        decayed = fact.model_copy(
            update={
                "decayScore": clamp_float(fact.decayScore + _decay_delta(fact)),
                "updatedAt": now,
            }
        )
        if decayed != fact:
            upserts.append(decayed)

    merged = {fact.id: fact for fact in memory.canon}
    merged.update({fact.id: fact for fact in upserts})
    should_trim = len(merged) > MAX_CANON_FACTS
    deletes: list[str] = []
    if should_trim:
        removable = sorted(
            (
                fact
                for fact in merged.values()
                if not fact.pinned and fact.type != "milestone" and fact.decayScore > 0.85
            ),
            key=lambda fact: (fact.importance, -fact.decayScore, fact.updatedAt),
        )
        overflow = len(merged) - MAX_CANON_FACTS
        deletes = [fact.id for fact in removable[:overflow]]

    return upserts, deletes
