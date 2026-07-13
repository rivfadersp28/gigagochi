from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

PetStageValue = Literal["baby", "teen", "adult"]
PetStateValue = Literal["idle", "happy", "sad", "hungry"]
PetStatKeyValue = Literal["hunger", "happiness", "energy"]
GeneratePetJobStatusValue = Literal["queued", "running", "succeeded", "failed"]
GeneratePetJobPhaseValue = Literal[
    "queued",
    "generating_images",
    "generating_video",
    "generating_sad_image",
    "generating_sad_video",
    "generating_happy_image",
    "generating_happy_video",
    "generating_kandinsky",
    "completed",
]
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
HappinessDeltaValue = Literal[-80, -60, -40, -20, 0, 30, 100]
ComplimentKeyValue = Annotated[str, Field(min_length=1, max_length=120)]
PET_STAGE_VALUES: tuple[PetStageValue, ...] = ("baby", "teen", "adult")
PET_STATE_VALUES: tuple[PetStateValue, ...] = ("idle", "happy", "hungry", "sad")
HAPPINESS_DELTA_VALUES: tuple[HappinessDeltaValue, ...] = (-80, -60, -40, -20, 0, 30, 100)


class GeneratePetRequest(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    style: str = "cute mobile game pet"
    stages: list[PetStageValue] = Field(default_factory=lambda: ["baby", "teen", "adult"])
    moods: list[PetStateValue] = Field(default_factory=lambda: ["idle", "happy", "hungry", "sad"])


class GeneratedPetImages(BaseModel):
    baby: dict[PetStateValue, str]
    teen: dict[PetStateValue, str]
    adult: dict[PetStateValue, str]


class GeneratePetStaticAssetResponse(BaseModel):
    assetSetId: str
    generatedAt: datetime
    images: GeneratedPetImages
    videoUrl: str | None = None

    @model_validator(mode="after")
    def require_complete_image_set(self) -> GeneratePetStaticAssetResponse:
        for stage in PET_STAGE_VALUES:
            stage_images = getattr(self.images, stage)
            for mood in PET_STATE_VALUES:
                if not stage_images.get(mood):
                    raise ValueError(f"missing generated image for {stage}/{mood}")
        return self


class GeneratePetAssetResponse(GeneratePetStaticAssetResponse):
    videoUrl: str | None = None
    sadVideoUrl: str | None = None
    happyVideoUrl: str | None = None
    tapReactionImageUrl: str | None = None
    blinkImageUrl: str | None = None
    spriteSheetUrl: str | None = None
    characterBible: dict[str, Any] | None = None
    kandinskyAssets: GeneratePetStaticAssetResponse | None = None


class GeneratePetJobResponse(BaseModel):
    jobId: str
    status: GeneratePetJobStatusValue
    phase: GeneratePetJobPhaseValue = "queued"
    createdAt: datetime
    updatedAt: datetime
    result: GeneratePetAssetResponse | None = None
    error: dict[str, Any] | None = None
    backgroundError: dict[str, Any] | None = None
    comparisonError: dict[str, Any] | None = None


class GenerationDurationSummary(BaseModel):
    count: int
    averageSeconds: float | None = None
    medianSeconds: float | None = None
    p95Seconds: float | None = None
    minSeconds: float | None = None
    maxSeconds: float | None = None


class GenerationStatsRecentJob(BaseModel):
    jobId: str
    ownerName: str | None = None
    queuedAt: datetime
    status: str
    normalSeconds: float | None = None
    fullSeconds: float | None = None


class GenerationStatsResponse(BaseModel):
    windowDays: int
    totalJobs: int
    activeJobs: int
    failedJobs: int
    normal: GenerationDurationSummary
    full: GenerationDurationSummary
    recent: list[GenerationStatsRecentJob]


class LocalPetStats(BaseModel):
    hunger: int = Field(ge=0, le=100)
    happiness: int = Field(ge=0, le=100)
    energy: int = Field(ge=0, le=100)


class LocalPetStatsPatch(BaseModel):
    stats: dict[PetStatKeyValue, int] = Field(default_factory=dict)
    lastStatsTickAt: str | None = Field(default=None, max_length=80)
    lastStatTickAt: dict[PetStatKeyValue, str] | None = None


class LocalPetChatContext(BaseModel):
    name: str | None = None
    description: str = Field(min_length=1, max_length=300)
    stage: PetStageValue
    mood: PetStateValue
    stats: LocalPetStats
    characterBible: dict[str, Any] | None = None
    assetImages: dict[PetStageValue, dict[PetStateValue, str]] | None = None


class LocalChatHistoryItem(BaseModel):
    role: Literal["user", "pet"]
    text: str = Field(min_length=1, max_length=8000)
    createdAt: str | None = Field(default=None, max_length=80)


class LocalVisibleContext(BaseModel):
    lastPetLine: str = Field(min_length=1, max_length=800)


class LocalPetMemoryContextItem(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    kind: UserMemoryKind
    text: str = Field(min_length=1, max_length=500)
    memoryClass: Literal["core", "fact", "episode"] = "fact"
    recordedAt: str | None = Field(default=None, max_length=80)
    occurredAt: str | None = Field(default=None, max_length=80)
    lastMentionedAt: str | None = Field(default=None, max_length=80)
    dueAt: str | None = Field(default=None, max_length=80)


class LocalChatEpisodeMessage(BaseModel):
    role: Literal["user", "pet"]
    text: str = Field(min_length=1, max_length=8000)
    createdAt: str | None = Field(default=None, max_length=80)


class LocalChatMemoryEpisode(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    messages: list[LocalChatEpisodeMessage] = Field(default_factory=list, max_length=8)


class LocalPetProactiveCandidate(BaseModel):
    memoryIds: list[str] = Field(default_factory=list, max_length=5)
    episodeIds: list[str] = Field(default_factory=list, max_length=5)
    reason: str = Field(min_length=1, max_length=280)


class LocalPetMemoryContext(BaseModel):
    summary: str | None = Field(default=None, max_length=1000)
    userProfile: str | None = Field(default=None, max_length=1000)
    relevantMemories: list[LocalPetMemoryContextItem] = Field(default_factory=list, max_length=5)
    episodes: list[LocalChatMemoryEpisode] = Field(default_factory=list, max_length=3)
    proactiveCandidate: LocalPetProactiveCandidate | None = None


class LocalChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    pet: LocalPetChatContext
    history: list[LocalChatHistoryItem] = Field(default_factory=list, max_length=12)
    complimentHistory: list[ComplimentKeyValue] = Field(default_factory=list, max_length=500)
    visibleContext: LocalVisibleContext | None = None
    memoryContext: LocalPetMemoryContext | None = None
    nowIso: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=80)
    replyMaxChars: int | None = Field(default=None, ge=1, le=300)
    includeDebug: bool = False


class LocalChatDebug(BaseModel):
    usedFallback: bool = False
    validationFlags: list[str] = Field(default_factory=list)
    promptDebug: list[dict[str, Any]] = Field(default_factory=list)
    structuredReplyDebug: dict[str, Any] | None = None
    liteToolCalls: list[dict[str, Any]] = Field(default_factory=list)
    liteOverlayPatch: dict[str, Any] | None = None
    storyLibraryPatch: dict[str, Any] | None = None
    storyLibraryDebug: dict[str, Any] | None = None
    contextRoutingDebug: dict[str, Any] | None = None
    memoryDebug: dict[str, Any] | None = None


class LocalPetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=32)


class TravelStoryScene(BaseModel):
    index: int = Field(ge=1, le=7)
    arc: Literal["beginning", "exploration", "discovery", "reward", "final"]
    title: str = Field(min_length=1, max_length=70)
    text: str = Field(min_length=1, max_length=260)
    visualBrief: str = Field(min_length=1, max_length=1800)


class TravelStory(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    scenes: list[TravelStoryScene] = Field(min_length=7, max_length=7)


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
    happinessDelta: HappinessDeltaValue = 0
    complimentKey: str | None = Field(default=None, min_length=1, max_length=120)
    innerThought: str | None = Field(default=None, max_length=80)
    faceHint: FaceHintValue | None = None
    petPatch: LocalPetPatch | None = None
    storyLibraryPatch: dict[str, Any] | None = None
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


class LocalPushRequest(BaseModel):
    pet: LocalPetChatContext
    memoryContext: LocalPetMemoryContext | None = None
    reason: str | None = Field(default=None, max_length=280)
    nowIso: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=80)
    includeDebug: bool = False


class LocalAmbientRequest(BaseModel):
    pet: LocalPetChatContext
    history: list[LocalChatHistoryItem] = Field(default_factory=list, max_length=12)
    recentAmbientReplies: list[str] = Field(default_factory=list, max_length=10)
    memoryContext: LocalPetMemoryContext | None = None
    replyMaxChars: int | None = Field(default=None, ge=1, le=300)
    nowIso: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=80)
    includeDebug: bool = False


