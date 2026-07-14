import { afterEach, describe, expect, it, vi } from "vitest";

import {
  parseGeneratePetJobResponse,
  parseInteractiveTravelResponse,
  parseLocalChatResponse,
  parseMemoryExtractionResponse,
  parsePushSnapshotResponse,
} from "./apiContracts";
import { ApiContractError, ApiError, request } from "./apiTransport";

describe("API response contracts", () => {
  afterEach(() => {
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
        travelId: "travel-1",
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

  it("preserves an intentional request abort", async () => {
    const controller = new AbortController();
    const abortError = new DOMException("Aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError));
    controller.abort();

    await expect(
      request("/api/chat", { signal: controller.signal }, parseLocalChatResponse),
    ).rejects.toBe(abortError);
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
