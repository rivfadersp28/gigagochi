from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


json_type = JSON().with_variant(JSONB, "postgresql")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    pets: Mapped[list[Pet]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Pet(Base):
    __tablename__ = "pets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    original_description: Mapped[str] = mapped_column(Text)
    character_profile_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    current_stage: Mapped[str] = mapped_column(String(20), default="baby")
    hunger: Mapped[int] = mapped_column(Integer, default=80)
    mood: Mapped[int] = mapped_column(Integer, default=80)
    status: Mapped[str] = mapped_column(String(20), default="generating")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_tick_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    generation_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="pets")
    images: Mapped[list[PetImage]] = relationship(
        back_populates="pet",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="pet",
        cascade="all, delete-orphan",
    )
    memories: Mapped[list[Memory]] = relationship(
        back_populates="pet",
        cascade="all, delete-orphan",
    )


class PetImage(Base):
    __tablename__ = "pet_images"
    __table_args__ = (
        UniqueConstraint("pet_id", "stage", "state", name="uq_pet_images_pet_stage_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20))
    image_url: Mapped[str] = mapped_column(Text)
    generation_prompt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    pet: Mapped[Pet] = relationship(back_populates="images")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    pet: Mapped[Pet] = relationship(back_populates="messages")
    sourced_memories: Mapped[list[Memory]] = relationship(back_populates="source_message")


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pet_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"), index=True)
    fact: Mapped[str] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_referenced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    pet: Mapped[Pet] = relationship(back_populates="memories")
    source_message: Mapped[Message | None] = relationship(back_populates="sourced_memories")
