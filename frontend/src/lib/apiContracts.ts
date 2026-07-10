import type { components } from "./generated/openapi";
import type {
  MemoryConsolidationOperation,
  MemoryConsolidationResponse,
  MemoryExtractionResponse,
  MemoryOperation,
  UserMemoryKind,
} from "./localPetMemoryTypes";
import type {
  GenerateTravelResponse,
  LiteFactExtractionResponse,
  LocalChatResponse,
  PetMood,
  PetStatKey,
  PetStatsPatch,
} from "./types";
import { ApiContractError } from "./apiTransport";

type ApiSchemas = components["schemas"];

export type GeneratePetApiResponse = ApiSchemas["GeneratePetAssetResponse"];
export type GeneratePetJobResponse = ApiSchemas["GeneratePetJobResponse"];

export type PushSnapshotResponse = {
  registered: boolean;
  telegramId: number;
  updatedAt: string;
  statsPatch?: PetStatsPatch;
  storyLibraryPatch?: Record<string, unknown>;
  liteOverlayPatch?: Record<string, unknown>;
  recentStoryEventsPatch?: Record<string, unknown>;
};

type JsonRecord = Record<string, unknown>;

const PET_MOODS = new Set<PetMood>(["idle", "happy", "hungry", "sad"]);
const FACE_HINTS = new Set<NonNullable<LocalChatResponse["faceHint"]>>([
  "happy",
  "excited",
  "curious",
  "content",
  "grumpy",
  "sleepy",
]);
const TRAVEL_ARCS = new Set<GenerateTravelResponse["story"]["scenes"][number]["arc"]>([
  "beginning",
  "exploration",
  "discovery",
  "reward",
  "final",
]);
const JOB_STATUSES = new Set(["queued", "running", "succeeded", "failed"]);
const JOB_PHASES = new Set([
  "queued",
  "generating_images",
  "generating_video",
  "completed",
]);
const MEMORY_KINDS = new Set<UserMemoryKind>([
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
]);
const PET_STAT_KEYS = ["hunger", "happiness", "energy"] as const satisfies readonly PetStatKey[];

function fail(path: string, expectation: string): never {
  throw new ApiContractError(`${path}: ожидалось ${expectation}`);
}

function record(value: unknown, path: string): JsonRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return fail(path, "object");
  }
  return value as JsonRecord;
}

function optionalRecord(value: unknown, path: string): JsonRecord | undefined {
  return value === null || value === undefined ? undefined : record(value, path);
}

function array(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) {
    return fail(path, "array");
  }
  return value;
}

function optionalArray(value: unknown, path: string): unknown[] {
  return value === null || value === undefined ? [] : array(value, path);
}

function string(value: unknown, path: string): string {
  if (typeof value !== "string") {
    return fail(path, "string");
  }
  return value;
}

function optionalString(value: unknown, path: string): string | undefined {
  return value === null || value === undefined ? undefined : string(value, path);
}

function number(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fail(path, "finite number");
  }
  return value;
}

function boolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") {
    return fail(path, "boolean");
  }
  return value;
}

function enumValue<T extends string>(value: unknown, values: Set<T>, path: string): T {
  if (typeof value !== "string" || !values.has(value as T)) {
    return fail(path, "known enum value");
  }
  return value as T;
}

function optionalEnumValue<T extends string>(
  value: unknown,
  values: Set<T>,
  path: string,
): T | undefined {
  return value === null || value === undefined ? undefined : enumValue(value, values, path);
}

function parseDebug(value: unknown, path: string): LocalChatResponse["debug"] {
  return optionalRecord(value, path) as LocalChatResponse["debug"];
}

function parseGeneratedPetAsset(value: unknown, path: string): GeneratePetApiResponse {
  const payload = record(value, path);
  string(payload.assetSetId, `${path}.assetSetId`);
  string(payload.generatedAt, `${path}.generatedAt`);
  const images = record(payload.images, `${path}.images`);
  for (const stage of ["baby", "teen", "adult"] as const) {
    const stageImages = record(images[stage], `${path}.images.${stage}`);
    for (const mood of PET_MOODS) {
      string(stageImages[mood], `${path}.images.${stage}.${mood}`);
    }
  }
  optionalString(payload.videoUrl, `${path}.videoUrl`);
  optionalString(payload.blinkImageUrl, `${path}.blinkImageUrl`);
  optionalString(payload.spriteSheetUrl, `${path}.spriteSheetUrl`);
  optionalRecord(payload.characterBible, `${path}.characterBible`);
  return payload as GeneratePetApiResponse;
}

