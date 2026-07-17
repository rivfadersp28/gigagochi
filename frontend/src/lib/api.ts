import type {
  GeneratePetResponse,
  InteractiveTravelAnimationResponse,
  InteractiveTravelIllustrationResponse,
  InteractiveTravelPart,
  InteractiveTravelResult,
  InteractiveTravelResponse,
  InteractiveTravelState,
  InteractiveTravelSuggestionsResponse,
  LocalPetAssetSet,
  LocalChatHistoryV1,
  LiteFactExtractionResponse,
  LocalChatMessage,
  LocalChatResponse,
  LocalPetState,
  PetMood,
  PetStage,
  TravelVideoPrototype,
} from "./types";
import type {
  LocalPetMemoryContext,
  LocalPetMemoryStateV1,
  MemoryConsolidationResponse,
  MemoryExtractionResponse,
} from "./localPetMemoryTypes";
import { buildExistingMemoryBrief } from "./localPetMemoryStorage";
import { characterNameFromAssetSet } from "./localPetCharacter";
import { nextPushSnapshotOrder } from "./pushSnapshotRevision";
import { compactPushSnapshotPayload } from "./pushSnapshotPayload";
import { resolveApiAssetUrl } from "./publicAssetUrl";
import { getTelegramInitData } from "./telegram";
import {
  parseGeneratePetJobResponse,
  parseDeletePushSnapshotResponse,
  parseInteractiveTravelAnimationResponse,
  parseInteractiveTravelIllustrationResponse,
  parseInteractiveTravelResponse,
  parseInteractiveTravelSuggestionsResponse,
  parseLiteFactExtractionResponse,
  parseLocalChatResponse,
  parseMemoryConsolidationResponse,
  parseMemoryExtractionResponse,
  parsePushSnapshotResponse,
  parseTravelVideoPrototype,
  type GeneratePetApiResponse,
  type GeneratePetJobResponse,
  type DeletePushSnapshotResponse,
  type PushSnapshotResponse,
} from "./apiContracts";
import {
  ApiContractError,
  ApiError,
  apiErrorFromDetail,
  request,
} from "./apiTransport";

export { API_URL, ApiError } from "./apiTransport";

const ENABLE_TMA_DEV_FALLBACK = process.env.NEXT_PUBLIC_ENABLE_TMA_DEV_FALLBACK === "true";
const MAX_CHAT_INPUT_LENGTH = 1000;
const MAX_CHAT_HISTORY_ITEMS = 12;
const MAX_CHAT_HISTORY_TEXT_LENGTH = 8000;

type LocalChatOptions = {
  includeDebug?: boolean;
  memoryContext?: LocalPetMemoryContext;
  complimentHistory?: string[];
  excludeMutableCharacterContext?: boolean;
  replyMaxChars?: number;
  signal?: AbortSignal;
  visibleContext?: {
    lastPetLine: string;
  };
};

export type GenerationDurationSummary = {
  count: number;
  averageSeconds?: number;
  medianSeconds?: number;
  p95Seconds?: number;
  minSeconds?: number;
  maxSeconds?: number;
};

export type TelegramCapabilities = {
  telegramUserId: number;
  debugMenu: boolean;
  interactiveTravel: boolean;
};

export type DebugSavedPetBundle = {
  petId: string;
  pet: LocalPetState;
  chatHistory: LocalChatHistoryV1;
  memory: LocalPetMemoryStateV1;
};

export type DebugSavedPetSaveResponse = {
  saved: true;
  created: boolean;
  petId: string;
};

export type DebugSavedPetActivateResponse = {
  activated: true;
  petId: string;
  bundle: DebugSavedPetBundle;
};

export type OutfitSimplification = {
  item: string;
  displayItem: string;
  generationDescription: string;
};

export type AutomaticInteractiveStory = {
  token: string;
  travelId: string;
  destination: string;
  createdAt: string;
  title: string;
  storyText: string;
  question: string;
  choices: [string, string, string, string];
  outcomes: [string, string, string, string];
  selectedChoice?: string;
  result?: InteractiveTravelResult;
  situationImageUrl: string;
  situationVideoUrl: string;
  outcomeImageUrls: [string, string, string, string];
  outcomeVideoUrls: [string, string, string, string];
};

