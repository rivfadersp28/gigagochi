from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Pet

STAGES = ("baby", "teen", "adult")
STATES = ("idle", "happy", "sad", "hungry")


def clamp_stat(value: float) -> int:
    return max(0, min(100, round(value)))


def now_utc() -> datetime:
    return datetime.now(UTC)


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def calculate_stage(
    created_at: datetime,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    now = now or now_utc()
    created_at = ensure_aware(created_at)
    age_hours = max(0.0, (now - created_at).total_seconds() / 3600)

    if age_hours < settings.baby_duration_hours:
        return "baby"
    if age_hours < settings.baby_duration_hours + settings.teen_duration_hours:
        return "teen"
    return "adult"


def select_visual_state(hunger: int, mood: int) -> str:
    if hunger < 30:
        return "hungry"
    if mood < 30:
        return "sad"
    if hunger > 70 and mood > 70:
        return "happy"
    return "idle"


def tick_pet(pet: Pet, settings: Settings | None = None, now: datetime | None = None) -> Pet:
    settings = settings or get_settings()
    now = now or now_utc()
    last_tick_at = ensure_aware(pet.last_tick_at)
    elapsed_minutes = max(0.0, (now - last_tick_at).total_seconds() / 60)

    if elapsed_minutes > 0:
        pet.hunger = clamp_stat(pet.hunger - elapsed_minutes * settings.hunger_decay_per_min)
        pet.mood = clamp_stat(pet.mood - elapsed_minutes * settings.mood_decay_per_min)
        pet.last_tick_at = now

    pet.current_stage = calculate_stage(pet.created_at, now=now, settings=settings)
    return pet


def tick_and_commit(db: Session, pet: Pet) -> Pet:
    tick_pet(pet)
    db.add(pet)
    db.commit()
    db.refresh(pet)
    return pet


def feed_pet(db: Session, pet: Pet) -> Pet:
    tick_pet(pet)
    pet.hunger = min(100, pet.hunger + 25)
    db.add(pet)
    db.commit()
    db.refresh(pet)
    return pet
