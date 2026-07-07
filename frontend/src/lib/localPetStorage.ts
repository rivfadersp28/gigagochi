import type {
  LocalChatHistoryV1,
  LocalChatMessage,
  LocalPetAssetSet,
  LocalPetState,
  LocalPetStateV2,
  PetLifeStage,
  PetMood,
} from "./types";

export const PET_STATE_STORAGE_KEY = "tamagochi:v1:pet-state";
export const CHAT_HISTORY_STORAGE_KEY = "tamagochi:v1:chat-history";
export const SETTINGS_STORAGE_KEY = "tamagochi:v1:settings";
export const PENDING_MAIN_REPLY_STORAGE_KEY = "tamagochi:v1:pending-main-reply";

const MAX_CHAT_MESSAGES = 50;
const MAX_CHAT_MESSAGE_TEXT = 8000;
const MAX_PENDING_MAIN_REPLY_TEXT = 260;
const PENDING_MAIN_REPLY_MAX_AGE_MS = 5 * 60 * 1000;
const MIN_STAT = 0;
const MAX_STAT = 100;
const MOODS: PetMood[] = ["idle", "happy", "hungry", "sad"];
const STAGES: PetLifeStage[] = ["baby", "teen", "adult"];
const LITE_FACT_SPHERES = ["character", "appearance", "world", "relationship"] as const;

type PetStats = LocalPetState["stats"];

export type LocalPetSettings = {
  includePromptDebug: boolean;
};

export type PendingMainPetReply = {
  version: 1;
  petId: string;
  text: string;
  createdAt: string;
};

function storage() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage;
}

