import type {
  GeneratePetResponse,
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
import { getTelegramInitData } from "./telegram";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const ENABLE_TMA_DEV_FALLBACK = process.env.NEXT_PUBLIC_ENABLE_TMA_DEV_FALLBACK === "true";
const MAX_CHAT_INPUT_LENGTH = 1000;

type GeneratePetApiResponse = Omit<GeneratePetResponse, "characterBible"> & {
  characterBible?: Record<string, unknown> | null;
};

type GeneratePetJobResponse = {
  jobId: string;
  status: "queued" | "running" | "succeeded" | "failed";
  createdAt: string;
  updatedAt: string;
  result?: GeneratePetApiResponse | null;
  error?: ApiErrorDetail | null;
};

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
};

type LocalChatOptions = {
  includeDebug?: boolean;
  memoryContext?: LocalPetMemoryContext;
  replyMaxChars?: number;
};

const REQUIRED_STAGES = ["baby", "teen", "adult"] as const satisfies readonly PetStage[];
const REQUIRED_MOODS = ["idle", "happy", "hungry", "sad"] as const satisfies readonly PetMood[];
const MAX_ERROR_BODY_CHARS = 900;
const GENERATION_POLL_INTERVAL_MS = 2000;
const MAX_GENERATION_POLL_MS = 10 * 60 * 1000;

type ApiErrorDetail = {
  error?: unknown;
  message?: unknown;
  code?: unknown;
  providerStatus?: unknown;
  providerMessage?: unknown;
  requestId?: unknown;
  retryAfterSeconds?: unknown;
};

export class ApiError extends Error {
  code?: string;
  status?: number;