function parseAutomaticInteractiveStory(payload: unknown): AutomaticInteractiveStory {
  if (!isRecord(payload)) {
    throw new ApiContractError("automatic story: expected an object");
  }
  const text = (value: unknown, field: string) => {
    if (typeof value !== "string" || !value.trim()) {
      throw new ApiContractError(`automatic story: invalid ${field}`);
    }
    return value.trim();
  };
  const quartet = (value: unknown, field: string): [string, string, string, string] => {
    if (!Array.isArray(value) || value.length !== 4) {
      throw new ApiContractError(`automatic story: invalid ${field}`);
    }
    return [
      text(value[0], `${field}[0]`),
      text(value[1], `${field}[1]`),
      text(value[2], `${field}[2]`),
      text(value[3], `${field}[3]`),
    ];
  };
  const resultPayload = isRecord(payload.result) ? payload.result : undefined;
  const result: InteractiveTravelResult | undefined = resultPayload ? {
    text: text(resultPayload.text, "result.text"),
    adviceAssessment: resultPayload.adviceAssessment === "helpful" ? "helpful" as const : "harmful" as const,
    reaction: text(resultPayload.reaction, "result.reaction"),
    reactionTone: resultPayload.reactionTone === "determined" ? "determined" as const : "worried" as const,
    consequence: text(resultPayload.consequence, "result.consequence"),
    outcomeValence: resultPayload.outcomeValence === "positive" ? "positive" as const : "negative" as const,
    experienceGained: typeof resultPayload.experienceGained === "number"
      ? resultPayload.experienceGained
      : 0,
    statImpacts: Array.isArray(resultPayload.statImpacts)
      ? resultPayload.statImpacts.flatMap((item) => {
          if (!isRecord(item) || typeof item.amount !== "number") {
            return [];
          }
          const stat = item.stat === "happiness" || item.stat === "energy" || item.stat === "hunger"
            ? item.stat as InteractiveTravelResult["statImpacts"][number]["stat"]
            : null;
          return stat ? [{
            stat,
            amount: item.amount,
            reason: text(item.reason, "result.statImpacts.reason"),
          }] : [];
        })
      : [],
  } : undefined;
  return {
    token: text(payload.token, "token"),
    travelId: text(payload.travelId, "travelId"),
    destination: text(payload.destination, "destination"),
    createdAt: text(payload.createdAt, "createdAt"),
    title: text(payload.title, "title"),
    storyText: text(payload.storyText, "storyText"),
    question: text(payload.question, "question"),
    choices: quartet(payload.choices, "choices"),
    outcomes: quartet(payload.outcomes, "outcomes"),
    selectedChoice: typeof payload.selectedChoice === "string"
      ? payload.selectedChoice.trim() || undefined
      : undefined,
    result,
    situationImageUrl: publicImageUrl(text(payload.situationImageUrl, "situationImageUrl")),
    situationVideoUrl: publicImageUrl(text(payload.situationVideoUrl, "situationVideoUrl")),
    outcomeImageUrls: quartet(payload.outcomeImageUrls, "outcomeImageUrls").map(publicImageUrl) as [string, string, string, string],
    outcomeVideoUrls: quartet(payload.outcomeVideoUrls, "outcomeVideoUrls").map(publicImageUrl) as [string, string, string, string],
  };
}

function parseTelegramCapabilities(payload: unknown): TelegramCapabilities {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new ApiContractError("capabilities: expected an object");
  }
  const value = payload as Record<string, unknown>;
  if (
    typeof value.telegramUserId !== "number"
    || !Number.isSafeInteger(value.telegramUserId)
    || typeof value.debugMenu !== "boolean"
    || typeof value.interactiveTravel !== "boolean"
  ) {
    throw new ApiContractError("capabilities: invalid capability fields");
  }
  return {
    telegramUserId: value.telegramUserId,
    debugMenu: value.debugMenu,
    interactiveTravel: value.interactiveTravel,
  };
}

export type GenerationStats = {
  windowDays: number;
  totalJobs: number;
  activeJobs: number;
  failedJobs: number;
  normal: GenerationDurationSummary;
  full: GenerationDurationSummary;
  recent: Array<{
    jobId: string;
    ownerName?: string;
    queuedAt: string;
    status: string;
    normalSeconds?: number;
    fullSeconds?: number;
  }>;
};

const REQUIRED_STAGES = ["baby", "teen", "adult"] as const satisfies readonly PetStage[];
const REQUIRED_MOODS = ["idle", "happy", "hungry", "sad"] as const satisfies readonly PetMood[];
const GENERATION_POLL_INTERVAL_MS = 2000;
const MAX_GENERATION_POLL_MS = 25 * 60 * 1000;
const MEDIA_REQUEST_TIMEOUT_MS = 20 * 60 * 1000;

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason ?? new DOMException("Request aborted", "AbortError"));
      return;
    }
    const timeoutId = window.setTimeout(() => {
      signal?.removeEventListener("abort", handleAbort);
      resolve();
    }, ms);
    const handleAbort = () => {
      window.clearTimeout(timeoutId);
      reject(signal?.reason ?? new DOMException("Request aborted", "AbortError"));
    };
    signal?.addEventListener("abort", handleAbort, { once: true });
  });
}

