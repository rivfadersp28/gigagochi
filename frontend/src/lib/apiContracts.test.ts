import { afterEach, describe, expect, it, vi } from "vitest";

import {
  parseGeneratePetJobResponse,
  parseLocalChatResponse,
  parseMemoryExtractionResponse,
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

  it("shows the field path for FastAPI validation errors", async () => {
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
      message: "Некорректное поле history.0.text: значение не должно быть пустым.",
      status: 422,
    });
  });
});
