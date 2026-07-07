import type {
  LocalChatHistoryV1,
  LocalChatMessage,
  LocalPetAssetSet,
  LocalPetState,
  LocalPetStateV2,
  PetLifeStage,
  PetMood,
} from "./types";
import {
  characterNameFromAssetSet,
  normalizeCharacterAssetSet,
} from "./localPetCharacter";

export const PET_STATE_STORAGE_KEY = "tamagochi:v1:pet-state";
export const CHAT_HISTORY_STORAGE_KEY = "tamagochi:v1:chat-history";
export const SETTINGS_STORAGE_KEY = "tamagochi:v1:settings";

const MAX_CHAT_MESSAGES = 50;
const MAX_CHAT_MESSAGE_TEXT = 8000;
const MAX_STORY_LIBRARY_BRICKS = 80;
const MIN_STAT = 0;
const MAX_STAT = 100;
const STAT_FULL_DECAY_HOURS = 24;
const STAT_DECAY_PER_HOUR = MAX_STAT / STAT_FULL_DECAY_HOURS;
const MOODS: PetMood[] = ["idle", "happy", "hungry", "sad"];
const STAGES: PetLifeStage[] = ["baby", "teen", "adult"];
const LITE_FACT_SPHERES = ["character", "appearance", "world", "relationship"] as const;

type PetStats = LocalPetState["stats"];

export type LocalPetSettings = {
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

export function clampStat(value: unknown): number {
  const numericValue = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return Math.max(MIN_STAT, Math.min(MAX_STAT, numericValue));
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

  return normalizeCharacterAssetSet({
    assetSetId: value.assetSetId,
    generatedAt: isIsoDate(value.generatedAt) ? value.generatedAt : new Date().toISOString(),
    characterTemplate: isRecord(value.characterTemplate) ? value.characterTemplate : undefined,
    characterBible: isRecord(value.characterBible) ? value.characterBible : undefined,
    images: normalizedImages,
    spriteSheetUrl: typeof value.spriteSheetUrl === "string" ? value.spriteSheetUrl : undefined,
  });
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

function normalizeStoryLibraryBrick(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) {
    return null;
  }
  const name = normalizeText(value.name, 120);
  const text = normalizeText(value.text ?? value.description, 8000);
  const pool = normalizeText(value.pool, 80) || "personal";
  if (!name || !text) {
    return null;
  }
  const id =
    normalizeText(value.id, 160) ||
    `pet:${pool}:${name.toLocaleLowerCase("ru-RU")}:${text.slice(0, 32).toLocaleLowerCase("ru-RU")}`;

  return {
    ...value,
    id,
    source: normalizeText(value.source, 80) || "pet_overlay",
    pool,
    poolLabel: normalizeText(value.poolLabel, 160) || pool,
    name,
    text,
    attributes: isRecord(value.attributes) ? value.attributes : {},
    basedOnBrickIds: Array.isArray(value.basedOnBrickIds)
      ? value.basedOnBrickIds.filter((item): item is string => typeof item === "string")
      : [],
    createdAt: isIsoDate(value.createdAt) ? value.createdAt : new Date().toISOString(),
  };
}

function normalizeStoryLibraryBricks(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map(normalizeStoryLibraryBrick)
    .filter((brick): brick is Record<string, unknown> => Boolean(brick));
}

function dedupeStoryLibraryBricks(values: Record<string, unknown>[]): Record<string, unknown>[] {
  const seen = new Set<string>();
  const result: Record<string, unknown>[] = [];
  for (const brick of values) {
    const id = String(brick.id ?? "");
    if (!id || seen.has(id)) {
      continue;
    }
    seen.add(id);
    result.push(brick);
  }
  return result.slice(-MAX_STORY_LIBRARY_BRICKS);
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

export function applyStoryLibraryPatch(
  state: LocalPetState,
  patch: Record<string, unknown> | undefined,
): LocalPetState {
  if (!patch || !state.assetSet?.characterBible) {
    return state;
  }

  const patchBricks = normalizeStoryLibraryBricks(patch.bricks);
  if (!patchBricks.length) {
    return state;
  }

  const characterBible = state.assetSet.characterBible;
  const currentExtensions = isRecord(characterBible.extensions) ? characterBible.extensions : {};
  const currentOverlay = isRecord(currentExtensions.story_library_overlay)
    ? currentExtensions.story_library_overlay
    : {};
  const mergedBricks = dedupeStoryLibraryBricks([
    ...normalizeStoryLibraryBricks(currentOverlay.bricks),
    ...patchBricks,
  ]);

  return {
    ...state,
    assetSet: {
      ...state.assetSet,
      characterBible: {
        ...characterBible,
        extensions: {
          ...currentExtensions,
          story_library_overlay: {
            ...currentOverlay,
            ...patch,
            version: 1,
            bricks: mergedBricks,
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
  const lastStatsTickAt = isIsoDate(value.lastStatsTickAt) ? value.lastStatsTickAt : updatedAt;
  const stats = normalizeStats(value.stats);
  const assetSet = normalizeAssetSet(value.assetSet);

  return {
    version: 2,
    petId: value.petId,
    name:
      typeof value.name === "string" && value.name.trim()
        ? value.name
        : characterNameFromAssetSet(assetSet),
    description: value.description.trim(),
    createdAt,
    updatedAt,
    lastInteractionAt: isIsoDate(value.lastInteractionAt) ? value.lastInteractionAt : updatedAt,
    lastStatsTickAt,
    stage: normalizeStage(value.stage),
    mood: normalizeMood(value.mood ?? calculatePetMood(stats)),
    stats,
    assetSet,
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

export function writeLocalPetState(state: LocalPetState): LocalPetState {
  const normalizedState = normalizePetState(state) ?? state;
  storage()?.setItem(PET_STATE_STORAGE_KEY, JSON.stringify(normalizedState));
  return normalizedState;
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
  const characterAssetSet = assetSet ? normalizeCharacterAssetSet(assetSet, now) : undefined;
  return {
    version: 2,
    petId: createLocalId("pet"),
    name: characterNameFromAssetSet(characterAssetSet),
    description: description.trim(),
    createdAt: now,
    updatedAt: now,
    lastInteractionAt: now,
    lastStatsTickAt: now,
    stage: "baby",
    mood: "idle",
    stats: {
      hunger: 80,
      happiness: 80,
      energy: 80,
      cleanliness: 80,
    },
    assetSet: characterAssetSet,
  };
}

export function applyOfflineProgress(state: LocalPetState, now = new Date()): LocalPetState {
  const lastStatsTickAt = Date.parse(state.lastStatsTickAt);
  if (Number.isNaN(lastStatsTickAt)) {
    return {
      ...state,
      lastStatsTickAt: now.toISOString(),
      stage: calculatePetStage(state.createdAt, now),
      mood: calculatePetMood(state.stats),
    };
  }

  const elapsedHours = Math.max(0, (now.getTime() - lastStatsTickAt) / 3_600_000);
  if (elapsedHours <= 0) {
    return {
      ...state,
      stage: calculatePetStage(state.createdAt, now),
      mood: calculatePetMood(state.stats),
    };
  }

  const decay = elapsedHours * STAT_DECAY_PER_HOUR;
  const stats = {
    ...state.stats,
    hunger: clampStat(state.stats.hunger - decay),
    happiness: clampStat(state.stats.happiness - decay),
    energy: clampStat(state.stats.energy - decay),
  };

  return {
    ...state,
    lastStatsTickAt: now.toISOString(),
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
    lastStatsTickAt: now.toISOString(),
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
