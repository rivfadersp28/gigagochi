import { afterEach, describe, expect, it, vi } from "vitest";

import {
  parseGeneratePetJobResponse,
  parseInteractiveTravelAnimationResponse,
  parseInteractiveTravelIllustrationResponse,
  parseInteractiveTravelResponse,
  parseLocalChatResponse,
  parseMemoryExtractionResponse,
  parsePushSnapshotResponse,
} from "./apiContracts";
import { ApiContractError, ApiError, request } from "./apiTransport";

describe("API response contracts", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("normalizes nullable chat fields for the app domain", () => {
    expect(
      parseLocalChatResponse({
        reply: "Я здесь",
        moodHint: null,
        happinessDelta: null,
        petPatch: null,
        debug: null,
      }),
    ).toEqual({
      reply: "Я здесь",
      moodHint: undefined,
      happinessDelta: undefined,
      complimentKey: undefined,
      innerThought: undefined,
      faceHint: undefined,
      petPatch: undefined,
      storyLibraryPatch: undefined,
      debug: undefined,
    });
  });

  it("parses a server-requested local pet reset", () => {
    expect(
      parsePushSnapshotResponse({
        registered: true,
        telegramId: 549733365,
        updatedAt: "2026-07-13T19:00:00Z",
        resetPet: true,
      }).resetPet,
    ).toBe(true);
  });

  it("accepts only supported conversation happiness deltas", () => {
    expect(parseLocalChatResponse({ reply: "Я здесь", happinessDelta: -80 }).happinessDelta)
      .toBe(-80);
    expect(parseLocalChatResponse({ reply: "Спасибо", happinessDelta: 30 }).happinessDelta)
      .toBe(30);
    expect(parseLocalChatResponse({ reply: "Спасибо", happinessDelta: 100 }).happinessDelta)
      .toBe(100);
    expect(() => parseLocalChatResponse({ reply: "Я здесь", happinessDelta: 20 }))
      .toThrow(ApiContractError);
    expect(() => parseLocalChatResponse({ reply: "Я здесь", happinessDelta: -10 }))
      .toThrow(ApiContractError);
  });

  it("rejects an incomplete generation job", () => {
    expect(() =>
      parseGeneratePetJobResponse({
        jobId: "job-1",
        status: "succeeded",
        phase: "completed",
        createdAt: "2026-07-15T12:00:00Z",
        updatedAt: "2026-07-15T12:01:00Z",
      }),
    ).toThrow(ApiContractError);
  });

  it("rejects unknown memory operations before persistence", () => {
    expect(() =>
      parseMemoryExtractionResponse({
        operations: [{ type: "erase_everything" }],
      }),
    ).toThrow(ApiContractError);
  });

  it("rejects oversized chat, patch, and memory extraction payloads", () => {
    expect(() => parseLocalChatResponse({ reply: "р".repeat(8_001) })).toThrow(
      ApiContractError,
    );
    expect(() =>
      parseLocalChatResponse({
        reply: "Я здесь",
        storyLibraryPatch: { text: "п".repeat(130_000) },
      }),
    ).toThrow(ApiContractError);
    expect(() =>
      parseMemoryExtractionResponse({
        operations: Array.from({ length: 13 }, () => ({ type: "forget_user_fact" })),
      }),
    ).toThrow(ApiContractError);
  });

  it.each([
    "https://evil.example/generated.png",
    "javascript:alert(1)",
    "//evil.example/generated.png",
  ])("rejects an untrusted public asset URL: %s", (imageUrl) => {
    expect(() =>
      parseInteractiveTravelIllustrationResponse({ partNumber: 1, imageUrl }),
    ).toThrow(ApiContractError);
  });

  it("reconstructs generated pet jobs without unknown or unsafe nested fields", () => {
    const stageImages = {
      idle: "/static/generated/idle.png",
      happy: "/static/generated/happy.png",
      hungry: "/static/generated/hungry.png",
      sad: "/static/generated/sad.png",
      ignoredMood: "/static/generated/ignored.png",
    };
    const characterBible = JSON.parse(
      '{"identity":{"name":"Листик"},"__proto__":{"polluted":true},"constructor":{"prototype":{"polluted":true}}}',
    ) as Record<string, unknown>;
    let cursor = characterBible;
    for (let index = 0; index < 20; index += 1) {
      const next: Record<string, unknown> = {};
      cursor.next = next;
      cursor = next;
    }
    cursor.tooDeep = "drop-me";
    const error = JSON.parse(
      '{"message":"generation failed","__proto__":{"polluted":true},"constructor":{"prototype":{"polluted":true}}}',
    ) as Record<string, unknown>;
    let errorCursor = error;
    for (let index = 0; index < 15; index += 1) {
      const next: Record<string, unknown> = {};
      errorCursor.next = next;
      errorCursor = next;
    }
    errorCursor.tooDeep = "drop-error";

    const parsed = parseGeneratePetJobResponse({
      jobId: "job-1",
      status: "succeeded",
      phase: "completed",
      createdAt: "2026-07-15T12:00:00Z",
      updatedAt: "2026-07-15T12:01:00Z",
      unknownTopLevel: "drop-me",
      error,
      result: {
        assetSetId: "assets-1",
        generatedAt: "2026-07-15T12:01:00Z",
        images: {
          baby: stageImages,
          teen: stageImages,
          adult: stageImages,
          unknownStage: stageImages,
        },
        characterBible,
        unknownResultField: "drop-me",
      },
    });

    expect(Object.hasOwn(parsed, "unknownTopLevel")).toBe(false);
    expect(Object.hasOwn(parsed.result ?? {}, "unknownResultField")).toBe(false);
    expect(Object.keys(parsed.result?.images.baby ?? {})).toEqual([
      "idle",
      "happy",
      "hungry",
      "sad",
    ]);
    expect(parsed.result?.characterBible?.identity).toEqual({ name: "Листик" });
    expect(JSON.stringify(parsed.result?.characterBible)).not.toContain("drop-me");
    expect(Object.hasOwn(parsed.result?.characterBible ?? {}, "__proto__")).toBe(false);
    expect(Object.hasOwn(parsed.result?.characterBible ?? {}, "constructor")).toBe(false);
    expect(Object.hasOwn(parsed.error ?? {}, "__proto__")).toBe(false);
    expect(Object.hasOwn(parsed.error ?? {}, "constructor")).toBe(false);
    expect(JSON.stringify(parsed.error)).not.toContain("drop-error");
    expect(({} as { polluted?: boolean }).polluted).toBeUndefined();
  });

  it("rejects oversized generated character bibles and errors", () => {
    const stageImages = {
      idle: "/static/generated/idle.png",
      happy: "/static/generated/happy.png",
      hungry: "/static/generated/hungry.png",
      sad: "/static/generated/sad.png",
    };
    const baseJob = {
      jobId: "job-1",
      status: "succeeded",
      phase: "completed",
      createdAt: "2026-07-15T12:00:00Z",
      updatedAt: "2026-07-15T12:01:00Z",
    };
    const result = {
      assetSetId: "assets-1",
      generatedAt: "2026-07-15T12:01:00Z",
      images: { baby: stageImages, teen: stageImages, adult: stageImages },
    };

    expect(() =>
      parseGeneratePetJobResponse({
        ...baseJob,
        result: { ...result, characterBible: { archive: "x".repeat(512_001) } },
      }),
    ).toThrow(ApiContractError);
    expect(() =>
      parseGeneratePetJobResponse({
        ...baseJob,
        error: { diagnostic: "x".repeat(64_001) },
        result,
      }),
    ).toThrow(ApiContractError);
  });

  it("accepts an overlong departure hook after the backend retry fallback", () => {
    const departureHook =
      "Я снова склоняюсь над картой и продолжаю долгий путь к верхнему мосту через провал.";
    const resolvedResult = {
      text: "Я перешла мост.",
      adviceAssessment: "helpful",
      reaction: "Хороший план!",
      reactionTone: "enthusiastic",
      consequence: "Путь открыт.",
      outcomeValence: "positive",
      statImpacts: [],
    };

    const parsed = parseInteractiveTravelResponse({
      travel: {
        travelId: "interactive-travel-1",
        generatedAt: "2026-07-14T12:00:00Z",
        destination: "гора",
        overallTitle: "Путь",
        arcPlan: {},
        parts: [
          {
            partNumber: 1,
            title: "Начало",
            storyText: "Я подошла к мосту.",
            challenge: "Что делать?",
            actionSuggestions: [],
            answer: "Идти",
            result: resolvedResult,
          },
          {
            partNumber: 2,
            title: "Продолжение",
            storyText: "Через 4 часа я иду дальше.",
            transition: { elapsedHours: 4, summary: "Мир изменился.", departureHook },
            challenge: "Куда идти?",
            actionSuggestions: [],
          },
        ],
        completed: false,
      },
    });

    expect(parsed.travel.parts[1].transition?.departureHook).toBe(departureHook);
  });

  it("rejects a departure hook above the shared persistence boundary", () => {
    expect(() =>
      parseInteractiveTravelResponse({
        travel: {
          travelId: "interactive-travel-1",
          generatedAt: "2026-07-14T12:00:00Z",
          destination: "гора",
          overallTitle: "Путь",
          arcPlan: {},
          parts: [
            {
              partNumber: 1,
              title: "Начало",
              storyText: "Я подошла к мосту.",
              challenge: "Что делать?",
              actionSuggestions: [],
              answer: "Идти",
              result: {
                text: "Я перешла мост.",
                adviceAssessment: "helpful",
                reaction: "Хороший план!",
                reactionTone: "enthusiastic",
                consequence: "Путь открыт.",
                outcomeValence: "positive",
                statImpacts: [],
              },
            },
            {
              partNumber: 2,
              title: "Продолжение",
              storyText: "Через 4 часа я иду дальше.",
              transition: {
                elapsedHours: 4,
                summary: "Мир изменился.",
                departureHook: "д".repeat(281),
              },
              challenge: "Куда идти?",
              actionSuggestions: [],
            },
          ],
          completed: false,
        },
      }),
    ).toThrow(ApiContractError);
  });

  it("rejects interactive travel ids outside the backend schema", () => {
    expect(() =>
      parseInteractiveTravelResponse({
        travel: {
          travelId: "travel/../foreign",
          generatedAt: "2026-07-14T12:00:00Z",
          destination: "гора",
          overallTitle: "Путь",
          arcPlan: {},
          parts: [{
            partNumber: 1,
            title: "Начало",
            storyText: "Я подошла к мосту.",
            challenge: "Что делать?",
            actionSuggestions: [],
          }],
          completed: false,
        },
      }),
    ).toThrow(ApiContractError);
  });

  it.each([
    [
      parseInteractiveTravelIllustrationResponse,
      { partNumber: 7, imageUrl: "/static/generated/travel/part-7.png" },
    ],
    [
      parseInteractiveTravelAnimationResponse,
      { partNumber: 7, videoUrl: "/static/generated/travel/part-7.mp4" },
    ],
  ])("accepts media for the seventh interactive travel part", (parse, payload) => {
    expect(parse(payload).partNumber).toBe(7);
    expect(() => parse({ ...payload, partNumber: 8 })).toThrow(ApiContractError);
  });

  it("maps an invalid successful payload to a safe API error", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ reply: 42 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const promise = request("/api/chat", {}, parseLocalChatResponse);

    await expect(promise).rejects.toBeInstanceOf(ApiError);
    await expect(promise).rejects.toMatchObject({ code: "INVALID_RESPONSE", status: 200 });
  });

  it("rejects a response whose declared body exceeds 8 MiB", async () => {
    const response = new Response('{"reply":"ok"}', {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Content-Length": String(8 * 1024 * 1024 + 1),
      },
    });
    const cancel = vi.spyOn(response.body!, "cancel");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(request("/api/chat", {}, parseLocalChatResponse)).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
      status: 200,
    });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("cancels and rejects a chunked response after 8 MiB", async () => {
    let cancelled = false;
    let chunkIndex = 0;
    const chunks = [new Uint8Array(5 * 1024 * 1024), new Uint8Array(4 * 1024 * 1024)];
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(chunks[chunkIndex]);
        chunkIndex += 1;
      },
      cancel() {
        cancelled = true;
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(stream, { status: 200 })));

    await expect(request("/api/chat", {}, parseLocalChatResponse)).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
      status: 200,
    });
    expect(cancelled).toBe(true);
  });

  it("keeps a safe service error when an unsuccessful response is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("Bad gateway", { status: 502 })),
    );

    await expect(request("/api/chat", {}, parseLocalChatResponse)).rejects.toMatchObject({
      message: "Сервис временно недоступен. Попробуйте позже.",
      status: 502,
    });
  });

  it("preserves a validated active generation job id from a conflict", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({
        detail: {
          code: "GENERATION_ALREADY_ACTIVE",
          message: "Уже создаём питомца.",
          activeJobId: "job-active-123",
          activeDescription: "  мышонок  ",
        },
      }), { status: 409, headers: { "Content-Type": "application/json" } })),
    );

    await expect(request("/api/generate-pet", {}, parseLocalChatResponse)).rejects.toMatchObject({
      code: "GENERATION_ALREADY_ACTIVE",
      status: 409,
      activeJobId: "job-active-123",
      activeDescription: "мышонок",
    });
  });

  it("drops a malformed active generation job id from a conflict", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({
        detail: {
          code: "GENERATION_ALREADY_ACTIVE",
          activeJobId: "../../not a job",
        },
      }), { status: 409, headers: { "Content-Type": "application/json" } })),
    );

    try {
      await request("/api/generate-pet", {}, parseLocalChatResponse);
      throw new Error("request should reject");
    } catch (error) {
      expect(error).toMatchObject({
        code: "GENERATION_ALREADY_ACTIVE",
        status: 409,
        activeJobId: undefined,
      });
    }
  });

  it("preserves only a validated active interactive travel id from a conflict", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({
        detail: {
          code: "INTERACTIVE_TRAVEL_ALREADY_ACTIVE",
          message: "Сначала отмените текущее путешествие.",
          travelId: "  interactive-travel-owner-session  ",
        },
      }), { status: 409, headers: { "Content-Type": "application/json" } })),
    );

    await expect(request("/api/travel/interactive/start", {}, parseLocalChatResponse))
      .rejects.toMatchObject({
        code: "INTERACTIVE_TRAVEL_ALREADY_ACTIVE",
        status: 409,
        travelId: "interactive-travel-owner-session",
      });
  });

  it("drops an unsafe active interactive travel id from a conflict", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({
        detail: {
          code: "INTERACTIVE_TRAVEL_ALREADY_ACTIVE",
          travelId: "../../foreign-travel",
        },
      }), { status: 409, headers: { "Content-Type": "application/json" } })),
    );

    try {
      await request("/api/travel/interactive/start", {}, parseLocalChatResponse);
      throw new Error("request should reject");
    } catch (error) {
      expect(error).toMatchObject({
        code: "INTERACTIVE_TRAVEL_ALREADY_ACTIVE",
        status: 409,
        travelId: undefined,
      });
    }
  });

  it.each([
    ["missing", undefined],
    ["non-string", { prompt: "мышонок" }],
    ["too long", "x".repeat(301)],
  ])("drops an active generation description when it is %s", async (_label, activeDescription) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({
        detail: {
          code: "GENERATION_ALREADY_ACTIVE",
          activeJobId: "job-active-123",
          ...(activeDescription === undefined ? {} : { activeDescription }),
        },
      }), { status: 409, headers: { "Content-Type": "application/json" } })),
    );

    try {
      await request("/api/generate-pet", {}, parseLocalChatResponse);
      throw new Error("request should reject");
    } catch (error) {
      expect(error).toMatchObject({
        code: "GENERATION_ALREADY_ACTIVE",
        status: 409,
        activeJobId: "job-active-123",
        activeDescription: undefined,
      });
    }
  });

  it("preserves an intentional request abort", async () => {
    const controller = new AbortController();
    const abortError = new DOMException("Aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError));
    controller.abort();

    await expect(
      request("/api/chat", { signal: controller.signal }, parseLocalChatResponse),
    ).rejects.toBe(abortError);
  });

  it("aborts and classifies a request that exceeds its timeout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string | URL | Request, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => reject(new DOMException("Aborted", "AbortError")),
            { once: true },
          );
        }),
      ),
    );

    const pendingRequest = request(
      "/api/chat",
      { timeoutMs: 25 },
      parseLocalChatResponse,
    );
    const rejection = expect(pendingRequest).rejects.toMatchObject({
      code: "REQUEST_TIMEOUT",
      message: "Сервис не ответил вовремя. Попробуйте ещё раз.",
    });
    await vi.advanceTimersByTimeAsync(25);

    await rejection;
  });

  it("hides FastAPI field paths from the user", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({
          detail: [{
            type: "string_too_short",
            loc: ["body", "history", 0, "text"],
            msg: "String should have at least 1 character",
          }],
        }), { status: 422, headers: { "Content-Type": "application/json" } }),
      ),
    );

    await expect(request("/api/chat", {}, parseLocalChatResponse)).rejects.toMatchObject({
      message: "Не получилось обработать данные. Обновите приложение и попробуйте снова.",
      status: 422,
    });
  });
});
