import { afterEach, describe, expect, it, vi } from "vitest";

import {
  appendRecentAmbientReply,
  clearRecentAmbientReplies,
  hasShownAmbientReplyInSession,
  localPetAmbientStorageKey,
  markAmbientReplyShownInSession,
  readRecentAmbientReplies,
} from "./localPetAmbientMemory";

describe("localPetAmbientMemory", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  it("keeps and later persists replies when WebView storage rejects writes", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });

    expect(appendRecentAmbientReply("pet-1", "  Я рядом.  ")).toEqual(["Я рядом."]);
    expect(readRecentAmbientReplies("pet-1")).toEqual(["Я рядом."]);

    setItem.mockRestore();
    expect(readRecentAmbientReplies("pet-1")).toEqual(["Я рядом."]);
    expect(JSON.parse(window.localStorage.getItem("tamagochi:v1:ambient-replies:pet-1") ?? "[]"))
      .toEqual(["Я рядом."]);
  });

  it("normalizes and retains only the thirty newest replies", () => {
    for (let index = 0; index < 32; index += 1) {
      appendRecentAmbientReply("pet-1", `  Реплика   ${index}  `);
    }

    expect(readRecentAmbientReplies("pet-1")).toEqual(
      Array.from({ length: 30 }, (_, index) => `Реплика ${index + 2}`),
    );
  });

  it("drops cached replies that expose the JSON response contract", () => {
    window.localStorage.setItem(
      localPetAmbientStorageKey("pet-json-leak"),
      JSON.stringify([
        "Ого, строгий формат! Что мы будем делать в этом JSON?",
        "Пойдём искать следы у ручья?",
      ]),
    );

    expect(readRecentAmbientReplies("pet-json-leak")).toEqual([
      "Пойдём искать следы у ручья?",
    ]);
  });

  it("keeps a clear tombstone until persistent storage recovers", () => {
    appendRecentAmbientReply("pet-clear", "Старая реплика");
    const removeItem = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });

    clearRecentAmbientReplies("pet-clear");
    expect(readRecentAmbientReplies("pet-clear")).toEqual([]);
    expect(window.localStorage.getItem("tamagochi:v1:ambient-replies:pet-clear")).not.toBeNull();

    removeItem.mockRestore();
    expect(readRecentAmbientReplies("pet-clear")).toEqual([]);
    expect(window.localStorage.getItem("tamagochi:v1:ambient-replies:pet-clear")).toBeNull();
  });

  it("marks one ambient reply for the current browser session", () => {
    expect(hasShownAmbientReplyInSession("pet-session")).toBe(false);
    markAmbientReplyShownInSession("pet-session");
    expect(hasShownAmbientReplyInSession("pet-session")).toBe(true);
  });
});
