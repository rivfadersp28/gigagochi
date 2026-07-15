import { API_URL, ApiContractError, request } from "./apiTransport";
import { parseInteractiveTravelResponse } from "./apiContracts";
import type { InteractiveTravelState } from "./types";

const MEDIA_REQUEST_TIMEOUT_MS = 20 * 60 * 1000;

export type TravelFinaleSummary = {
  travelId: string;
  title: string;
  destination: string;
  savedAt?: string;
  owner: { telegramId?: number; username?: string; firstName?: string };
  partCount: number;
  videoCount: number;
};

export type TravelFinaleAttempt = {
  id: string;
  createdAt: string;
  prompt: string;
  model: string;
  durationSeconds: number;
  referenceUrls: string[];
  videoUrl: string;
};

export type TravelFinaleDetail = {
  travel: InteractiveTravelState;
  story: string;
  defaultDirection: string;
  attempts: TravelFinaleAttempt[];
};

function record(value: unknown, path: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiContractError(`Invalid ${path}`);
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, path: string): string {
  if (typeof value !== "string" || !value.trim() || value.length > 100_000) {
    throw new ApiContractError(`Invalid ${path}`);
  }
  return value;
}

function finiteNumber(value: unknown, path: string, options: { integer?: boolean } = {}) {
  if (
    typeof value !== "number"
    || !Number.isFinite(value)
    || value < 0
    || options.integer && !Number.isSafeInteger(value)
  ) {
    throw new ApiContractError(`Invalid ${path}`);
  }
  return value;
}

function mediaUrl(value: unknown, path: string) {
  const url = text(value, path).trim();
  if (
    url.length > 2_000
    || !(/^https?:\/\//iu.test(url) || url.startsWith("/static/generated/"))
  ) {
    throw new ApiContractError(`Invalid ${path}`);
  }
  return url;
}

function attempt(value: unknown): TravelFinaleAttempt {
  const item = record(value, "travelFinaleAttempt");
  return {
    id: text(item.id, "travelFinaleAttempt.id"),
    createdAt: text(item.createdAt, "travelFinaleAttempt.createdAt"),
    prompt: text(item.prompt, "travelFinaleAttempt.prompt"),
    model: text(item.model, "travelFinaleAttempt.model"),
    durationSeconds: finiteNumber(item.durationSeconds, "travelFinaleAttempt.durationSeconds"),
    referenceUrls: Array.isArray(item.referenceUrls)
      ? item.referenceUrls.map((url, index) => mediaUrl(
          url,
          `travelFinaleAttempt.referenceUrls.${index}`,
        ))
      : (() => {
          throw new ApiContractError("Invalid travelFinaleAttempt.referenceUrls");
        })(),
    videoUrl: mediaUrl(item.videoUrl, "travelFinaleAttempt.videoUrl"),
  };
}

export function finaleAssetUrl(value: string): string {
  return /^https?:\/\//.test(value) ? value : `${API_URL}${value}`;
}

export function fetchTravelFinales(): Promise<TravelFinaleSummary[]> {
  return request("/api/admin/travel-finales", {}, (payload) => {
    const root = record(payload, "travelFinales");
    if (!Array.isArray(root.items)) {
      throw new ApiContractError("Invalid travelFinales.items");
    }
    return root.items.map((value) => {
      const item = record(value, "travelFinale");
      const owner = record(item.owner ?? {}, "travelFinale.owner");
      return {
        travelId: text(item.travelId, "travelFinale.travelId"),
        title: text(item.title, "travelFinale.title"),
        destination: text(item.destination, "travelFinale.destination"),
        savedAt: typeof item.savedAt === "string" ? item.savedAt : undefined,
        owner: {
          telegramId: owner.telegramId === undefined
            ? undefined
            : finiteNumber(owner.telegramId, "travelFinale.owner.telegramId", { integer: true }),
          username: typeof owner.username === "string" ? owner.username : undefined,
          firstName: typeof owner.firstName === "string" ? owner.firstName : undefined,
        },
        partCount: finiteNumber(item.partCount, "travelFinale.partCount", { integer: true }),
        videoCount: finiteNumber(item.videoCount, "travelFinale.videoCount", { integer: true }),
      };
    });
  });
}

export function fetchTravelFinale(travelId: string): Promise<TravelFinaleDetail> {
  return request(`/api/admin/travel-finales/${encodeURIComponent(travelId)}`, {}, (payload) => {
    const item = record(payload, "travelFinaleDetail");
    if (!Array.isArray(item.attempts)) {
      throw new ApiContractError("Invalid travelFinaleDetail.attempts");
    }
    return {
      travel: parseInteractiveTravelResponse({
        travel: record(item.travel, "travelFinaleDetail.travel"),
      }).travel,
      story: text(item.story, "travelFinaleDetail.story"),
      defaultDirection: text(item.defaultDirection, "travelFinaleDetail.defaultDirection"),
      attempts: item.attempts.map(attempt),
    };
  });
}

export async function importTravelFinale(travel: InteractiveTravelState): Promise<void> {
  const validatedTravel = parseInteractiveTravelResponse({ travel }).travel;
  await request(
    "/api/admin/travel-finales/import",
    { method: "POST", body: { travel: validatedTravel } },
    () => undefined,
  );
}

export function compileTravelFinalePrompt(
  travelId: string,
  direction: string,
): Promise<string> {
  return request(
    `/api/admin/travel-finales/${encodeURIComponent(travelId)}/prompt`,
    { method: "POST", body: { direction } },
    (payload) => text(record(payload, "travelFinalePrompt").prompt, "travelFinalePrompt.prompt"),
  );
}

export function generateTravelFinaleVideo(
  travelId: string,
  prompt: string,
  referenceBaseUrl: string,
): Promise<TravelFinaleAttempt> {
  return request(
    `/api/admin/travel-finales/${encodeURIComponent(travelId)}/generate`,
    {
      method: "POST",
      body: { prompt, referenceBaseUrl },
      timeoutMs: MEDIA_REQUEST_TIMEOUT_MS,
    },
    attempt,
  );
}
