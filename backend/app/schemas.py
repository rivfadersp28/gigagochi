from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.pet_memory.models import LocalChatDebug, PetMemoryPatch, PetMemoryStateV1

PetStageValue = Literal["baby", "teen", "adult"]
PetStateValue = Literal["idle", "happy", "sad", "hungry"]
ReplyMode = Literal["full", "lite"]
PET_STAGE_VALUES: tuple[PetStageValue, ...] = ("baby", "teen", "adult")
PET_STATE_VALUES: tuple[PetStateValue, ...] = ("idle", "happy", "hungry", "sad")
AdminGenerateMode = Literal["profile_only", "full_assets"]
CalibrationTaskType = Literal[
    "lore_pairwise",
    "dialogue_pairwise",
    "full_character_pairwise",
]
CalibrationPromptVariant = Literal[
    "current",
    "tiny_story_cards",
    "game_dialogue_cards",
    "mixed_cards",
]
CalibrationVoteOutcome = Literal["winner", "tie", "reject_all", "skip"]


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


class PromptLayers(BaseModel):
    ageStyle: bool = True
    moodStyle: bool = True
    statNeeds: bool = True
    characterCore: bool = True
    importedSeedchat: bool = True
    lore: bool = True
    characterBook: bool = True
    memory: bool = True
    referenceCards: bool = True
    dialogueMoves: bool = True
    proactivity: bool = True
    postHistoryInstructions: bool = True


class ChatRequest(BaseModel):
    message: str
    selected_stage: PetStageValue | None = None
    selected_state: PetStateValue | None = None
    promptLayers: PromptLayers = Field(default_factory=PromptLayers)


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
    useTemplatePresets: bool = False


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
    qualityAxes: dict[str, int] = Field(default_factory=dict)


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


class CalibrationLabStatusResponse(BaseModel):
    status: Literal["ready"]
    storage: Literal["jsonl"]
    taskCount: int
    voteCount: int


class CalibrationRunCreateRequest(BaseModel):
    taskType: CalibrationTaskType
    descriptions: list[str] = Field(min_length=1, max_length=50)
    count: int = Field(ge=1, le=50)
    candidatesPerTask: int = Field(ge=2, le=3)
    promptVariants: list[CalibrationPromptVariant] = Field(min_length=1, max_length=4)
    includeDebug: bool = False
    autoFilterBadCandidates: bool = False


class CalibrationRunCreateResponse(BaseModel):
    runId: str
    createdAt: str
    taskIds: list[str]


class CalibrationBenchmarkTurn(BaseModel):
    question: str
    reply: str
    moodHint: PetStateValue | None = None
    usedFallback: bool
    validationFlags: list[str] = Field(default_factory=list)
    qualityScore: int | None = None
    qualityPassed: bool | None = None
    qualityFlags: list[str] = Field(default_factory=list)
    qualityAxes: dict[str, int] = Field(default_factory=dict)


class CalibrationCandidate(BaseModel):
    candidateId: str
    promptVariant: CalibrationPromptVariant
    model: str
    seed: str
    characterBible: dict[str, Any] | None = None
    turns: list[CalibrationBenchmarkTurn] = Field(default_factory=list)
    autoScore: int
    qualityFlags: list[str] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)


class CalibrationTaskResponse(BaseModel):
    schemaVersion: Literal[1]
    taskId: str
    runId: str
    createdAt: str
    taskType: CalibrationTaskType
    description: str
    benchmarkQuestions: list[str]
    candidateIds: list[str]
    candidates: list[CalibrationCandidate]


class CalibrationVoteCreateRequest(BaseModel):
    taskId: str
    winnerCandidateId: str | None = None
    outcome: CalibrationVoteOutcome
    positiveTags: list[str] = Field(default_factory=list, max_length=20)
    negativeTags: list[str] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=2000)
    latencyMs: int = Field(default=0, ge=0)
    reviewerId: str = Field(default="local", max_length=80)


class CalibrationVoteResponse(BaseModel):
    schemaVersion: Literal[1]
    voteId: str
    taskId: str
    runId: str
    createdAt: str
    reviewerId: str
    outcome: CalibrationVoteOutcome
    winnerCandidateId: str | None = None
    positiveTags: list[str] = Field(default_factory=list)
    negativeTags: list[str] = Field(default_factory=list)
    note: str = ""
    latencyMs: int


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
    memory: PetMemoryStateV1 | None = None
    loreMemories: list[str] = Field(default_factory=list, max_length=30)


class LocalChatHistoryItem(BaseModel):
    role: Literal["user", "pet"]
    text: str = Field(min_length=1, max_length=8000)


class LocalChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    pet: LocalPetChatContext
    history: list[LocalChatHistoryItem] = Field(default_factory=list, max_length=12)
    includeDebug: bool = False
    promptLayers: PromptLayers = Field(default_factory=PromptLayers)
    replyMode: ReplyMode = "full"


class LocalChatResponse(BaseModel):
    reply: str
    moodHint: PetStateValue | None = None
    loreMemoriesToSave: list[str] = Field(default_factory=list, max_length=10)
    memoryPatch: PetMemoryPatch | None = None
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
