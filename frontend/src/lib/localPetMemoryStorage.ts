import type {
  LocalPetLearning,
  LocalPetLearningStatus,
  LocalPetMemoryStateV1,
  LocalPetProactiveLogItem,
  LocalPetUserMemory,
  MemoryConsolidationOperation,
  MemoryOperation,
  UserMemoryKind,
} from "./localPetMemoryTypes";

const PET_MEMORY_STORAGE_KEY_PREFIX = "tamagochi:v1:pet-memory:";
const MAX_LEARNINGS = 100;
const MAX_MEMORIES = 50;
const MAX_PROACTIVE_LOG = 30;
const MAX_TEXT = 500;
const MAX_SUMMARY = 1000;
const USER_MEMORY_KINDS: UserMemoryKind[] = [
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
];

function storage() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage;
}

function nowIso() {
  return new Date().toISOString();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isIsoDate(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function normalizeText(value: unknown, limit = MAX_TEXT): string {
  return typeof value === "string" ? value.trim().replace(/\s+/g, " ").slice(0, limit) : "";
}

function normalizeStringArray(value: unknown, limit: number, itemLimit = 120): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => normalizeText(item, itemLimit))
    .filter(Boolean)
    .slice(0, limit);
}

function clamp01(value: unknown, fallback = 0.5): number {
  const number = typeof value === "number" && Number.isFinite(value) ? value : fallback;
  return Math.max(0, Math.min(1, number));
}

function normalizeKind(value: unknown, fallback: UserMemoryKind = "user_fact"): UserMemoryKind {
  return typeof value === "string" && USER_MEMORY_KINDS.includes(value as UserMemoryKind)
    ? (value as UserMemoryKind)
    : fallback;
}

function normalizeStatus(value: unknown): LocalPetLearningStatus {
  return value === "promoted" || value === "pruned" ? value : "pending";
}

function normalizeKey(value: unknown, fallbackText: string): string {
  const explicit = normalizeText(value, 160)
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
  if (explicit) {
    return explicit;
  }
  return (
    fallbackText
      .toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 160) || "memory"
  );
}

export function localPetMemoryStorageKey(petId: string) {
  return `${PET_MEMORY_STORAGE_KEY_PREFIX}${petId}`;
}

export function createEmptyLocalPetMemory(petId: string): LocalPetMemoryStateV1 {
  const now = nowIso();
  return {
    version: 1,
    petId,
    createdAt: now,
    updatedAt: now,
    learnings: [],
    memories: [],
    proactiveLog: [],
  };
}

function normalizeLearning(value: unknown): LocalPetLearning | null {
  if (!isRecord(value) || typeof value.id !== "string") {
    return null;
  }
  const observation = normalizeText(value.observation);
  if (!observation) {
    return null;
  }
  const now = nowIso();
  return {
    id: value.id,
    status: normalizeStatus(value.status),
    observation,
    patternKey: normalizeText(value.patternKey, 120) || undefined,
    kind: value.kind ? normalizeKind(value.kind) : undefined,
    confidence: clamp01(value.confidence, 0.5),
    importance: clamp01(value.importance, 0.5),
    recurrenceCount:
      typeof value.recurrenceCount === "number" && Number.isFinite(value.recurrenceCount)
        ? Math.max(1, Math.round(value.recurrenceCount))
        : 1,
    firstSeenAt: isIsoDate(value.firstSeenAt) ? value.firstSeenAt : now,
    lastSeenAt: isIsoDate(value.lastSeenAt) ? value.lastSeenAt : now,
    sourceMessageIds: normalizeStringArray(value.sourceMessageIds, 20),
    dueAt: isIsoDate(value.dueAt) ? value.dueAt : undefined,
  };
}

function normalizeMemory(value: unknown): LocalPetUserMemory | null {
  if (!isRecord(value) || typeof value.id !== "string") {
    return null;
  }
  const text = normalizeText(value.text);
  if (!text) {
    return null;
  }
  const now = nowIso();
  return {
    id: value.id,
    kind: normalizeKind(value.kind),
    text,
    normalizedKey: normalizeKey(value.normalizedKey, text),
    confidence: clamp01(value.confidence, 0.5),
    importance: clamp01(value.importance, 0.5),
    createdAt: isIsoDate(value.createdAt) ? value.createdAt : now,
    updatedAt: isIsoDate(value.updatedAt) ? value.updatedAt : now,
    lastMentionedAt: isIsoDate(value.lastMentionedAt) ? value.lastMentionedAt : undefined,
    mentionCount:
      typeof value.mentionCount === "number" && Number.isFinite(value.mentionCount)
        ? Math.max(0, Math.round(value.mentionCount))
        : 0,
    sourceLearningIds: normalizeStringArray(value.sourceLearningIds, 20),
    dueAt: isIsoDate(value.dueAt) ? value.dueAt : undefined,
    expiresAt: isIsoDate(value.expiresAt) ? value.expiresAt : undefined,
    tags: normalizeStringArray(value.tags, 12, 40),
  };
}

