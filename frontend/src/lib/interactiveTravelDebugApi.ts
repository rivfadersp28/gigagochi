import { request } from "./apiTransport";
import { getTelegramInitData } from "./telegram";

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
