import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cancelInteractiveTravel,
  continueInteractiveTravel,
  generateOutfitAssets,
  generatePetAssets,
  refreshPetBackgroundAssets,
  registerPetPushSnapshot,
  sendLocalChatMessage,
  startInteractiveTravel,
} from "./api";
import { buildMemoryContextForMessage } from "./localPetMemoryRecall";
import {
  interactiveTravelPartFixture,
  interactiveTravelPlanFixture,
} from "./interactiveTravelTestFixtures";
import type { LocalPetMemoryContext } from "./localPetMemoryTypes";
import {
  compactPushSnapshotPayload,
  serializedJsonBytes,
} from "./pushSnapshotPayload";
import type {
  InteractiveTravelState,
  LocalChatMessage,
  LocalPetAssetSet,
  LocalPetState,
  PetMood,
} from "./types";

function assetImages(prefix: string): LocalPetAssetSet["images"] {
  const stageImages = (stage: "baby" | "teen" | "adult"): Record<PetMood, string> => ({
    idle: `${prefix}/${stage}-idle.png`,
    happy: `${prefix}/${stage}-happy.png`,
    hungry: `${prefix}/${stage}-hungry.png`,
    sad: `${prefix}/${stage}-sad.png`,
  });
  return {
    baby: stageImages("baby"),
    teen: stageImages("teen"),
    adult: stageImages("adult"),
  };
}

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

  it("uses the normal owner-bound endpoint to cancel interactive travel", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ cancelled: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await cancelInteractiveTravel("interactive-travel-api-test");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/travel/interactive/interactive-travel-api-test/cancel",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("sends the stable local pet id in interactive travel context", async () => {
    const plan = interactiveTravelPlanFixture();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        travel: {
          travelId: "interactive-travel-api-payload",
          generatedAt: "2026-07-15T12:00:00Z",
          destination: "маяк",
          overallTitle: "Путь к маяку",
          plan,
          parts: [interactiveTravelPartFixture(plan, 0)],
          completed: false,
        },
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await startInteractiveTravel("маяк", petState());

    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const payload = JSON.parse(String(requestInit.body));
    expect(payload.pet.petId).toBe("pet-1");
    expect(payload).not.toHaveProperty("history");
    expect(payload).not.toHaveProperty("memoryContext");
  });

  it("continues travel with only the authoritative travel, answer, pet, and debug flag", async () => {
    const plan = interactiveTravelPlanFixture();
    const travel: InteractiveTravelState = {
      travelId: "interactive-travel-api-continue",
      generatedAt: "2026-07-15T12:00:00Z",
      destination: "маяк",
      overallTitle: "Путь к маяку",
      generationStatus: "ready",
      plan,
      parts: [interactiveTravelPartFixture(plan, 0)],
      completed: false,
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ travel }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await continueInteractiveTravel(travel, plan.tasks[0].correctChoice, petState(), {
      includeDebug: true,
    });

    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const payload = JSON.parse(String(requestInit.body));
    expect(Object.keys(payload).sort()).toEqual(["advice", "includeDebug", "pet", "travel"]);
    expect(payload).toMatchObject({
      advice: plan.tasks[0].correctChoice,
      includeDebug: true,
      pet: { petId: "pet-1" },
      travel: { travelId: travel.travelId, plan },
    });
    expect(payload).not.toHaveProperty("history");
    expect(payload).not.toHaveProperty("memoryContext");
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

  it("sends the pet-generation idempotency key through the transport", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        jobId: "job-1",
        status: "succeeded",
        phase: "completed",
        createdAt: "2026-07-10T12:00:00Z",
        updatedAt: "2026-07-10T12:01:00Z",
        result: {
          assetSetId: "asset-1",
          generatedAt: "2026-07-10T12:01:00Z",
          images: assetImages("/generated"),
        },
      }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await generatePetAssets("мышонок", { requestKey: "pet-request-api-0001" });

    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const headers = new Headers(requestInit.headers);
    expect(headers.get("Idempotency-Key")).toBe("pet-request-api-0001");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(requestInit.body))).toEqual({ description: "мышонок" });
  });

  it("sends three mood references for outfit generation", async () => {
    const onJobQueued = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        jobId: "outfit-job-1",
        status: "succeeded",
        phase: "completed",
        createdAt: "2026-07-10T12:00:00Z",
        updatedAt: "2026-07-10T12:01:00Z",
        result: {
          assetSetId: "outfit-assets-1",
          generatedAt: "2026-07-10T12:01:00Z",
          images: assetImages("/outfit"),
        },
      }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await generateOutfitAssets(
      "Одень персонажа в футболку Аргентины.",
      {
        idleImageUrl: "/generated/idle.png",
        sadImageUrl: "/generated/sad.png",
        happyImageUrl: "/generated/happy.png",
      },
      { requestKey: "outfit-request-api-0001", onJobQueued },
    );

    expect(onJobQueued).toHaveBeenCalledWith("outfit-job-1");
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/api/outfit/generate");
    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(requestInit.headers).get("Idempotency-Key")).toBe(
      "outfit-request-api-0001",
    );
    expect(JSON.parse(String(requestInit.body))).toEqual({
      prompt: "Одень персонажа в футболку Аргентины.",
      idleImageUrl: "/generated/idle.png",
      sadImageUrl: "/generated/sad.png",
      happyImageUrl: "/generated/happy.png",
    });
  });

  it("marks a server-confirmed failed generation as terminal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({
          jobId: "job-failed",
          status: "failed",
          phase: "generating_images",
          createdAt: "2026-07-10T12:00:00Z",
          updatedAt: "2026-07-10T12:01:00Z",
          error: { code: "GENERATION_FAILED", message: "Не получилось создать питомца." },
        }), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(
      generatePetAssets("мышонок", { requestKey: "pet-request-api-0002" }),
    ).rejects.toMatchObject({
      code: "GENERATION_FAILED",
      generationTerminal: true,
    });
  });

  it("stops pet-generation polling when the caller aborts", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        jobId: "job-pending",
        status: "running",
        phase: "generating_images",
        createdAt: "2026-07-10T12:00:00Z",
        updatedAt: "2026-07-10T12:01:00Z",
      }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    const generation = generatePetAssets("мышонок", {
      requestKey: "pet-request-api-0003",
      signal: controller.signal,
    });
    controller.abort();

    await expect(generation).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("sends a stable writer id with a monotonic push revision", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify({
        registered: true,
        telegramId: 42,
        updatedAt: "2026-07-15T12:00:00Z",
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const localPet = {
      ...petState(),
      travelImpactReceipts: ["travel-local-only:1"],
    };
    await registerPetPushSnapshot(localPet);
    await registerPetPushSnapshot(localPet);

    const first = JSON.parse(String((fetchMock.mock.calls[0]?.[1] as RequestInit).body));
    const second = JSON.parse(String((fetchMock.mock.calls[1]?.[1] as RequestInit).body));
    expect(first.snapshotWriterId).toMatch(/^[A-Za-z0-9_-]{16,120}$/);
    expect(second.snapshotWriterId).toBe(first.snapshotWriterId);
    expect(second.snapshotRevision).toBeGreaterThan(first.snapshotRevision);
    expect(first.travelImpactReceipts).toBeUndefined();
    expect(first.pet.travelImpactReceipts).toBeUndefined();
  });

  it("compacts an oversized push snapshot below the backend persistence cap", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        registered: true,
        telegramId: 42,
        updatedAt: "2026-07-15T12:00:00Z",
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const pet: LocalPetState = {
      ...petState(),
      name: "Л".repeat(200),
      assetSet: {
        assetSetId: "oversized-assets",
        generatedAt: "2026-07-15T12:00:00Z",
        images: assetImages("/generated"),
        characterBible: {
          identity: { name: "Листик", species: "мышонок" },
          oversizedArchive: { text: "б".repeat(400_000) },
        },
      },
    };
    const history: LocalChatMessage[] = Array.from({ length: 12 }, (_, index) => ({
      id: `history-${index}`,
      role: index % 2 === 0 ? "user" : "pet",
      text: `${index}:${"я".repeat(8_000)}`,
      createdAt: "2026-07-15T12:00:00Z",
    }));
    const memoryContext: LocalPetMemoryContext = {
      summary: "с".repeat(1_000),
      userProfile: "п".repeat(1_000),
      relevantMemories: Array.from({ length: 5 }, (_, index) => ({
        id: `memory-${index}`,
        kind: "user_fact" as const,
        text: "ф".repeat(500),
      })),
      episodes: Array.from({ length: 3 }, (_, episodeIndex) => ({
        id: `episode-${episodeIndex}`,
        messages: Array.from({ length: 8 }, (_, messageIndex) => ({
          role: messageIndex % 2 === 0 ? "user" as const : "pet" as const,
          text: "🦊".repeat(8_000),
          createdAt: "2026-07-15T12:00:00Z",
        })),
      })),
    };
    const originalBible = structuredClone(pet.assetSet?.characterBible);
    const originalMemory = structuredClone(memoryContext);
    const originalHistory = structuredClone(history);

    await registerPetPushSnapshot(pet, memoryContext, {
      history,
      recentAmbientReplies: Array.from({ length: 10 }, () => "р".repeat(8_000)),
    });

    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const serializedBody = String(requestInit.body);
    const payload = JSON.parse(serializedBody);
    expect(new TextEncoder().encode(serializedBody).byteLength).toBeLessThanOrEqual(240_000);
    expect(payload.pet.name).toHaveLength(80);
    expect(payload.pet.characterBible.identity).toEqual({
      name: "Листик",
      species: "мышонок",
    });
    expect(payload.pet.characterBible.oversizedArchive).toBeUndefined();
    expect(pet.assetSet?.characterBible).toEqual(originalBible);
    expect(memoryContext).toEqual(originalMemory);
    expect(history).toEqual(originalHistory);
  });

  it("preserves per-stat decay and zero timers in the minimal push snapshot", () => {
    const timestamp = "2026-07-15T12:00:00.000Z";
    const compacted = compactPushSnapshotPayload({
      petId: `pet-${"x".repeat(300_000)}`,
      snapshotWriterId: "writer-1234567890",
      snapshotRevision: 7,
      createdAt: timestamp,
      updatedAt: timestamp,
      lastStatsTickAt: timestamp,
      lastStatTickAt: {
        hunger: timestamp,
        happiness: timestamp,
        energy: timestamp,
      },
      zeroStatSinceAt: { hunger: timestamp },
      timezone: "Europe/Moscow",
      history: [],
      recentAmbientReplies: [],
      pet: {
        description: "мышонок",
        stage: "baby",
        mood: "hungry",
        stats: { hunger: 0, happiness: 50, energy: 60 },
      },
    });

    expect(serializedJsonBytes(compacted)).toBeLessThanOrEqual(240_000);
    expect(compacted.petId).toHaveLength(120);
    expect(compacted.lastStatTickAt).toEqual({
      hunger: timestamp,
      happiness: timestamp,
      energy: timestamp,
    });
    expect(compacted.zeroStatSinceAt).toEqual({ hunger: timestamp });
  });

  it("safe-clones a small deep character bible before sending a snapshot", () => {
    const characterBible: Record<string, unknown> = { identity: { name: "Листик" } };
    let cursor = characterBible;
    for (let index = 0; index < 20; index += 1) {
      const next: Record<string, unknown> = {};
      cursor.next = next;
      cursor = next;
    }
    cursor.leaf = "слишком глубоко";

    const compacted = compactPushSnapshotPayload({
      petId: "pet-1",
      history: [],
      recentAmbientReplies: [],
      pet: {
        description: "мышонок",
        stage: "baby",
        mood: "idle",
        stats: { hunger: 100, happiness: 100, energy: 100 },
        characterBible,
      },
    });

    expect(compacted.pet.characterBible).not.toBe(characterBible);
    expect(JSON.stringify(compacted.pet.characterBible)).not.toContain("слишком глубоко");
  });

  it("drops cycles and prototype-pollution keys from snapshot character bibles", () => {
    const characterBible = JSON.parse(
      '{"safe":"ok","__proto__":{"polluted":true},"constructor":{"prototype":{"polluted":true}}}',
    ) as Record<string, unknown>;
    characterBible.self = characterBible;

    const compacted = compactPushSnapshotPayload({
      petId: "pet-1",
      history: [],
      recentAmbientReplies: [],
      pet: {
        description: "мышонок",
        stage: "baby",
        mood: "idle",
        stats: { hunger: 100, happiness: 100, energy: 100 },
        characterBible,
      },
    });
    const sanitized = compacted.pet.characterBible ?? {};

    expect(sanitized.safe).toBe("ok");
    expect(Object.hasOwn(sanitized, "__proto__")).toBe(false);
    expect(Object.hasOwn(sanitized, "constructor")).toBe(false);
    expect(Object.hasOwn(sanitized, "self")).toBe(false);
    expect(({} as { polluted?: boolean }).polluted).toBeUndefined();
  });

  it("forwards an abort signal for replaceable chat requests", async () => {
    const captured: { signal?: AbortSignal } = {};
    const abortError = new DOMException("Aborted", "AbortError");
    const fetchMock = vi.fn((_url: string | URL | Request, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        captured.signal = init?.signal ?? undefined;
        captured.signal?.addEventListener("abort", () => reject(abortError), { once: true });
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    const pendingRequest = sendLocalChatMessage("Ням", petState(), [], {
      signal: controller.signal,
    });
    controller.abort();

    await expect(pendingRequest).rejects.toBe(abortError);
    expect(captured.signal).not.toBe(controller.signal);
    expect(captured.signal?.aborted).toBe(true);
  });

  it("preserves the locally enriched character bible during asset refresh", async () => {
    const localCharacterBible = {
      identity: { name: "Лея" },
      lite_overlay: { facts: ["любит сыр"] },
    };
    const assetSet: LocalPetAssetSet = {
      assetSetId: "asset-1",
      generatedAt: "2026-07-10T12:00:00Z",
      images: assetImages("/old"),
      generationJobId: "job-1",
      backgroundGenerationStatus: "running",
      characterBible: localCharacterBible,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({
          jobId: "job-1",
          status: "succeeded",
          phase: "completed",
          createdAt: "2026-07-10T12:00:00Z",
          updatedAt: "2026-07-10T12:01:00Z",
          result: {
            assetSetId: "asset-1",
            generatedAt: "2026-07-10T12:00:00Z",
            images: assetImages("/new"),
            characterBible: { identity: { name: "Устаревшее имя" } },
          },
        }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const refreshed = await refreshPetBackgroundAssets(assetSet);

    expect(refreshed.images.teen.idle).toContain("/new/teen-idle.png");
    expect(refreshed.characterBible).toBe(localCharacterBible);
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