function statForApi(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function petStatsForApi(stats: LocalPetState["stats"]) {
  return {
    hunger: statForApi(stats.hunger),
    happiness: statForApi(stats.happiness),
    energy: statForApi(stats.energy),
  };
}

function petNameForApi(pet: LocalPetState): string | undefined {
  const explicitName = pet.name?.trim();
  return explicitName || characterNameFromAssetSet(pet.assetSet);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function parseOutfitSimplification(value: unknown): OutfitSimplification {
  if (
    !isRecord(value)
    || typeof value.item !== "string"
    || !value.item.trim()
    || typeof value.displayItem !== "string"
    || !value.displayItem.trim()
    || typeof value.generationDescription !== "string"
    || !value.generationDescription.trim()
  ) {
    throw new ApiContractError("Invalid outfit simplification response");
  }
  return {
    item: value.item.trim(),
    displayItem: value.displayItem.trim(),
    generationDescription: value.generationDescription.trim(),
  };
}

function parseGenerationStats(value: unknown): GenerationStats {
  if (!isRecord(value) || !isRecord(value.normal) || !isRecord(value.full)) {
    throw new ApiContractError("Invalid generation stats response");
  }
  const number = (input: unknown, path: string) => {
    if (typeof input !== "number" || !Number.isFinite(input)) {
      throw new ApiContractError(`Invalid ${path}`);
    }
    return input;
  };
  const optionalNumber = (input: unknown, path: string) =>
    input === null || input === undefined ? undefined : number(input, path);
  const summary = (input: Record<string, unknown>, path: string) => ({
    count: number(input.count, `${path}.count`),
    averageSeconds: optionalNumber(input.averageSeconds, `${path}.averageSeconds`),
    medianSeconds: optionalNumber(input.medianSeconds, `${path}.medianSeconds`),
    p95Seconds: optionalNumber(input.p95Seconds, `${path}.p95Seconds`),
    minSeconds: optionalNumber(input.minSeconds, `${path}.minSeconds`),
    maxSeconds: optionalNumber(input.maxSeconds, `${path}.maxSeconds`),
  });
  const recent = Array.isArray(value.recent) ? value.recent : [];
  return {
    windowDays: number(value.windowDays, "generationStats.windowDays"),
    totalJobs: number(value.totalJobs, "generationStats.totalJobs"),
    activeJobs: number(value.activeJobs, "generationStats.activeJobs"),
    failedJobs: number(value.failedJobs, "generationStats.failedJobs"),
    normal: summary(value.normal, "generationStats.normal"),
    full: summary(value.full, "generationStats.full"),
    recent: recent.flatMap((item) => {
      if (!isRecord(item) || typeof item.jobId !== "string" || typeof item.status !== "string") {
        return [];
      }
      return [{
        jobId: item.jobId,
        ownerName: typeof item.ownerName === "string" ? item.ownerName : undefined,
        queuedAt: typeof item.queuedAt === "string" ? item.queuedAt : "",
        status: item.status,
        normalSeconds: optionalNumber(item.normalSeconds, "generationStats.recent.normalSeconds"),
        fullSeconds: optionalNumber(item.fullSeconds, "generationStats.recent.fullSeconds"),
      }];
    }),
  };
}

export function fetchGenerationStats(days = 30): Promise<GenerationStats> {
  return request(
    `/api/generation-stats?days=${days}&mine=true`,
    { headers: tmaAuthHeaders() },
    parseGenerationStats,
  );
}

function cleanApiText(value: unknown, maxLength: number): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const clean = value.trim().slice(0, maxLength);
  return clean || undefined;
}

function cleanApiDate(value: unknown): string | undefined {
  const text = cleanApiText(value, 80);
  return text && !Number.isNaN(Date.parse(text)) ? text : undefined;
}

function chatHistoryForApi(
  history: (Pick<LocalChatMessage, "role" | "text"> & Partial<Pick<LocalChatMessage, "createdAt">>)[],
  limit = MAX_CHAT_HISTORY_ITEMS,
) {
  return history
    .flatMap((item) => {
      const text = cleanApiText(item.text, MAX_CHAT_HISTORY_TEXT_LENGTH);
      const createdAt = cleanApiDate(item.createdAt);
      return text ? [{
        role: item.role,
        text,
        ...(createdAt ? { createdAt } : {}),
      }] : [];
    })
    .slice(-limit);
}

function memoryContextForApi(
  memoryContext: LocalPetMemoryContext | undefined,
): LocalPetMemoryContext | undefined {
  if (!memoryContext) {
    return undefined;
  }

  const relevantMemories = memoryContext.relevantMemories
    .flatMap((item) => {
      const id = cleanApiText(item.id, 120);
      const text = cleanApiText(item.text, 500);
      if (!id || !text) {
        return [];
      }
      const recordedAt = cleanApiDate(item.recordedAt);
      const occurredAt = cleanApiDate(item.occurredAt);
      const lastMentionedAt = cleanApiDate(item.lastMentionedAt);
      return [{
        id,
        kind: item.kind,
        text,
        ...(item.memoryClass ? { memoryClass: item.memoryClass } : {}),
        ...(recordedAt ? { recordedAt } : {}),
        ...(occurredAt ? { occurredAt } : {}),
        ...(lastMentionedAt ? { lastMentionedAt } : {}),
        dueAt: cleanApiText(item.dueAt, 80),
      }];
    })
    .slice(0, 5);
  const episodes = (memoryContext.episodes ?? [])
    .flatMap((episode) => {
      const id = cleanApiText(episode.id, 160);
      const messages = episode.messages
        .flatMap((message) => {
          const text = cleanApiText(message.text, MAX_CHAT_HISTORY_TEXT_LENGTH);
          const createdAt = cleanApiDate(message.createdAt);
          return text
            ? [{
                role: message.role,
                text,
                ...(createdAt ? { createdAt } : {}),
              }]
            : [];
        })
        .slice(-8);
      return id && messages.length ? [{ id, messages }] : [];
    })
    .slice(0, 3);
  const proactiveCandidate = memoryContext.proactiveCandidate;
  const proactiveReason = cleanApiText(proactiveCandidate?.reason, 280);

  return {
    summary: cleanApiText(memoryContext.summary, 1000),
    userProfile: cleanApiText(memoryContext.userProfile, 1000),
    relevantMemories,
    episodes,
    proactiveCandidate: proactiveCandidate && proactiveReason
      ? {
          memoryIds: (proactiveCandidate.memoryIds ?? [])
            .flatMap((id) => cleanApiText(id, 120) ?? [])
            .slice(0, 5),
          episodeIds: (proactiveCandidate.episodeIds ?? [])
            .flatMap((id) => cleanApiText(id, 160) ?? [])
            .slice(0, 5),
          reason: proactiveReason,
        }
      : undefined,
  };
}

const MUTABLE_CHARACTER_CONTEXT_KEYS = [
  "lite_overlay",
  "story_library_overlay",
  "recent_story_events",
] as const;

export function characterBibleForApi(
  pet: LocalPetState,
  options: { excludeMutableContext?: boolean } = {},
): Record<string, unknown> | undefined {
  const template = isRecord(pet.assetSet?.characterTemplate)
    ? pet.assetSet.characterTemplate
    : {};
  const instance = isRecord(pet.assetSet?.characterBible)
    ? pet.assetSet.characterBible
    : {};
  if (!Object.keys(template).length && !Object.keys(instance).length) {
    return undefined;
  }
  const result: Record<string, unknown> = {
    ...template,
    ...instance,
  };
  for (const key of new Set([...Object.keys(template), ...Object.keys(instance)])) {
    const templateSection = template[key];
    const instanceSection = instance[key];
    if (isRecord(templateSection) || isRecord(instanceSection)) {
      result[key] = {
        ...(isRecord(templateSection) ? templateSection : {}),
        ...(isRecord(instanceSection) ? instanceSection : {}),
      };
    }
  }
  if (options.excludeMutableContext && isRecord(result.extensions)) {
    const extensions = { ...result.extensions };
    for (const key of MUTABLE_CHARACTER_CONTEXT_KEYS) {
      delete extensions[key];
    }
    if (Object.keys(extensions).length) {
      result.extensions = extensions;
    } else {
      delete result.extensions;
    }
  }
  return result;
}

function petContextForApi(
  pet: LocalPetState,
  options: { excludeMutableCharacterContext?: boolean } = {},
) {
  return {
    name: cleanApiText(petNameForApi(pet), 80),
    description: cleanApiText(pet.description, 300) ?? "Персонаж",
    characterBible: characterBibleForApi(pet, {
      excludeMutableContext: options.excludeMutableCharacterContext,
    }),
    stage: pet.stage,
    mood: pet.mood,
    stats: petStatsForApi(pet.stats),
  };
}

function interactiveTravelPetContextForApi(pet: LocalPetState) {
  return {
    ...petContextForApi(pet),
    petId: pet.petId,
    assetImages: publicAssetImagesForApi(pet.assetSet?.images),
  };
}

function tmaAuthHeaders(): HeadersInit {
  const initData = getTelegramInitData();
  if (initData) {
    return { Authorization: `tma ${initData}` };
  }
  if (shouldUseDevTmaAuth()) {
    return { "X-Telegram-Init-Data": "dev" };
  }
  return {};
}

export function canUseTmaApi() {
  return Boolean(getTelegramInitData()) || shouldUseDevTmaAuth();
}

export async function fetchTelegramCapabilities(): Promise<TelegramCapabilities> {
  return request(
    "/api/capabilities",
    {
      method: "GET",
      headers: tmaAuthHeaders(),
      timeoutMs: 15_000,
    },
    parseTelegramCapabilities,
  );
}

function parseDebugSavedPetSaveResponse(value: unknown): DebugSavedPetSaveResponse {
  if (
    !isRecord(value)
    || value.saved !== true
    || typeof value.created !== "boolean"
    || typeof value.petId !== "string"
  ) {
    throw new ApiContractError("invalid saved debug pet response");
  }
  return { saved: true, created: value.created, petId: value.petId };
}

function parseDebugSavedPetActivateResponse(value: unknown): DebugSavedPetActivateResponse {
  if (
    !isRecord(value)
    || value.activated !== true
    || typeof value.petId !== "string"
    || !isRecord(value.bundle)
    || value.bundle.petId !== value.petId
    || !isRecord(value.bundle.pet)
    || !isRecord(value.bundle.chatHistory)
    || !isRecord(value.bundle.memory)
  ) {
    throw new ApiContractError("invalid activated debug pet response");
  }
  return {
    activated: true,
    petId: value.petId,
    bundle: value.bundle as DebugSavedPetBundle,
  };
}

export function saveDebugPet(bundle: DebugSavedPetBundle): Promise<DebugSavedPetSaveResponse> {
  return request(
    "/api/debug/saved-pet",
    { method: "POST", headers: tmaAuthHeaders(), body: bundle },
    parseDebugSavedPetSaveResponse,
  );
}

export function activateDebugSavedPet(): Promise<DebugSavedPetActivateResponse> {
  return request(
    "/api/debug/saved-pet/activate",
    { method: "POST", headers: tmaAuthHeaders() },
    parseDebugSavedPetActivateResponse,
  );
}

export function getAutomaticInteractiveStory(token: string): Promise<AutomaticInteractiveStory> {
  return request(
    `/api/travel/automatic/${encodeURIComponent(token)}`,
    { method: "GET", headers: tmaAuthHeaders(), timeoutMs: 15_000 },
    parseAutomaticInteractiveStory,
  );
}

export function chooseAutomaticInteractiveStory(
  token: string,
  choice: string,
): Promise<AutomaticInteractiveStory> {
  return request(
    `/api/travel/automatic/${encodeURIComponent(token)}/choice`,
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      body: { choice: choice.trim().slice(0, 1000) },
      timeoutMs: 15_000,
    },
    parseAutomaticInteractiveStory,
  );
}

