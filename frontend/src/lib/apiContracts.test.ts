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
        petPatch: null,
        debug: null,
      }),
    ).toEqual({
      reply: "Я здесь",
      moodHint: undefined,
      innerThought: undefined,
      faceHint: undefined,
      petPatch: undefined,
      storyLibraryPatch: undefined,
      debug: undefined,
    });
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
});