export function parseGeneratePetJobResponse(value: unknown): GeneratePetJobResponse {
  const payload = record(value, "generatePetJob");
  string(payload.jobId, "generatePetJob.jobId");
  enumValue(payload.status, JOB_STATUSES, "generatePetJob.status");
  enumValue(payload.phase, JOB_PHASES, "generatePetJob.phase");
  string(payload.createdAt, "generatePetJob.createdAt");
  string(payload.updatedAt, "generatePetJob.updatedAt");
  if (payload.result !== null && payload.result !== undefined) {
    parseGeneratedPetAsset(payload.result, "generatePetJob.result");
  }
  optionalRecord(payload.error, "generatePetJob.error");
  return payload as GeneratePetJobResponse;
}

export function parseLocalChatResponse(value: unknown): LocalChatResponse {
  const payload = record(value, "localChat");
  const petPatch = optionalRecord(payload.petPatch, "localChat.petPatch");
  return {
    reply: string(payload.reply, "localChat.reply"),
    moodHint: optionalEnumValue(payload.moodHint, PET_MOODS, "localChat.moodHint"),
    innerThought: optionalString(payload.innerThought, "localChat.innerThought"),
    faceHint: optionalEnumValue(payload.faceHint, FACE_HINTS, "localChat.faceHint"),
    petPatch: petPatch
      ? { name: optionalString(petPatch.name, "localChat.petPatch.name") }
      : undefined,
    storyLibraryPatch: optionalRecord(
      payload.storyLibraryPatch,
      "localChat.storyLibraryPatch",
    ),
    debug: parseDebug(payload.debug, "localChat.debug"),
  };
}

export function parseGenerateTravelResponse(value: unknown): GenerateTravelResponse {
  const payload = record(value, "travel");
  const story = record(payload.story, "travel.story");
  const scenes = array(story.scenes, "travel.story.scenes").map((item, index) => {
    const scene = record(item, `travel.story.scenes[${index}]`);
    return {
      index: number(scene.index, `travel.story.scenes[${index}].index`),
      arc: enumValue(
        scene.arc,
        TRAVEL_ARCS,
        `travel.story.scenes[${index}].arc`,
      ),
      title: string(scene.title, `travel.story.scenes[${index}].title`),
      text: string(scene.text, `travel.story.scenes[${index}].text`),
      visualBrief: string(scene.visualBrief, `travel.story.scenes[${index}].visualBrief`),
    };
  });
  const images = optionalArray(payload.images, "travel.images").map((item, index) => {
    const image = record(item, `travel.images[${index}]`);
    return {
      sceneIndex: number(image.sceneIndex, `travel.images[${index}].sceneIndex`),
      imageUrl: string(image.imageUrl, `travel.images[${index}].imageUrl`),
    };
  });
  return {
    travelId: string(payload.travelId, "travel.travelId"),
    generatedAt: string(payload.generatedAt, "travel.generatedAt"),
    story: {
      title: string(story.title, "travel.story.title"),
      summary: string(story.summary, "travel.story.summary"),
      scenes,
    },
    images,
    debug: parseDebug(payload.debug, "travel.debug"),
  };
}

function parseStatsPatch(value: unknown, path: string): PetStatsPatch | undefined {
  const payload = optionalRecord(value, path);
  if (!payload) {
    return undefined;
  }
  const statsRecord = optionalRecord(payload.stats, `${path}.stats`);
  const stats: PetStatsPatch["stats"] = {};
  for (const key of PET_STAT_KEYS) {
    if (statsRecord?.[key] !== undefined) {
      stats[key] = number(statsRecord[key], `${path}.stats.${key}`);
    }
  }
  const tickRecord = optionalRecord(payload.lastStatTickAt, `${path}.lastStatTickAt`);
  const lastStatTickAt: NonNullable<PetStatsPatch["lastStatTickAt"]> = {};
  for (const key of PET_STAT_KEYS) {
    if (tickRecord?.[key] !== undefined) {
      lastStatTickAt[key] = string(tickRecord[key], `${path}.lastStatTickAt.${key}`);
    }
  }
  return {
    stats,
    lastStatsTickAt: optionalString(payload.lastStatsTickAt, `${path}.lastStatsTickAt`),
    lastStatTickAt,
  };
}

