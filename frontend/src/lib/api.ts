import type {
  GeneratePetResponse,
  GenerateTravelResponse,
  LocalPetAssetSet,
  LiteFactExtractionResponse,
  LocalChatMessage,
  LocalChatResponse,
  LocalPetState,
  PetMood,
  PetStage,
} from "./types";
import type {
  LocalPetMemoryContext,
  LocalPetMemoryStateV1,
  MemoryConsolidationResponse,
  MemoryExtractionResponse,
} from "./localPetMemoryTypes";
import { buildExistingMemoryBrief } from "./localPetMemoryStorage";
import { characterNameFromAssetSet } from "./localPetCharacter";
import { getTelegramInitData } from "./telegram";
import {
  parseGeneratePetJobResponse,
  parseGenerateTravelResponse,
  parseLiteFactExtractionResponse,
  parseLocalChatResponse,
  parseMemoryConsolidationResponse,
  parseMemoryExtractionResponse,
  parsePushSnapshotResponse,
  type GeneratePetApiResponse,
  type GeneratePetJobResponse,
  type PushSnapshotResponse,
} from "./apiContracts";
import {
  API_URL,
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

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
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

export function characterBibleForApi(pet: LocalPetState): Record<string, unknown> | undefined {
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
  return result;
}

function petContextForApi(pet: LocalPetState) {
  return {
    name: petNameForApi(pet),
    description: cleanApiText(pet.description, 300) ?? "Персонаж",
    characterBible: characterBibleForApi(pet),
    stage: pet.stage,
    mood: pet.mood,
    stats: petStatsForApi(pet.stats),
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
  return resolveImageUrl(imageUrl) ?? imageUrl;
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
      idle: absolutePublicImageUrl(images.baby.idle),
      happy: absolutePublicImageUrl(images.baby.happy),
      hungry: absolutePublicImageUrl(images.baby.hungry),
      sad: absolutePublicImageUrl(images.baby.sad),
    },
    teen: {
      idle: absolutePublicImageUrl(images.teen.idle),
      happy: absolutePublicImageUrl(images.teen.happy),
      hungry: absolutePublicImageUrl(images.teen.hungry),
      sad: absolutePublicImageUrl(images.teen.sad),
    },
    adult: {
      idle: absolutePublicImageUrl(images.adult.idle),
      happy: absolutePublicImageUrl(images.adult.happy),
      hungry: absolutePublicImageUrl(images.adult.hungry),
      sad: absolutePublicImageUrl(images.adult.sad),
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
  const phase = job.phase === "generating_sad_image"
    || job.phase === "generating_sad_video"
    || job.phase === "generating_happy_image"
    || job.phase === "generating_happy_video"
    || job.phase === "completed"
    ? job.phase
    : undefined;

  return {
    generationJobId: job.jobId,
    backgroundGenerationStatus: status,
    backgroundGenerationPhase: phase,
    backgroundGenerationError: error,
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
    tapReactionImageUrl: response.tapReactionImageUrl
      ? publicImageUrl(response.tapReactionImageUrl)
      : undefined,
    blinkImageUrl: response.blinkImageUrl ? publicImageUrl(response.blinkImageUrl) : undefined,
    spriteSheetUrl: response.spriteSheetUrl ? publicImageUrl(response.spriteSheetUrl) : undefined,
  };
}

export async function generatePetAssets(
  description: string,
  options: { onJobQueued?: (jobId: string) => void } = {},
): Promise<GeneratePetResponse> {
  const job = await request(
    "/api/generate-pet",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      body: {
        description,
        style: "cute mobile game pet",
        stages: ["baby", "teen", "adult"],
        moods: ["idle", "happy", "hungry", "sad"],
      },
    },
    parseGeneratePetJobResponse,
  );
  options.onJobQueued?.(job.jobId);
  return generatedPetResponseFromJob(await waitForGeneratedPet(job));
}

export async function resumePetGeneration(jobId: string): Promise<GeneratePetResponse> {
  const job = await request(
    `/api/generate-pet/jobs/${encodeURIComponent(jobId)}`,
    { headers: tmaAuthHeaders() },
    parseGeneratePetJobResponse,
  );
  return generatedPetResponseFromJob(await waitForGeneratedPet(job));
}

export async function refreshPetBackgroundAssets(
  assetSet: LocalPetAssetSet,
): Promise<LocalPetAssetSet> {
  if (!assetSet.generationJobId) {
    return assetSet;
  }
  const job = await request(
    `/api/generate-pet/jobs/${encodeURIComponent(assetSet.generationJobId)}`,
    { headers: tmaAuthHeaders() },
    parseGeneratePetJobResponse,
  );
  if (!job.result) {
    return {
      ...assetSet,
      ...backgroundGenerationState(job),
    };
  }
  return {
    ...assetSet,
    ...generatedPetResponseFromJob(job),
  };
}

export async function generatePetTravel(
  pet: LocalPetState,
  options: { includeDebug?: boolean } = {},
): Promise<GenerateTravelResponse> {
  const response = await request(
    "/api/travel",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      body: {
        includeDebug: options.includeDebug ?? false,
        pet: {
          ...petContextForApi(pet),
          assetImages: publicAssetImagesForApi(pet.assetSet?.images),
        },
      },
    },
    parseGenerateTravelResponse,
  );

  return {
    ...response,
    images: response.images.map((image) => ({
      ...image,
      imageUrl: publicImageUrl(image.imageUrl),
    })),
  };
}

