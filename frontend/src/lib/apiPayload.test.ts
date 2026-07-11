import { afterEach, describe, expect, it, vi } from "vitest";

import { sendLocalChatMessage } from "./api";
import { buildMemoryContextForMessage } from "./localPetMemoryRecall";
import type { LocalPetMemoryContext } from "./localPetMemoryTypes";
import type { LocalChatMessage, LocalPetState } from "./types";

function petState(): LocalPetState {
  const tick = "2026-07-10T12:00:00.000Z";
  return {
    version: 2,
    petId: "pet-1",
    description: ` мышь ${"а".repeat(320)} `,
    createdAt: tick,
    updatedAt: tick,
    lastInteractionAt: tick,
    lastStatsTickAt: tick,
    lastStatTickAt: {
      hunger: tick,
      happiness: tick,
      energy: tick,
    },
    stage: "baby",
    mood: "idle",
    stats: { hunger: 50, happiness: 50, energy: 50 },
  };
}

describe("API request payloads", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("removes invalid legacy chat and memory entries before sending", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ reply: "Голод отступает" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const history: LocalChatMessage[] = [
      { id: "m-1", role: "user", text: "   ", createdAt: "invalid" },
      { id: "m-2", role: "pet", text: "  Старая реплика  ", createdAt: "invalid" },
    ];
    const memoryContext: LocalPetMemoryContext = {
      relevantMemories: [
        { id: "", kind: "user_fact", text: "сломанная запись" },
        { id: "memory-1", kind: "preference", text: "  любит сыр  " },
      ],
      episodes: [
        {
          id: "episode-1",
          messages: [
            { role: "user", text: " " },
            { role: "pet", text: "  Я это помню  ", createdAt: "2026-07-10T12:00:00Z" },
          ],
        },
      ],
    };

    await sendLocalChatMessage("Скажи, что голод отступает", petState(), history, {
      complimentHistory: ["  редкая   смелость  ", " ", "x".repeat(140)],
      memoryContext,
      replyMaxChars: 120,
    });

    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const payload = JSON.parse(String(requestInit.body));

    expect(payload.pet.description).toHaveLength(300);
    expect(payload.history).toEqual([{ role: "pet", text: "Старая реплика" }]);
    expect(payload.complimentHistory).toEqual([
      "редкая   смелость",
      "x".repeat(120),
    ]);
    expect(payload.memoryContext.relevantMemories).toEqual([
      { id: "memory-1", kind: "preference", text: "любит сыр" },
    ]);
    expect(payload.memoryContext.episodes).toEqual([
      {
        id: "episode-1",
        messages: [{
          role: "pet",
          text: "Я это помню",
          createdAt: "2026-07-10T12:00:00Z",
        }],
      },
    ]);
  });

  it("forwards an abort signal for replaceable chat requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ reply: "Ням" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await sendLocalChatMessage("Ням", petState(), [], { signal: controller.signal });

    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(requestInit.signal).toBe(controller.signal);
  });

  it("sanitizes a legacy episode recalled by the identity question", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ reply: "Я лесная мышь" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const history: LocalChatMessage[] = [
      { id: "old-question", role: "user", text: "А ты кто?", createdAt: "invalid" },
      { id: "old-blank", role: "pet", text: "   ", createdAt: "invalid" },
      ...Array.from({ length: 11 }, (_, index): LocalChatMessage => ({
        id: `recent-${index}`,
        role: index % 2 === 0 ? "user" : "pet",
        text: `Недавняя реплика ${index}`,
        createdAt: "2026-07-10T12:00:00Z",
      })),
    ];
    const message = "ты кто?";
    const memoryContext = buildMemoryContextForMessage(
      history,
      message,
      new Date("2026-07-10T13:00:00Z"),
    );

    expect(memoryContext.episodes?.[0]?.messages.some((item) => !item.text.trim())).toBe(true);

    await sendLocalChatMessage(message, petState(), history.slice(-12), { memoryContext });

    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const payload = JSON.parse(String(requestInit.body));

    expect(payload.message).toBe(message);
    expect(payload.history.every((item: { text: string }) => item.text.length > 0)).toBe(true);
    expect(
      payload.memoryContext.episodes[0].messages.every(
        (item: { text: string }) => item.text.length > 0,
      ),
    ).toBe(true);
  });
});
