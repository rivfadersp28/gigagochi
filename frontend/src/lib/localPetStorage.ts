import type {
  ConversationHappinessDelta,
  InteractiveTravelPart,
  LocalChatHistoryV1,
  LocalChatMessage,
  LocalPetAssetSet,
  LocalPetState,
  LocalPetStateV2,
  PetStatKey,
  PetLifeStage,
  PetMood,
  PetStatTickMap,
  PetStatsPatch,
} from "./types";
import {
  characterNameFromAssetSet,
  normalizeCharacterAssetSet,
} from "./localPetCharacter";
import {
  applyOfflineProgress,
  applyPetTap,
  applyStatsPatch,
  calculatePetMood,
  calculatePetStage,
  clampStat,
  legacyStatsTick,
  normalizeStats,
  normalizeStatTickMap,
  normalizeZeroStatSinceMap,
  PET_STAT_KEYS,
  staggeredStatTicks,
  withPetInteraction,
} from "./localPetStats";
import { applyFoodEffect, FOOD_IDS, type FoodId } from "./localPetFood";
import {
  clampPetExperience,
  PET_INITIAL_EXPERIENCE,
} from "./localPetExperience";
import { markLocalPetSnapshotDirty } from "./localPetSnapshotRevision";
import {
  applyInteractiveTravelImpactsToPet,
  normalizeTravelImpactReceipts,
  type TravelImpactApplication,
} from "./localPetTravelImpacts";
import { isSafePublicAssetUrl } from "./publicAssetUrl";
import { serializedJsonBytes } from "./pushSnapshotPayload";
import { isSafeJsonRecord, safeJsonClone } from "./safeJsonValue";

export {
  applyOfflineProgress,
  applyPetTap,
  applyStatsPatch,
  calculatePetMood,
  calculatePetStage,
  clampStat,
  withPetInteraction,
} from "./localPetStats";

export const PET_STATE_STORAGE_KEY = "tamagochi:v1:pet-state";
export const CHAT_HISTORY_STORAGE_KEY = "tamagochi:v1:chat-history";
export const SETTINGS_STORAGE_KEY = "tamagochi:v1:settings";

const MAX_CHAT_MESSAGES = 300;
const MAX_CHAT_MESSAGE_TEXT = 8000;
const LOCAL_PET_ID_PATTERN = /^[A-Za-z0-9_-]{1,120}$/u;
const MAX_CHAT_HISTORY_STORAGE_BYTES = 512_000;
const MAX_CHARACTER_JSON_BYTES = 512_000;
const MAX_STORY_ATTRIBUTES_BYTES = 8_000;
const MAX_STORY_LIBRARY_BRICKS = 80;
const MAX_RECENT_STORY_EVENTS = 10;
const MAX_LITE_OVERLAY_FACTS = 80;
const MAX_LITE_SPHERE_FACTS = 40;
const MOODS: PetMood[] = ["idle", "happy", "hungry", "sad"];
const STAGES: PetLifeStage[] = ["baby", "teen", "adult"];
const LITE_FACT_SPHERES = ["character", "appearance", "world", "relationship"] as const;
const LITE_FACT_KINDS = [
  "character_fact",
  "appearance_fact",
  "world_fact",
  "relationship_fact",
] as const;
let inMemoryPetState: LocalPetState | null = null;
let petStateNeedsPersistence = false;
let inMemoryChatHistory: LocalChatHistoryV1 = { version: 1, messages: [] };
let chatHistoryPersistence: "write" | "remove" | null = null;
let localPetResetNeedsPersistence = false;
let pendingResetPetId: string | null = null;

export type LocalPetSettings = {
  includePromptDebug: boolean;
};

let inMemorySettings: LocalPetSettings = { includePromptDebug: false };
let settingsNeedPersistence = false;

