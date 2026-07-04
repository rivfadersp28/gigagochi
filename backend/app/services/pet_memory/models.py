from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryFactType = Literal[
    "world_fact",
    "home_fact",
    "friend_fact",
    "family_fact",
    "origin_fact",
    "preference_fact",
    "fear_fact",
    "habit_fact",
    "voice_fact",
    "milestone",
]
MemoryCandidateType = MemoryFactType | Literal["user_fact", "relationship_event"]
MemorySource = Literal["model", "user", "system"]
ThreadStatus = Literal["open", "paused", "resolved"]
ReflectionScope = Literal["self", "user", "relationship", "world"]
GoalKind = Literal[
    "learn_about_user",
    "share_lore",
    "seek_care",
    "return_to_thread",
    "play",
    "comfort_user",
]
GoalStatus = Literal["active", "paused", "completed", "expired"]
ProactiveKind = Literal[
    "ask_user",
    "continue_lore",
    "return_to_thread",
    "request_care",
    "share_observation",
    "none",
]


class MemoryBaseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CanonMemoryFact(MemoryBaseModel):
    id: str
    type: MemoryFactType
    text: str = Field(max_length=500)
    source: MemorySource = "model"
    confidence: float = Field(default=0.6, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    useCount: int = Field(default=0, ge=0)
    decayScore: float = Field(default=0, ge=0, le=1)
    createdAt: str
    updatedAt: str
    lastUsedAt: str | None = None
    lastReinforcedAt: str | None = None
    relatedThreadId: str | None = None
    pinned: bool = False


class RelationshipEvent(MemoryBaseModel):
    id: str
    text: str = Field(max_length=500)
    importance: float = Field(default=0.5, ge=0, le=1)
    createdAt: str
    updatedAt: str


class UserFact(MemoryBaseModel):
    id: str
    text: str = Field(max_length=500)
    confidence: float = Field(default=0.7, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    createdAt: str
    updatedAt: str
    lastUsedAt: str | None = None


class RelationshipMemory(MemoryBaseModel):
    userName: str | None = Field(default=None, max_length=80)
    preferredAddress: str | None = Field(default=None, max_length=80)
    trust: int = Field(default=20, ge=0, le=100)
    attachment: int = Field(default=20, ge=0, le=100)
    familiarity: int = Field(default=0, ge=0, le=100)
    sharedEvents: list[RelationshipEvent] = Field(default_factory=list)
    userFacts: list[UserFact] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    lastWarmMomentAt: str | None = None


class ConversationThread(MemoryBaseModel):
    id: str
    topic: str = Field(max_length=160)
    summary: str = Field(max_length=500)
    status: ThreadStatus = "open"
    priority: float = Field(default=0.5, ge=0, le=1)
    createdAt: str
    updatedAt: str
    lastMentionedAt: str | None = None
    suggestedFollowUp: str | None = Field(default=None, max_length=240)
    lastQuestionAskedAt: str | None = None


class ReflectionMemory(MemoryBaseModel):
    id: str
    text: str = Field(max_length=500)
    scope: ReflectionScope = "relationship"
    sourceEventIds: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    createdAt: str
    updatedAt: str
    lastUsedAt: str | None = None


class ActiveGoal(MemoryBaseModel):
    id: str
    kind: GoalKind
    text: str = Field(max_length=300)
    priority: float = Field(default=0.5, ge=0, le=1)
    status: GoalStatus = "active"
    createdAt: str
    updatedAt: str
    expiresAt: str | None = None
    relatedThreadId: str | None = None


class DevelopmentState(MemoryBaseModel):
    trust: int = Field(default=20, ge=0, le=100)
    attachment: int = Field(default=20, ge=0, le=100)
    curiosity: int = Field(default=45, ge=0, le=100)
    confidence: int = Field(default=30, ge=0, le=100)
    loneliness: int = Field(default=10, ge=0, le=100)
    playfulness: int = Field(default=50, ge=0, le=100)
    lastDevelopmentReason: str | None = Field(default=None, max_length=300)


class PetEvent(MemoryBaseModel):
    id: str
    kind: Literal[
        "user_message",
        "pet_reply",
        "memory_accepted",
        "relationship",
        "development",
        "thread",
        "goal",
        "care",
        "reflection",
    ]
    text: str = Field(max_length=500)
    importance: float = Field(default=0.5, ge=0, le=1)
    createdAt: str
    relatedMemoryId: str | None = None


class RejectedMemoryCandidate(MemoryBaseModel):
    id: str
    type: MemoryCandidateType
    text: str = Field(max_length=500)
    reason: str = Field(max_length=160)
    confidence: float = Field(default=0, ge=0, le=1)
    importance: float = Field(default=0, ge=0, le=1)
    createdAt: str


class PetMemoryStateV1(MemoryBaseModel):
    schemaVersion: Literal[1] = 1
    canon: list[CanonMemoryFact] = Field(default_factory=list)
    relationship: RelationshipMemory = Field(default_factory=RelationshipMemory)
    threads: list[ConversationThread] = Field(default_factory=list)
    reflections: list[ReflectionMemory] = Field(default_factory=list)
    activeGoals: list[ActiveGoal] = Field(default_factory=list)
    development: DevelopmentState = Field(default_factory=DevelopmentState)
    events: list[PetEvent] = Field(default_factory=list)
    rejectedCandidates: list[RejectedMemoryCandidate] = Field(default_factory=list)


class ProactiveIntent(MemoryBaseModel):
    kind: ProactiveKind = "none"
    text: str | None = Field(default=None, max_length=240)
    priority: float = Field(default=0, ge=0, le=1)


class MemoryCandidate(MemoryBaseModel):
    type: MemoryCandidateType
    text: str = Field(max_length=500)
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.6, ge=0, le=1)
    sourceSpan: str | None = Field(default=None, max_length=240)


class RelationshipPatch(MemoryBaseModel):
    userName: str | None = Field(default=None, max_length=80)
    preferredAddress: str | None = Field(default=None, max_length=80)
    trustDelta: int | None = Field(default=None, ge=-5, le=5)
    attachmentDelta: int | None = Field(default=None, ge=-5, le=5)
    familiarityDelta: int | None = Field(default=None, ge=-5, le=5)
    sharedEvent: str | None = Field(default=None, max_length=500)
    userFact: str | None = Field(default=None, max_length=500)


class DevelopmentPatch(MemoryBaseModel):
    trustDelta: int | None = Field(default=None, ge=-5, le=5)
    attachmentDelta: int | None = Field(default=None, ge=-5, le=5)
    curiosityDelta: int | None = Field(default=None, ge=-5, le=5)
    confidenceDelta: int | None = Field(default=None, ge=-5, le=5)
    lonelinessDelta: int | None = Field(default=None, ge=-5, le=5)
    playfulnessDelta: int | None = Field(default=None, ge=-5, le=5)
    reason: str | None = Field(default=None, max_length=300)


class ThreadPatchOpen(MemoryBaseModel):
    topic: str = Field(max_length=160)
    summary: str = Field(max_length=500)
    suggestedFollowUp: str | None = Field(default=None, max_length=240)
    priority: float = Field(default=0.5, ge=0, le=1)


class ThreadPatchUpdate(MemoryBaseModel):
    threadId: str
    summary: str | None = Field(default=None, max_length=500)
    suggestedFollowUp: str | None = Field(default=None, max_length=240)
    status: ThreadStatus | None = None


class ThreadPatch(MemoryBaseModel):
    open: ThreadPatchOpen | None = None
    update: ThreadPatchUpdate | None = None


class GoalPatchOpen(MemoryBaseModel):
    kind: GoalKind
    text: str = Field(max_length=300)
    priority: float = Field(default=0.5, ge=0, le=1)
    expiresAt: str | None = None
    relatedThreadId: str | None = None


class GoalPatchUpdate(MemoryBaseModel):
    goalId: str
    status: GoalStatus | None = None
    priority: float | None = Field(default=None, ge=0, le=1)


class GoalPatch(MemoryBaseModel):
    open: GoalPatchOpen | None = None
    update: GoalPatchUpdate | None = None


class PetReplyModelOutputV2(MemoryBaseModel):
    reply: str = Field(max_length=1500)
    moodHint: Literal["idle", "happy", "hungry", "sad"] | None = None
    proactiveIntent: ProactiveIntent | None = None
    memoryCandidates: list[MemoryCandidate] = Field(default_factory=list, max_length=3)
    relationshipPatch: RelationshipPatch | None = None
    developmentPatch: DevelopmentPatch | None = None
    threadPatch: ThreadPatch | None = None
    goalPatch: GoalPatch | None = None


class RelationshipMemoryPatch(MemoryBaseModel):
    userName: str | None = None
    clearUserName: bool = False
    preferredAddress: str | None = None
    clearPreferredAddress: bool = False
    trust: int | None = Field(default=None, ge=0, le=100)
    attachment: int | None = Field(default=None, ge=0, le=100)
    familiarity: int | None = Field(default=None, ge=0, le=100)
    sharedEventUpserts: list[RelationshipEvent] = Field(default_factory=list)
    sharedEventDeletes: list[str] = Field(default_factory=list)
    userFactUpserts: list[UserFact] = Field(default_factory=list)
    userFactDeletes: list[str] = Field(default_factory=list)
    boundaryUpserts: list[str] = Field(default_factory=list)
    boundaryDeletes: list[str] = Field(default_factory=list)
    lastWarmMomentAt: str | None = None


class AppliedDevelopmentPatch(MemoryBaseModel):
    trust: int | None = Field(default=None, ge=0, le=100)
    attachment: int | None = Field(default=None, ge=0, le=100)
    curiosity: int | None = Field(default=None, ge=0, le=100)
    confidence: int | None = Field(default=None, ge=0, le=100)
    loneliness: int | None = Field(default=None, ge=0, le=100)
    playfulness: int | None = Field(default=None, ge=0, le=100)
    lastDevelopmentReason: str | None = None


class PetMemoryPatch(MemoryBaseModel):
    canonUpserts: list[CanonMemoryFact] = Field(default_factory=list)
    canonDeletes: list[str] = Field(default_factory=list)
    relationshipPatch: RelationshipMemoryPatch | None = None
    threadUpserts: list[ConversationThread] = Field(default_factory=list)
    threadDeletes: list[str] = Field(default_factory=list)
    reflectionUpserts: list[ReflectionMemory] = Field(default_factory=list)
    reflectionDeletes: list[str] = Field(default_factory=list)
    activeGoalUpserts: list[ActiveGoal] = Field(default_factory=list)
    activeGoalDeletes: list[str] = Field(default_factory=list)
    developmentPatch: AppliedDevelopmentPatch | None = None
    eventAppends: list[PetEvent] = Field(default_factory=list)
    rejectedCandidateAppends: list[RejectedMemoryCandidate] = Field(default_factory=list)


class LocalChatDebug(MemoryBaseModel):
    usedFallback: bool | None = None
    validationFlags: list[str] = Field(default_factory=list)
    rejectedMemoryCount: int | None = None
    proactivityFlags: list[str] = Field(default_factory=list)
