from __future__ import annotations

import uuid

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models import Memory


def list_relevant_memories(db: Session, pet_id: uuid.UUID, limit: int = 10) -> list[Memory]:
    return list(
        db.scalars(
            select(Memory)
            .where(Memory.pet_id == pet_id)
            .order_by(desc(Memory.importance), desc(Memory.created_at))
            .limit(limit)
        )
    )


def save_memories(
    db: Session,
    pet_id: uuid.UUID,
    memories: list[dict],
    source_message_id: uuid.UUID | None,
) -> None:
    for item in memories:
        fact = str(item.get("fact", "")).strip()
        if not fact:
            continue
        importance = float(item.get("importance", 0.5))
        importance = max(0.0, min(1.0, importance))
        db.add(
            Memory(
                pet_id=pet_id,
                fact=fact[:500],
                importance=importance,
                source_message_id=source_message_id,
            )
        )