function storage() {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function flushLocalPetSessionState(store: Storage | null) {
  if (!store) {
    return;
  }

  if (localPetResetNeedsPersistence) {
    try {
      const rawState = store.getItem(PET_STATE_STORAGE_KEY);
      const storedPet = rawState ? normalizePetState(JSON.parse(rawState)) : null;
      if (pendingResetPetId && storedPet && storedPet.petId !== pendingResetPetId) {
        inMemoryPetState = storedPet;
        localPetResetNeedsPersistence = false;
        pendingResetPetId = null;
      } else {
        for (const key of [PET_STATE_STORAGE_KEY, CHAT_HISTORY_STORAGE_KEY, SETTINGS_STORAGE_KEY]) {
          store.removeItem(key);
        }
        if (store.getItem(PET_STATE_STORAGE_KEY) === null) {
          localPetResetNeedsPersistence = false;
          pendingResetPetId = null;
        }
      }
    } catch {
      return;
    }
  }

  if (petStateNeedsPersistence) {
    try {
      if (inMemoryPetState) {
        const stateRaw = JSON.stringify(inMemoryPetState);
        store.setItem(PET_STATE_STORAGE_KEY, stateRaw);
        const observedRaw = store.getItem(PET_STATE_STORAGE_KEY);
        if (observedRaw === stateRaw) {
          petStateNeedsPersistence = false;
        } else {
          petStateNeedsPersistence = false;
          inMemoryPetState = observedRaw
            ? normalizePetState(JSON.parse(observedRaw))
            : null;
        }
      } else {
        store.removeItem(PET_STATE_STORAGE_KEY);
        petStateNeedsPersistence = false;
      }
    } catch {
      // Keep the session snapshot authoritative until persistent storage recovers.
    }
  }

  if (chatHistoryPersistence) {
    try {
      if (chatHistoryPersistence === "remove") {
        store.removeItem(CHAT_HISTORY_STORAGE_KEY);
      } else {
        store.setItem(CHAT_HISTORY_STORAGE_KEY, JSON.stringify(inMemoryChatHistory));
      }
      chatHistoryPersistence = null;
    } catch {
      // Keep the session history authoritative until persistent storage recovers.
    }
  }

  if (settingsNeedPersistence) {
    try {
      store.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(inMemorySettings));
      settingsNeedPersistence = false;
    } catch {
      // Keep the bounded session setting authoritative until persistence recovers.
    }
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
  flushLocalPetSessionState(store);
  if (localPetResetNeedsPersistence || settingsNeedPersistence || !store) {
    return { ...inMemorySettings };
  }

  try {
    const rawValue = store.getItem(SETTINGS_STORAGE_KEY);
    inMemorySettings = rawValue
      ? normalizeLocalPetSettings(JSON.parse(rawValue))
      : defaultLocalPetSettings();
    return { ...inMemorySettings };
  } catch {
    return { ...inMemorySettings };
  }
}

export function writeLocalPetSettings(settings: LocalPetSettings) {
  inMemorySettings = normalizeLocalPetSettings(settings);
  settingsNeedPersistence = true;
  flushLocalPetSessionState(storage());
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

function normalizeAssetUrl(value: unknown): string {
  return isSafePublicAssetUrl(value) ? value.trim().slice(0, 2_000) : "";
}

function normalizeBoundedJsonRecord(
  value: unknown,
  maxBytes: number,
): Record<string, unknown> | undefined {
  if (!isRecord(value) || serializedJsonBytes(value) > maxBytes) {
    return undefined;
  }
  const cloned = safeJsonClone(value, {
    maxDepth: 12,
    maxNodes: 5_000,
    maxStringLength: 32_000,
    maxArrayLength: 200,
    maxObjectKeys: 200,
  });
  return isSafeJsonRecord(cloned) && serializedJsonBytes(cloned) <= maxBytes
    ? cloned
    : undefined;
}

function normalizeStage(value: unknown): PetLifeStage {
  return typeof value === "string" && STAGES.includes(value as PetLifeStage)
    ? (value as PetLifeStage)
    : "baby";
}

export function normalizeMood(value: unknown): PetMood {
  return typeof value === "string" && MOODS.includes(value as PetMood) ? (value as PetMood) : "idle";
}

function normalizeAssetSet(value: unknown, depth = 0): LocalPetAssetSet | undefined {
  if (
    depth > 1
    || !isRecord(value)
    || typeof value.assetSetId !== "string"
    || !isRecord(value.images)
  ) {
    return undefined;
  }
  const assetSetId = normalizeText(value.assetSetId, 160);
  if (!assetSetId) {
    return undefined;
  }

  const images = value.images;
  const normalizedImages = STAGES.reduce<LocalPetAssetSet["images"]>(
    (result, stage) => {
      const stageImages = isRecord(images[stage]) ? images[stage] : {};
      result[stage] = MOODS.reduce<Record<PetMood, string>>(
        (moodResult, mood) => {
          const url = stageImages[mood];
          moodResult[mood] = normalizeAssetUrl(url);
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
    assetSetId,
    generatedAt: isIsoDate(value.generatedAt) ? value.generatedAt : new Date().toISOString(),
    characterTemplate: normalizeBoundedJsonRecord(
      value.characterTemplate,
      MAX_CHARACTER_JSON_BYTES,
    ),
    characterBible: normalizeBoundedJsonRecord(value.characterBible, MAX_CHARACTER_JSON_BYTES),
    images: normalizedImages,
    characterImageUrl: normalizeAssetUrl(value.characterImageUrl) || undefined,
    videoUrl: normalizeAssetUrl(value.videoUrl) || undefined,
    sadVideoUrl: normalizeAssetUrl(value.sadVideoUrl) || undefined,
    happyVideoUrl: normalizeAssetUrl(value.happyVideoUrl) || undefined,
    generationJobId:
      typeof value.generationJobId === "string"
        ? value.generationJobId.trim().slice(0, 120) || undefined
        : undefined,
    backgroundGenerationStatus:
      value.backgroundGenerationStatus === "running"
      || value.backgroundGenerationStatus === "succeeded"
      || value.backgroundGenerationStatus === "failed"
        ? value.backgroundGenerationStatus
        : undefined,
    backgroundGenerationPhase:
      value.backgroundGenerationPhase === "generating_sad_image"
      || value.backgroundGenerationPhase === "generating_sad_video"
      || value.backgroundGenerationPhase === "generating_happy_image"
      || value.backgroundGenerationPhase === "generating_happy_video"
      || value.backgroundGenerationPhase === "generating_kandinsky"
      || value.backgroundGenerationPhase === "completed"
        ? value.backgroundGenerationPhase
        : undefined,
    backgroundGenerationError:
      typeof value.backgroundGenerationError === "string"
        ? value.backgroundGenerationError.trim().slice(0, 500)
        : undefined,
    comparisonGenerationError:
      typeof value.comparisonGenerationError === "string"
        ? value.comparisonGenerationError.trim().slice(0, 500)
        : undefined,
    backgroundGenerationUpdatedAt:
      isIsoDate(value.backgroundGenerationUpdatedAt)
        ? value.backgroundGenerationUpdatedAt
        : undefined,
    blinkImageUrl: normalizeAssetUrl(value.blinkImageUrl) || undefined,
    spriteSheetUrl: normalizeAssetUrl(value.spriteSheetUrl) || undefined,
    kandinskyAssets: normalizeAssetSet(value.kandinskyAssets, depth + 1),
  });
}

function normalizeLiteFact(
  value: unknown,
  fallbackSphere: (typeof LITE_FACT_SPHERES)[number] = "character",
  fallbackCreatedAt = new Date().toISOString(),
): Record<string, unknown> | null {
  if (!isRecord(value)) {
    return null;
  }
  const text = normalizeText(value.text, 500);
  if (!text) {
    return null;
  }
  const rawSphere = normalizeText(value.sphere, 40);
  const sphere = LITE_FACT_SPHERES.includes(
    rawSphere as (typeof LITE_FACT_SPHERES)[number],
  )
    ? rawSphere
    : fallbackSphere;
  const defaultKind = `${sphere}_fact`;
  const rawKind = normalizeText(value.kind, 40);
  const kind = LITE_FACT_KINDS.includes(rawKind as (typeof LITE_FACT_KINDS)[number])
    ? rawKind
    : LITE_FACT_KINDS.includes(defaultKind as (typeof LITE_FACT_KINDS)[number])
      ? defaultKind
      : "character_fact";
  return {
    sphere,
    kind,
    text,
    pathHint: normalizeText(value.pathHint, 120) || `lite_overlay.spheres.${sphere}`,
    source: normalizeText(value.source, 80) || "pet_overlay",
    createdAt: normalizeText(value.createdAt, 80) || fallbackCreatedAt,
  };
}

function normalizeLiteFacts(
  value: unknown,
  fallbackSphere: (typeof LITE_FACT_SPHERES)[number] = "character",
  fallbackCreatedAt = new Date().toISOString(),
): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((fact) => normalizeLiteFact(fact, fallbackSphere, fallbackCreatedAt))
    .filter((fact): fact is Record<string, unknown> => Boolean(fact));
}

function liteFactKey(fact: Record<string, unknown>): string {
  return `${String(fact.sphere ?? "character")}:${normalizeText(fact.text).toLocaleLowerCase()}`;
}

function normalizeStoryLibraryBrick(
  value: unknown,
  fallbackCreatedAt = new Date().toISOString(),
): Record<string, unknown> | null {
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
    id,
    source: "pet_overlay",
    pool,
    poolLabel: normalizeText(value.poolLabel, 160) || pool,
    name,
    text: text.slice(0, 700),
    attributes:
      normalizeBoundedJsonRecord(value.attributes, MAX_STORY_ATTRIBUTES_BYTES) ?? {},
    basedOnBrickIds: normalizeStringList(value.basedOnBrickIds, 8, 160),
    createdAt: normalizeText(value.createdAt, 80) || fallbackCreatedAt,
  };
}

function normalizeStoryLibraryBricks(
  value: unknown,
  fallbackCreatedAt = new Date().toISOString(),
): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((brick) => normalizeStoryLibraryBrick(brick, fallbackCreatedAt))
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

function normalizeStringList(value: unknown, limit: number, itemLimit: number): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => normalizeText(item, itemLimit))
    .filter(Boolean)
    .slice(0, limit);
}

