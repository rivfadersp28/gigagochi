import type {
  ActiveGoal,
  CanonMemoryFact,
  ConversationThread,
  DevelopmentState,
  LocalChatHistoryV1,
  LocalChatMessage,
  LocalPetAssetSet,
  LocalPetState,
  LocalPetStateV2,
  PetEvent,
  PetMemoryPatch,
  PetMemoryStateV1,
  PetLifeStage,
  PetMood,
  PromptLayers,
  ReflectionMemory,
  RejectedMemoryCandidate,
  RelationshipEvent,
  RelationshipMemory,
  UserFact,
} from "./types";
import { defaultPromptLayers, normalizePromptLayers } from "./promptLayers";

export const PET_STATE_STORAGE_KEY = "tamagochi:v1:pet-state";
export const CHAT_HISTORY_STORAGE_KEY = "tamagochi:v1:chat-history";
export const SETTINGS_STORAGE_KEY = "tamagochi:v1:settings";

const MAX_CHAT_MESSAGES = 50;
const MAX_LORE_MEMORIES = 30;
const MAX_CANON_FACTS = 60;
const MAX_RELATIONSHIP_EVENTS = 30;
const MAX_USER_FACTS = 30;
const MAX_THREADS = 12;
const MAX_OPEN_THREADS = 6;
const MAX_REFLECTIONS = 20;
const MAX_ACTIVE_GOALS = 8;
const MAX_LIVE_ACTIVE_GOALS = 5;
const MAX_EVENTS = 100;
const MAX_REJECTED = 30;
const MIN_STAT = 0;
const MAX_STAT = 100;
const MOODS: PetMood[] = ["idle", "happy", "hungry", "sad"];
const STAGES: PetLifeStage[] = ["baby", "teen", "adult"];

type PetStats = LocalPetState["stats"];

export type LocalPetSettings = {
  promptLayers: PromptLayers;
  includePromptDebug: boolean;
};

function storage() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage;
}

function defaultLocalPetSettings(): LocalPetSettings {
  return {
    promptLayers: defaultPromptLayers,
    includePromptDebug: false,
  };
}

function normalizeLocalPetSettings(value: unknown): LocalPetSettings {
  if (!isRecord(value)) {
    return defaultLocalPetSettings();
  }

  return {
    promptLayers: normalizePromptLayers(value.promptLayers),
    includePromptDebug: value.includePromptDebug === true,
  };
}

export function readLocalPetSettings(): LocalPetSettings {
  const store = storage();
  if (!store) {
    return defaultLocalPetSettings();
  }

  try {
    const rawValue = store.getItem(SETTINGS_STORAGE_KEY);
    return rawValue ? normalizeLocalPetSettings(JSON.parse(rawValue)) : defaultLocalPetSettings();
  } catch {
    return defaultLocalPetSettings();
  }
}

export function writeLocalPetSettings(settings: LocalPetSettings) {
  storage()?.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(normalizeLocalPetSettings(settings)));
}