  constructor(message: string, code?: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

function compactText(value: string, limit = MAX_ERROR_BODY_CHARS): string {
  return value.replace(/\s+/g, " ").trim().slice(0, limit);
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function numericValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function formatRetryAfter(seconds: number | undefined): string | undefined {
  if (seconds === undefined || seconds <= 0) {
    return undefined;
  }

  const minutes = Math.max(1, Math.ceil(seconds / 60));
  if (minutes < 60) {
    return `${minutes} мин`;
  }

  const hours = Math.ceil(minutes / 60);
  if (hours < 24) {
    return `${hours} ч`;
  }

  return `${Math.ceil(hours / 24)} дн`;
}

function rateLimitMessage(detail: ApiErrorDetail, fallbackMessage?: string): string {
  const base = fallbackMessage ?? "Слишком много запросов.";
  const retryAfter = formatRetryAfter(numericValue(detail.retryAfterSeconds));
  return retryAfter ? `${base} Попробуйте через ${retryAfter}.` : base;
}

function errorDetail(payload: unknown): ApiErrorDetail {
  if (!payload || typeof payload !== "object") {
    return {};
  }
  const record = payload as Record<string, unknown>;
  const detail = record.detail;
  if (detail && typeof detail === "object") {
    return detail as ApiErrorDetail;
  }
  return record as ApiErrorDetail;
}

function errorMessageFromResponse(
  path: string,
  response: Response,
  payload: unknown,
  rawBody: string,
): { message: string; code?: string } {
  const detail = errorDetail(payload);
  const statusLine = `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ""}`;
  const code = stringValue(detail.code);
  const error = stringValue(detail.error);
  const message = stringValue(detail.message) ?? stringValue((payload as { message?: unknown })?.message);
  const providerStatus = numericValue(detail.providerStatus);
  const providerMessage = stringValue(detail.providerMessage);
  const requestId = stringValue(detail.requestId);

  if (code === "rate_limited") {
    return { message: rateLimitMessage(detail, message), code };
  }

  const lines = [message ?? statusLine, `Endpoint: ${path}`, `Status: ${statusLine}`];
  if (code) {
    lines.push(`Code: ${code}`);
  }
  if (error) {
    lines.push(`Error: ${error}`);
  }
  if (providerStatus !== undefined) {
    lines.push(`Provider status: ${providerStatus}`);
  }
  if (providerMessage) {
    lines.push(`Provider message: ${providerMessage}`);
  }
  if (requestId) {
    lines.push(`Request ID: ${requestId}`);
  }
  if (!payload && rawBody.trim()) {
    lines.push(`Response: ${compactText(rawBody)}`);
  }
  return { message: lines.join("\n"), code };
}

function errorMessageFromDetail(
  path: string,
  statusLine: string,
  detail: ApiErrorDetail,
): { message: string; code?: string } {
  const code = stringValue(detail.code);
  const error = stringValue(detail.error);
  const message = stringValue(detail.message);
  const providerStatus = numericValue(detail.providerStatus);
  const providerMessage = stringValue(detail.providerMessage);
  const requestId = stringValue(detail.requestId);

  if (code === "rate_limited") {
    return { message: rateLimitMessage(detail, message), code };
  }

  const lines = [message ?? statusLine, `Endpoint: ${path}`, `Status: ${statusLine}`];
  if (code) {
    lines.push(`Code: ${code}`);
  }
  if (error) {
    lines.push(`Error: ${error}`);
  }
  if (providerStatus !== undefined) {
    lines.push(`Provider status: ${providerStatus}`);
  }
  if (providerMessage) {
    lines.push(`Provider message: ${providerMessage}`);
  }
  if (requestId) {
    lines.push(`Request ID: ${requestId}`);
  }
  return { message: lines.join("\n"), code };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = `${API_URL}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (caught) {
    const message = caught instanceof Error ? caught.message : String(caught);
    throw new ApiError(`Network error\nEndpoint: ${path}\nURL: ${url}\nMessage: ${message}`, "NETWORK_ERROR");
  }

  if (!response.ok) {
    let rawBody = "";
    let payload: unknown;
    try {
      rawBody = await response.text();
      payload = rawBody ? JSON.parse(rawBody) : undefined;
    } catch {
      payload = undefined;
    }
    const { message, code } = errorMessageFromResponse(path, response, payload, rawBody);
    throw new ApiError(message, code, response.status);
  }

  return response.json() as Promise<T>;
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

function requiredGeneratedImage(
  response: GeneratePetApiResponse,
  stage: PetStage,
  mood: PetMood,
): string {
  const imageUrl = response.images[stage]?.[mood];
  if (!imageUrl) {
    throw new ApiError(
      `Сгенерировался неполный набор спрайтов: нет ${stage}/${mood}. Попробуйте еще раз.`,
      "INCOMPLETE_ASSET_SET",
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

export async function generatePetAssets(description: string): Promise<GeneratePetResponse> {
  const job = await request<GeneratePetJobResponse>("/api/generate-pet", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      description,
      style: "cute mobile game pet",
      stages: ["baby", "teen", "adult"],
      moods: ["idle", "happy", "hungry", "sad"],
    },
  });
  const response = await waitForGeneratedPet(job);

  return {
    ...response,
    characterBible: response.characterBible ?? undefined,
    images: completeGeneratedImages(response),
    spriteSheetUrl: response.spriteSheetUrl ? publicImageUrl(response.spriteSheetUrl) : undefined,
  };
}

async function waitForGeneratedPet(initialJob: GeneratePetJobResponse): Promise<GeneratePetApiResponse> {
  let job = initialJob;
  const deadline = Date.now() + MAX_GENERATION_POLL_MS;

  while (true) {
    if (job.status === "succeeded") {
      if (!job.result) {
        throw new ApiError(
          `Генерация завершилась без результата.\nEndpoint: /api/generate-pet/jobs/${job.jobId}\nStatus: succeeded`,
          "GENERATION_RESULT_MISSING",
        );
      }
      return job.result;
    }

    if (job.status === "failed") {
      const { message, code } = errorMessageFromDetail(
        `/api/generate-pet/jobs/${job.jobId}`,
        "generation failed",
        job.error ?? {},
      );
      throw new ApiError(message, code);
    }

    if (Date.now() > deadline) {
      throw new ApiError(
        `Генерация заняла слишком много времени.\nEndpoint: /api/generate-pet/jobs/${job.jobId}\nStatus: ${job.status}`,
        "GENERATION_POLL_TIMEOUT",
      );
    }

    await delay(GENERATION_POLL_INTERVAL_MS);
    job = await request<GeneratePetJobResponse>(
      `/api/generate-pet/jobs/${encodeURIComponent(job.jobId)}`,
      {
        headers: tmaAuthHeaders(),
      },
    );
  }
}

export async function sendLocalChatMessage(
  message: string,
  pet: LocalPetState,
  history: LocalChatMessage[],
  options: LocalChatOptions = {},
): Promise<LocalChatResponse> {
  return request<LocalChatResponse>("/api/chat", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      message: message.slice(0, MAX_CHAT_INPUT_LENGTH),
      includeDebug: options.includeDebug ?? false,
      memoryContext: options.memoryContext,
      replyMaxChars: options.replyMaxChars,
      pet: {
        name: pet.name,
        description: pet.description,
        characterBible: pet.assetSet?.characterBible,
        stage: pet.stage,
        mood: pet.mood,
        stats: pet.stats,
      },
      history: history.slice(-12).map((item) => ({
        role: item.role,
        text: item.text,
      })),
    },
  });
}

export async function extractLocalUserMemory(
  message: string,
  reply: string,
  pet: LocalPetState,
  history: LocalChatMessage[],
  memory: LocalPetMemoryStateV1,
  options: { includeDebug?: boolean; memoryContext?: LocalPetMemoryContext } = {},
): Promise<MemoryExtractionResponse> {
  return request<MemoryExtractionResponse>("/api/chat/memory-extract", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      message: message.slice(0, MAX_CHAT_INPUT_LENGTH),
      reply,
      includeDebug: options.includeDebug ?? false,
      nowIso: new Date().toISOString(),
      timezone: browserTimezone(),
      existingMemoryBrief: buildExistingMemoryBrief(memory),
      memoryContext: options.memoryContext,
      pet: {
        name: pet.name,
        description: pet.description,
        characterBible: pet.assetSet?.characterBible,
        stage: pet.stage,
        mood: pet.mood,
        stats: pet.stats,
      },
      history: history.slice(-12).map((item) => ({
        role: item.role,
        text: item.text,
      })),
    },
  });
}

export async function consolidateLocalUserMemory(
  memory: LocalPetMemoryStateV1,
  options: { includeDebug?: boolean } = {},
): Promise<MemoryConsolidationResponse> {
  return request<MemoryConsolidationResponse>("/api/chat/memory-consolidate", {
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
  });
}

export async function generateLocalProactiveMessage(
  pet: LocalPetState,
  memoryContext: LocalPetMemoryContext,
  options: { includeDebug?: boolean } = {},
): Promise<LocalChatResponse> {
  return request<LocalChatResponse>("/api/chat/proactive", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      includeDebug: options.includeDebug ?? false,
      nowIso: new Date().toISOString(),
      timezone: browserTimezone(),
      memoryContext,
      pet: {
        name: pet.name,
        description: pet.description,
        characterBible: pet.assetSet?.characterBible,
        stage: pet.stage,
        mood: pet.mood,
        stats: pet.stats,
      },
    },
  });
}

export async function extractLocalLiteFacts(
  message: string,
  reply: string,
  pet: LocalPetState,
  history: LocalChatMessage[],
  options: { includeDebug?: boolean } = {},
): Promise<LiteFactExtractionResponse> {
  return request<LiteFactExtractionResponse>("/api/chat/lite-facts", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      message: message.slice(0, MAX_CHAT_INPUT_LENGTH),
      reply,
      includeDebug: options.includeDebug ?? false,
      pet: {
        name: pet.name,
        description: pet.description,
        characterBible: pet.assetSet?.characterBible,
        stage: pet.stage,
        mood: pet.mood,
        stats: pet.stats,
      },
      history: history.slice(-12).map((item) => ({
        role: item.role,
        text: item.text,
      })),
    },
  });
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
