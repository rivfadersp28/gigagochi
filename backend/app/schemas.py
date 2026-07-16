from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PetStageValue = Literal["baby", "teen", "adult"]
PetStateValue = Literal["idle", "happy", "sad", "hungry"]
PetStatKeyValue = Literal["hunger", "happiness", "energy"]
AssetImageUrl = Annotated[str, Field(max_length=1000)]
ShortPetName = Annotated[str, Field(max_length=80)]
RecentAmbientReply = Annotated[str, Field(max_length=1000)]
TimestampText = Annotated[str, Field(max_length=80)]
InteractiveTravelId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=120,
        pattern=r"^interactive-travel-[A-Za-z0-9_-]+$",
    ),
]
SNAPSHOT_MAX_SERIALIZED_BYTES = 262_144
CHARACTER_BIBLE_MAX_SERIALIZED_BYTES = 262_144
CHARACTER_BIBLE_MAX_DEPTH = 20
CHARACTER_BIBLE_MAX_NODES = 10_000
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


class TmaCapabilitiesResponse(BaseModel):
    telegramUserId: int
    debugMenu: bool
    interactiveTravel: bool


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
InteractiveTravelSuggestionValue = Annotated[str, Field(min_length=1, max_length=72)]
InteractiveTravelTaskId = Annotated[str, Field(min_length=1, max_length=40)]
InteractiveTravelLeadIn = Annotated[str, Field(min_length=1, max_length=200)]
InteractiveTravelTaskSituation = Annotated[str, Field(min_length=1, max_length=300)]
InteractiveTravelTaskQuestion = Annotated[str, Field(min_length=1, max_length=120)]
InteractiveTravelTaskChoice = Annotated[str, Field(min_length=1, max_length=80)]
InteractiveTravelTaskExplanation = Annotated[str, Field(min_length=1, max_length=300)]
InteractiveTravelTaskOutcome = Annotated[str, Field(min_length=1, max_length=700)]
PET_STAGE_VALUES: tuple[PetStageValue, ...] = ("baby", "teen", "adult")
PET_STATE_VALUES: tuple[PetStateValue, ...] = ("idle", "happy", "hungry", "sad")
HAPPINESS_DELTA_VALUES: tuple[HappinessDeltaValue, ...] = (-80, -60, -40, -20, 0, 30, 100)


class GeneratePetRequest(BaseModel):
    description: str = Field(min_length=1, max_length=300)


class OutfitSimplificationRequest(BaseModel):
    request: str = Field(min_length=1, max_length=1000)
    petDescription: str = Field(min_length=1, max_length=300)


class OutfitSimplificationResponse(BaseModel):
    item: str = Field(min_length=1, max_length=80)
    displayItem: str = Field(min_length=1, max_length=80)
    generationDescription: str = Field(min_length=1, max_length=300)


class GenerateOutfitRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=300)
    idleImageUrl: AssetImageUrl
    sadImageUrl: AssetImageUrl
    happyImageUrl: AssetImageUrl


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
    lastStatTickAt: dict[PetStatKeyValue, TimestampText] | None = None


