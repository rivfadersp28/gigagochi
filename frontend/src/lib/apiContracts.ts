import type { components } from "./generated/openapi";
import type {
  MemoryConsolidationOperation,
  MemoryConsolidationResponse,
  MemoryExtractionResponse,
  MemoryOperation,
  UserMemoryKind,
} from "./localPetMemoryTypes";
import type {
  ConversationHappinessDelta,
  InteractiveTravelAnimationResponse,
  InteractiveTravelIllustrationResponse,
  InteractiveTravelResponse,
  InteractiveTravelSuggestionsResponse,
  LiteFactExtractionResponse,
  LocalChatResponse,
  PetMood,
  PetStatKey,
  PetStatsPatch,
} from "./types";
import { ApiContractError } from "./apiTransport";
import { isSafePublicAssetUrl } from "./publicAssetUrl";
import { serializedJsonBytes } from "./pushSnapshotPayload";
import {
  isSafeJsonRecord,
  safeJsonClone,
  type SafeJsonCloneOptions,
} from "./safeJsonValue";

type ApiSchemas = components["schemas"];

export type GeneratePetApiResponse = ApiSchemas["GeneratePetAssetResponse"];
export type GeneratePetJobResponse = ApiSchemas["GeneratePetJobResponse"];

export type PushSnapshotResponse = {
  registered: boolean;
  telegramId: number;
  updatedAt: string;
  resetPet?: boolean;
  statsPatch?: PetStatsPatch;
  storyLibraryPatch?: Record<string, unknown>;
  liteOverlayPatch?: Record<string, unknown>;
  recentStoryEventsPatch?: Record<string, unknown>;
};

