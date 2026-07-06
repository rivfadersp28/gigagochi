from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

PetStageValue = Literal["baby", "teen", "adult"]
PetStateValue = Literal["idle", "happy", "sad", "hungry"]
GeneratePetJobStatusValue = Literal["queued", "running", "succeeded", "failed"]
UserMemoryKind = Literal[
    "user_fact",
    "preference",
    "event",
    "deadline",
    "relationship",
    "routine",
    "goal",
    "promise",
    "emotion",
    "boundary",
]
FaceHintValue = Literal["happy", "excited", "curious", "content", "grumpy", "sleepy"]
PET_STAGE_VALUES: tuple[PetStageValue, ...] = ("baby", "teen", "adult")
PET_STATE_VALUES: tuple[PetStateValue, ...] = ("idle", "happy", "hungry", "sad")


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

    @model_validator(mode="after")
    def require_complete_image_set(self) -> GeneratePetAssetResponse:
        for stage in PET_STAGE_VALUES:
            stage_images = getattr(self.images, stage)
            for mood in PET_STATE_VALUES:
                if not stage_images.get(mood):
                    raise ValueError(f"missing generated image for {stage}/{mood}")
        return self


class GeneratePetJobResponse(BaseModel):
    jobId: str
    status: GeneratePetJobStatusValue
    createdAt: datetime
    updatedAt: datetime
    result: GeneratePetAssetResponse | None = None
    error: dict[str, Any] | None = None


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


class LocalChatHistoryItem(BaseModel):
    role: Literal["user", "pet"]
    text: str = Field(min_length=1, max_length=8000)


class LocalPetMemoryContextItem(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    kind: UserMemoryKind
    text: str = Field(min_length=1, max_length=500)
    dueAt: str | None = Field(default=None, max_length=80)


class LocalPetProactiveCandidate(BaseModel):
    memoryIds: list[str] = Field(default_factory=list, max_length=5)
    reason: str = Field(min_length=1, max_length=280)


class LocalPetMemoryContext(BaseModel):
    summary: str | None = Field(default=None, max_length=1000)
    userProfile: str | None = Field(default=None, max_length=1000)
    relevantMemories: list[LocalPetMemoryContextItem] = Field(default_factory=list, max_length=5)
    proactiveCandidate: LocalPetProactiveCandidate | None = None


class LocalChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    pet: LocalPetChatContext
    history: list[LocalChatHistoryItem] = Field(default_factory=list, max_length=12)
    memoryContext: LocalPetMemoryContext | None = None
    replyMaxChars: int | None = Field(default=None, ge=1, le=300)
    includeDebug: bool = False


class LocalChatDebug(BaseModel):
    usedFallback: bool = False
    validationFlags: list[str] = Field(default_factory=list)
    promptDebug: list[dict[str, Any]] = Field(default_factory=list)
    liteToolCalls: list[dict[str, Any]] = Field(default_factory=list)
    liteOverlayPatch: dict[str, Any] | None = None
    memoryDebug: dict[str, Any] | None = None


class TravelStoryScene(BaseModel):
    index: int = Field(ge=1, le=7)
    arc: Literal["beginning", "exploration", "discovery", "reward", "final"]
    title: str = Field(min_length=1, max_length=70)
    text: str = Field(min_length=1, max_length=260)
    visualBrief: str = Field(min_length=1, max_length=900)


class TravelStory(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=260)
    scenes: list[TravelStoryScene] = Field(min_length=5, max_length=7)


class TravelSceneImage(BaseModel):
    sceneIndex: int = Field(ge=1, le=7)
    imageUrl: str = Field(min_length=1)


class GenerateTravelRequest(BaseModel):
    pet: LocalPetChatContext
    includeDebug: bool = False


class GenerateTravelResponse(BaseModel):
    travelId: str
    generatedAt: datetime
    story: TravelStory
    images: list[TravelSceneImage] = Field(default_factory=list, max_length=7)
    debug: LocalChatDebug | None = None


class LocalChatResponse(BaseModel):
    reply: str
    moodHint: PetStateValue | None = None
    innerThought: str | None = Field(default=None, max_length=80)
    faceHint: FaceHintValue | None = None
    debug: LocalChatDebug | None = None


class LiteFactExtractionRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    reply: str = Field(min_length=1, max_length=8000)
    pet: LocalPetChatContext
    history: list[LocalChatHistoryItem] = Field(default_factory=list, max_length=12)
    includeDebug: bool = False


class LiteFactExtractionResponse(BaseModel):
    liteOverlayPatch: dict[str, Any] | None = None
    debug: LocalChatDebug | None = None


class MemoryExtractionRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    reply: str = Field(min_length=1, max_length=8000)
    pet: LocalPetChatContext
    history: list[LocalChatHistoryItem] = Field(default_factory=list, max_length=12)
    memoryContext: LocalPetMemoryContext | None = None
    nowIso: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=80)
    existingMemoryBrief: str | None = Field(default=None, max_length=4000)
    includeDebug: bool = False


class MemoryExtractionResponse(BaseModel):
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    debug: LocalChatDebug | None = None


class MemoryConsolidationRequest(BaseModel):
    pendingLearnings: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    existingMemories: list[dict[str, Any]] = Field(default_factory=list, max_length=80)
    summary: str | None = Field(default=None, max_length=1000)
    userProfile: str | None = Field(default=None, max_length=1000)
    nowIso: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=80)
    includeDebug: bool = False


class MemoryConsolidationResponse(BaseModel):
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=120)
    debug: LocalChatDebug | None = None


class LocalProactiveRequest(BaseModel):
    pet: LocalPetChatContext
    memoryContext: LocalPetMemoryContext
    nowIso: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=80)
    includeDebug: bool = False


class LocalProactiveResponse(BaseModel):
    reply: str
    moodHint: PetStateValue | None = None
    innerThought: str | None = Field(default=None, max_length=80)
    faceHint: FaceHintValue | None = None
    debug: LocalChatDebug | None = None