export function clampStat(value: unknown): number {
  const numericValue = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return Math.max(MIN_STAT, Math.min(MAX_STAT, Math.round(numericValue)));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isIsoDate(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function clampUnit(value: unknown, fallback = 0): number {
  const numericValue = typeof value === "number" && Number.isFinite(value) ? value : fallback;
  return Math.max(0, Math.min(1, numericValue));
}

function clampMemoryStat(value: unknown, fallback: number): number {
  const numericValue = typeof value === "number" && Number.isFinite(value) ? value : fallback;
  return Math.max(0, Math.min(100, Math.round(numericValue)));
}

function normalizeText(value: unknown, limit = 500): string {
  return typeof value === "string" ? value.trim().replace(/\s+/g, " ").slice(0, limit) : "";
}

function normalizedKey(value: string): string {
  return normalizeText(value).replace(/^лор:\s*/i, "").toLocaleLowerCase();
}

function fallbackDate(value: unknown, fallback: string): string {
  return isIsoDate(value) ? value : fallback;
}

function normalizeStage(value: unknown): PetLifeStage {
  return typeof value === "string" && STAGES.includes(value as PetLifeStage)
    ? (value as PetLifeStage)
    : "baby";
}

export function normalizeMood(value: unknown): PetMood {
  return typeof value === "string" && MOODS.includes(value as PetMood) ? (value as PetMood) : "idle";
}

function normalizeStats(value: unknown): PetStats {
  const stats = isRecord(value) ? value : {};
  return {
    hunger: clampStat(stats.hunger ?? 80),
    happiness: clampStat(stats.happiness ?? 80),
    energy: clampStat(stats.energy ?? 80),
    cleanliness: clampStat(stats.cleanliness ?? 80),
  };
}

function normalizeAssetSet(value: unknown): LocalPetAssetSet | undefined {
  if (!isRecord(value) || typeof value.assetSetId !== "string" || !isRecord(value.images)) {
    return undefined;
  }

  const images = value.images;
  const normalizedImages = STAGES.reduce<LocalPetAssetSet["images"]>(
    (result, stage) => {
      const stageImages = isRecord(images[stage]) ? images[stage] : {};
      result[stage] = MOODS.reduce<Record<PetMood, string>>(
        (moodResult, mood) => {
          const url = stageImages[mood];
          moodResult[mood] = typeof url === "string" ? url : "";
          return moodResult;
        },
        { idle: "", happy: "", hungry: "", sad: "" },
      );
      return result;
    },
    {
      baby: { idle: "", happy: "", hungry: "", sad: "" },
      teen: { idle: "", happy: "", hungry: "", sad: "" },
      adult: { idle: "", happy: "", hungry: "", sad: "" },
    },
  );

  return {
    assetSetId: value.assetSetId,
    generatedAt: isIsoDate(value.generatedAt) ? value.generatedAt : new Date().toISOString(),
    characterBible: isRecord(value.characterBible) ? value.characterBible : undefined,
    images: normalizedImages,
    spriteSheetUrl: typeof value.spriteSheetUrl === "string" ? value.spriteSheetUrl : undefined,
  };
}

function normalizeLoreMemories(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const seen = new Set<string>();
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim().slice(0, 500))
    .filter((item) => {
      if (!item) {
        return false;
      }
      const key = item.toLocaleLowerCase();
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    })
    .slice(-MAX_LORE_MEMORIES);
}

export function mergeLoreMemories(
  current: string[] | undefined,
  next: string[] | undefined,
): string[] {
  return normalizeLoreMemories([...(current ?? []), ...(next ?? [])]);
}

const CANON_FACT_TYPES: CanonMemoryFact["type"][] = [
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
];

function normalizeCanonFact(value: unknown, now: string): CanonMemoryFact | null {
  if (!isRecord(value)) {
    return null;
  }
  const text = normalizeText(value.text);
  const type = typeof value.type === "string" && CANON_FACT_TYPES.includes(value.type as CanonMemoryFact["type"])
    ? (value.type as CanonMemoryFact["type"])
    : "world_fact";
  if (!text) {
    return null;
  }
  return {
    id: typeof value.id === "string" && value.id ? value.id : createLocalId("canon"),
    type,
    text,
    source: value.source === "user" || value.source === "system" ? value.source : "model",
    confidence: clampUnit(value.confidence, 0.6),
    importance: clampUnit(value.importance, 0.5),
    useCount: Math.max(0, Math.round(typeof value.useCount === "number" ? value.useCount : 0)),
    decayScore: clampUnit(value.decayScore, 0),
    createdAt: fallbackDate(value.createdAt, now),
    updatedAt: fallbackDate(value.updatedAt, now),
    lastUsedAt: isIsoDate(value.lastUsedAt) ? value.lastUsedAt : undefined,
    lastReinforcedAt: isIsoDate(value.lastReinforcedAt) ? value.lastReinforcedAt : undefined,
    relatedThreadId: typeof value.relatedThreadId === "string" ? value.relatedThreadId : undefined,
    pinned: value.pinned === true,
  };
}

function dedupeCanonFacts(items: CanonMemoryFact[]): CanonMemoryFact[] {
  const byText = new Map<string, CanonMemoryFact>();
  for (const item of items) {
    const key = normalizedKey(item.text);
    const existing = byText.get(key);
    if (!existing) {
      byText.set(key, item);
      continue;
    }
    byText.set(key, {
      ...existing,
      confidence: Math.max(existing.confidence, item.confidence),
      importance: Math.max(existing.importance, item.importance),
      useCount: Math.max(existing.useCount, item.useCount),
      decayScore: Math.min(existing.decayScore, item.decayScore),
      updatedAt: existing.updatedAt > item.updatedAt ? existing.updatedAt : item.updatedAt,
      pinned: existing.pinned || item.pinned,
    });
  }
  return Array.from(byText.values())
    .sort((a, b) => {
      const aPinned = a.pinned || a.type === "milestone" ? 1 : 0;
      const bPinned = b.pinned || b.type === "milestone" ? 1 : 0;
      return bPinned - aPinned || b.importance - a.importance || b.updatedAt.localeCompare(a.updatedAt);
    })
    .slice(0, MAX_CANON_FACTS);
}

function normalizeUserFact(value: unknown, now: string): UserFact | null {
  if (!isRecord(value)) {
    return null;
  }
  const text = normalizeText(value.text);
  if (!text) {
    return null;
  }
  return {
    id: typeof value.id === "string" && value.id ? value.id : createLocalId("userfact"),
    text,
    confidence: clampUnit(value.confidence, 0.7),
    importance: clampUnit(value.importance, 0.5),
    createdAt: fallbackDate(value.createdAt, now),
    updatedAt: fallbackDate(value.updatedAt, now),
    lastUsedAt: isIsoDate(value.lastUsedAt) ? value.lastUsedAt : undefined,
  };
}

function normalizeRelationshipEvent(value: unknown, now: string): RelationshipEvent | null {
  if (!isRecord(value)) {
    return null;
  }
  const text = normalizeText(value.text);
  if (!text) {
    return null;
  }
  return {
    id: typeof value.id === "string" && value.id ? value.id : createLocalId("relevent"),
    text,
    importance: clampUnit(value.importance, 0.5),
    createdAt: fallbackDate(value.createdAt, now),
    updatedAt: fallbackDate(value.updatedAt, now),
  };
}

function dedupeByText<T extends { text: string; importance?: number; updatedAt?: string }>(
  items: T[],
): T[] {
  const byText = new Map<string, T>();
  for (const item of items) {
    const key = normalizedKey(item.text);
    const existing = byText.get(key);
    if (!existing) {
      byText.set(key, item);
      continue;
    }
    const existingImportance = existing.importance ?? 0;
    const itemImportance = item.importance ?? 0;
    byText.set(key, itemImportance >= existingImportance ? item : existing);
  }
  return Array.from(byText.values()).sort((a, b) => {
    return (b.importance ?? 0) - (a.importance ?? 0)
      || (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "");
  });
}

function normalizeRelationshipMemory(value: unknown, now: string): RelationshipMemory {
  const record = isRecord(value) ? value : {};
  const userFacts = Array.isArray(record.userFacts)
    ? dedupeByText(record.userFacts.map((item) => normalizeUserFact(item, now)).filter(Boolean) as UserFact[])
    : [];
  const sharedEvents = Array.isArray(record.sharedEvents)
    ? dedupeByText(
        record.sharedEvents
          .map((item) => normalizeRelationshipEvent(item, now))
          .filter(Boolean) as RelationshipEvent[],
      )
    : [];
  const boundaries = Array.isArray(record.boundaries)
    ? Array.from(
        new Set(
          record.boundaries
            .map((item) => normalizeText(item, 160))
            .filter(Boolean)
            .map((item) => item),
        ),
      ).slice(0, 20)
    : [];
  const userName = normalizeText(record.userName, 80);
  const preferredAddress = normalizeText(record.preferredAddress, 80);
  return {
    userName: userName || undefined,
    preferredAddress: preferredAddress || undefined,
    trust: clampMemoryStat(record.trust, 20),
    attachment: clampMemoryStat(record.attachment, 20),
    familiarity: clampMemoryStat(record.familiarity, 0),
    sharedEvents: sharedEvents.slice(0, MAX_RELATIONSHIP_EVENTS),
    userFacts: userFacts.slice(0, MAX_USER_FACTS),
    boundaries,
    lastWarmMomentAt: isIsoDate(record.lastWarmMomentAt) ? record.lastWarmMomentAt : undefined,
  };
}

function normalizeThread(value: unknown, now: string): ConversationThread | null {
  if (!isRecord(value)) {
    return null;
  }
  const topic = normalizeText(value.topic, 160);
  const summary = normalizeText(value.summary);
  if (!topic || !summary) {
    return null;
  }
  return {
    id: typeof value.id === "string" && value.id ? value.id : createLocalId("thread"),
    topic,
    summary,
    status: value.status === "paused" || value.status === "resolved" ? value.status : "open",
    priority: clampUnit(value.priority, 0.5),
    createdAt: fallbackDate(value.createdAt, now),
    updatedAt: fallbackDate(value.updatedAt, now),
    lastMentionedAt: isIsoDate(value.lastMentionedAt) ? value.lastMentionedAt : undefined,
    suggestedFollowUp: normalizeText(value.suggestedFollowUp, 240) || undefined,
    lastQuestionAskedAt: isIsoDate(value.lastQuestionAskedAt) ? value.lastQuestionAskedAt : undefined,
  };
}

function normalizeThreads(value: unknown, now: string): ConversationThread[] {
  const threads = Array.isArray(value)
    ? (value.map((item) => normalizeThread(item, now)).filter(Boolean) as ConversationThread[])
    : [];
  let openCount = 0;
  return threads
    .sort((a, b) => b.priority - a.priority || b.updatedAt.localeCompare(a.updatedAt))
    .map((thread) => {
      if (thread.status !== "open") {
        return thread;
      }
      openCount += 1;
      return openCount <= MAX_OPEN_THREADS ? thread : { ...thread, status: "paused" as const };
    })
    .slice(0, MAX_THREADS);
}

function normalizeReflection(value: unknown, now: string): ReflectionMemory | null {
  if (!isRecord(value)) {
    return null;
  }
  const text = normalizeText(value.text);
  const sourceEventIds = Array.isArray(value.sourceEventIds)
    ? value.sourceEventIds.filter((item): item is string => typeof item === "string").slice(0, 12)
    : [];
  if (!text || sourceEventIds.length < 2) {
    return null;
  }
  const scope = ["self", "user", "relationship", "world"].includes(String(value.scope))
    ? (value.scope as ReflectionMemory["scope"])
    : "relationship";
  return {
    id: typeof value.id === "string" && value.id ? value.id : createLocalId("reflection"),
    text,
    scope,
    sourceEventIds,
    confidence: clampUnit(value.confidence, 0.6),
    importance: clampUnit(value.importance, 0.5),
    createdAt: fallbackDate(value.createdAt, now),
    updatedAt: fallbackDate(value.updatedAt, now),
    lastUsedAt: isIsoDate(value.lastUsedAt) ? value.lastUsedAt : undefined,
  };
}

function normalizeGoal(value: unknown, now: string): ActiveGoal | null {
  if (!isRecord(value)) {
    return null;
  }
  const text = normalizeText(value.text, 300);
  const kinds: ActiveGoal["kind"][] = [
    "learn_about_user",
    "share_lore",
    "seek_care",
    "return_to_thread",
    "play",
    "comfort_user",
  ];
  if (!text || typeof value.kind !== "string" || !kinds.includes(value.kind as ActiveGoal["kind"])) {
    return null;
  }
  const status = ["paused", "completed", "expired"].includes(String(value.status))
    ? (value.status as ActiveGoal["status"])
    : "active";
  return {
    id: typeof value.id === "string" && value.id ? value.id : createLocalId("goal"),
    kind: value.kind as ActiveGoal["kind"],
    text,
    priority: clampUnit(value.priority, 0.5),
    status,
    createdAt: fallbackDate(value.createdAt, now),
    updatedAt: fallbackDate(value.updatedAt, now),
    expiresAt: isIsoDate(value.expiresAt) ? value.expiresAt : undefined,
    relatedThreadId: typeof value.relatedThreadId === "string" ? value.relatedThreadId : undefined,
  };
}

function normalizeGoals(value: unknown, now: string): ActiveGoal[] {
  const goals = Array.isArray(value)
    ? (value.map((item) => normalizeGoal(item, now)).filter(Boolean) as ActiveGoal[])
    : [];
  let activeCount = 0;
  return goals
    .sort((a, b) => b.priority - a.priority || b.updatedAt.localeCompare(a.updatedAt))
    .map((goal) => {
      if (goal.status !== "active") {
        return goal;
      }
      activeCount += 1;
      return activeCount <= MAX_LIVE_ACTIVE_GOALS ? goal : { ...goal, status: "paused" as const };
    })
    .slice(0, MAX_ACTIVE_GOALS);
}

function normalizeDevelopment(value: unknown): DevelopmentState {
  const record = isRecord(value) ? value : {};
  return {
    trust: clampMemoryStat(record.trust, 20),
    attachment: clampMemoryStat(record.attachment, 20),
    curiosity: clampMemoryStat(record.curiosity, 45),
    confidence: clampMemoryStat(record.confidence, 30),
    loneliness: clampMemoryStat(record.loneliness, 10),
    playfulness: clampMemoryStat(record.playfulness, 50),
    lastDevelopmentReason: normalizeText(record.lastDevelopmentReason, 300) || undefined,
  };
}

function normalizeEvent(value: unknown, now: string): PetEvent | null {
  if (!isRecord(value)) {
    return null;
  }
  const text = normalizeText(value.text);
  const kinds: PetEvent["kind"][] = [
    "user_message",
    "pet_reply",
    "memory_accepted",
    "relationship",
    "development",
    "thread",
    "goal",
    "care",
    "reflection",
  ];
  if (!text || typeof value.kind !== "string" || !kinds.includes(value.kind as PetEvent["kind"])) {
    return null;
  }
  return {
    id: typeof value.id === "string" && value.id ? value.id : createLocalId("event"),
    kind: value.kind as PetEvent["kind"],
    text,
    importance: clampUnit(value.importance, 0.5),
    createdAt: fallbackDate(value.createdAt, now),
    relatedMemoryId: typeof value.relatedMemoryId === "string" ? value.relatedMemoryId : undefined,
  };
}

function normalizeRejected(value: unknown, now: string): RejectedMemoryCandidate | null {
  if (!isRecord(value)) {
    return null;
  }
  const text = normalizeText(value.text);
  const reason = normalizeText(value.reason, 160);
  if (!text || !reason) {
    return null;
  }
  const type = typeof value.type === "string" ? value.type : "world_fact";
  return {
    id: typeof value.id === "string" && value.id ? value.id : createLocalId("rejected"),
    type: type as RejectedMemoryCandidate["type"],
    text,
    reason,
    confidence: clampUnit(value.confidence, 0),
    importance: clampUnit(value.importance, 0),
    createdAt: fallbackDate(value.createdAt, now),
  };
}

function canonFromLoreMemories(loreMemories: string[] | undefined, now: string): CanonMemoryFact[] {
  return normalizeLoreMemories(loreMemories).map((memory) => ({
    id: createLocalId("canon"),
    type: "world_fact",
    text: memory.replace(/^лор:\s*/i, ""),
    source: "model",
    confidence: 0.7,
    importance: 0.55,
    useCount: 0,
    decayScore: 0.05,
    createdAt: now,
    updatedAt: now,
  }));
}

export function createEmptyMemoryState(): PetMemoryStateV1 {
  return {
    schemaVersion: 1,
    canon: [],
    relationship: {
      trust: 20,
      attachment: 20,
      familiarity: 0,
      sharedEvents: [],
      userFacts: [],
      boundaries: [],
    },
    threads: [],
    reflections: [],
    activeGoals: [],
    development: {
      trust: 20,
      attachment: 20,
      curiosity: 45,
      confidence: 30,
      loneliness: 10,
      playfulness: 50,
    },
    events: [],
    rejectedCandidates: [],
  };
}

export function normalizeMemoryState(
  value: unknown,
  legacyLoreMemories?: string[],
  now = new Date().toISOString(),
): PetMemoryStateV1 {
  const record = isRecord(value) ? value : {};
  const canon = Array.isArray(record.canon)
    ? (record.canon.map((item) => normalizeCanonFact(item, now)).filter(Boolean) as CanonMemoryFact[])
    : [];
  const memory: PetMemoryStateV1 = {
    schemaVersion: 1,
    canon: dedupeCanonFacts([...canon, ...canonFromLoreMemories(legacyLoreMemories, now)]),
    relationship: normalizeRelationshipMemory(record.relationship, now),
    threads: normalizeThreads(record.threads, now),
    reflections: Array.isArray(record.reflections)
      ? (record.reflections
          .map((item) => normalizeReflection(item, now))
          .filter(Boolean) as ReflectionMemory[]).slice(0, MAX_REFLECTIONS)
      : [],
    activeGoals: normalizeGoals(record.activeGoals, now),
    development: normalizeDevelopment(record.development),
    events: Array.isArray(record.events)
      ? (record.events.map((item) => normalizeEvent(item, now)).filter(Boolean) as PetEvent[]).slice(
          -MAX_EVENTS,
        )
      : [],
    rejectedCandidates: Array.isArray(record.rejectedCandidates)
      ? (record.rejectedCandidates
          .map((item) => normalizeRejected(item, now))
          .filter(Boolean) as RejectedMemoryCandidate[]).slice(-MAX_REJECTED)
      : [],
  };
  return memory;
}

function upsertById<T extends { id: string }>(current: T[], upserts: T[] | undefined): T[] {
  const byId = new Map(current.map((item) => [item.id, item]));
  for (const item of upserts ?? []) {
    byId.set(item.id, item);
  }
  return Array.from(byId.values());
}

function deleteById<T extends { id: string }>(current: T[], ids: string[] | undefined): T[] {
  const deleted = new Set(ids ?? []);
  return deleted.size ? current.filter((item) => !deleted.has(item.id)) : current;
}

function applyRelationshipPatch(
  relationship: RelationshipMemory,
  patch: PetMemoryPatch["relationshipPatch"],
): RelationshipMemory {
  if (!patch) {
    return relationship;
  }
  const userFacts = deleteById(relationship.userFacts, patch.userFactDeletes);
  const sharedEvents = deleteById(relationship.sharedEvents, patch.sharedEventDeletes);
  const deletedBoundaries = new Set(patch.boundaryDeletes ?? []);
  const boundaries = Array.from(
    new Set([
      ...relationship.boundaries.filter((item) => !deletedBoundaries.has(item)),
      ...(patch.boundaryUpserts ?? []),
    ]),
  );
  return {
    ...relationship,
    userName: patch.clearUserName ? undefined : patch.userName ?? relationship.userName,
    preferredAddress: patch.clearPreferredAddress
      ? undefined
      : patch.preferredAddress ?? relationship.preferredAddress,
    trust: patch.trust === undefined ? relationship.trust : clampMemoryStat(patch.trust, relationship.trust),
    attachment:
      patch.attachment === undefined
        ? relationship.attachment
        : clampMemoryStat(patch.attachment, relationship.attachment),
    familiarity:
      patch.familiarity === undefined
        ? relationship.familiarity
        : clampMemoryStat(patch.familiarity, relationship.familiarity),
    sharedEvents: upsertById(sharedEvents, patch.sharedEventUpserts),
    userFacts: upsertById(userFacts, patch.userFactUpserts),
    boundaries,
    lastWarmMomentAt: patch.lastWarmMomentAt ?? relationship.lastWarmMomentAt,
  };
}

export function applyMemoryPatch(
  memory: PetMemoryStateV1,
  patch?: PetMemoryPatch,
): PetMemoryStateV1 {
  if (!patch) {
    return normalizeMemoryState(memory);
  }
  const nextMemory: PetMemoryStateV1 = {
    schemaVersion: 1,
    canon: upsertById(deleteById(memory.canon, patch.canonDeletes), patch.canonUpserts),
    relationship: applyRelationshipPatch(memory.relationship, patch.relationshipPatch),
    threads: upsertById(deleteById(memory.threads, patch.threadDeletes), patch.threadUpserts),
    reflections: upsertById(
      deleteById(memory.reflections, patch.reflectionDeletes),
      patch.reflectionUpserts,
    ),
    activeGoals: upsertById(
      deleteById(memory.activeGoals, patch.activeGoalDeletes),
      patch.activeGoalUpserts,
    ),
    development: {
      ...memory.development,
      ...patch.developmentPatch,
    },
    events: [...memory.events, ...(patch.eventAppends ?? [])],
    rejectedCandidates: [...memory.rejectedCandidates, ...(patch.rejectedCandidateAppends ?? [])],
  };
  return normalizeMemoryState(nextMemory);
}

export function calculatePetStage(createdAt: string, now = new Date()): PetLifeStage {
  const createdTime = Date.parse(createdAt);
  if (Number.isNaN(createdTime)) {
    return "baby";
  }

  const ageDays = Math.max(0, (now.getTime() - createdTime) / 86_400_000);
  if (ageDays < 2) {
    return "baby";
  }
  if (ageDays < 7) {
    return "teen";
  }
  return "adult";
}

export function calculatePetMood(stats: PetStats): PetMood {
  if (stats.hunger < 30) {
    return "hungry";
  }
  if (stats.happiness < 30 || stats.cleanliness < 25) {
    return "sad";
  }
  if (stats.happiness > 75 && stats.hunger > 60) {
    return "happy";
  }
  return "idle";
}

function normalizePetState(value: unknown): LocalPetStateV2 | null {
  if (!isRecord(value) || (value.version !== 1 && value.version !== 2) || typeof value.petId !== "string") {
    return null;
  }
  if (typeof value.description !== "string" || !value.description.trim()) {
    return null;
  }

  const now = new Date().toISOString();
  const createdAt = isIsoDate(value.createdAt) ? value.createdAt : now;
  const updatedAt = isIsoDate(value.updatedAt) ? value.updatedAt : createdAt;
  const stats = normalizeStats(value.stats);
  const loreMemories = normalizeLoreMemories(value.loreMemories);
  const memory = normalizeMemoryState(value.version === 2 ? value.memory : undefined, loreMemories, now);

  return {
    version: 2,
    petId: value.petId,
    name: typeof value.name === "string" && value.name.trim() ? value.name : undefined,
    description: value.description.trim(),
    createdAt,
    updatedAt,
    lastInteractionAt: isIsoDate(value.lastInteractionAt) ? value.lastInteractionAt : updatedAt,
    stage: normalizeStage(value.stage),
    mood: normalizeMood(value.mood ?? calculatePetMood(stats)),
    stats,
    assetSet: normalizeAssetSet(value.assetSet),
    memory,
    loreMemories,
  };
}

export function readLocalPetState(): LocalPetState | null {
  const store = storage();
  if (!store) {
    return null;
  }

  try {
    const rawValue = store.getItem(PET_STATE_STORAGE_KEY);
    return rawValue ? normalizePetState(JSON.parse(rawValue)) : null;
  } catch {
    return null;
  }
}

export function writeLocalPetState(state: LocalPetState) {
  storage()?.setItem(PET_STATE_STORAGE_KEY, JSON.stringify(normalizePetState(state) ?? state));
}

export function resetLocalPetState() {
  const store = storage();
  if (!store) {
    return;
  }
  store.removeItem(PET_STATE_STORAGE_KEY);
  store.removeItem(CHAT_HISTORY_STORAGE_KEY);
  store.removeItem(SETTINGS_STORAGE_KEY);
}

export function clearLocalChatHistory() {
  storage()?.removeItem(CHAT_HISTORY_STORAGE_KEY);
}

export function createLocalId(prefix = "local") {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function createLocalPetState(
  description: string,
  assetSet?: LocalPetAssetSet,
): LocalPetState {
  const now = new Date().toISOString();
  return {
    version: 2,
    petId: createLocalId("pet"),
    description: description.trim(),
    createdAt: now,
    updatedAt: now,
    lastInteractionAt: now,
    stage: "baby",
    mood: "idle",
    stats: {
      hunger: 80,
      happiness: 80,
      energy: 80,
      cleanliness: 80,
    },
    assetSet,
    memory: createEmptyMemoryState(),
    loreMemories: [],
  };
}

export function applyOfflineProgress(state: LocalPetState, now = new Date()): LocalPetState {
  const updatedAt = Date.parse(state.updatedAt);
  if (Number.isNaN(updatedAt)) {
    return {
      ...state,
      updatedAt: now.toISOString(),
      stage: calculatePetStage(state.createdAt, now),
      mood: calculatePetMood(state.stats),
      memory: normalizeMemoryState(state.memory),
    };
  }

  const elapsedHours = Math.max(0, (now.getTime() - updatedAt) / 3_600_000);
  if (elapsedHours <= 0) {
    return {
      ...state,
      stage: calculatePetStage(state.createdAt, now),
      mood: calculatePetMood(state.stats),
      memory: normalizeMemoryState(state.memory),
    };
  }

  const stats = {
    hunger: clampStat(state.stats.hunger - elapsedHours * 4),
    happiness: clampStat(state.stats.happiness - elapsedHours * 2),
    energy: clampStat(state.stats.energy - elapsedHours * 1.5),
    cleanliness: clampStat(state.stats.cleanliness - elapsedHours),
  };

  return {
    ...state,
    updatedAt: now.toISOString(),
    stage: calculatePetStage(state.createdAt, now),
    mood: calculatePetMood(stats),
    stats,
    memory: normalizeMemoryState({
      ...state.memory,
      development: {
        ...state.memory.development,
        loneliness: clampMemoryStat(
          state.memory.development.loneliness + (elapsedHours > 6 ? elapsedHours / 4 : 0),
          state.memory.development.loneliness,
        ),
      },
    }),
  };
}

export function withPetInteraction(
  state: LocalPetState,
  updateStats: (stats: PetStats) => PetStats,
): LocalPetState {
  const now = new Date();
  const stats = normalizeStats(updateStats(state.stats));
  return {
    ...state,
    updatedAt: now.toISOString(),
    lastInteractionAt: now.toISOString(),
    stage: calculatePetStage(state.createdAt, now),
    mood: calculatePetMood(stats),
    stats,
  };
}

function normalizeChatHistory(value: unknown): LocalChatHistoryV1 {
  if (!isRecord(value) || value.version !== 1 || !Array.isArray(value.messages)) {
    return { version: 1, messages: [] };
  }

  return {
    version: 1,
    messages: value.messages
      .filter(isRecord)
      .map((message): LocalChatMessage | null => {
        if (
          typeof message.id !== "string" ||
          (message.role !== "user" && message.role !== "pet") ||
          typeof message.text !== "string"
        ) {
          return null;
        }

        return {
          id: message.id,
          role: message.role,
          text: message.text.slice(0, 1500),
          createdAt: isIsoDate(message.createdAt) ? message.createdAt : new Date().toISOString(),
        };
      })
      .filter((message): message is LocalChatMessage => Boolean(message))
      .slice(-MAX_CHAT_MESSAGES),
  };
}

export function readLocalChatHistory(): LocalChatHistoryV1 {
  const store = storage();
  if (!store) {
    return { version: 1, messages: [] };
  }

  try {
    const rawValue = store.getItem(CHAT_HISTORY_STORAGE_KEY);
    return rawValue ? normalizeChatHistory(JSON.parse(rawValue)) : { version: 1, messages: [] };
  } catch {
    return { version: 1, messages: [] };
  }
}

export function writeLocalChatHistory(history: LocalChatHistoryV1) {
  storage()?.setItem(CHAT_HISTORY_STORAGE_KEY, JSON.stringify(normalizeChatHistory(history)));
}

export function appendLocalChatMessages(messages: LocalChatMessage[]): LocalChatHistoryV1 {
  const history = readLocalChatHistory();
  const nextHistory = {
    version: 1 as const,
    messages: [...history.messages, ...messages].slice(-MAX_CHAT_MESSAGES),
  };
  writeLocalChatHistory(nextHistory);
  return nextHistory;
}

export function latestChatMessages(limit = 12): LocalChatMessage[] {
  return readLocalChatHistory().messages.slice(-limit);
}