function normalizeStatusChanges(value: unknown): Record<string, string>[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const result: Record<string, string>[] = [];
  for (const item of value) {
    if (!isRecord(item)) {
      continue;
    }
    const entity = normalizeText(item.entity, 120);
    const state = normalizeText(item.state, 80);
    const owner = normalizeText(item.owner, 120);
    if (entity && state) {
      result.push({ entity, state, owner });
      if (result.length >= 5) {
        break;
      }
    }
  }
  return result;
}

function normalizeStatImpactItem(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) {
    return null;
  }
  const stat = normalizeText(value.stat, 40);
  const amount =
    typeof value.amount === "number" && Number.isFinite(value.amount)
      ? Math.max(-25, Math.min(25, value.amount))
      : null;
  if (!PET_STAT_KEYS.includes(stat as PetStatKey) || amount === null || amount === 0) {
    return null;
  }
  return {
    stat,
    amount,
    reason: normalizeText(value.reason, 280),
  };
}

function normalizeStatImpacts(value: unknown, legacy?: unknown): Record<string, unknown>[] {
  const source = Array.isArray(value) ? value : isRecord(legacy) ? [legacy] : [];
  const result: Record<string, unknown>[] = [];
  for (const item of source) {
    if (isRecord(item) && item.applies === false) {
      continue;
    }
    const impact = normalizeStatImpactItem(item);
    if (!impact) {
      continue;
    }
    result.push(impact);
    if (result.length >= 2) {
      break;
    }
  }
  return result;
}

function normalizeRecentStoryEvent(
  value: unknown,
  fallbackCreatedAt = new Date().toISOString(),
): Record<string, unknown> | null {
  if (!isRecord(value)) {
    return null;
  }
  const compactText = normalizeText(value.compactText, 500);
  const summary =
    normalizeText(value.summary, 500) ||
    compactText ||
    normalizeText(value.storyText, 500) ||
    normalizeText(value.title, 120);
  if (!summary) {
    return null;
  }
  return {
    id: normalizeText(value.id, 120),
    title: normalizeText(value.title, 120),
    summary,
    compactText: compactText || summary,
    storyText: normalizeText(value.storyText, 2000),
    imageUrl: normalizeAssetUrl(value.imageUrl),
    eventType: normalizeText(value.eventType, 60),
    valence: normalizeText(value.valence, 20),
    participants: normalizeStringList(value.participants, 6, 80),
    actions: normalizeStringList(value.actions, 6, 80),
    objects: normalizeStringList(value.objects, 6, 80),
    location: normalizeText(value.location, 160),
    outcome: normalizeText(value.outcome, 260),
    canonicalFacts: normalizeStringList(value.canonicalFacts, 5, 180),
    statusChanges: normalizeStatusChanges(value.statusChanges),
    statImpacts: normalizeStatImpacts(value.statImpacts, value.statImpact),
    tags: normalizeStringList(value.tags, 8, 60),
    createdAt: isIsoDate(value.createdAt) ? value.createdAt : fallbackCreatedAt,
    generatedAt: isIsoDate(value.generatedAt) ? value.generatedAt : undefined,
    source: normalizeText(value.source, 80) || "background_story",
  };
}

function normalizeRecentStoryEvents(
  value: unknown,
  fallbackCreatedAt = new Date().toISOString(),
): Record<string, unknown>[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((event) => normalizeRecentStoryEvent(event, fallbackCreatedAt))
    .filter((event): event is Record<string, unknown> => Boolean(event));
}

function storyEventKey(event: Record<string, unknown>): string {
  const id = normalizeText(event.id, 120);
  if (id) {
    return id.toLocaleLowerCase("ru-RU");
  }
  return [
    normalizeText(event.eventType, 60),
    normalizeText(event.title, 120),
    normalizeText(event.summary, 500),
  ]
    .join(":")
    .toLocaleLowerCase("ru-RU");
}

function dedupeRecentStoryEvents(values: Record<string, unknown>[]): Record<string, unknown>[] {
  const seen = new Set<string>();
  const result: Record<string, unknown>[] = [];
  for (const event of values) {
    const key = storyEventKey(event);
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(event);
  }
  return result.slice(-MAX_RECENT_STORY_EVENTS);
}

function liteFactsFromSpheres(
  value: unknown,
  fallbackCreatedAt = new Date().toISOString(),
): Record<string, unknown>[] {
  if (!isRecord(value)) {
    return [];
  }

  return LITE_FACT_SPHERES.flatMap((sphere) => {
    const sphereValue = value[sphere];
    if (!isRecord(sphereValue)) {
      return [];
    }
    return normalizeLiteFacts(sphereValue.facts, sphere, fallbackCreatedAt);
  });
}

function dedupeLiteFacts(
  facts: Record<string, unknown>[],
  limit = MAX_LITE_OVERLAY_FACTS,
): Record<string, unknown>[] {
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

  return mergedFacts.slice(-limit);
}