function shouldUseDevTmaAuth() {
  if (ENABLE_TMA_DEV_FALLBACK) {
    return true;
  }
  if (process.env.NODE_ENV !== "development" || typeof window === "undefined") {
    return false;
  }
  return ["localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"].includes(
    window.location.hostname,
  );
}

function browserTimezone() {
  if (typeof Intl === "undefined") {
    return "Europe/Moscow";
  }
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Moscow";
}

function publicImageUrl(imageUrl: string): string {
  const resolved = resolveImageUrl(imageUrl);
  if (!resolved) {
    throw new ApiContractError("Invalid public asset URL");
  }
  return resolved;
}

function absolutePublicImageUrl(imageUrl: string): string {
  const resolved = publicImageUrl(imageUrl);
  if (resolved.startsWith("http://") || resolved.startsWith("https://")) {
    return resolved;
  }
  if (typeof window === "undefined") {
    return resolved;
  }
  return new URL(resolved, window.location.origin).toString();
}

function publicAssetImagesForApi(
  images: LocalPetAssetSet["images"] | undefined,
): LocalPetAssetSet["images"] | undefined {
  if (!images) {
    return undefined;
  }

  return {
    baby: {
      idle: absolutePublicImageUrl(images.baby.idle).slice(0, 1000),
      happy: absolutePublicImageUrl(images.baby.happy).slice(0, 1000),
      hungry: absolutePublicImageUrl(images.baby.hungry).slice(0, 1000),
      sad: absolutePublicImageUrl(images.baby.sad).slice(0, 1000),
    },
    teen: {
      idle: absolutePublicImageUrl(images.teen.idle).slice(0, 1000),
      happy: absolutePublicImageUrl(images.teen.happy).slice(0, 1000),
      hungry: absolutePublicImageUrl(images.teen.hungry).slice(0, 1000),
      sad: absolutePublicImageUrl(images.teen.sad).slice(0, 1000),
    },
    adult: {
      idle: absolutePublicImageUrl(images.adult.idle).slice(0, 1000),
      happy: absolutePublicImageUrl(images.adult.happy).slice(0, 1000),
      hungry: absolutePublicImageUrl(images.adult.hungry).slice(0, 1000),
      sad: absolutePublicImageUrl(images.adult.sad).slice(0, 1000),
    },
  };
}