function normalizeProactiveLogItem(value: unknown): LocalPetProactiveLogItem | null {
  if (!isRecord(value) || typeof value.id !== "string") {
    return null;
  }
  const text = normalizeText(value.text);
  if (!text) {
    return null;
  }
  return {
    id: value.id,
    createdAt: isIsoDate(value.createdAt) ? value.createdAt : nowIso(),
    memoryIds: normalizeStringArray(value.memoryIds, 8),
    text,
    deliveredVia: value.deliveredVia === "telegram_push" ? "telegram_push" : "local_open",
  };
}

function learningScore(item: LocalPetLearning): number {
  const statusScore = item.status === "pending" ? 200 : item.status === "promoted" ? 80 : 0;
  return statusScore + item.importance * 100 + item.confidence * 30 + item.recurrenceCount;
}

function memoryScore(item: LocalPetUserMemory): number {
  const dueScore = item.dueAt && Date.parse(item.dueAt) >= Date.now() ? 200 : 0;
  return dueScore + item.importance * 100 + item.confidence * 30;
}

function trimMemoryState(memory: LocalPetMemoryStateV1): LocalPetMemoryStateV1 {
  return {
    ...memory,
    learnings: [...memory.learnings]
      .sort((left, right) => learningScore(right) - learningScore(left))
      .slice(0, MAX_LEARNINGS),
    memories: [...memory.memories]
      .sort((left, right) => memoryScore(right) - memoryScore(left))
      .slice(0, MAX_MEMORIES),
    proactiveLog: [...memory.proactiveLog]
      .sort((left, right) => Date.parse(right.createdAt) - Date.parse(left.createdAt))
      .slice(0, MAX_PROACTIVE_LOG),
  };
}

function normalizeMemoryState(value: unknown, petId: string): LocalPetMemoryStateV1 {
  if (!isRecord(value) || value.version !== 1 || value.petId !== petId) {
    return createEmptyLocalPetMemory(petId);
  }
  const fallback = createEmptyLocalPetMemory(petId);
  return trimMemoryState({
    version: 1,
    petId,
    createdAt: isIsoDate(value.createdAt) ? value.createdAt : fallback.createdAt,
    updatedAt: isIsoDate(value.updatedAt) ? value.updatedAt : fallback.updatedAt,
    lastExtractionAt: isIsoDate(value.lastExtractionAt) ? value.lastExtractionAt : undefined,
    lastConsolidationAt: isIsoDate(value.lastConsolidationAt)
      ? value.lastConsolidationAt
      : undefined,
    lastProactiveAt: isIsoDate(value.lastProactiveAt) ? value.lastProactiveAt : undefined,
    userProfile: normalizeText(value.userProfile, MAX_SUMMARY) || undefined,
    summary: normalizeText(value.summary, MAX_SUMMARY) || undefined,
    learnings: Array.isArray(value.learnings)
      ? value.learnings
          .map(normalizeLearning)
          .filter((item): item is LocalPetLearning => Boolean(item))
      : [],
    memories: Array.isArray(value.memories)
      ? value.memories
          .map(normalizeMemory)
          .filter((item): item is LocalPetUserMemory => Boolean(item))
      : [],
    proactiveLog: Array.isArray(value.proactiveLog)
      ? value.proactiveLog
          .map(normalizeProactiveLogItem)
          .filter((item): item is LocalPetProactiveLogItem => Boolean(item))
      : [],
  });
}

export function readLocalPetMemory(petId: string): LocalPetMemoryStateV1 {
  const store = storage();
  if (!store) {
    return createEmptyLocalPetMemory(petId);
  }
  try {
    const rawValue = store.getItem(localPetMemoryStorageKey(petId));
    return rawValue
      ? normalizeMemoryState(JSON.parse(rawValue), petId)
      : createEmptyLocalPetMemory(petId);
  } catch {
    return createEmptyLocalPetMemory(petId);
  }
}

export function writeLocalPetMemory(memory: LocalPetMemoryStateV1) {
  storage()?.setItem(
    localPetMemoryStorageKey(memory.petId),
    JSON.stringify(trimMemoryState(memory)),
  );
}