function mergeLiteSpheres(
  currentSpheres: unknown,
  patchSpheres: unknown,
  mergedFacts: Record<string, unknown>[],
  fallbackCreatedAt = new Date().toISOString(),
): Record<string, unknown> | undefined {
  const current = isRecord(currentSpheres) ? currentSpheres : {};
  const patch = isRecord(patchSpheres) ? patchSpheres : {};
  const result: Record<string, unknown> = {};

  for (const sphere of LITE_FACT_SPHERES) {
    const currentSphere = isRecord(current[sphere]) ? current[sphere] : {};
    const patchSphere = isRecord(patch[sphere]) ? patch[sphere] : {};
    const facts = dedupeLiteFacts(
      [
        ...normalizeLiteFacts(currentSphere.facts, sphere, fallbackCreatedAt),
        ...normalizeLiteFacts(patchSphere.facts, sphere, fallbackCreatedAt),
        ...mergedFacts.filter((fact) => fact.sphere === sphere),
      ],
      MAX_LITE_SPHERE_FACTS,
    );

    if (facts.length || "facts" in currentSphere || "facts" in patchSphere) {
      result[sphere] = { facts };
    }
  }

  return Object.keys(result).length ? result : undefined;
}

export function applyLiteOverlayPatch(
  state: LocalPetState,
  patch: Record<string, unknown> | undefined,
  now = new Date(),
): LocalPetState {
  if (!patch || !state.assetSet) {
    return state;
  }

  const nowIso = now.toISOString();
  const characterBible = isRecord(state.assetSet.characterBible)
    ? state.assetSet.characterBible
    : {};
  const currentExtensions = isRecord(characterBible.extensions) ? characterBible.extensions : {};
  const currentOverlay = isRecord(currentExtensions.lite_overlay)
    ? currentExtensions.lite_overlay
    : {};
  const mergedFacts = dedupeLiteFacts([
    ...normalizeLiteFacts(currentOverlay.facts, "character", nowIso),
    ...liteFactsFromSpheres(currentOverlay.spheres, nowIso),
    ...normalizeLiteFacts(patch.facts, "character", nowIso),
    ...liteFactsFromSpheres(patch.spheres, nowIso),
  ]);
  const mergedSpheres = mergeLiteSpheres(
    currentOverlay.spheres,
    patch.spheres,
    mergedFacts,
    nowIso,
  );
  const currentWorldSeed = isRecord(currentOverlay.worldSeed) ? currentOverlay.worldSeed : {};
  const patchWorldSeed = isRecord(patch.worldSeed) ? patch.worldSeed : {};
  const worldSeedSource =
    normalizeText(patchWorldSeed.source, 80) || normalizeText(currentWorldSeed.source, 80);
  const worldSeedCreatedAt =
    normalizeText(patchWorldSeed.createdAt, 80)
    || normalizeText(currentWorldSeed.createdAt, 80);
  const worldSeed = {
    ...(worldSeedSource ? { source: worldSeedSource } : {}),
    ...(worldSeedCreatedAt ? { createdAt: worldSeedCreatedAt } : {}),
  };
  const hasWorldSeed = Object.keys(worldSeed).length > 0;

  return {
    ...state,
    assetSet: {
      ...state.assetSet,
      characterBible: {
        ...characterBible,
        extensions: {
          ...currentExtensions,
          lite_overlay: {
            facts: mergedFacts,
            ...(mergedSpheres ? { spheres: mergedSpheres } : {}),
            ...(hasWorldSeed ? { worldSeed } : {}),
            updatedAt: nowIso,
          },
        },
      },
    },
  };
}

export function applyStoryLibraryPatch(
  state: LocalPetState,
  patch: Record<string, unknown> | undefined,
  now = new Date(),
): LocalPetState {
  if (!patch || !state.assetSet) {
    return state;
  }

  const nowIso = now.toISOString();
  const patchBricks = normalizeStoryLibraryBricks(patch.bricks, nowIso);
  if (!patchBricks.length) {
    return state;
  }

  const characterBible = isRecord(state.assetSet.characterBible)
    ? state.assetSet.characterBible
    : {};
  const currentExtensions = isRecord(characterBible.extensions) ? characterBible.extensions : {};
  const currentOverlay = isRecord(currentExtensions.story_library_overlay)
    ? currentExtensions.story_library_overlay
    : {};
  const mergedBricks = dedupeStoryLibraryBricks([
    ...normalizeStoryLibraryBricks(currentOverlay.bricks, nowIso),
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
            version: 1,
            bricks: mergedBricks,
            updatedAt: nowIso,
          },
        },
      },
    },
  };
}

export function applyRecentStoryEventsPatch(
  state: LocalPetState,
  patch: Record<string, unknown> | undefined,
  now = new Date(),
): LocalPetState {
  if (!patch || !state.assetSet) {
    return state;
  }

  const nowIso = now.toISOString();
  const patchEvents = normalizeRecentStoryEvents(patch.events, nowIso);
  if (!patchEvents.length) {
    return state;
  }

  const characterBible = isRecord(state.assetSet.characterBible)
    ? state.assetSet.characterBible
    : {};
  const currentExtensions = isRecord(characterBible.extensions) ? characterBible.extensions : {};
  const mergedEvents = dedupeRecentStoryEvents([
    ...normalizeRecentStoryEvents(currentExtensions.recent_story_events, nowIso),
    ...patchEvents,
  ]);

  return {
    ...state,
    assetSet: {
      ...state.assetSet,
      characterBible: {
        ...characterBible,
        extensions: {
          ...currentExtensions,
          recent_story_events: mergedEvents,
        },
      },
    },
  };
}

export type LocalPetStateMutationOperation =
  | { kind: "offline-progress" }
  | { kind: "feed"; foodId: FoodId }
  | { kind: "play" }
  | { kind: "tap" }
  | { kind: "spend-experience"; amount: number }
  | { kind: "reset-stats" }
  | { kind: "kill" }
  | { kind: "revive" }
  | { kind: "update-name"; name: string }
  | {
      kind: "generated-assets";
      assetSet: LocalPetAssetSet;
      expectedBaseAssetSet?: LocalPetAssetSet;
    }
  | {
      kind: "mood-hint";
      moodHint?: PetMood;
      storyLibraryPatch?: Record<string, unknown>;
      happinessDelta?: ConversationHappinessDelta;
    }
  | { kind: "lite-overlay-patch"; patch: Record<string, unknown> }
  | { kind: "story-library-patch"; patch: Record<string, unknown> }
  | { kind: "recent-story-events-patch"; patch: Record<string, unknown> }
  | {
      kind: "interactive-travel-impacts";
      travelId: string;
      parts: readonly InteractiveTravelPart[];
      legacyAppliedResultParts?: readonly number[];
    }
  | {
      kind: "stats-patch";
      patch: PetStatsPatch;
      expectedBaseStatTicks?: PetStatTickMap;
    };