class LocalProactiveResponse(BaseModel):
    reply: str
    moodHint: PetStateValue | None = None
    innerThought: str | None = Field(default=None, max_length=80)
    faceHint: FaceHintValue | None = None
    debug: LocalChatDebug | None = None


class LocalPetPushSnapshotRequest(BaseModel):
    petId: str = Field(min_length=1, max_length=120)
    pet: LocalPetChatContext
    history: list[LocalChatHistoryItem] = Field(default_factory=list, max_length=12)
    recentAmbientReplies: list[str] = Field(default_factory=list, max_length=10)
    memoryContext: LocalPetMemoryContext | None = None
    createdAt: str | None = Field(default=None, max_length=80)
    updatedAt: str | None = Field(default=None, max_length=80)
    lastStatsTickAt: str | None = Field(default=None, max_length=80)
    lastStatTickAt: dict[PetStatKeyValue, str] | None = None
    zeroStatSinceAt: dict[PetStatKeyValue, str] | None = None
    diedAt: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=80)


class LocalPetPushSnapshotResponse(BaseModel):
    registered: bool
    telegramId: int
    updatedAt: str
    resetPet: bool = False
    statsPatch: LocalPetStatsPatch | None = None
    storyLibraryPatch: dict[str, Any] | None = None
    liteOverlayPatch: dict[str, Any] | None = None
    recentStoryEventsPatch: dict[str, Any] | None = None