function requiredGeneratedImage(
  response: GeneratePetApiResponse,
  stage: PetStage,
  mood: PetMood,
): string {
  const imageUrl = response.images[stage]?.[mood];
  if (!imageUrl) {
    throw new ApiError(
      "Не получилось подготовить питомца. Попробуйте ещё раз.",
      "INCOMPLETE_ASSET_SET",
      undefined,
      { diagnostic: { missingAsset: `${stage}/${mood}` } },
    );
  }
  return publicImageUrl(imageUrl);
}

function completeGeneratedImages(response: GeneratePetApiResponse): GeneratePetResponse["images"] {
  for (const stage of REQUIRED_STAGES) {
    for (const mood of REQUIRED_MOODS) {
      requiredGeneratedImage(response, stage, mood);
    }
  }

  return {
    baby: {
      idle: requiredGeneratedImage(response, "baby", "idle"),
      happy: requiredGeneratedImage(response, "baby", "happy"),
      hungry: requiredGeneratedImage(response, "baby", "hungry"),
      sad: requiredGeneratedImage(response, "baby", "sad"),
    },
    teen: {
      idle: requiredGeneratedImage(response, "teen", "idle"),
      happy: requiredGeneratedImage(response, "teen", "happy"),
      hungry: requiredGeneratedImage(response, "teen", "hungry"),
      sad: requiredGeneratedImage(response, "teen", "sad"),
    },
    adult: {
      idle: requiredGeneratedImage(response, "adult", "idle"),
      happy: requiredGeneratedImage(response, "adult", "happy"),
      hungry: requiredGeneratedImage(response, "adult", "hungry"),
      sad: requiredGeneratedImage(response, "adult", "sad"),
    },
  };
}

function backgroundGenerationState(job: GeneratePetJobResponse) {
  const hasBackgroundError = Boolean(job.backgroundError);
  const status = job.status === "failed"
    ? "failed"
    : job.phase !== "completed"
      ? "running"
      : hasBackgroundError
        ? "failed"
        : "succeeded";
  const errorDetail = job.backgroundError ?? (job.status === "failed" ? job.error : undefined);
  const error = errorDetail ? apiErrorFromDetail(errorDetail).message : undefined;
  const comparisonError = job.comparisonError
    ? apiErrorFromDetail(job.comparisonError).message
    : undefined;
  const phase = job.phase === "generating_sad_image"
    || job.phase === "generating_sad_video"
    || job.phase === "generating_happy_image"
    || job.phase === "generating_happy_video"
    || job.phase === "generating_kandinsky"
    || job.phase === "completed"
    ? job.phase
    : undefined;

  return {
    generationJobId: job.jobId,
    backgroundGenerationStatus: status,
    backgroundGenerationPhase: phase,
    backgroundGenerationError: error,
    comparisonGenerationError: comparisonError,
    backgroundGenerationUpdatedAt: job.updatedAt,
  } as const;
}

function generatedPetResponseFromJob(job: GeneratePetJobResponse): GeneratePetResponse {
  const response = job.result;
  if (!response) {
    throw new ApiError(
      "Генерация завершилась без результата. Попробуйте снова.",
      "GENERATION_RESULT_MISSING",
    );
  }

  return {
    ...response,
    ...backgroundGenerationState(job),
    characterBible: response.characterBible ?? undefined,
    images: completeGeneratedImages(response),
    videoUrl: response.videoUrl ? publicImageUrl(response.videoUrl) : undefined,
    sadVideoUrl: response.sadVideoUrl ? publicImageUrl(response.sadVideoUrl) : undefined,
    happyVideoUrl: response.happyVideoUrl ? publicImageUrl(response.happyVideoUrl) : undefined,
    blinkImageUrl: response.blinkImageUrl ? publicImageUrl(response.blinkImageUrl) : undefined,
    spriteSheetUrl: response.spriteSheetUrl ? publicImageUrl(response.spriteSheetUrl) : undefined,
    kandinskyAssets: response.kandinskyAssets
      ? {
          assetSetId: response.kandinskyAssets.assetSetId,
          generatedAt: response.kandinskyAssets.generatedAt,
          images: completeGeneratedImages(response.kandinskyAssets),
          videoUrl: response.kandinskyAssets.videoUrl
            ? publicImageUrl(response.kandinskyAssets.videoUrl)
            : undefined,
        }
      : undefined,
  };
}

export async function simplifyOutfitRequest(
  outfitRequest: string,
  petDescription: string,
  options: { signal?: AbortSignal } = {},
): Promise<OutfitSimplification> {
  return request(
    "/api/outfit/simplify",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      signal: options.signal,
      body: {
        request: outfitRequest,
        petDescription,
      },
    },
    parseOutfitSimplification,
  );
}

export async function generatePetAssets(
  description: string,
  options: {
    requestKey: string;
    onJobQueued?: (jobId: string) => void;
    signal?: AbortSignal;
  },
): Promise<GeneratePetResponse> {
  const job = await request(
    "/api/generate-pet",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      idempotencyKey: options.requestKey,
      signal: options.signal,
      body: {
        description,
      },
    },
    parseGeneratePetJobResponse,
  );
  options.onJobQueued?.(job.jobId);
  return generatedPetResponseFromJob(await waitForGeneratedPet(job, options.signal));
}