export type LocalPetStateMutationCommit = {
  pet: LocalPetState;
  tapRewarded?: boolean;
  travelApplication?: TravelImpactApplication;
  rejected?: true;
};

type AppliedLocalPetStateMutation = LocalPetStateMutationCommit;

function changedAssetValue<T>(current: T, base: T, next: T): T {
  if (Object.is(base, next)) {
    return current;
  }
  if (base && next && typeof base === "object" && typeof next === "object") {
    try {
      if (JSON.stringify(base) === JSON.stringify(next)) {
        return current;
      }
    } catch {
      // Use the refreshed value when an unexpected object is not serializable.
    }
  }
  return next;
}

function mergeGeneratedAssetDelta(
  current: LocalPetAssetSet,
  base: LocalPetAssetSet,
  next: LocalPetAssetSet,
): LocalPetAssetSet {
  const mergeImages = (stage: keyof LocalPetAssetSet["images"]) => ({
    idle: changedAssetValue(
      current.images[stage].idle,
      base.images[stage].idle,
      next.images[stage].idle,
    ),
    happy: changedAssetValue(
      current.images[stage].happy,
      base.images[stage].happy,
      next.images[stage].happy,
    ),
    hungry: changedAssetValue(
      current.images[stage].hungry,
      base.images[stage].hungry,
      next.images[stage].hungry,
    ),
    sad: changedAssetValue(
      current.images[stage].sad,
      base.images[stage].sad,
      next.images[stage].sad,
    ),
  });

  return {
    ...current,
    images: {
      baby: mergeImages("baby"),
      teen: mergeImages("teen"),
      adult: mergeImages("adult"),
    },
    videoUrl: changedAssetValue(current.videoUrl, base.videoUrl, next.videoUrl),
    sadVideoUrl: changedAssetValue(current.sadVideoUrl, base.sadVideoUrl, next.sadVideoUrl),
    happyVideoUrl: changedAssetValue(
      current.happyVideoUrl,
      base.happyVideoUrl,
      next.happyVideoUrl,
    ),
    generationJobId: changedAssetValue(
      current.generationJobId,
      base.generationJobId,
      next.generationJobId,
    ),
    backgroundGenerationStatus: changedAssetValue(
      current.backgroundGenerationStatus,
      base.backgroundGenerationStatus,
      next.backgroundGenerationStatus,
    ),
    backgroundGenerationPhase: changedAssetValue(
      current.backgroundGenerationPhase,
      base.backgroundGenerationPhase,
      next.backgroundGenerationPhase,
    ),
    backgroundGenerationError: changedAssetValue(
      current.backgroundGenerationError,
      base.backgroundGenerationError,
      next.backgroundGenerationError,
    ),
    comparisonGenerationError: changedAssetValue(
      current.comparisonGenerationError,
      base.comparisonGenerationError,
      next.comparisonGenerationError,
    ),
    backgroundGenerationUpdatedAt: changedAssetValue(
      current.backgroundGenerationUpdatedAt,
      base.backgroundGenerationUpdatedAt,
      next.backgroundGenerationUpdatedAt,
    ),
    blinkImageUrl: changedAssetValue(
      current.blinkImageUrl,
      base.blinkImageUrl,
      next.blinkImageUrl,
    ),
    spriteSheetUrl: changedAssetValue(
      current.spriteSheetUrl,
      base.spriteSheetUrl,
      next.spriteSheetUrl,
    ),
    kandinskyAssets: changedAssetValue(
      current.kandinskyAssets,
      base.kandinskyAssets,
      next.kandinskyAssets,
    ),
  };
}

function normalizePetState(value: unknown): LocalPetStateV2 | null {
  if (
    !isRecord(value)
    || (value.version !== 1 && value.version !== 2)
    || typeof value.petId !== "string"
    || !value.petId.trim()
    || !LOCAL_PET_ID_PATTERN.test(value.petId.trim())
  ) {
    return null;
  }
  if (typeof value.description !== "string" || !value.description.trim()) {
    return null;
  }

  const now = new Date().toISOString();
  const createdAt = isIsoDate(value.createdAt) ? value.createdAt : now;
  const updatedAt = isIsoDate(value.updatedAt) ? value.updatedAt : createdAt;
  const lastStatsTickAt = isIsoDate(value.lastStatsTickAt) ? value.lastStatsTickAt : updatedAt;
  const lastStatTickAt = normalizeStatTickMap(value.lastStatTickAt, lastStatsTickAt);
  const stats = normalizeStats(value.stats);
  const zeroStatSinceAt = normalizeZeroStatSinceMap(value.zeroStatSinceAt, stats, lastStatTickAt);
  const assetSet = normalizeAssetSet(value.assetSet);
  const travelImpactReceipts = normalizeTravelImpactReceipts(value.travelImpactReceipts);

  return {
    version: 2,
    petId: value.petId.trim().slice(0, 120),
    experience: clampPetExperience(value.experience),
    petTapProgress:
      typeof value.petTapProgress === "number" && Number.isFinite(value.petTapProgress)
        ? Math.max(0, Math.min(4, Math.floor(value.petTapProgress)))
        : 0,
    ...(value.introductionPending === true ? { introductionPending: true as const } : {}),
    name:
      typeof value.name === "string" && value.name.trim()
        ? value.name.trim().replace(/\s+/gu, " ").slice(0, 32)
        : characterNameFromAssetSet(assetSet),
    description: value.description.trim().slice(0, 500),
    createdAt,
    updatedAt,
    lastInteractionAt: isIsoDate(value.lastInteractionAt) ? value.lastInteractionAt : updatedAt,
    lastStatsTickAt: legacyStatsTick(lastStatTickAt),
    lastStatTickAt,
    zeroStatSinceAt,
    diedAt: isIsoDate(value.diedAt) ? value.diedAt : undefined,
    stage: normalizeStage(value.stage),
    mood: normalizeMood(value.mood ?? calculatePetMood(stats)),
    stats,
    ...(travelImpactReceipts ? { travelImpactReceipts } : {}),
    assetSet,
  };
}