export function resetLocalPetMemory(petId?: string) {
  const store = storage();
  if (!store) {
    return;
  }
  if (petId) {
    store.removeItem(localPetMemoryStorageKey(petId));
    return;
  }
  for (let index = store.length - 1; index >= 0; index -= 1) {
    const key = store.key(index);
    if (key?.startsWith(PET_MEMORY_STORAGE_KEY_PREFIX)) {
      store.removeItem(key);
    }
  }
}

function findLearningIndex(memory: LocalPetMemoryStateV1, operation: MemoryOperation): number {
  if (operation.type !== "capture_learning") {
    return -1;
  }
  const patternKey = normalizeText(operation.patternKey, 120);
  if (patternKey) {
    return memory.learnings.findIndex((item) => item.patternKey === patternKey);
  }
  const observation = normalizeText(operation.observation).toLowerCase();
  return memory.learnings.findIndex((item) => item.observation.toLowerCase() === observation);
}

function applyCaptureLearning(
  memory: LocalPetMemoryStateV1,
  operation: Extract<MemoryOperation, { type: "capture_learning" }>,
  sourceMessageIds: string[],
  now: string,
): LocalPetLearning[] {
  const observation = normalizeText(operation.observation);
  if (!observation) {
    return memory.learnings;
  }
  const existingIndex = findLearningIndex(memory, operation);
  if (existingIndex >= 0) {
    return memory.learnings.map((item, index) =>
      index === existingIndex
        ? {
            ...item,
            status: item.status === "pruned" ? "pending" : item.status,
            kind: operation.kind ?? item.kind,
            confidence: Math.max(item.confidence, clamp01(operation.confidence, item.confidence)),
            importance: Math.max(item.importance, clamp01(operation.importance, item.importance)),
            recurrenceCount: item.recurrenceCount + 1,
            lastSeenAt: now,
            sourceMessageIds: Array.from(new Set([...item.sourceMessageIds, ...sourceMessageIds])),
            dueAt: isIsoDate(operation.dueAt) ? operation.dueAt : item.dueAt,
          }
        : item,
    );
  }
  return [
    ...memory.learnings,
    {
      id: createMemoryId("learning"),
      status: "pending",
      observation,
      patternKey: normalizeText(operation.patternKey, 120) || undefined,
      kind: operation.kind ? normalizeKind(operation.kind) : undefined,
      confidence: clamp01(operation.confidence, 0.6),
      importance: clamp01(operation.importance, 0.5),
      recurrenceCount: 1,
      firstSeenAt: now,
      lastSeenAt: now,
      sourceMessageIds,
      dueAt: isIsoDate(operation.dueAt) ? operation.dueAt : undefined,
    },
  ];
}

function upsertUserMemory(
  memory: LocalPetMemoryStateV1,
  fact: Omit<Extract<MemoryOperation, { type: "remember_user_fact" }>, "type">,
  sourceLearningIds: string[],
  now: string,
): LocalPetUserMemory[] {
  const text = normalizeText(fact.text);
  if (!text) {
    return memory.memories;
  }
  const normalizedKey = normalizeKey(fact.normalizedKey, text);
  const existingIndex = memory.memories.findIndex((item) => item.normalizedKey === normalizedKey);
  if (existingIndex >= 0) {
    return memory.memories.map((item, index) =>
      index === existingIndex
        ? {
            ...item,
            kind: fact.kind ? normalizeKind(fact.kind, item.kind) : item.kind,
            text: fact.confidence >= item.confidence ? text : item.text,
            confidence: Math.max(item.confidence, clamp01(fact.confidence, item.confidence)),
            importance: Math.max(item.importance, clamp01(fact.importance, item.importance)),
            updatedAt: now,
            mentionCount: item.mentionCount + 1,
            sourceLearningIds: Array.from(new Set([...item.sourceLearningIds, ...sourceLearningIds])),
            dueAt: isIsoDate(fact.dueAt) ? fact.dueAt : item.dueAt,
            expiresAt: isIsoDate(fact.expiresAt) ? fact.expiresAt : item.expiresAt,
            tags: Array.from(new Set([...item.tags, ...(fact.tags ?? []).map((tag) => normalizeText(tag, 40))]))
              .filter(Boolean)
              .slice(0, 12),
          }
        : item,
    );
  }
  return [
    ...memory.memories,
    {
      id: createMemoryId("memory"),
      kind: normalizeKind(fact.kind),
      text,
      normalizedKey,
      confidence: clamp01(fact.confidence, 0.75),
      importance: clamp01(fact.importance, 0.7),
      createdAt: now,
      updatedAt: now,
      mentionCount: 0,
      sourceLearningIds,
      dueAt: isIsoDate(fact.dueAt) ? fact.dueAt : undefined,
      expiresAt: isIsoDate(fact.expiresAt) ? fact.expiresAt : undefined,
      tags: normalizeStringArray(fact.tags, 12, 40),
    },
  ];
}

