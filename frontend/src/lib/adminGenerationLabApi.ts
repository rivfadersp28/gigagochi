import type {
  AdminGenerateError,
  AdminGenerateOneRequest,
  AdminGenerateOneResponse,
  AdminGenerationLabStatus,
  PetMood,
} from "./types";

export const ADMIN_API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

const ADMIN_TOKEN = process.env.NEXT_PUBLIC_ADMIN_GENERATION_LAB_TOKEN;
const PET_MOODS: PetMood[] = ["idle", "happy", "hungry", "sad"];

type AdminRequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
};

export class AdminGenerationLabApiError extends Error {
  code?: string;
  payload?: AdminGenerateError;

  constructor(message: string, code?: string, payload?: AdminGenerateError) {
    super(message);
    this.name = "AdminGenerationLabApiError";
    this.code = code;
    this.payload = payload;
  }
}

function adminHeaders(extraHeaders: HeadersInit | undefined): HeadersInit {
  return {
    "Content-Type": "application/json",
    ...(ADMIN_TOKEN ? { "X-Admin-Token": ADMIN_TOKEN } : {}),
    ...extraHeaders,
  };
}

async function adminRequest<T>(path: string, options: AdminRequestOptions = {}): Promise<T> {
  const response = await fetch(`${ADMIN_API_URL}${path}`, {
    ...options,
    headers: adminHeaders(options.headers),
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (!response.ok) {
    let message = response.statusText || "Admin generation lab request failed";
    let code: string | undefined;
    let errorPayload: AdminGenerateError | undefined;
    try {
      const payload = await response.json();
      const detail = payload?.detail ?? payload;
      message = detail?.message ?? payload?.message ?? message;
      code = detail?.code ?? payload?.code;
      if (detail?.status === "failed") {
        errorPayload = detail as AdminGenerateError;
      }
    } catch {
      // Keep the HTTP status text when the backend did not return JSON.
    }
    throw new AdminGenerationLabApiError(message, code, errorPayload);
  }

  return response.json() as Promise<T>;
}

function resolveAdminImageUrl(imageUrl: string | null | undefined): string | null | undefined {
  if (!imageUrl) {
    return imageUrl;
  }
  if (imageUrl.startsWith("http://") || imageUrl.startsWith("https://")) {
    return imageUrl;
  }
  return `${ADMIN_API_URL}${imageUrl}`;
}

function normalizeAdminResponse(response: AdminGenerateOneResponse): AdminGenerateOneResponse {
  if (!response.images) {
    return {
      ...response,
      spriteSheetUrl: resolveAdminImageUrl(response.spriteSheetUrl) ?? response.spriteSheetUrl,
    };
  }

  const images = {
    baby: { ...response.images.baby },
    teen: { ...response.images.teen },
    adult: { ...response.images.adult },
  };

  for (const stage of ["baby", "teen", "adult"] as const) {
    for (const mood of PET_MOODS) {
      images[stage][mood] = resolveAdminImageUrl(images[stage][mood]) ?? images[stage][mood];
    }
  }

  return {
    ...response,
    images,
    spriteSheetUrl: resolveAdminImageUrl(response.spriteSheetUrl) ?? response.spriteSheetUrl,
  };
}

export async function getAdminGenerationLabStatus(
  signal?: AbortSignal,
): Promise<AdminGenerationLabStatus> {
  return adminRequest<AdminGenerationLabStatus>("/admin/generation-lab/status", { signal });
}

export async function generateAdminCharacter(
  payload: AdminGenerateOneRequest,
  signal?: AbortSignal,
): Promise<AdminGenerateOneResponse> {
  const response = await adminRequest<AdminGenerateOneResponse>("/admin/generation-lab/generate-one", {
    method: "POST",
    signal,
    body: payload,
  });

  return normalizeAdminResponse(response);
}
