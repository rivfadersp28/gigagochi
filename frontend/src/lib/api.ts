import type {
  AnonymousUser,
  ChatResponse,
  ChatVisualContext,
  CreatePetResponse,
  FeedResponse,
  GeneratePetResponse,
  LocalChatMessage,
  LocalChatResponse,
  LocalPetStateV1,
  MessagesResponse,
  Pet,
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
  if (ENABLE_TMA_DEV_FALLBACK) {
    return { "X-Telegram-Init-Data": "dev" };
  }
  return {};
}

export function canUseTmaApi() {
  return Boolean(getTelegramInitData()) || ENABLE_TMA_DEV_FALLBACK;
}

function publicImageUrl(imageUrl: string): string {
  return resolveImageUrl(imageUrl) ?? imageUrl;
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

export async function generatePetAssets(description: string): Promise<GeneratePetResponse> {
  const response = await request<GeneratePetApiResponse>("/api/generate-pet", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      description,
      style: "cute mobile game pet",
      stages: ["baby", "teen", "adult"],
      moods: ["idle", "happy", "hungry", "sad"],
    },
  });

  return {
    ...response,
    characterBible: response.characterBible ?? undefined,
    images: {
      baby: {
        idle: publicImageUrl(response.images.baby.idle),
        happy: publicImageUrl(response.images.baby.happy),
        hungry: publicImageUrl(response.images.baby.hungry),
        sad: publicImageUrl(response.images.baby.sad),
      },
      teen: {
        idle: publicImageUrl(response.images.teen.idle),
        happy: publicImageUrl(response.images.teen.happy),
        hungry: publicImageUrl(response.images.teen.hungry),
        sad: publicImageUrl(response.images.teen.sad),
      },
      adult: {
        idle: publicImageUrl(response.images.adult.idle),
        happy: publicImageUrl(response.images.adult.happy),
        hungry: publicImageUrl(response.images.adult.hungry),
        sad: publicImageUrl(response.images.adult.sad),
      },
    },
    spriteSheetUrl: response.spriteSheetUrl ? publicImageUrl(response.spriteSheetUrl) : undefined,
  };
}

export async function sendLocalChatMessage(
  message: string,
  pet: LocalPetStateV1,
  history: LocalChatMessage[],
): Promise<LocalChatResponse> {
  return request<LocalChatResponse>("/api/chat", {
    method: "POST",
    headers: tmaAuthHeaders(),
    body: {
      message: message.slice(0, MAX_CHAT_INPUT_LENGTH),
      pet: {
        name: pet.name,
        description: pet.description,
        characterBible: pet.assetSet?.characterBible,
        stage: pet.stage,
        mood: pet.mood,
        stats: pet.stats,
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