function applyMutationOperation(
  state: LocalPetState,
  operation: LocalPetStateMutationOperation,
  now = new Date(),
): AppliedLocalPetStateMutation {
  const nowIso = now.toISOString();
  const progressed = () => applyOfflineProgress(state, now);

  try {
    switch (operation.kind) {
      case "offline-progress":
        return { pet: progressed() };
      case "feed": {
        const currentPet = progressed();
        if (currentPet.diedAt || !FOOD_IDS.includes(operation.foodId)) {
          return { pet: currentPet };
        }
        return {
          pet: withPetInteraction(
            currentPet,
            (stats) => applyFoodEffect(stats, operation.foodId),
            now,
          ),
        };
      }
      case "play": {
        const currentPet = progressed();
        return {
          pet: withPetInteraction(currentPet, (stats) => ({
            ...stats,
            happiness: stats.happiness + 18,
            energy: stats.energy - 8,
            hunger: stats.hunger - 4,
          }), now),
        };
      }
      case "tap": {
        const currentPet = progressed();
        if (currentPet.diedAt) {
          return { pet: currentPet, tapRewarded: false };
        }
        const result = applyPetTap(currentPet, now);
        return { pet: result.state, tapRewarded: result.rewarded };
      }
      case "spend-experience": {
        const currentPet = progressed();
        const amount = Math.floor(operation.amount);
        if (!Number.isFinite(amount) || amount <= 0) {
          return { pet: currentPet, rejected: true };
        }
        const experience = clampPetExperience(currentPet.experience);
        if (experience < amount) {
          return { pet: currentPet, rejected: true };
        }
        return {
          pet: {
            ...currentPet,
            experience: experience - amount,
            updatedAt: nowIso,
          },
        };
      }
      case "reset-stats": {
        const currentPet = progressed();
        const stats = { hunger: 0, happiness: 0, energy: 0 };
        const lastStatTickAt = { hunger: nowIso, happiness: nowIso, energy: nowIso };
        return {
          pet: {
            ...currentPet,
            updatedAt: nowIso,
            lastInteractionAt: nowIso,
            lastStatsTickAt: nowIso,
            lastStatTickAt,
            zeroStatSinceAt: {
              hunger: nowIso,
              happiness: nowIso,
              energy: nowIso,
            },
            diedAt: undefined,
            stage: calculatePetStage(currentPet.createdAt, now),
            mood: calculatePetMood(stats),
            stats,
          },
        };
      }
      case "kill": {
        const currentPet = progressed();
        return { pet: { ...currentPet, updatedAt: nowIso, diedAt: nowIso } };
      }
      case "revive": {
        if (!state.diedAt) {
          return { pet: state, rejected: true };
        }
        const stats = { hunger: 80, happiness: 80, energy: 80 };
        const lastStatTickAt = { hunger: nowIso, happiness: nowIso, energy: nowIso };
        return {
          pet: {
            ...state,
            updatedAt: nowIso,
            lastInteractionAt: nowIso,
            lastStatsTickAt: nowIso,
            lastStatTickAt,
            zeroStatSinceAt: {},
            diedAt: undefined,
            stage: calculatePetStage(state.createdAt, now),
            mood: calculatePetMood(stats),
            stats,
          },
        };
      }
      case "update-name": {
        const name = normalizeText(operation.name, 32);
        if (!name) {
          return { pet: state };
        }
        const currentPet = progressed();
        if (currentPet.diedAt) {
          return { pet: currentPet };
        }
        return {
          pet: {
            ...currentPet,
            name,
            updatedAt: nowIso,
            stage: calculatePetStage(currentPet.createdAt, now),
          },
        };
      }
      case "generated-assets": {
        const currentPet = progressed();
        const assetSet = normalizeAssetSet(operation.assetSet);
        const expectedBase = normalizeAssetSet(operation.expectedBaseAssetSet);
        if (!assetSet) {
          return { pet: currentPet };
        }
        const currentAssetSet = currentPet.assetSet;
        if (
          expectedBase
          && (
            !currentAssetSet
            || currentAssetSet.assetSetId !== expectedBase.assetSetId
            || assetSet.assetSetId !== expectedBase.assetSetId
          )
        ) {
          return { pet: state, rejected: true };
        }
        const nextAssetSet = expectedBase && currentAssetSet
          ? mergeGeneratedAssetDelta(currentAssetSet, expectedBase, assetSet)
          : assetSet;
        return {
          pet: {
            ...currentPet,
            assetSet: nextAssetSet,
            updatedAt: nowIso,
            stage: calculatePetStage(currentPet.createdAt, now),
            mood: calculatePetMood(currentPet.stats),
          },
        };
      }
      case "mood-hint": {
        const currentPet = progressed();
        if (currentPet.diedAt) {
          return { pet: currentPet };
        }
        const happinessDelta = typeof operation.happinessDelta === "number"
          && Number.isFinite(operation.happinessDelta)
          ? operation.happinessDelta
          : 0;
        const stats = {
          ...currentPet.stats,
          happiness: clampStat(currentPet.stats.happiness + happinessDelta),
        };
        const lastStatTickAt = { ...currentPet.lastStatTickAt, happiness: nowIso };
        const zeroStatSinceAt = { ...currentPet.zeroStatSinceAt };
        if (stats.happiness > 0) {
          delete zeroStatSinceAt.happiness;
        } else if (!zeroStatSinceAt.happiness) {
          zeroStatSinceAt.happiness = nowIso;
        }
        const hintedMood = operation.moodHint && MOODS.includes(operation.moodHint)
          ? operation.moodHint
          : calculatePetMood(stats);
        const nextState: LocalPetState = {
          ...currentPet,
          updatedAt: nowIso,
          lastInteractionAt: nowIso,
          lastStatsTickAt: nowIso,
          lastStatTickAt,
          zeroStatSinceAt,
          stage: calculatePetStage(currentPet.createdAt, now),
          mood: hintedMood,
          stats,
        };
        return {
          pet: isRecord(operation.storyLibraryPatch)
            ? applyStoryLibraryPatch(nextState, operation.storyLibraryPatch, now)
            : nextState,
        };
      }
      case "lite-overlay-patch":
        return {
          pet: isRecord(operation.patch)
            ? applyLiteOverlayPatch(state, operation.patch, now)
            : state,
        };
      case "story-library-patch":
        return {
          pet: isRecord(operation.patch)
            ? applyStoryLibraryPatch(state, operation.patch, now)
            : state,
        };
      case "recent-story-events-patch":
        return {
          pet: isRecord(operation.patch)
            ? applyRecentStoryEventsPatch(state, operation.patch, now)
            : state,
        };
      case "interactive-travel-impacts": {
        if (!Array.isArray(operation.parts)) {
          return { pet: state };
        }
        const travelApplication = applyInteractiveTravelImpactsToPet(
          state,
          typeof operation.travelId === "string" ? operation.travelId : "",
          operation.parts,
          Array.isArray(operation.legacyAppliedResultParts)
            ? operation.legacyAppliedResultParts
            : [],
          now,
        );
        return { pet: travelApplication.pet, travelApplication };
      }
      case "stats-patch": {
        if (!isRecord(operation.patch)) {
          return { pet: state };
        }
        let currentPatch = operation.patch;
        if (isRecord(operation.expectedBaseStatTicks)) {
          const stats: NonNullable<PetStatsPatch["stats"]> = {};
          const lastStatTickAt: NonNullable<PetStatsPatch["lastStatTickAt"]> = {};
          for (const key of PET_STAT_KEYS) {
            if (
              operation.patch.stats?.[key] === undefined
              || state.lastStatTickAt[key] !== operation.expectedBaseStatTicks[key]
            ) {
              continue;
            }
            stats[key] = operation.patch.stats[key];
            if (operation.patch.lastStatTickAt?.[key] !== undefined) {
              lastStatTickAt[key] = operation.patch.lastStatTickAt[key];
            }
          }
          if (Object.keys(stats).length === 0) {
            return { pet: state };
          }
          currentPatch = { ...operation.patch, stats, lastStatTickAt };
        }
        return { pet: applyStatsPatch(state, currentPatch, now) };
      }
    }
  } catch {
    // Invalid mutation payloads are rejected as no-ops.
    return { pet: state };
  }
}