function createMemoryId(prefix: string) {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function applyMemoryOperations(
  memory: LocalPetMemoryStateV1,
  operations: MemoryOperation[],
  sourceMessageIds: string[] = [],
): LocalPetMemoryStateV1 {
  const now = nowIso();
  let nextMemory: LocalPetMemoryStateV1 = {
    ...memory,
    updatedAt: now,
    lastExtractionAt: now,
  };
  for (const operation of operations) {
    if (operation.type === "capture_learning") {
      nextMemory = {
        ...nextMemory,
        learnings: applyCaptureLearning(nextMemory, operation, sourceMessageIds, now),
      };
    } else if (operation.type === "remember_user_fact") {
      nextMemory = {
        ...nextMemory,
        memories: upsertUserMemory(nextMemory, operation, [], now),
      };
    }
  }
  return trimMemoryState(nextMemory);
}

export function applyMemoryConsolidationOperations(
  memory: LocalPetMemoryStateV1,
  operations: MemoryConsolidationOperation[],
): LocalPetMemoryStateV1 {
  const now = nowIso();
  let nextMemory: LocalPetMemoryStateV1 = {
    ...memory,
    updatedAt: now,
    lastConsolidationAt: now,
  };
  for (const operation of operations) {
    if (operation.type === "promote_learning") {
      nextMemory = {
        ...nextMemory,
        learnings: nextMemory.learnings.map((learning) =>
          learning.id === operation.learningId ? { ...learning, status: "promoted" } : learning,
        ),
        memories: upsertUserMemory(nextMemory, operation.memory, [operation.learningId], now),
      };
    } else if (operation.type === "prune_learning") {
      nextMemory = {
        ...nextMemory,
        learnings: nextMemory.learnings.map((learning) =>
          learning.id === operation.learningId ? { ...learning, status: "pruned" } : learning,
        ),
      };
    } else if (operation.type === "rewrite_summary") {
      nextMemory = {
        ...nextMemory,
        summary: normalizeText(operation.content, MAX_SUMMARY) || undefined,
      };
    } else if (operation.type === "rewrite_user_profile") {
      nextMemory = {
        ...nextMemory,
        userProfile: normalizeText(operation.content, MAX_SUMMARY) || undefined,
      };
    }
  }
  return trimMemoryState(nextMemory);
}

export function buildExistingMemoryBrief(memory: LocalPetMemoryStateV1): string {
  const lines: string[] = [];
  if (memory.userProfile) {
    lines.push(`Профиль: ${memory.userProfile}`);
  }
  if (memory.summary) {
    lines.push(`Summary: ${memory.summary}`);
  }
  memory.memories.slice(0, 20).forEach((item) => {
    lines.push(`- [${item.kind}] ${item.text}`);
  });
  memory.learnings
    .filter((item) => item.status === "pending")
    .slice(0, 10)
    .forEach((item) => {
      lines.push(`- pending [${item.kind ?? "user_fact"}] ${item.observation}`);
    });
  return lines.join("\n").slice(0, 4000);
}

export function shouldRunDailyConsolidation(memory: LocalPetMemoryStateV1, now = new Date()) {
  const pendingCount = memory.learnings.filter((item) => item.status === "pending").length;
  if (pendingCount > 10) {
    return true;
  }
  if (pendingCount === 0) {
    return false;
  }
  if (!memory.lastConsolidationAt) {
    return true;
  }
  return new Date(memory.lastConsolidationAt).toLocaleDateString() !== now.toLocaleDateString();
}

export function markMemoryContextUsed(
  memory: LocalPetMemoryStateV1,
  memoryIds: string[],
): LocalPetMemoryStateV1 {
  if (!memoryIds.length) {
    return memory;
  }
  const now = nowIso();
  const idSet = new Set(memoryIds);
  return {
    ...memory,
    updatedAt: now,
    memories: memory.memories.map((item) =>
      idSet.has(item.id)
        ? {
            ...item,
            lastMentionedAt: now,
            mentionCount: item.mentionCount + 1,
          }
        : item,
    ),
  };
}

export function recordProactiveDelivery(
  memory: LocalPetMemoryStateV1,
  memoryIds: string[],
  text: string,
): LocalPetMemoryStateV1 {
  const now = nowIso();
  return trimMemoryState({
    ...markMemoryContextUsed(memory, memoryIds),
    updatedAt: now,
    lastProactiveAt: now,
    proactiveLog: [
      {
        id: createMemoryId("proactive"),
        createdAt: now,
        memoryIds,
        text: normalizeText(text),
        deliveredVia: "local_open",
      },
      ...memory.proactiveLog,
    ],
  });
}
