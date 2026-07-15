import { afterEach, expect, it, vi } from "vitest";

import { fetchTravelFinale, importTravelFinale } from "./travelFinaleApi";
import type { InteractiveTravelState } from "./types";

afterEach(() => vi.restoreAllMocks());

it("rejects malformed imported travel before sending it to the backend", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch");

  await expect(importTravelFinale({ travelId: "broken" } as InteractiveTravelState))
    .rejects.toThrow();
  expect(fetchMock).not.toHaveBeenCalled();
});

it("rejects a malformed persisted travel instead of casting it into the paid lab", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    travel: { travelId: "broken" },
    story: "История",
    defaultDirection: "Направление",
    attempts: [],
  }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));

  await expect(fetchTravelFinale("broken")).rejects.toThrow();
});