export async function generateOutfitAssets(
  prompt: string,
  references: {
    idleImageUrl: string;
    sadImageUrl: string;
    happyImageUrl: string;
  },
  options: {
    requestKey: string;
    onJobQueued?: (jobId: string) => void;
    signal?: AbortSignal;
  },
): Promise<GeneratePetResponse> {
  const job = await request(
    "/api/outfit/generate",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      idempotencyKey: options.requestKey,
      signal: options.signal,
      body: {
        prompt,
        ...references,
      },
    },
    parseGeneratePetJobResponse,
  );
  options.onJobQueued?.(job.jobId);
  const generated = generatedPetResponseFromJob(
    await waitForGeneratedPet(job, options.signal, true),
  );
  if (generated.backgroundGenerationStatus === "failed") {
    throw new ApiError(
      generated.backgroundGenerationError
        ?? "Не удалось подготовить все состояния нового наряда.",
      "OUTFIT_BACKGROUND_GENERATION_FAILED",
    );
  }
  return generated;
}

export async function queueOutfitGeneration(
  prompt: string,
  references: {
    idleImageUrl: string;
    sadImageUrl: string;
    happyImageUrl: string;
  },
  options: { requestKey: string; signal?: AbortSignal },
): Promise<string> {
  const job = await request(
    "/api/outfit/generate",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      idempotencyKey: options.requestKey,
      signal: options.signal,
      body: { prompt, ...references },
    },
    parseGeneratePetJobResponse,
  );
  return job.jobId;
}

export async function resumeCompletedPetGeneration(
  jobId: string,
  options: { signal?: AbortSignal } = {},
): Promise<GeneratePetResponse> {
  const job = await request(
    `/api/generate-pet/jobs/${encodeURIComponent(jobId)}`,
    { headers: tmaAuthHeaders(), signal: options.signal },
    parseGeneratePetJobResponse,
  );
  return generatedPetResponseFromJob(await waitForGeneratedPet(job, options.signal, true));
}

export async function resumePetGeneration(
  jobId: string,
  options: { signal?: AbortSignal } = {},
): Promise<GeneratePetResponse> {
  const job = await request(
    `/api/generate-pet/jobs/${encodeURIComponent(jobId)}`,
    { headers: tmaAuthHeaders(), signal: options.signal },
    parseGeneratePetJobResponse,
  );
  return generatedPetResponseFromJob(await waitForGeneratedPet(job, options.signal));
}

export async function refreshPetBackgroundAssets(
  assetSet: LocalPetAssetSet,
  options: { signal?: AbortSignal } = {},
): Promise<LocalPetAssetSet> {
  if (!assetSet.generationJobId) {
    return assetSet;
  }
  const job = await request(
    `/api/generate-pet/jobs/${encodeURIComponent(assetSet.generationJobId)}`,
    { headers: tmaAuthHeaders(), signal: options.signal },
    parseGeneratePetJobResponse,
  );
  if (!job.result) {
    return {
      ...assetSet,
      ...backgroundGenerationState(job),
    };
  }
  const refreshed = generatedPetResponseFromJob(job);
  return {
    ...assetSet,
    ...refreshed,
    characterBible: assetSet.characterBible ?? refreshed.characterBible,
  };
}

function withPublicInteractiveTravelImages(
  response: InteractiveTravelResponse,
): InteractiveTravelResponse {
  return {
    ...response,
    travel: {
      ...response.travel,
      parts: response.travel.parts.map((part) => ({
        ...part,
        backgroundImageUrl: part.backgroundImageUrl
          ? publicImageUrl(part.backgroundImageUrl)
          : undefined,
        backgroundVideoUrl: part.backgroundVideoUrl
          ? publicImageUrl(part.backgroundVideoUrl)
          : undefined,
      })),
    },
  };
}

export function getInteractiveTravelSuggestions(
  pet: LocalPetState,
  options: { includeDebug?: boolean } = {},
): Promise<InteractiveTravelSuggestionsResponse> {
  return request(
    "/api/travel/interactive/suggestions",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      body: {
        includeDebug: options.includeDebug ?? false,
        pet: interactiveTravelPetContextForApi(pet),
      },
    },
    parseInteractiveTravelSuggestionsResponse,
  );
}

export async function startInteractiveTravel(
  destination: string,
  pet: LocalPetState,
  options: { includeDebug?: boolean } = {},
): Promise<InteractiveTravelResponse> {
  const response = await request(
    "/api/travel/interactive/start",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      body: {
        destination: destination.trim().slice(0, 500),
        includeDebug: options.includeDebug ?? false,
        pet: interactiveTravelPetContextForApi(pet),
      },
    },
    parseInteractiveTravelResponse,
  );
  return withPublicInteractiveTravelImages(response);
}

function withPublicTravelVideoPrototypeAssets(
  prototype: TravelVideoPrototype,
): TravelVideoPrototype {
  return {
    ...prototype,
    ...(prototype.imageUrl ? { imageUrl: publicImageUrl(prototype.imageUrl) } : {}),
    ...(prototype.videoUrl ? { videoUrl: publicImageUrl(prototype.videoUrl) } : {}),
  };
}

export async function startTravelVideoPrototype(
  prompt: string,
  pet: LocalPetState,
  requestKey: string,
  options: { signal?: AbortSignal } = {},
): Promise<TravelVideoPrototype> {
  const response = await request(
    "/api/travel/video-prototype",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      signal: options.signal,
      body: {
        prompt: prompt.trim().slice(0, 1000),
        requestKey,
        pet: interactiveTravelPetContextForApi(pet),
      },
    },
    parseTravelVideoPrototype,
  );
  return withPublicTravelVideoPrototypeAssets(response);
}