async function waitForGeneratedPet(
  initialJob: GeneratePetJobResponse,
): Promise<GeneratePetJobResponse> {
  let job = initialJob;
  const deadline = Date.now() + MAX_GENERATION_POLL_MS;

  while (true) {
    if (job.result) {
      return job;
    }

    if (job.status === "failed") {
      const { message, code, diagnostic } = apiErrorFromDetail(job.error ?? {});
      throw new ApiError(message, code, undefined, { diagnostic });
    }

    if (Date.now() > deadline) {
      throw new ApiError(
        "Генерация заняла слишком много времени. Попробуйте позже.",
        "GENERATION_POLL_TIMEOUT",
      );
    }

    await delay(GENERATION_POLL_INTERVAL_MS);
    job = await request(
      `/api/generate-pet/jobs/${encodeURIComponent(job.jobId)}`,
      {
        headers: tmaAuthHeaders(),
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
      pet: petContextForApi(pet),
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
  options: { includeDebug?: boolean; memoryContext?: LocalPetMemoryContext } = {},
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
      pet: petContextForApi(pet),
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
  return request(
    "/api/push/snapshot",
    {
      method: "POST",
      headers: tmaAuthHeaders(),
      body: {
        petId: pet.petId,
        createdAt: pet.createdAt,
        updatedAt: pet.updatedAt,
        lastStatsTickAt: pet.lastStatsTickAt,
        lastStatTickAt: pet.lastStatTickAt,
        zeroStatSinceAt: pet.zeroStatSinceAt,
        diedAt: pet.diedAt,
        timezone: browserTimezone(),
        memoryContext: memoryContextForApi(memoryContext),
        history: chatHistoryForApi(options.history ?? []),
        recentAmbientReplies: (options.recentAmbientReplies ?? []).slice(-10),
        pet: {
          ...petContextForApi(pet),
          assetImages: publicAssetImagesForApi(pet.assetSet?.images),
        },
      },
    },
    parsePushSnapshotResponse,
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
      recentAmbientReplies: (options.recentAmbientReplies ?? []).slice(-10),
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
  if (!imageUrl) {
    return null;
  }
  if (imageUrl.startsWith("http://") || imageUrl.startsWith("https://")) {
    return imageUrl;
  }
  return `${API_URL}${imageUrl}`;
}
