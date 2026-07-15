import { parseInteractiveTravelResponse } from "./apiContracts";
import { ApiContractError, request } from "./apiTransport";
import { resolveApiAssetUrl } from "./publicAssetUrl";
import { getTelegramInitData } from "./telegram";
import type { InteractiveTravelDemoResponse } from "./types";

function debugAuthHeaders(): HeadersInit {
  const initData = getTelegramInitData();
  if (initData) {
    return { Authorization: `tma ${initData}` };
  }
  if (
    process.env.NODE_ENV === "development" &&
    typeof window !== "undefined" &&
    ["localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"].includes(
      window.location.hostname,
    )
  ) {
    return { "X-Telegram-Init-Data": "dev" };
  }
  return {};
}

export function resetInteractiveTravelGeneration(travelId: string): Promise<void> {
  return request(
    `/api/travel/interactive/${encodeURIComponent(travelId)}/debug/reset`,
    {
      method: "POST",
      headers: debugAuthHeaders(),
    },
    () => undefined,
  );
}

export function getInteractiveTravelDemo(): Promise<InteractiveTravelDemoResponse> {
  return request(
    "/api/travel/interactive/debug/demo",
    { headers: debugAuthHeaders() },
    (payload) => {
      if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
        throw new ApiContractError("Invalid interactive travel demo response");
      }
      const root = payload as Record<string, unknown>;
      if (typeof root.demoId !== "string" || !root.demoId.trim()) {
        throw new ApiContractError("Invalid interactive travel demo id");
      }
      const parsed = parseInteractiveTravelResponse({ travel: root.travel });
      return {
        demoId: root.demoId,
        travel: {
          ...parsed.travel,
          parts: parsed.travel.parts.map((part) => {
            const backgroundImageUrl = resolveApiAssetUrl(part.backgroundImageUrl);
            const backgroundVideoUrl = resolveApiAssetUrl(part.backgroundVideoUrl);
            if (!backgroundImageUrl || !backgroundVideoUrl) {
              throw new ApiContractError("Interactive travel demo media is missing");
            }
            return { ...part, backgroundImageUrl, backgroundVideoUrl };
          }),
        },
      };
    },
  );
}
