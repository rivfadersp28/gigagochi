from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PetStageValue = Literal["baby", "teen", "adult"]
PetStateValue = Literal["idle", "happy", "sad", "hungry"]
AdminGenerateMode = Literal["profile_only", "full_assets"]


class AnonymousUserResponse(BaseModel):
    id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreatePetRequest(BaseModel):
    user_id: uuid.UUID
    description: str


class CreatePetResponse(BaseModel):
    id: uuid.UUID
    status: str


class PetImageResponse(BaseModel):
    stage: str
    state: str
    image_url: str

    model_config = ConfigDict(from_attributes=True)


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PetResponse(BaseModel):
    id: uuid.UUID
    status: str
    current_stage: str
    current_state: str
    hunger: int
    mood: int
    image_url: str | None
    images: list[PetImageResponse]
    created_at: datetime
    generation_error: str | None = None
    intro_message: MessageResponse | None = None


class FeedResponse(BaseModel):
    id: uuid.UUID
    hunger: int
    mood: int
    current_stage: str
    current_state: str
    image_url: str | None


class MessagesResponse(BaseModel):
    messages: list[MessageResponse]


class ChatRequest(BaseModel):
    message: str
    selected_stage: PetStageValue | None = None
    selected_state: PetStateValue | None = None


class ChatResponse(BaseModel):
    reply: str
    mood: int
    hunger: int
    current_stage: str
    current_state: str
    image_url: str | None


class GeneratePetRequest(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    style: str = "cute mobile game pet"
    stages: list[PetStageValue] = Field(default_factory=lambda: ["baby", "teen", "adult"])
    moods: list[PetStateValue] = Field(default_factory=lambda: ["idle", "happy", "hungry", "sad"])


class GeneratedPetImages(BaseModel):
    baby: dict[PetStateValue, str]
    teen: dict[PetStateValue, str]
    adult: dict[PetStateValue, str]


class GeneratePetAssetResponse(BaseModel):
    assetSetId: str
    generatedAt: datetime
    images: GeneratedPetImages
    spriteSheetUrl: str | None = None
    characterBible: dict[str, Any] | None = None


class AdminGenerateOneRequest(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    mode: AdminGenerateMode
    slotId: str | None = None
    includeDebugPrompts: bool = False
    includeSelfIntroBenchmark: bool = False
    includeConversationBenchmark: bool = False


class AdminBenchmarkTurnResponse(BaseModel):
    question: str
    reply: str
    moodHint: PetStateValue | None = None
    usedFallback: bool
    validationFlags: list[str]
    qualityScore: int | None = None
    qualityPassed: bool | None = None
    qualityFlags: list[str] = Field(default_factory=list)


class AdminSelfIntroBenchmarkResponse(AdminBenchmarkTurnResponse):
    turns: list[AdminBenchmarkTurnResponse] | None = None


class AdminDebugMessage(BaseModel):
    role: str
    content: str


class AdminGenerateDebugResponse(BaseModel):
    chatModel: str
    imageModel: str | None = None
    imageSize: str | None = None
    imageQuality: str | None = None
    characterBiblePrompt: str | None = None
    spriteSheetPrompt: str | None = None
    selfIntroBenchmarkMessages: list[AdminDebugMessage] | None = None


class AdminGenerateOneResponse(BaseModel):
    slotId: str | None = None
    description: str
    mode: AdminGenerateMode
    status: Literal["ready"]
    generatedAt: datetime
    durationMs: int
    assetSetId: str | None = None
    spriteSheetUrl: str | None = None
    images: GeneratedPetImages | None = None
    characterBible: dict[str, Any]
    benchmark: AdminSelfIntroBenchmarkResponse | None = None
    debug: AdminGenerateDebugResponse | None = None


class AdminGenerateError(BaseModel):
    slotId: str | None = None
    description: str
    status: Literal["failed"]
    code: str
    message: str
    durationMs: int


class LocalPetStats(BaseModel):
    hunger: int = Field(ge=0, le=100)
    happiness: int = Field(ge=0, le=100)
    energy: int = Field(ge=0, le=100)
    cleanliness: int = Field(ge=0, le=100)


class LocalPetChatContext(BaseModel):
    name: str | None = None
    description: str = Field(min_length=1, max_length=300)
    stage: PetStageValue
    mood: PetStateValue
    stats: LocalPetStats
    characterBible: dict[str, Any] | None = None
    loreMemories: list[str] = Field(default_factory=list, max_length=30)


class LocalChatHistoryItem(BaseModel):
    role: Literal["user", "pet"]
    text: str = Field(min_length=1, max_length=1500)


class LocalChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    pet: LocalPetChatContext
    history: list[LocalChatHistoryItem] = Field(default_factory=list, max_length=12)


class LocalChatResponse(BaseModel):
    reply: str = Field(max_length=1500)
    moodHint: PetStateValue | None = None
    loreMemoriesToSave: list[str] = Field(default_factory=list, max_length=10)
