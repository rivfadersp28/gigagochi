import type {
  AnonymousUser,
  ChatResponse,
  ChatVisualContext,
  CreatePetResponse,
  FeedResponse,
  GeneratePetResponse,
  LiteFactExtractionResponse,
  LocalChatMessage,
  LocalChatResponse,
  LocalPetState,
  MessagesResponse,
  Pet,
  PetMood,
  PetStage,
  PromptLayers,
  ReplyMode,
} from "./types";
import { getTelegramInitData } from "./telegram";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const ENABLE_TMA_DEV_FALLBACK = process.env.NEXT_PUBLIC_ENABLE_TMA_DEV_FALLBACK === "true";
const MAX_CHAT_INPUT_LENGTH = 1000;

type GeneratePetApiResponse = Omit<GeneratePetResponse, "characterBible"> & {
  characterBible?: Record<string, unknown> | null;
};

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
};

type LocalChatOptions = {
  promptLayers?: PromptLayers;
  includeDebug?: boolean;
  replyMode?: ReplyMode;
};

type GeneratePetOptions = {
  useTemplatePresets?: boolean;
};

const REQUIRED_STAGES = ["baby", "teen", "adult"] as const satisfies readonly PetStage[];
const REQUIRED_MOODS = ["idle", "happy", "hungry", "sad"] as const satisfies readonly PetMood[];

export class ApiError extends Error {
  code?: string;

  constructor(message: string, code?: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (!response.ok) {
    let message = "Request failed";
    let code: string | undefined;
    try {
      const payload = await response.json();
      message = payload?.detail?.message ?? payload?.message ?? message;
      code = payload?.detail?.code ?? payload?.code;
    } catch {
      message = response.statusText || message;
    }
    throw new ApiError(message, code);
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

export async function createAnonymousUser(): Promise<AnonymousUser> {
  return request<AnonymousUser>("/users/anonymous", { method: "POST" });
}

export async function createPet(userId: string, description: string): Promise<CreatePetResponse> {
  return request<CreatePetResponse>("/pets", {
    method: "POST",
    body: { user_id: userId, description },
  });
}

export async function getPet(petId: string): Promise<Pet> {
  return request<Pet>(`/pets/${petId}`);
}

export async function feedPet(petId: string): Promise<FeedResponse> {
  return request<FeedResponse>(`/pets/${petId}/feed`, { method: "POST" });
}

export async function getMessages(petId: string): Promise<MessagesResponse> {
  return request<MessagesResponse>(`/pets/${petId}/messages`);
}

export async function sendChatMessage(
  petId: string,
  message: string,
  context: ChatVisualContext = {},
): Promise<ChatResponse> {
  return request<ChatResponse>(`/pets/${petId}/chat`, {
    method: "POST",
    body: { message, ...context },
  });
}

export async function generatePetAssets(
  description: string,
  options: GeneratePetOptions = {},
): Promise<GeneratePetResponse> {
  const response = await request<GeneratePetApiResponse>("/api/generate-pet", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      description,
      style: "cute mobile game pet",
      stages: ["baby", "teen", "adult"],
      moods: ["idle", "happy", "hungry", "sad"],
      useTemplatePresets: options.useTemplatePresets ?? false,
    },
  });

  return {
    ...response,
    characterBible: response.characterBible ?? undefined,
    images: completeGeneratedImages(response),
    spriteSheetUrl: response.spriteSheetUrl ? publicImageUrl(response.spriteSheetUrl) : undefined,
  };
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
      promptLayers: options.promptLayers,
      replyMode: options.replyMode ?? "full",
      pet: {
        name: pet.name,
        description: pet.description,
        characterBible: pet.assetSet?.characterBible,
        stage: pet.stage,
        mood: pet.mood,
        stats: pet.stats,
        memory: pet.memory,
        loreMemories: pet.loreMemories ?? [],
      },
      history: history.slice(-12).map((item) => ({
        role: item.role,
        text: item.text,
      })),
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
        memory: pet.memory,
        loreMemories: pet.loreMemories ?? [],
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