class LocalPetChatContext(BaseModel):
    petId: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    name: ShortPetName | None = None
    description: str = Field(min_length=1, max_length=300)
    stage: PetStageValue
    mood: PetStateValue
    stats: LocalPetStats
    characterBible: dict[str, Any] | None = None
    assetImages: dict[PetStageValue, dict[PetStateValue, AssetImageUrl]] | None = None

    @field_validator("petId", mode="before")
    @classmethod
    def normalize_pet_id(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("characterBible", mode="before")
    @classmethod
    def validate_character_bible_shape(cls, value: Any) -> Any:
        if value is None or not isinstance(value, dict):
            return value

        nodes = 0
        stack: list[tuple[Any, int]] = [(value, 1)]
        while stack:
            current, depth = stack.pop()
            nodes += 1
            if nodes > CHARACTER_BIBLE_MAX_NODES:
                raise ValueError("character bible contains too many values")
            if depth > CHARACTER_BIBLE_MAX_DEPTH:
                raise ValueError("character bible is nested too deeply")
            if isinstance(current, dict):
                stack.extend((item, depth + 1) for item in current.values())
            elif isinstance(current, list):
                stack.extend((item, depth + 1) for item in current)

        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(serialized) > CHARACTER_BIBLE_MAX_SERIALIZED_BYTES:
            raise ValueError("character bible exceeds the persisted size limit")
        return value


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


class InteractiveTravelStatImpact(BaseModel):
    stat: PetStatKeyValue
    amount: int = Field(ge=-15, le=15)
    reason: str = Field(min_length=1, max_length=280)


class InteractiveTravelIntroReaction(BaseModel):
    text: str = Field(min_length=1, max_length=220)
    tone: Literal[
        "enthusiastic",
        "confused",
        "worried",
        "amused",
        "indignant",
        "determined",
        "surprised",
    ]


class InteractiveTravelResult(BaseModel):
    text: str = Field(min_length=1, max_length=700)
    adviceAssessment: Literal["helpful", "harmful", "ambiguous"]
    reaction: str = Field(min_length=1, max_length=220)
    reactionTone: Literal[
        "enthusiastic",
        "confused",
        "worried",
        "amused",
        "indignant",
        "determined",
        "surprised",
    ]
    consequence: str = Field(min_length=1, max_length=280)
    outcomeValence: Literal["positive", "negative"]
    experienceGained: int = Field(default=0, ge=0, le=150)
    statImpacts: list[InteractiveTravelStatImpact] = Field(default_factory=list, max_length=2)


class InteractiveTravelTransition(BaseModel):
    elapsedHours: int = Field(ge=0, le=8)
    summary: str = Field(min_length=1, max_length=240)
    departureHook: str | None = Field(default=None, min_length=1, max_length=280)
    continuityAnchor: str | None = Field(default=None, min_length=1, max_length=60)


class InteractiveTravelPart(BaseModel):
    partNumber: int = Field(ge=1, le=4)
    title: str = Field(min_length=1, max_length=120)
    storyText: str = Field(min_length=1, max_length=700)
    transition: InteractiveTravelTransition | None = None
    challenge: str = Field(min_length=1, max_length=280)
    actionSuggestions: list[InteractiveTravelSuggestionValue] = Field(
        default_factory=list,
        max_length=4,
    )
    backgroundImageUrl: str | None = Field(default=None, min_length=1, max_length=1000)
    backgroundVideoUrl: str | None = Field(default=None, min_length=1, max_length=1000)
    answer: str | None = Field(default=None, min_length=1, max_length=1000)
    result: InteractiveTravelResult | None = None

    @model_validator(mode="after")
    def validate_answer_and_result(self) -> InteractiveTravelPart:
        if any(
            (
                self.answer is None and self.result is not None,
                self.answer is not None and self.result is None,
            )
        ):
            raise ValueError("interactive travel answer and result must appear together")
        return self


class InteractiveTravelTaskPlan(BaseModel):
    taskId: InteractiveTravelTaskId
    leadIn: InteractiveTravelLeadIn
    situation: InteractiveTravelTaskSituation
    question: InteractiveTravelTaskQuestion
    choices: list[InteractiveTravelTaskChoice] = Field(min_length=4, max_length=4)
    correctChoice: InteractiveTravelTaskChoice
    explanation: InteractiveTravelTaskExplanation | None = None
    choiceOutcomes: list[InteractiveTravelTaskOutcome] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_choices(self) -> InteractiveTravelTaskPlan:
        normalized = [choice.casefold() for choice in self.choices]
        if len(set(normalized)) != 4:
            raise ValueError("interactive travel task choices must be unique")
        if self.correctChoice not in self.choices:
            raise ValueError("interactive travel correct choice must be one of the choices")
        if self.choiceOutcomes and len(self.choiceOutcomes) != len(self.choices):
            raise ValueError("interactive travel task outcomes must match the task choices")
        return self


class InteractiveTravelPlan(BaseModel):
    version: Literal["task-bank-location-v4"] = "task-bank-location-v4"
    tasks: list[InteractiveTravelTaskPlan] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_unique_tasks(self) -> InteractiveTravelPlan:
        if len({task.taskId for task in self.tasks}) != 4:
            raise ValueError("interactive travel plan tasks must be unique")
        return self


class InteractiveTravelState(BaseModel):
    travelId: InteractiveTravelId
    generatedAt: datetime
    destination: str = Field(min_length=1, max_length=500)
    overallTitle: str = Field(min_length=1, max_length=120)
    plan: InteractiveTravelPlan | None = None
    introReaction: InteractiveTravelIntroReaction | None = None
    generationStatus: Literal["generating", "ready", "failed"] = "ready"
    generationError: str | None = Field(default=None, min_length=1, max_length=300)
    parts: list[InteractiveTravelPart] = Field(min_length=1, max_length=4)
    completed: bool = False
    outcomeValence: Literal["positive", "negative"] | None = None
    statImpact: InteractiveTravelStatImpact | None = None

    @model_validator(mode="after")
    def validate_part_sequence(self) -> InteractiveTravelState:
        if self.generationStatus == "generating":
            if self.plan is not None:
                raise ValueError("generating interactive travel cannot contain a plan")
            if self.completed:
                raise ValueError("generating interactive travel cannot be completed")
        elif self.generationStatus == "ready" and self.plan is None:
            raise ValueError("ready interactive travel must contain a plan")
        elif self.generationStatus == "failed" and (self.plan is not None or self.completed):
            raise ValueError("failed interactive travel cannot contain a plan or be completed")
        if self.generationStatus == "failed" and self.generationError is None:
            raise ValueError("failed interactive travel generation must contain an error")
        if self.generationStatus != "failed" and self.generationError is not None:
            raise ValueError("interactive travel generation error requires failed status")
        expected = list(range(1, len(self.parts) + 1))
        actual = [part.partNumber for part in self.parts]
        if actual != expected:
            raise ValueError("interactive travel parts must be sequential")
        if self.parts[0].transition is not None:
            raise ValueError("the first interactive travel part cannot have elapsed story time")
        if any(part.transition is None for part in self.parts[1:]):
            raise ValueError("later interactive travel parts must have elapsed story time")
        if self.plan is not None:
            for part, task in zip(self.parts, self.plan.tasks, strict=False):
                if part.storyText != f"{task.leadIn} {task.situation}":
                    raise ValueError("interactive travel part story must match its planned task")
                if part.challenge != task.question:
                    raise ValueError("interactive travel challenge must match its planned task")
                if part.actionSuggestions != task.choices:
                    raise ValueError("interactive travel choices must match their planned task")
        if self.completed:
            if len(self.parts) != 4:
                raise ValueError("completed interactive travel must contain four parts")
            if any(part.result is None for part in self.parts):
                raise ValueError("completed interactive travel cannot contain a pending part")
            if self.outcomeValence is None:
                raise ValueError("completed interactive travel must contain a final outcome")
        else:
            if self.parts[-1].result is not None:
                raise ValueError("incomplete interactive travel must end with a pending part")
            if any(part.result is None for part in self.parts[:-1]):
                raise ValueError("only the last interactive travel part may be pending")
            if self.outcomeValence is not None or self.statImpact is not None:
                raise ValueError("incomplete interactive travel cannot contain a final outcome")
        return self


class StartInteractiveTravelRequest(BaseModel):
    pet: LocalPetChatContext
    destination: str = Field(min_length=1, max_length=500)
    includeDebug: bool = False


class InteractiveTravelSuggestionsRequest(BaseModel):
    pet: LocalPetChatContext
    includeDebug: bool = False


class InteractiveTravelSuggestionsResponse(BaseModel):
    destinations: list[InteractiveTravelSuggestionValue] = Field(min_length=3, max_length=3)
    debug: LocalChatDebug | None = None


class IllustrateInteractiveTravelPartRequest(BaseModel):
    pet: LocalPetChatContext
    travelId: InteractiveTravelId
    destination: str = Field(min_length=1, max_length=500)
    partNumber: int = Field(ge=1, le=4)
    title: str = Field(min_length=1, max_length=120)
    storyText: str = Field(min_length=1, max_length=700)


class InteractiveTravelIllustrationResponse(BaseModel):
    partNumber: int = Field(ge=1, le=4)
    imageUrl: str = Field(min_length=1, max_length=1000)


class AnimateInteractiveTravelPartRequest(BaseModel):
    travelId: InteractiveTravelId
    partNumber: int = Field(ge=1, le=4)


class InteractiveTravelAnimationResponse(BaseModel):
    partNumber: int = Field(ge=1, le=4)
    videoUrl: str = Field(min_length=1, max_length=1000)


class ContinueInteractiveTravelRequest(BaseModel):
    pet: LocalPetChatContext
    travel: InteractiveTravelState
    advice: str = Field(min_length=1, max_length=1000)
    includeDebug: bool = False


class AutomaticInteractiveStoryChoiceRequest(BaseModel):
    choice: str = Field(min_length=1, max_length=1000)


class InteractiveTravelResponse(BaseModel):
    travel: InteractiveTravelState
    debug: LocalChatDebug | None = None


TravelVideoPrototypeStatusValue = Literal[
    "queued",
    "writing",
    "illustrating",
    "animating",
    "ready",
    "failed",
]


class StartTravelVideoPrototypeRequest(BaseModel):
    pet: LocalPetChatContext
    prompt: str = Field(min_length=1, max_length=1000)
    requestKey: str = Field(
        min_length=36,
        max_length=36,
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
    )

    @field_validator("prompt", mode="before")
    @classmethod
    def normalize_prompt(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class TravelVideoPrototypeResponse(BaseModel):
    jobId: str = Field(
        min_length=55,
        max_length=55,
        pattern=r"^travel-video-prototype-[a-f0-9]{32}$",
    )
    status: TravelVideoPrototypeStatusValue
    prompt: str = Field(min_length=1, max_length=1000)
    title: str | None = Field(default=None, max_length=100)
    scenario: str | None = Field(default=None, max_length=1600)
    imageUrl: str | None = None
    videoUrl: str | None = None
    error: str | None = Field(default=None, max_length=300)
    createdAt: str
    updatedAt: str


class InteractiveTravelDemoResponse(BaseModel):
    demoId: str = Field(min_length=1, max_length=120)
    travel: InteractiveTravelState


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
    recentAmbientReplies: list[RecentAmbientReply] = Field(default_factory=list, max_length=30)
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
    petId: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    snapshotWriterId: str | None = Field(
        default=None,
        min_length=16,
        max_length=120,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    snapshotRevision: int | None = Field(default=None, ge=1, le=9_007_199_254_740_991)
    pet: LocalPetChatContext
    history: list[LocalChatHistoryItem] = Field(default_factory=list, max_length=12)
    recentAmbientReplies: list[RecentAmbientReply] = Field(default_factory=list, max_length=30)
    memoryContext: LocalPetMemoryContext | None = None
    createdAt: str | None = Field(default=None, max_length=80)
    updatedAt: str | None = Field(default=None, max_length=80)
    lastStatsTickAt: str | None = Field(default=None, max_length=80)
    lastStatTickAt: dict[PetStatKeyValue, TimestampText] | None = None
    zeroStatSinceAt: dict[PetStatKeyValue, TimestampText] | None = None
    diedAt: str | None = Field(default=None, max_length=80)
    timezone: str | None = Field(default=None, max_length=80)

    @field_validator("petId", mode="before")
    @classmethod
    def normalize_pet_id(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_serialized_size(self) -> LocalPetPushSnapshotRequest:
        if (self.snapshotWriterId is None) != (self.snapshotRevision is None):
            raise ValueError("snapshot writer and revision must be provided together")
        if len(self.model_dump_json().encode("utf-8")) > SNAPSHOT_MAX_SERIALIZED_BYTES:
            raise ValueError("snapshot payload exceeds the persisted size limit")
        return self


class LocalPetPushSnapshotResponse(BaseModel):
    registered: bool
    telegramId: int
    updatedAt: str
    resetPet: bool = False
    statsPatch: LocalPetStatsPatch | None = None
    storyLibraryPatch: dict[str, Any] | None = None
    liteOverlayPatch: dict[str, Any] | None = None
    recentStoryEventsPatch: dict[str, Any] | None = None


class LocalPetPushSnapshotDeleteResponse(BaseModel):
    unregistered: bool
    petId: str = Field(min_length=1, max_length=120)
