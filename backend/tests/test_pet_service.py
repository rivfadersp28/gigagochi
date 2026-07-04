from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Message, Pet, User
from app.services.game_service import feed_pet
from app.services.pet_service import build_pet_response, create_pet


def make_db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_feed_increases_hunger() -> None:
    db = make_db()
    user = User()
    pet = Pet(
        user_id=user.id or uuid.uuid4(),
        original_description="small dragon",
        hunger=70,
        mood=80,
    )
    db.add(user)
    db.add(pet)
    db.commit()

    updated = feed_pet(db, pet)

    assert updated.hunger == 95


def test_create_pet_status_flow() -> None:
    db = make_db()
    user = User()
    db.add(user)
    db.commit()
    db.refresh(user)

    background_tasks = BackgroundTasks()
    pet = create_pet(db, user.id, "Маленький добрый дракон", background_tasks)

    assert pet.status == "generating"
    assert pet.hunger == 80
    assert pet.mood == 80
    assert len(background_tasks.tasks) == 1


def test_pet_response_includes_intro_message() -> None:
    db = make_db()
    user = User()
    pet = Pet(
        user_id=user.id or uuid.uuid4(),
        original_description="small dragon",
        status="ready",
    )
    intro = Message(pet=pet, role="assistant", content="Я появился. Как тебя зовут?")
    db.add(user)
    db.add(pet)
    db.add(intro)
    db.commit()
    db.refresh(pet, attribute_names=["images", "messages"])

    response = build_pet_response(pet)

    assert response.intro_message is not None
    assert response.intro_message.content == "Я появился. Как тебя зовут?"


def test_pet_response_does_not_treat_chat_reply_as_intro() -> None:
    db = make_db()
    user = User()
    pet = Pet(
        user_id=user.id or uuid.uuid4(),
        original_description="small dragon",
        status="ready",
    )
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    user_message = Message(pet=pet, role="user", content="Привет", created_at=started_at)
    assistant_message = Message(
        pet=pet,
        role="assistant",
        content="Привет!",
        created_at=started_at + timedelta(seconds=1),
    )
    db.add(user)
    db.add(pet)
    db.add(user_message)
    db.add(assistant_message)
    db.commit()
    db.refresh(pet, attribute_names=["images", "messages"])

    response = build_pet_response(pet)

    assert response.intro_message is None