export type DeletePushSnapshotResponse = {
  unregistered: boolean;
  petId: string;
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
const INTERACTIVE_TRAVEL_ASSESSMENTS = new Set(["helpful", "harmful", "ambiguous"] as const);
const INTERACTIVE_TRAVEL_VALENCES = new Set(["positive", "negative"] as const);
const INTERACTIVE_TRAVEL_REACTION_TONES = new Set(
  [
    "enthusiastic",
    "confused",
    "worried",
    "amused",
    "indignant",
    "determined",
    "surprised",
  ] as const,
);
const INTERACTIVE_TRAVEL_ID_PATTERN = /^interactive-travel-[A-Za-z0-9_-]+$/u;
const JOB_STATUSES = new Set<GeneratePetJobResponse["status"]>([
  "queued",
  "running",
  "succeeded",
  "failed",
]);
const JOB_PHASES = new Set<GeneratePetJobResponse["phase"]>([
  "queued",
  "generating_images",
  "generating_video",
  "generating_sad_image",
  "generating_sad_video",
  "generating_happy_image",
  "generating_happy_video",
  "generating_kandinsky",
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
const HAPPINESS_DELTAS = new Set<ConversationHappinessDelta>([-80, -60, -40, -20, 0, 30, 100]);
const MAX_LOCAL_CHAT_REPLY_LENGTH = 8_000;
const MAX_PATCH_BYTES = 128_000;
const MAX_DEBUG_BYTES = 512_000;
const MAX_MEMORY_EXTRACTION_OPERATIONS = 12;
const MAX_MEMORY_CONSOLIDATION_OPERATIONS = 120;
const MAX_CHARACTER_BIBLE_BYTES = 512_000;
const MAX_GENERATION_ERROR_BYTES = 64_000;
const MAX_INTERACTIVE_TRAVEL_ARC_PLAN_ENTRIES = 32;

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

function boundedRecord(
  value: unknown,
  path: string,
  maxBytes: number,
  options: SafeJsonCloneOptions = {},
): JsonRecord {
  const parsed = record(value, path);
  if (serializedJsonBytes(parsed) > maxBytes) {
    return fail(path, `JSON object no larger than ${maxBytes} bytes`);
  }
  const cloned = safeJsonClone(parsed, {
    maxDepth: 12,
    maxNodes: 2_500,
    maxStringLength: 32_000,
    maxArrayLength: 200,
    maxObjectKeys: 200,
    ...options,
  });
  if (!isSafeJsonRecord(cloned)) {
    return fail(path, "safe JSON object");
  }
  return cloned;
}

function optionalBoundedRecord(
  value: unknown,
  path: string,
  maxBytes: number,
  options: SafeJsonCloneOptions = {},
): JsonRecord | undefined {
  return value === null || value === undefined
    ? undefined
    : boundedRecord(value, path, maxBytes, options);
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

function boundedString(
  value: unknown,
  path: string,
  { min = 1, max }: { min?: number; max: number },
): string {
  const parsed = string(value, path);
  if (parsed.length < min || parsed.length > max) {
    return fail(path, `string with length ${min}–${max}`);
  }
  return parsed;
}

function interactiveTravelId(value: unknown, path: string): string {
  const parsed = boundedString(value, path, { max: 120 });
  if (!INTERACTIVE_TRAVEL_ID_PATTERN.test(parsed)) {
    return fail(path, "interactive-travel identifier");
  }
  return parsed;
}

function integerInRange(value: unknown, path: string, min: number, max: number): number {
  const parsed = number(value, path);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) {
    return fail(path, `integer ${min}–${max}`);
  }
  return parsed;
}

function optionalString(value: unknown, path: string): string | undefined {
  return value === null || value === undefined ? undefined : string(value, path);
}

function optionalBoundedString(
  value: unknown,
  path: string,
  bounds: { min?: number; max: number },
): string | undefined {
  return value === null || value === undefined
    ? undefined
    : boundedString(value, path, bounds);
}

function boundedDateTime(value: unknown, path: string): string {
  const parsed = boundedString(value, path, { max: 80 });
  if (Number.isNaN(Date.parse(parsed))) {
    return fail(path, "ISO date-time string");
  }
  return parsed;
}

function boundedAssetUrl(value: unknown, path: string): string {
  const parsed = boundedString(value, path, { max: 2_000 });
  if (!isSafePublicAssetUrl(parsed)) {
    return fail(path, "same-origin or configured API asset URL");
  }
  return parsed.trim();
}

function optionalBoundedAssetUrl(value: unknown, path: string): string | undefined {
  return value === null || value === undefined ? undefined : boundedAssetUrl(value, path);
}

function number(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fail(path, "finite number");
  }
  return value;
}

function numberInRange(value: unknown, path: string, min: number, max: number): number {
  const parsed = number(value, path);
  if (parsed < min || parsed > max) {
    return fail(path, `number ${min}–${max}`);
  }
  return parsed;
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

function optionalConversationHappinessDelta(
  value: unknown,
  path: string,
): ConversationHappinessDelta | undefined {
  if (value === null || value === undefined) {
    return undefined;
  }
  const parsed = number(value, path) as ConversationHappinessDelta;
  if (!HAPPINESS_DELTAS.has(parsed)) {
    return fail(path, "supported happiness delta");
  }
  return parsed;
}

function parseDebug(value: unknown, path: string): LocalChatResponse["debug"] {
  return optionalBoundedRecord(value, path, MAX_DEBUG_BYTES) as LocalChatResponse["debug"];
}

type GeneratedPetImages = GeneratePetApiResponse["images"];
type GeneratedPetStaticAsset = NonNullable<GeneratePetApiResponse["kandinskyAssets"]>;

function parseGeneratedPetImages(value: unknown, path: string): GeneratedPetImages {
  const images = record(value, path);
  const parseStage = (stage: "baby" | "teen" | "adult") => {
    const stageImages = record(images[stage], `${path}.${stage}`);
    return {
      idle: boundedAssetUrl(stageImages.idle, `${path}.${stage}.idle`),
      happy: boundedAssetUrl(stageImages.happy, `${path}.${stage}.happy`),
      hungry: boundedAssetUrl(stageImages.hungry, `${path}.${stage}.hungry`),
      sad: boundedAssetUrl(stageImages.sad, `${path}.${stage}.sad`),
    };
  };
  return {
    baby: parseStage("baby"),
    teen: parseStage("teen"),
    adult: parseStage("adult"),
  };
}

function parseGeneratedPetAsset(value: unknown, path: string): GeneratePetApiResponse {
  const payload = record(value, path);
  const characterBible = optionalBoundedRecord(
    payload.characterBible,
    `${path}.characterBible`,
    MAX_CHARACTER_BIBLE_BYTES,
    {
      maxDepth: 12,
      maxNodes: 5_000,
      maxStringLength: 32_000,
      maxArrayLength: 200,
      maxObjectKeys: 200,
    },
  );
  const kandinskyAssets =
    payload.kandinskyAssets === null || payload.kandinskyAssets === undefined
      ? undefined
      : parseGeneratedPetStaticAsset(payload.kandinskyAssets, `${path}.kandinskyAssets`);
  const videoUrl = optionalBoundedAssetUrl(payload.videoUrl, `${path}.videoUrl`);
  const sadVideoUrl = optionalBoundedAssetUrl(payload.sadVideoUrl, `${path}.sadVideoUrl`);
  const happyVideoUrl = optionalBoundedAssetUrl(payload.happyVideoUrl, `${path}.happyVideoUrl`);
  const blinkImageUrl = optionalBoundedAssetUrl(payload.blinkImageUrl, `${path}.blinkImageUrl`);
  const spriteSheetUrl = optionalBoundedAssetUrl(
    payload.spriteSheetUrl,
    `${path}.spriteSheetUrl`,
  );
  return {
    assetSetId: boundedString(payload.assetSetId, `${path}.assetSetId`, { max: 160 }),
    generatedAt: boundedDateTime(payload.generatedAt, `${path}.generatedAt`),
    images: parseGeneratedPetImages(payload.images, `${path}.images`),
    ...(videoUrl ? { videoUrl } : {}),
    ...(sadVideoUrl ? { sadVideoUrl } : {}),
    ...(happyVideoUrl ? { happyVideoUrl } : {}),
    ...(blinkImageUrl ? { blinkImageUrl } : {}),
    ...(spriteSheetUrl ? { spriteSheetUrl } : {}),
    ...(characterBible ? { characterBible } : {}),
    ...(kandinskyAssets ? { kandinskyAssets } : {}),
  };
}

function parseGeneratedPetStaticAsset(
  value: unknown,
  path: string,
): GeneratedPetStaticAsset {
  const payload = record(value, path);
  const videoUrl = optionalBoundedAssetUrl(payload.videoUrl, `${path}.videoUrl`);
  return {
    assetSetId: boundedString(payload.assetSetId, `${path}.assetSetId`, { max: 160 }),
    generatedAt: boundedDateTime(payload.generatedAt, `${path}.generatedAt`),
    images: parseGeneratedPetImages(payload.images, `${path}.images`),
    ...(videoUrl ? { videoUrl } : {}),
  };
}

export function parseGeneratePetJobResponse(value: unknown): GeneratePetJobResponse {
  const payload = record(value, "generatePetJob");
  const result =
    payload.result === null || payload.result === undefined
      ? undefined
      : parseGeneratedPetAsset(payload.result, "generatePetJob.result");
  const errorOptions: SafeJsonCloneOptions = {
    maxDepth: 8,
    maxNodes: 500,
    maxStringLength: 8_000,
    maxArrayLength: 50,
    maxObjectKeys: 80,
  };
  const error = optionalBoundedRecord(
    payload.error,
    "generatePetJob.error",
    MAX_GENERATION_ERROR_BYTES,
    errorOptions,
  );
  const backgroundError = optionalBoundedRecord(
    payload.backgroundError,
    "generatePetJob.backgroundError",
    MAX_GENERATION_ERROR_BYTES,
    errorOptions,
  );
  const comparisonError = optionalBoundedRecord(
    payload.comparisonError,
    "generatePetJob.comparisonError",
    MAX_GENERATION_ERROR_BYTES,
    errorOptions,
  );
  const status = enumValue(payload.status, JOB_STATUSES, "generatePetJob.status");
  const phase = enumValue(payload.phase, JOB_PHASES, "generatePetJob.phase");
  if (status === "succeeded" && !result) {
    return fail("generatePetJob.result", "a result for a succeeded job");
  }
  if (status === "succeeded" && phase !== "completed") {
    return fail("generatePetJob.phase", "completed for a succeeded job");
  }
  if (phase === "completed" && status !== "succeeded" && status !== "failed") {
    return fail("generatePetJob.status", "a terminal status for a completed job");
  }
  return {
    jobId: boundedString(payload.jobId, "generatePetJob.jobId", { max: 160 }),
    status,
    phase,
    createdAt: boundedDateTime(payload.createdAt, "generatePetJob.createdAt"),
    updatedAt: boundedDateTime(payload.updatedAt, "generatePetJob.updatedAt"),
    ...(result ? { result } : {}),
    ...(error ? { error } : {}),
    ...(backgroundError ? { backgroundError } : {}),
    ...(comparisonError ? { comparisonError } : {}),
  };
}

export function parseLocalChatResponse(value: unknown): LocalChatResponse {
  const payload = record(value, "localChat");
  const petPatch = optionalRecord(payload.petPatch, "localChat.petPatch");
  return {
    reply: boundedString(payload.reply, "localChat.reply", {
      min: 0,
      max: MAX_LOCAL_CHAT_REPLY_LENGTH,
    }),
    moodHint: optionalEnumValue(payload.moodHint, PET_MOODS, "localChat.moodHint"),
    happinessDelta: optionalConversationHappinessDelta(
      payload.happinessDelta,
      "localChat.happinessDelta",
    ),
    complimentKey: optionalBoundedString(payload.complimentKey, "localChat.complimentKey", {
      max: 120,
    }),
    innerThought: optionalBoundedString(payload.innerThought, "localChat.innerThought", {
      min: 0,
      max: 80,
    }),
    faceHint: optionalEnumValue(payload.faceHint, FACE_HINTS, "localChat.faceHint"),
    petPatch: petPatch
      ? { name: optionalBoundedString(petPatch.name, "localChat.petPatch.name", { max: 32 }) }
      : undefined,
    storyLibraryPatch: optionalBoundedRecord(
      payload.storyLibraryPatch,
      "localChat.storyLibraryPatch",
      MAX_PATCH_BYTES,
    ),
    debug: parseDebug(payload.debug, "localChat.debug"),
  };
}

export function parseInteractiveTravelResponse(value: unknown): InteractiveTravelResponse {
  const payload = record(value, "interactiveTravelResponse");
  const travel = record(payload.travel, "interactiveTravelResponse.travel");
  const rawParts = array(travel.parts, "interactiveTravelResponse.travel.parts");
  if (rawParts.length < 1 || rawParts.length > 7) {
    return fail("interactiveTravelResponse.travel.parts", "1–7 parts");
  }
  const parts = rawParts.map((item, index) => {
    const partPath = `interactiveTravelResponse.travel.parts[${index}]`;
    const part = record(item, partPath);
    const partNumber = integerInRange(
      part.partNumber,
      `${partPath}.partNumber`,
      1,
      7,
    );
    if (partNumber !== index + 1) {
      return fail(`${partPath}.partNumber`, `${index + 1}`);
    }
    const transitionValue = optionalRecord(part.transition, `${partPath}.transition`);
    const transition = transitionValue
      ? {
          elapsedHours: integerInRange(
            transitionValue.elapsedHours,
            `${partPath}.transition.elapsedHours`,
            0,
            8,
          ),
          summary: boundedString(transitionValue.summary, `${partPath}.transition.summary`, {
            max: 240,
          }),
          departureHook: optionalBoundedString(
            transitionValue.departureHook,
            `${partPath}.transition.departureHook`,
            { max: 280 },
          ),
          continuityAnchor: optionalBoundedString(
            transitionValue.continuityAnchor,
            `${partPath}.transition.continuityAnchor`,
            { max: 60 },
          ),
        }
      : undefined;
    if (index === 0 && transition) {
      return fail(`${partPath}.transition`, "no transition for the first part");
    }
    if (index > 0 && !transition) {
      return fail(`${partPath}.transition`, "elapsed story transition");
    }
    const answer = optionalBoundedString(part.answer, `${partPath}.answer`, { max: 1000 });
    const rawActionSuggestions = optionalArray(
      part.actionSuggestions,
      `${partPath}.actionSuggestions`,
    );
    if (rawActionSuggestions.length > 3) {
      return fail(`${partPath}.actionSuggestions`, "0–3 suggestions");
    }
    const actionSuggestions = rawActionSuggestions.map((suggestion, suggestionIndex) =>
      boundedString(
        suggestion,
        `${partPath}.actionSuggestions[${suggestionIndex}]`,
        { max: 72 },
      ),
    );
    const backgroundImageUrl = optionalBoundedAssetUrl(
      part.backgroundImageUrl,
      `${partPath}.backgroundImageUrl`,
    );
    const backgroundVideoUrl = optionalBoundedAssetUrl(
      part.backgroundVideoUrl,
      `${partPath}.backgroundVideoUrl`,
    );
    const resultValue = optionalRecord(part.result, `${partPath}.result`);
    if ((answer === undefined) !== (resultValue === undefined)) {
      return fail(partPath, "answer and result together");
    }
    const result = resultValue
      ? (() => {
          const resultPath = `${partPath}.result`;
          const rawStatImpacts = array(
            resultValue.statImpacts,
            `${resultPath}.statImpacts`,
          );
          if (rawStatImpacts.length > 2) {
            return fail(`${resultPath}.statImpacts`, "0–2 impacts");
          }
          const statImpacts = rawStatImpacts.map((impactItem, impactIndex) => {
            const impactPath = `${resultPath}.statImpacts[${impactIndex}]`;
            const impact = record(impactItem, impactPath);
            return {
              stat: enumValue(impact.stat, new Set(PET_STAT_KEYS), `${impactPath}.stat`),
              amount: integerInRange(impact.amount, `${impactPath}.amount`, -15, 15),
              reason: boundedString(impact.reason, `${impactPath}.reason`, { max: 280 }),
            };
          });
          return {
            text: boundedString(resultValue.text, `${resultPath}.text`, { max: 700 }),
            adviceAssessment: enumValue(
              resultValue.adviceAssessment,
              INTERACTIVE_TRAVEL_ASSESSMENTS,
              `${resultPath}.adviceAssessment`,
            ),
            reaction: boundedString(resultValue.reaction, `${resultPath}.reaction`, {
              max: 220,
            }),
            reactionTone: enumValue(
              resultValue.reactionTone,
              INTERACTIVE_TRAVEL_REACTION_TONES,
              `${resultPath}.reactionTone`,
            ),
            consequence: boundedString(resultValue.consequence, `${resultPath}.consequence`, {
              max: 280,
            }),
            outcomeValence: enumValue(
              resultValue.outcomeValence,
              INTERACTIVE_TRAVEL_VALENCES,
              `${resultPath}.outcomeValence`,
            ),
            statImpacts,
          };
        })()
      : undefined;
    return {
      partNumber,
      title: boundedString(part.title, `${partPath}.title`, { max: 120 }),
      storyText: boundedString(part.storyText, `${partPath}.storyText`, { max: 700 }),
      transition,
      challenge: boundedString(part.challenge, `${partPath}.challenge`, { max: 280 }),
      actionSuggestions,
      backgroundImageUrl,
      backgroundVideoUrl,
      answer,
      result,
    };
  });
  const completed = boolean(travel.completed, "interactiveTravelResponse.travel.completed");
  if (completed && parts.length < 3) {
    return fail("interactiveTravelResponse.travel.completed", "at least three completed parts");
  }
  if (completed && parts.some((part) => !part.result)) {
    return fail("interactiveTravelResponse.travel.parts", "all completed parts resolved");
  }
  if (!completed) {
    if (parts.at(-1)?.result) {
      return fail("interactiveTravelResponse.travel.parts", "pending final part");
    }
    if (parts.slice(0, -1).some((part) => !part.result)) {
      return fail("interactiveTravelResponse.travel.parts", "only the final part pending");
    }
  }
  const arcPlanValue = record(travel.arcPlan, "interactiveTravelResponse.travel.arcPlan");
  const arcPlanEntries = Object.entries(arcPlanValue);
  if (arcPlanEntries.length > MAX_INTERACTIVE_TRAVEL_ARC_PLAN_ENTRIES) {
    return fail(
      "interactiveTravelResponse.travel.arcPlan",
      `at most ${MAX_INTERACTIVE_TRAVEL_ARC_PLAN_ENTRIES} entries`,
    );
  }
  const arcPlan: Record<string, string> = Object.create(null) as Record<string, string>;
  for (const [key, item] of arcPlanEntries) {
    if (key === "__proto__" || key === "constructor" || key === "prototype" || key.length > 80) {
      return fail("interactiveTravelResponse.travel.arcPlan", "safe bounded keys");
    }
    arcPlan[key] = boundedString(
      item,
      `interactiveTravelResponse.travel.arcPlan.${key}`,
      { max: 500 },
    );
  }
  const impactValue = optionalRecord(
    travel.statImpact,
    "interactiveTravelResponse.travel.statImpact",
  );
  const statImpact = impactValue
    ? {
        stat: enumValue(
          impactValue.stat,
          new Set(PET_STAT_KEYS),
          "interactiveTravelResponse.travel.statImpact.stat",
        ),
        amount: integerInRange(
          impactValue.amount,
          "interactiveTravelResponse.travel.statImpact.amount",
          -15,
          15,
        ),
        reason: boundedString(
          impactValue.reason,
          "interactiveTravelResponse.travel.statImpact.reason",
          { max: 280 },
        ),
      }
    : undefined;
  const outcomeValence = optionalEnumValue(
    travel.outcomeValence,
    INTERACTIVE_TRAVEL_VALENCES,
    "interactiveTravelResponse.travel.outcomeValence",
  );
  if (completed && !outcomeValence) {
    return fail("interactiveTravelResponse.travel.outcomeValence", "completed outcome");
  }
  if (!completed && (outcomeValence || statImpact)) {
    return fail("interactiveTravelResponse.travel", "no final outcome before completion");
  }
  const introReactionValue = optionalRecord(
    travel.introReaction,
    "interactiveTravelResponse.travel.introReaction",
  );
  const introReaction = introReactionValue
    ? {
        text: boundedString(
          introReactionValue.text,
          "interactiveTravelResponse.travel.introReaction.text",
          { max: 220 },
        ),
        tone: enumValue(
          introReactionValue.tone,
          INTERACTIVE_TRAVEL_REACTION_TONES,
          "interactiveTravelResponse.travel.introReaction.tone",
        ),
      }
    : undefined;
  return {
    travel: {
      travelId: interactiveTravelId(
        travel.travelId,
        "interactiveTravelResponse.travel.travelId",
      ),
      generatedAt: boundedDateTime(
        travel.generatedAt,
        "interactiveTravelResponse.travel.generatedAt",
      ),
      destination: boundedString(
        travel.destination,
        "interactiveTravelResponse.travel.destination",
        { max: 500 },
      ),
      overallTitle: boundedString(
        travel.overallTitle,
        "interactiveTravelResponse.travel.overallTitle",
        { max: 120 },
      ),
      introReaction,
      arcPlan,
      parts,
      completed,
      outcomeValence,
      statImpact,
    },
    debug: parseDebug(payload.debug, "interactiveTravelResponse.debug"),
  };
}

export function parseInteractiveTravelSuggestionsResponse(
  value: unknown,
): InteractiveTravelSuggestionsResponse {
  const payload = record(value, "interactiveTravelSuggestions");
  const rawDestinations = array(
    payload.destinations,
    "interactiveTravelSuggestions.destinations",
  );
  if (rawDestinations.length !== 3) {
    return fail("interactiveTravelSuggestions.destinations", "exactly 3 destinations");
  }
  return {
    destinations: rawDestinations.map((destination, index) =>
      boundedString(
        destination,
        `interactiveTravelSuggestions.destinations[${index}]`,
        { max: 72 },
      ),
    ),
    debug: parseDebug(payload.debug, "interactiveTravelSuggestions.debug"),
  };
}

export function parseInteractiveTravelIllustrationResponse(
  value: unknown,
): InteractiveTravelIllustrationResponse {
  const payload = record(value, "interactiveTravelIllustration");
  return {
    partNumber: integerInRange(
      payload.partNumber,
      "interactiveTravelIllustration.partNumber",
      1,
      7,
    ),
    imageUrl: boundedAssetUrl(payload.imageUrl, "interactiveTravelIllustration.imageUrl"),
  };
}

export function parseInteractiveTravelAnimationResponse(
  value: unknown,
): InteractiveTravelAnimationResponse {
  const payload = record(value, "interactiveTravelAnimation");
  return {
    partNumber: integerInRange(
      payload.partNumber,
      "interactiveTravelAnimation.partNumber",
      1,
      7,
    ),
    videoUrl: boundedAssetUrl(payload.videoUrl, "interactiveTravelAnimation.videoUrl"),
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
    resetPet:
      payload.resetPet === undefined || payload.resetPet === null
        ? undefined
        : boolean(payload.resetPet, "pushSnapshot.resetPet"),
    statsPatch: parseStatsPatch(payload.statsPatch, "pushSnapshot.statsPatch"),
    storyLibraryPatch: optionalBoundedRecord(
      payload.storyLibraryPatch,
      "pushSnapshot.storyLibraryPatch",
      MAX_PATCH_BYTES,
    ),
    liteOverlayPatch: optionalBoundedRecord(
      payload.liteOverlayPatch,
      "pushSnapshot.liteOverlayPatch",
      MAX_PATCH_BYTES,
    ),
    recentStoryEventsPatch: optionalBoundedRecord(
      payload.recentStoryEventsPatch,
      "pushSnapshot.recentStoryEventsPatch",
      MAX_PATCH_BYTES,
    ),
  };
}

export function parseDeletePushSnapshotResponse(value: unknown): DeletePushSnapshotResponse {
  const payload = record(value, "deletePushSnapshot");
  return {
    unregistered: boolean(payload.unregistered, "deletePushSnapshot.unregistered"),
    petId: boundedString(payload.petId, "deletePushSnapshot.petId", { max: 120 }),
  };
}

export function parseLiteFactExtractionResponse(value: unknown): LiteFactExtractionResponse {
  const payload = record(value, "liteFacts");
  return {
    liteOverlayPatch: optionalBoundedRecord(
      payload.liteOverlayPatch,
      "liteFacts.liteOverlayPatch",
      MAX_PATCH_BYTES,
    ),
    debug: parseDebug(payload.debug, "liteFacts.debug"),
  };
}

function optionalMemoryKind(value: unknown, path: string): UserMemoryKind | undefined {
  return optionalEnumValue(value, MEMORY_KINDS, path);
}

function parseMemoryFields(payload: JsonRecord, path: string) {
  const rawTags = optionalArray(payload.tags, `${path}.tags`);
  if (rawTags.length > 12) {
    return fail(`${path}.tags`, "0–12 tags");
  }
  return {
    kind: enumValue(payload.kind, MEMORY_KINDS, `${path}.kind`),
    text: boundedString(payload.text, `${path}.text`, { max: 500 }),
    normalizedKey: boundedString(payload.normalizedKey, `${path}.normalizedKey`, {
      max: 160,
    }),
    confidence: numberInRange(payload.confidence, `${path}.confidence`, 0, 1),
    importance: numberInRange(payload.importance, `${path}.importance`, 0, 1),
    occurredAt: optionalBoundedString(payload.occurredAt, `${path}.occurredAt`, {
      min: 0,
      max: 80,
    }),
    dueAt: optionalBoundedString(payload.dueAt, `${path}.dueAt`, { min: 0, max: 80 }),
    expiresAt: optionalBoundedString(payload.expiresAt, `${path}.expiresAt`, {
      min: 0,
      max: 80,
    }),
    tags: rawTags.map((tag, index) =>
      boundedString(tag, `${path}.tags[${index}]`, { max: 40 }),
    ),
  };
}

function parseMemoryOperation(value: unknown, path: string): MemoryOperation {
  const payload = record(value, path);
  const type = string(payload.type, `${path}.type`);
  if (type === "capture_learning") {
    return {
      type,
      observation: boundedString(payload.observation, `${path}.observation`, { max: 500 }),
      patternKey: optionalBoundedString(payload.patternKey, `${path}.patternKey`, {
        max: 120,
      }),
      kind: optionalMemoryKind(payload.kind, `${path}.kind`),
      confidence: numberInRange(payload.confidence, `${path}.confidence`, 0, 1),
      importance: numberInRange(payload.importance, `${path}.importance`, 0, 1),
      occurredAt: optionalBoundedString(payload.occurredAt, `${path}.occurredAt`, {
        min: 0,
        max: 80,
      }),
      dueAt: optionalBoundedString(payload.dueAt, `${path}.dueAt`, { min: 0, max: 80 }),
    };
  }
  if (type === "remember_user_fact" || type === "replace_user_fact") {
    return { type, ...parseMemoryFields(payload, path) };
  }
  if (type === "forget_user_fact") {
    return {
      type,
      normalizedKey: optionalBoundedString(payload.normalizedKey, `${path}.normalizedKey`, {
        min: 0,
        max: 160,
      }),
      matchText: optionalBoundedString(
        payload.matchText ?? payload.text,
        `${path}.matchText`,
        { min: 0, max: 180 },
      ),
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
      learningId: boundedString(payload.learningId, `${path}.learningId`, { max: 160 }),
      memory: parseMemoryFields(record(payload.memory, `${path}.memory`), `${path}.memory`),
    };
  }
  if (type === "prune_learning") {
    return {
      type,
      learningId: boundedString(payload.learningId, `${path}.learningId`, { max: 160 }),
      reason: optionalBoundedString(payload.reason, `${path}.reason`, { min: 0, max: 500 }),
    };
  }
  if (type === "rewrite_summary" || type === "rewrite_user_profile") {
    return {
      type,
      content: boundedString(payload.content, `${path}.content`, { min: 0, max: 1_000 }),
    };
  }
  return fail(`${path}.type`, "known consolidation operation");
}

export function parseMemoryExtractionResponse(value: unknown): MemoryExtractionResponse {
  const payload = record(value, "memoryExtraction");
  const operations = optionalArray(payload.operations, "memoryExtraction.operations");
  if (operations.length > MAX_MEMORY_EXTRACTION_OPERATIONS) {
    return fail("memoryExtraction.operations", "0–12 operations");
  }
  return {
    operations: operations.map(
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
  const operations = optionalArray(payload.operations, "memoryConsolidation.operations");
  if (operations.length > MAX_MEMORY_CONSOLIDATION_OPERATIONS) {
    return fail("memoryConsolidation.operations", "0–120 operations");
  }
  return {
    operations: operations.map(
      (operation, index) =>
        parseConsolidationOperation(operation, `memoryConsolidation.operations[${index}]`),
    ),
    debug: parseDebug(payload.debug, "memoryConsolidation.debug"),
  };
}