export async function getTravelVideoPrototype(
  jobId: string,
  options: { signal?: AbortSignal } = {},
): Promise<TravelVideoPrototype> {
  const response = await request(
    `/api/travel/video-prototype/${encodeURIComponent(jobId)}`,
    {
      method: "GET",
      headers: tmaAuthHeaders(),
      signal: options.signal,
      timeoutMs: 15_000,
    },
    parseTravelVideoPrototype,
  );
  return withPublicTravelVideoPrototypeAssets(response);
}

export async function getInteractiveTravelStatus(
  travelId: string,
): Promise<InteractiveTravelResponse> {
  const response = await request(
    `/api/travel/interactive/${encodeURIComponent(travelId)}/status`,
    {
      method: "GET",
      headers: tmaAuthHeaders(),
      timeoutMs: 15_000,
    },
    parseInteractiveTravelResponse,
  );
  return withPublicInteractiveTravelImages(response);
}

export async function continueInteractiveTravel(
  travel: InteractiveTravelState,
  advice: string,
  pet: LocalPetState,
  options: { includeDebug?: boolean } = {},
): Promise<InteractiveTravelResponse> {
  const response = await request(
    "/api/travel/interactive/continue",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      body: {
        travel,
        advice: advice.trim().slice(0, 1000),
        includeDebug: options.includeDebug ?? false,
        pet: interactiveTravelPetContextForApi(pet),
      },
    },
    parseInteractiveTravelResponse,
  );
  return withPublicInteractiveTravelImages(response);
}

export async function captureInteractiveTravelFinale(
  travel: InteractiveTravelState,
): Promise<void> {
  await request(
    "/api/travel/interactive/finale/capture",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      body: { travel },
    },
    (payload) => {
      if (!payload || typeof payload !== "object" || (payload as { saved?: unknown }).saved !== true) {
        throw new ApiContractError("Invalid interactive travel finale capture response");
      }
      return undefined;
    },
  );
}

export async function cancelInteractiveTravel(travelId: string): Promise<void> {
  await request(
    `/api/travel/interactive/${encodeURIComponent(travelId)}/cancel`,
    {
      method: "POST",
      headers: tmaAuthHeaders(),
    },
    (payload) => {
      if (
        !payload ||
        typeof payload !== "object" ||
        (payload as { cancelled?: unknown }).cancelled !== true
      ) {
        throw new ApiContractError("Invalid interactive travel cancellation response");
      }
      return undefined;
    },
  );
}

export async function illustrateInteractiveTravelPart(
  travel: InteractiveTravelState,
  part: InteractiveTravelPart,
  pet: LocalPetState,
): Promise<InteractiveTravelIllustrationResponse> {
  const response = await request(
    "/api/travel/interactive/illustrate",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      timeoutMs: MEDIA_REQUEST_TIMEOUT_MS,
      body: {
        travelId: travel.travelId,
        destination: travel.destination,
        partNumber: part.partNumber,
        title: part.title,
        storyText: part.storyText,
        pet: interactiveTravelPetContextForApi(pet),
      },
    },
    parseInteractiveTravelIllustrationResponse,
  );
  return { ...response, imageUrl: publicImageUrl(response.imageUrl) };
}

export async function animateInteractiveTravelPart(
  travel: InteractiveTravelState,
  part: InteractiveTravelPart,
): Promise<InteractiveTravelAnimationResponse> {
  const response = await request(
    "/api/travel/interactive/animate",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      timeoutMs: MEDIA_REQUEST_TIMEOUT_MS,
      body: {
        travelId: travel.travelId,
        partNumber: part.partNumber,
      },
    },
    parseInteractiveTravelAnimationResponse,
  );
  return { ...response, videoUrl: publicImageUrl(response.videoUrl) };
}

async function waitForGeneratedPet(
  initialJob: GeneratePetJobResponse,
  signal?: AbortSignal,
  requireComplete = false,
): Promise<GeneratePetJobResponse> {
  let job = initialJob;
  const deadline = Date.now() + MAX_GENERATION_POLL_MS;

  while (true) {
    if (job.result && (!requireComplete || job.phase === "completed")) {
      return job;
    }

    if (job.status === "failed") {
      const { message, code, diagnostic, activeJobId, activeDescription, travelId } =
        apiErrorFromDetail(job.error ?? {});
      throw new ApiError(message, code, undefined, {
        diagnostic,
        activeJobId,
        activeDescription,
        travelId,
        generationTerminal: true,
      });
    }

    if (Date.now() > deadline) {
      throw new ApiError(
        "Генерация заняла слишком много времени. Попробуйте позже.",
        "GENERATION_POLL_TIMEOUT",
      );
    }

    await delay(GENERATION_POLL_INTERVAL_MS, signal);
    job = await request(
      `/api/generate-pet/jobs/${encodeURIComponent(job.jobId)}`,
      {
        headers: tmaAuthHeaders(),
        signal,
      },
      parseGeneratePetJobResponse,
    );
  }
}

export async function sendLocalChatMessage(
  message: string,
  pet: LocalPetState,
  history: LocalChatMessage[],
  options: LocalChatOptions = {},
): Promise<LocalChatResponse> {
  return request("/api/chat", {
    method: "POST",
    headers: tmaAuthHeaders(),
    signal: options.signal,
    body: {
      message: message.slice(0, MAX_CHAT_INPUT_LENGTH),
      includeDebug: options.includeDebug ?? false,
      nowIso: new Date().toISOString(),
      timezone: browserTimezone(),
      memoryContext: memoryContextForApi(options.memoryContext),
      complimentHistory: (options.complimentHistory ?? [])
        .flatMap((value) => cleanApiText(value, 120) ?? [])
        .slice(-500),
      replyMaxChars: options.replyMaxChars,
      visibleContext: options.visibleContext,
      pet: petContextForApi(pet, {
        excludeMutableCharacterContext: options.excludeMutableCharacterContext,
      }),
      history: chatHistoryForApi(history),
    },
  }, parseLocalChatResponse);
}