export function commitLocalPetStateMutation(
  operation: LocalPetStateMutationOperation,
  expectedPetId?: string,
): LocalPetStateMutationCommit | null {
  const store = storage();
  if (!store) {
    return null;
  }
  try {
    flushLocalPetSessionState(store);
    if (localPetResetNeedsPersistence || petStateNeedsPersistence) {
      return null;
    }
    const baseRaw = store.getItem(PET_STATE_STORAGE_KEY);
    const currentPet = baseRaw ? normalizePetState(JSON.parse(baseRaw)) : null;
    if (!currentPet || (expectedPetId && currentPet.petId !== expectedPetId)) {
      return null;
    }
    const preview = applyMutationOperation(currentPet, operation);
    if (preview.pet === currentPet) {
      return {
        pet: currentPet,
        ...(preview.tapRewarded !== undefined ? { tapRewarded: preview.tapRewarded } : {}),
        ...(preview.travelApplication
          ? { travelApplication: preview.travelApplication }
          : {}),
        ...(preview.rejected ? { rejected: true } : {}),
      };
    }
    const nextPet = normalizePetState(preview.pet);
    if (!nextPet || store.getItem(PET_STATE_STORAGE_KEY) !== baseRaw) {
      const winnerRaw = store.getItem(PET_STATE_STORAGE_KEY);
      inMemoryPetState = winnerRaw ? normalizePetState(JSON.parse(winnerRaw)) : null;
      petStateNeedsPersistence = false;
      return inMemoryPetState ? { pet: inMemoryPetState, rejected: true } : null;
    }
    const nextRaw = JSON.stringify(nextPet);
    store.setItem(PET_STATE_STORAGE_KEY, nextRaw);
    const observedRaw = store.getItem(PET_STATE_STORAGE_KEY);
    if (observedRaw !== nextRaw) {
      inMemoryPetState = observedRaw ? normalizePetState(JSON.parse(observedRaw)) : null;
      petStateNeedsPersistence = false;
      return inMemoryPetState ? { pet: inMemoryPetState, rejected: true } : null;
    }
    inMemoryPetState = nextPet;
    petStateNeedsPersistence = false;
    markLocalPetSnapshotDirty(nextPet.petId);
    return {
      pet: nextPet,
      ...(preview.tapRewarded !== undefined ? { tapRewarded: preview.tapRewarded } : {}),
      ...(preview.travelApplication
        ? { travelApplication: { ...preview.travelApplication, pet: nextPet } }
        : {}),
      ...(preview.rejected ? { rejected: true } : {}),
    };
  } catch {
    try {
      const winnerRaw = store.getItem(PET_STATE_STORAGE_KEY);
      inMemoryPetState = winnerRaw ? normalizePetState(JSON.parse(winnerRaw)) : null;
      petStateNeedsPersistence = false;
      return inMemoryPetState ? { pet: inMemoryPetState, rejected: true } : null;
    } catch {}
    return null;
  }
}

export function readLocalPetState(): LocalPetState | null {
  const store = storage();
  flushLocalPetSessionState(store);
  if (localPetResetNeedsPersistence || petStateNeedsPersistence) {
    return inMemoryPetState;
  }
  if (!store) {
    return inMemoryPetState;
  }

  try {
    const rawValue = store.getItem(PET_STATE_STORAGE_KEY);
    const storedState = rawValue ? normalizePetState(JSON.parse(rawValue)) : null;
    inMemoryPetState = storedState;
    return storedState;
  } catch {
    return inMemoryPetState;
  }
}

export function writeLocalPetState(state: LocalPetState): LocalPetState {
  const normalizedState =
    normalizePetState(state)
    ?? normalizePetState({
      ...state,
      version: 2,
      petId: LOCAL_PET_ID_PATTERN.test(normalizeText(state.petId, 120))
        ? normalizeText(state.petId, 120)
        : createLocalId("pet"),
      description: normalizeText(state.description, 500) || "Персонаж",
    });
  if (!normalizedState) {
    return inMemoryPetState ?? state;
  }
  inMemoryPetState = normalizedState;
  petStateNeedsPersistence = true;
  flushLocalPetSessionState(storage());
  const storedState = inMemoryPetState ?? normalizedState;
  markLocalPetSnapshotDirty(storedState.petId);
  return storedState;
}