export function parsePushSnapshotResponse(value: unknown): PushSnapshotResponse {
  const payload = record(value, "pushSnapshot");
  return {
    registered: boolean(payload.registered, "pushSnapshot.registered"),
    telegramId: number(payload.telegramId, "pushSnapshot.telegramId"),
    updatedAt: string(payload.updatedAt, "pushSnapshot.updatedAt"),
    statsPatch: parseStatsPatch(payload.statsPatch, "pushSnapshot.statsPatch"),
    storyLibraryPatch: optionalRecord(
      payload.storyLibraryPatch,
      "pushSnapshot.storyLibraryPatch",
    ),
    liteOverlayPatch: optionalRecord(payload.liteOverlayPatch, "pushSnapshot.liteOverlayPatch"),
    recentStoryEventsPatch: optionalRecord(
      payload.recentStoryEventsPatch,
      "pushSnapshot.recentStoryEventsPatch",
    ),
  };
}

export function parseLiteFactExtractionResponse(value: unknown): LiteFactExtractionResponse {
  const payload = record(value, "liteFacts");
  return {
    liteOverlayPatch: optionalRecord(payload.liteOverlayPatch, "liteFacts.liteOverlayPatch"),
    debug: parseDebug(payload.debug, "liteFacts.debug"),
  };
}

function optionalMemoryKind(value: unknown, path: string): UserMemoryKind | undefined {
  return optionalEnumValue(value, MEMORY_KINDS, path);
}

function parseMemoryFields(payload: JsonRecord, path: string) {
  return {
    kind: enumValue(payload.kind, MEMORY_KINDS, `${path}.kind`),
    text: string(payload.text, `${path}.text`),
    normalizedKey: string(payload.normalizedKey, `${path}.normalizedKey`),
    confidence: number(payload.confidence, `${path}.confidence`),
    importance: number(payload.importance, `${path}.importance`),
    dueAt: optionalString(payload.dueAt, `${path}.dueAt`),
    expiresAt: optionalString(payload.expiresAt, `${path}.expiresAt`),
    tags: optionalArray(payload.tags, `${path}.tags`).map((tag, index) =>
      string(tag, `${path}.tags[${index}]`),
    ),
  };
}

function parseMemoryOperation(value: unknown, path: string): MemoryOperation {
  const payload = record(value, path);
  const type = string(payload.type, `${path}.type`);
  if (type === "capture_learning") {
    return {
      type,
      observation: string(payload.observation, `${path}.observation`),
      patternKey: optionalString(payload.patternKey, `${path}.patternKey`),
      kind: optionalMemoryKind(payload.kind, `${path}.kind`),
      confidence: number(payload.confidence, `${path}.confidence`),
      importance: number(payload.importance, `${path}.importance`),
      dueAt: optionalString(payload.dueAt, `${path}.dueAt`),
    };
  }
  if (type === "remember_user_fact" || type === "replace_user_fact") {
    return { type, ...parseMemoryFields(payload, path) };
  }
  if (type === "forget_user_fact") {
    return {
      type,
      normalizedKey: optionalString(payload.normalizedKey, `${path}.normalizedKey`),
      matchText: optionalString(payload.matchText ?? payload.text, `${path}.matchText`),
    };
  }
  return fail(`${path}.type`, "known memory operation");
}

function parseConsolidationOperation(
  value: unknown,
  path: string,
): MemoryConsolidationOperation {
  const payload = record(value, path);
  const type = string(payload.type, `${path}.type`);
  if (type === "promote_learning") {
    return {
      type,
      learningId: string(payload.learningId, `${path}.learningId`),
      memory: parseMemoryFields(record(payload.memory, `${path}.memory`), `${path}.memory`),
    };
  }
  if (type === "prune_learning") {
    return {
      type,
      learningId: string(payload.learningId, `${path}.learningId`),
      reason: optionalString(payload.reason, `${path}.reason`),
    };
  }
  if (type === "rewrite_summary" || type === "rewrite_user_profile") {
    return { type, content: string(payload.content, `${path}.content`) };
  }
  return fail(`${path}.type`, "known consolidation operation");
}

export function parseMemoryExtractionResponse(value: unknown): MemoryExtractionResponse {
  const payload = record(value, "memoryExtraction");
  return {
    operations: optionalArray(payload.operations, "memoryExtraction.operations").map(
      (operation, index) =>
        parseMemoryOperation(operation, `memoryExtraction.operations[${index}]`),
    ),
    debug: parseDebug(payload.debug, "memoryExtraction.debug"),
  };
}

export function parseMemoryConsolidationResponse(
  value: unknown,
): MemoryConsolidationResponse {
  const payload = record(value, "memoryConsolidation");
  return {
    operations: optionalArray(payload.operations, "memoryConsolidation.operations").map(
      (operation, index) =>
        parseConsolidationOperation(operation, `memoryConsolidation.operations[${index}]`),
    ),
    debug: parseDebug(payload.debug, "memoryConsolidation.debug"),
  };
}