export async function extractLocalUserMemory(
  message: string,
  reply: string,
  pet: LocalPetState,
  history: LocalChatMessage[],
  memory: LocalPetMemoryStateV1,
  options: {
    includeDebug?: boolean;
    memoryContext?: LocalPetMemoryContext;
    excludeMutableCharacterContext?: boolean;
  } = {},
): Promise<MemoryExtractionResponse> {
  return request("/api/chat/memory-extract", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      message: message.slice(0, MAX_CHAT_INPUT_LENGTH),
      reply,
      includeDebug: options.includeDebug ?? false,
      nowIso: new Date().toISOString(),
      timezone: browserTimezone(),
      existingMemoryBrief: buildExistingMemoryBrief(memory),
      memoryContext: memoryContextForApi(options.memoryContext),
      pet: petContextForApi(pet, {
        excludeMutableCharacterContext: options.excludeMutableCharacterContext,
      }),
      history: chatHistoryForApi(history),
    },
  }, parseMemoryExtractionResponse);
}

export async function consolidateLocalUserMemory(
  memory: LocalPetMemoryStateV1,
  options: { includeDebug?: boolean } = {},
): Promise<MemoryConsolidationResponse> {
  return request("/api/chat/memory-consolidate", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      pendingLearnings: memory.learnings.filter((item) => item.status === "pending"),
      existingMemories: memory.memories,
      summary: memory.summary,
      userProfile: memory.userProfile,
      nowIso: new Date().toISOString(),
      timezone: browserTimezone(),
      includeDebug: options.includeDebug ?? false,
    },
  }, parseMemoryConsolidationResponse);
}

export async function generateLocalProactiveMessage(
  pet: LocalPetState,
  memoryContext: LocalPetMemoryContext,
  options: { includeDebug?: boolean } = {},
): Promise<LocalChatResponse> {
  return request("/api/chat/proactive", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      includeDebug: options.includeDebug ?? false,
      nowIso: new Date().toISOString(),
      timezone: browserTimezone(),
      memoryContext: memoryContextForApi(memoryContext),
      pet: petContextForApi(pet),
    },
  }, parseLocalChatResponse);
}

export async function registerPetPushSnapshot(
  pet: LocalPetState,
  memoryContext?: LocalPetMemoryContext,
  options: {
    history?: LocalChatMessage[];
    recentAmbientReplies?: string[];
  } = {},
): Promise<PushSnapshotResponse> {
  const snapshotOrder = nextPushSnapshotOrder();
  const snapshotPayload = compactPushSnapshotPayload({
    petId: pet.petId,
    snapshotWriterId: snapshotOrder.writerId,
    snapshotRevision: snapshotOrder.revision,
    createdAt: pet.createdAt,
    updatedAt: pet.updatedAt,
    lastStatsTickAt: pet.lastStatsTickAt,
    lastStatTickAt: pet.lastStatTickAt,
    zeroStatSinceAt: pet.zeroStatSinceAt,
    diedAt: pet.diedAt,
    timezone: browserTimezone(),
    memoryContext: memoryContextForApi(memoryContext),
    history: chatHistoryForApi(options.history ?? []),
    recentAmbientReplies: (options.recentAmbientReplies ?? [])
      .flatMap((reply) => cleanApiText(reply, 1000) ?? [])
      .slice(-30),
    pet: {
      ...petContextForApi(pet),
      assetImages: publicAssetImagesForApi(pet.assetSet?.images),
    },
  });
  return request(
    "/api/push/snapshot",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      body: snapshotPayload,
    },
    parsePushSnapshotResponse,
  );
}

export async function unregisterPetPushSnapshot(
  petId: string,
  options: { signal?: AbortSignal; keepalive?: boolean } = {},
): Promise<DeletePushSnapshotResponse> {
  const normalizedPetId = petId.trim();
  if (!normalizedPetId || normalizedPetId.length > 120) {
    throw new ApiContractError("push snapshot pet id must contain 1–120 characters");
  }
  return request(
    `/api/push/snapshot/${encodeURIComponent(normalizedPetId)}`,
    {
      method: "DELETE",
      headers: tmaAuthHeaders(),
      signal: options.signal,
      keepalive: options.keepalive ?? true,
      timeoutMs: 15_000,
    },
    parseDeletePushSnapshotResponse,
  );
}

export async function generateLocalAmbientMessage(
  pet: LocalPetState,
  options: {
    includeDebug?: boolean;
    memoryContext?: LocalPetMemoryContext;
    history?: LocalChatMessage[];
    recentAmbientReplies?: string[];
    replyMaxChars?: number;
  } = {},
): Promise<LocalChatResponse> {
  return request("/api/chat/ambient", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      includeDebug: options.includeDebug ?? false,
      memoryContext: memoryContextForApi(options.memoryContext),
      replyMaxChars: options.replyMaxChars,
      nowIso: new Date().toISOString(),
      timezone: browserTimezone(),
      recentAmbientReplies: (options.recentAmbientReplies ?? []).slice(-30),
      pet: petContextForApi(pet),
      history: chatHistoryForApi(options.history ?? []),
    },
  }, parseLocalChatResponse);
}

export async function extractLocalLiteFacts(
  message: string,
  reply: string,
  pet: LocalPetState,
  history: LocalChatMessage[],
  options: { includeDebug?: boolean } = {},
): Promise<LiteFactExtractionResponse> {
  return request("/api/chat/lite-facts", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      message: message.slice(0, MAX_CHAT_INPUT_LENGTH),
      reply,
      includeDebug: options.includeDebug ?? false,
      pet: petContextForApi(pet),
      history: chatHistoryForApi(history),
    },
  }, parseLiteFactExtractionResponse);
}

export function resolveImageUrl(imageUrl: string | null): string | null {
  return resolveApiAssetUrl(imageUrl);
}