export function writeLocalPetStateDurably(state: LocalPetState): LocalPetState | null {
  const previousState = inMemoryPetState;
  const previousNeedsPersistence = petStateNeedsPersistence;
  const normalizedState = normalizePetState(state);
  if (!normalizedState) {
    return null;
  }
  writeLocalPetState(normalizedState);
  const store = storage();
  let persisted = false;
  if (!petStateNeedsPersistence && store) {
    try {
      persisted = store.getItem(PET_STATE_STORAGE_KEY) === JSON.stringify(normalizedState);
    } catch {
      persisted = false;
    }
  }
  if (persisted) {
    return normalizedState;
  }
  // A paid result must not become session-authoritative until its full asset set
  // has a read-back-verified durable record. Preserve the previous session view
  // and leave the caller's recovery marker intact.
  inMemoryPetState = previousState;
  petStateNeedsPersistence = previousNeedsPersistence;
  return null;
}

export function resetLocalPetState(expectedPetId?: string): boolean {
  const store = storage();
  try {
    const rawValue = store?.getItem(PET_STATE_STORAGE_KEY) ?? null;
    const storedPet = rawValue ? normalizePetState(JSON.parse(rawValue)) : null;
    if (expectedPetId && storedPet?.petId !== expectedPetId) {
      inMemoryPetState = storedPet;
      return false;
    }
    pendingResetPetId = expectedPetId ?? storedPet?.petId ?? inMemoryPetState?.petId ?? null;
  } catch {
    pendingResetPetId = expectedPetId ?? inMemoryPetState?.petId ?? null;
  }
  inMemoryPetState = null;
  petStateNeedsPersistence = false;
  inMemoryChatHistory = { version: 1, messages: [] };
  chatHistoryPersistence = null;
  inMemorySettings = defaultLocalPetSettings();
  settingsNeedPersistence = false;
  localPetResetNeedsPersistence = true;
  flushLocalPetSessionState(store);
  return !localPetResetNeedsPersistence;
}

export function clearLocalChatHistory() {
  const petId = inMemoryPetState?.petId ?? readLocalPetState()?.petId;
  inMemoryChatHistory = { version: 1, messages: [] };
  chatHistoryPersistence = "remove";
  flushLocalPetSessionState(storage());
  if (petId) {
    markLocalPetSnapshotDirty(petId);
  }
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
  const lastStatTickAt = staggeredStatTicks(new Date(now));
  const characterAssetSet = assetSet ? normalizeCharacterAssetSet(assetSet, now) : undefined;
  return {
    version: 2,
    petId: createLocalId("pet"),
    experience: PET_INITIAL_EXPERIENCE,
    petTapProgress: 0,
    introductionPending: true,
    name: characterNameFromAssetSet(characterAssetSet),
    description: description.trim(),
    createdAt: now,
    updatedAt: now,
    lastInteractionAt: now,
    lastStatsTickAt: legacyStatsTick(lastStatTickAt),
    lastStatTickAt,
    zeroStatSinceAt: {},
    stage: "baby",
    mood: "idle",
    stats: {
      hunger: 100,
      happiness: 100,
      energy: 100,
    },
    assetSet: characterAssetSet,
  };
}

function normalizeChatHistory(value: unknown): LocalChatHistoryV1 {
  if (!isRecord(value) || value.version !== 1 || !Array.isArray(value.messages)) {
    return { version: 1, messages: [] };
  }

  const messages = value.messages
    .filter(isRecord)
    .map((message): LocalChatMessage | null => {
      if (
        typeof message.id !== "string" ||
        (message.role !== "user" && message.role !== "pet") ||
        typeof message.text !== "string"
      ) {
        return null;
      }

      const id = message.id.trim().slice(0, 160);
      if (!id) {
        return null;
      }
      return {
        id,
        role: message.role,
        text: message.text.slice(0, MAX_CHAT_MESSAGE_TEXT),
        createdAt: isIsoDate(message.createdAt) ? message.createdAt : new Date().toISOString(),
      };
    })
    .filter((message): message is LocalChatMessage => Boolean(message));

  const boundedMessages: LocalChatMessage[] = [];
  let storageBytes = serializedJsonBytes({ version: 1, messages: [] });
  for (const message of messages.slice(-MAX_CHAT_MESSAGES).reverse()) {
    const messageBytes = serializedJsonBytes(message);
    const separatorBytes = boundedMessages.length > 0 ? 1 : 0;
    if (storageBytes + messageBytes + separatorBytes > MAX_CHAT_HISTORY_STORAGE_BYTES) {
      break;
    }
    storageBytes += messageBytes + separatorBytes;
    boundedMessages.push(message);
  }
  boundedMessages.reverse();

  return {
    version: 1,
    messages: boundedMessages,
  };
}

export function readLocalChatHistory(): LocalChatHistoryV1 {
  const store = storage();
  flushLocalPetSessionState(store);
  if (localPetResetNeedsPersistence || chatHistoryPersistence) {
    return inMemoryChatHistory;
  }
  if (!store) {
    return inMemoryChatHistory;
  }

  try {
    const rawValue = store.getItem(CHAT_HISTORY_STORAGE_KEY);
    inMemoryChatHistory = rawValue
      ? normalizeChatHistory(JSON.parse(rawValue))
      : { version: 1, messages: [] };
    return inMemoryChatHistory;
  } catch {
    return inMemoryChatHistory;
  }
}

export function writeLocalChatHistory(history: LocalChatHistoryV1): LocalChatHistoryV1 {
  const petId = inMemoryPetState?.petId ?? readLocalPetState()?.petId;
  inMemoryChatHistory = normalizeChatHistory(history);
  chatHistoryPersistence = "write";
  flushLocalPetSessionState(storage());
  if (petId) {
    markLocalPetSnapshotDirty(petId);
  }
  return inMemoryChatHistory;
}

export function appendLocalChatMessages(messages: LocalChatMessage[]): LocalChatHistoryV1 {
  const history = readLocalChatHistory();
  const nextHistory = {
    version: 1 as const,
    messages: [...history.messages, ...messages].slice(-MAX_CHAT_MESSAGES),
  };
  return writeLocalChatHistory(nextHistory);
}

export function removeLocalChatMessages(messageIds: Iterable<string>): LocalChatHistoryV1 {
  const ids = new Set(messageIds);
  const history = readLocalChatHistory();
  const nextHistory = {
    version: 1 as const,
    messages: history.messages.filter((message) => !ids.has(message.id)),
  };
  return writeLocalChatHistory(nextHistory);
}

export function latestChatMessages(limit = 12): LocalChatMessage[] {
  return readLocalChatHistory().messages.slice(-limit);
}
