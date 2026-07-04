from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.config import Settings
from app.models import Pet
from app.services.game_service import calculate_stage, select_visual_state, tick_pet


def test_hunger_and_mood_decay() -> None:
    settings = Settings(hunger_decay_per_min=1, mood_decay_per_min=2)
    created_at = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    pet = Pet(
        original_description="small dragon",
        user_id=UUID("00000000-0000-0000-0000-000000000001"),
        hunger=80,
        mood=80,
        created_at=created_at,
        last_tick_at=created_at,
    )

    tick_pet(pet, settings=settings, now=created_at + timedelta(minutes=10))

    assert pet.hunger == 70
    assert pet.mood == 60


def test_stage_transition() -> None:
    settings = Settings(baby_duration_hours=1, teen_duration_hours=2)
    created_at = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)

    assert calculate_stage(created_at, created_at + timedelta(minutes=30), settings) == "baby"
    assert calculate_stage(created_at, created_at + timedelta(hours=2), settings) == "teen"
    assert calculate_stage(created_at, created_at + timedelta(hours=4), settings) == "adult"


def test_visual_state_priority() -> None:
    assert select_visual_state(hunger=20, mood=20) == "hungry"
    assert select_visual_state(hunger=50, mood=20) == "sad"
    assert select_visual_state(hunger=80, mood=80) == "happy"
    assert select_visual_state(hunger=50, mood=50) == "idle"