function sessionStore() {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function defaultLocalPetSettings(): LocalPetSettings {
  return {
    includePromptDebug: false,
  };
}

function normalizeLocalPetSettings(value: unknown): LocalPetSettings {
  if (!isRecord(value)) {
    return defaultLocalPetSettings();
  }

  return {
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

function normalizePendingMainPetReply(value: unknown): PendingMainPetReply | null {
  if (!isRecord(value) || value.version !== 1 || typeof value.petId !== "string") {
    return null;
  }

  const text = normalizeText(value.text, MAX_PENDING_MAIN_REPLY_TEXT);
  if (!text || !isIsoDate(value.createdAt)) {
    return null;
  }

  return {
    version: 1,
    petId: value.petId,
    text,
    createdAt: value.createdAt,
  };
}

export function writePendingMainPetReply(petId: string, text: string) {
  const normalizedText = normalizeText(text, MAX_PENDING_MAIN_REPLY_TEXT);
  if (!petId || !normalizedText) {
    return;
  }

  sessionStore()?.setItem(
    PENDING_MAIN_REPLY_STORAGE_KEY,
    JSON.stringify({
      version: 1,
      petId,
      text: normalizedText,
      createdAt: new Date().toISOString(),
    } satisfies PendingMainPetReply),
  );
}

export function consumePendingMainPetReply(petId: string): PendingMainPetReply | null {
  const store = sessionStore();
  if (!store) {
    return null;
  }

  try {
    const rawValue = store.getItem(PENDING_MAIN_REPLY_STORAGE_KEY);
    if (!rawValue) {
      return null;
    }

    store.removeItem(PENDING_MAIN_REPLY_STORAGE_KEY);
    const reply = normalizePendingMainPetReply(JSON.parse(rawValue));
    if (!reply || reply.petId !== petId) {
      return null;
    }

    const createdAtTime = Date.parse(reply.createdAt);
    if (Number.isNaN(createdAtTime) || Date.now() - createdAtTime > PENDING_MAIN_REPLY_MAX_AGE_MS) {
      return null;
    }

    return reply;
  } catch {
    store.removeItem(PENDING_MAIN_REPLY_STORAGE_KEY);
    return null;
  }
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

function normalizeText(value: unknown, limit = 500): string {
  return typeof value === "string" ? value.trim().replace(/\s+/g, " ").slice(0, limit) : "";
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

function normalizeLiteFact(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) {
    return null;
  }
  const text = normalizeText(value.text, 8000);
  if (!text) {
    return null;
  }
  return {
    ...value,
    kind: typeof value.kind === "string" && value.kind.trim() ? value.kind.trim() : "lore_fact",
    text,
    createdAt: isIsoDate(value.createdAt) ? value.createdAt : new Date().toISOString(),
  };
}

function normalizeLiteFacts(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map(normalizeLiteFact)
    .filter((fact): fact is Record<string, unknown> => Boolean(fact));
}

function liteFactKey(fact: Record<string, unknown>): string {
  return `${String(fact.sphere ?? "")}:${String(fact.kind ?? "lore_fact")}:${normalizeText(fact.text).toLocaleLowerCase()}`;
}

function liteFactsFromSpheres(value: unknown): Record<string, unknown>[] {
  if (!isRecord(value)) {
    return [];
  }

  return LITE_FACT_SPHERES.flatMap((sphere) => {
    const sphereValue = value[sphere];
    if (!isRecord(sphereValue)) {
      return [];
    }
    return normalizeLiteFacts(sphereValue.facts).map((fact) => ({
      ...fact,
      sphere: typeof fact.sphere === "string" && fact.sphere.trim() ? fact.sphere : sphere,
    }));
  });
}

function dedupeLiteFacts(facts: Record<string, unknown>[]): Record<string, unknown>[] {
  const mergedFacts: Record<string, unknown>[] = [];
  const seen = new Set<string>();

  for (const fact of facts) {
    const key = liteFactKey(fact);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    mergedFacts.push(fact);
  }

  return mergedFacts;
}

function mergeLiteSpheres(
  currentSpheres: unknown,
  patchSpheres: unknown,
  mergedFacts: Record<string, unknown>[],
): Record<string, unknown> | undefined {
  const current = isRecord(currentSpheres) ? currentSpheres : {};
  const patch = isRecord(patchSpheres) ? patchSpheres : {};
  const result: Record<string, unknown> = {};

  for (const sphere of LITE_FACT_SPHERES) {
    const currentSphere = isRecord(current[sphere]) ? current[sphere] : {};
    const patchSphere = isRecord(patch[sphere]) ? patch[sphere] : {};
    const facts = dedupeLiteFacts([
      ...normalizeLiteFacts(currentSphere.facts),
      ...normalizeLiteFacts(patchSphere.facts),
      ...mergedFacts.filter((fact) => fact.sphere === sphere),
    ]);

    if (facts.length || Object.keys(currentSphere).length || Object.keys(patchSphere).length) {
      result[sphere] = {
        ...currentSphere,
        ...patchSphere,
        facts,
      };
    }
  }

  return Object.keys(result).length ? result : undefined;
}

export function applyLiteOverlayPatch(
  state: LocalPetState,
  patch: Record<string, unknown> | undefined,
): LocalPetState {
  if (!patch || !state.assetSet?.characterBible) {
    return state;
  }

  const characterBible = state.assetSet.characterBible;
  const currentExtensions = isRecord(characterBible.extensions) ? characterBible.extensions : {};
  const currentOverlay = isRecord(currentExtensions.lite_overlay)
    ? currentExtensions.lite_overlay
    : {};
  const mergedFacts = dedupeLiteFacts([
    ...normalizeLiteFacts(currentOverlay.facts),
    ...liteFactsFromSpheres(currentOverlay.spheres),
    ...normalizeLiteFacts(patch.facts),
    ...liteFactsFromSpheres(patch.spheres),
  ]);
  const mergedSpheres = mergeLiteSpheres(currentOverlay.spheres, patch.spheres, mergedFacts);

  return {
    ...state,
    assetSet: {
      ...state.assetSet,
      characterBible: {
        ...characterBible,
        extensions: {
          ...currentExtensions,
          lite_overlay: {
            ...currentOverlay,
            ...patch,
            facts: mergedFacts,
            ...(mergedSpheres ? { spheres: mergedSpheres } : {}),
            updatedAt: new Date().toISOString(),
          },
        },
      },
    },
  };
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
          text: message.text.slice(0, MAX_CHAT_MESSAGE_TEXT),
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
