import type {
  LocalChatHistoryV1,
  LocalChatMessage,
  LocalPetAssetSet,
  LocalPetStateV1,
  PetLifeStage,
  PetMood,
} from "./types";

export const PET_STATE_STORAGE_KEY = "tamagochi:v1:pet-state";
export const CHAT_HISTORY_STORAGE_KEY = "tamagochi:v1:chat-history";
export const SETTINGS_STORAGE_KEY = "tamagochi:v1:settings";

const MAX_CHAT_MESSAGES = 50;
const MAX_LORE_MEMORIES = 30;
const MIN_STAT = 0;
const MAX_STAT = 100;
const MOODS: PetMood[] = ["idle", "happy", "hungry", "sad"];
const STAGES: PetLifeStage[] = ["baby", "teen", "adult"];

type PetStats = LocalPetStateV1["stats"];

function storage() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage;
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

function normalizePetState(value: unknown): LocalPetStateV1 | null {
  if (!isRecord(value) || value.version !== 1 || typeof value.petId !== "string") {
    return null;
  }
  if (typeof value.description !== "string" || !value.description.trim()) {
    return null;
  }

  const now = new Date().toISOString();
  const createdAt = isIsoDate(value.createdAt) ? value.createdAt : now;
  const updatedAt = isIsoDate(value.updatedAt) ? value.updatedAt : createdAt;
  const stats = normalizeStats(value.stats);

  return {
    version: 1,
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
    loreMemories: normalizeLoreMemories(value.loreMemories),
  };
}

export function readLocalPetState(): LocalPetStateV1 | null {
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

export function writeLocalPetState(state: LocalPetStateV1) {
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
): LocalPetStateV1 {
  const now = new Date().toISOString();
  return {
    version: 1,
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
    loreMemories: [],
  };
}

export function applyOfflineProgress(state: LocalPetStateV1, now = new Date()): LocalPetStateV1 {
  const updatedAt = Date.parse(state.updatedAt);
  if (Number.isNaN(updatedAt)) {
    return {
      ...state,
      updatedAt: now.toISOString(),
      stage: calculatePetStage(state.createdAt, now),
      mood: calculatePetMood(state.stats),
    };
  }

  const elapsedHours = Math.max(0, (now.getTime() - updatedAt) / 3_600_000);
  if (elapsedHours <= 0) {
    return {
      ...state,
      stage: calculatePetStage(state.createdAt, now),
      mood: calculatePetMood(state.stats),
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
  };
}

export function withPetInteraction(
  state: LocalPetStateV1,
  updateStats: (stats: PetStats) => PetStats,
): LocalPetStateV1 {
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
