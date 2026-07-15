import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  appendDebugPanelEvents,
  clearDebugPanelEvents,
  readDebugPanelEvents,
} from "./debugPanelStorage";

const STORAGE_KEY = "tamagochi:v1:debug-events";

describe("debugPanelStorage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    clearDebugPanelEvents();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps and later persists events after a rejected write", () => {
    appendDebugPanelEvents([{ kind: "prompt", title: "Первый", text: "one" }]);
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage full", "QuotaExceededError");
    });

    appendDebugPanelEvents([{ kind: "memory", title: "Второй", text: "two" }]);
    expect(readDebugPanelEvents().map((event) => event.title)).toEqual([
      "Первый",
      "Второй",
    ]);

    setItem.mockRestore();
    expect(readDebugPanelEvents().map((event) => event.title)).toEqual([
      "Первый",
      "Второй",
    ]);
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]")).toHaveLength(2);
  });

  it("keeps a clear tombstone until a rejected removal succeeds", () => {
    appendDebugPanelEvents([{ kind: "prompt", title: "Старое", text: "old" }]);
    const removeItem = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });

    clearDebugPanelEvents();
    expect(readDebugPanelEvents()).toEqual([]);
    expect(window.localStorage.getItem(STORAGE_KEY)).not.toBeNull();

    removeItem.mockRestore();
    expect(readDebugPanelEvents()).toEqual([]);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("bounds the session and persisted event list", () => {
    appendDebugPanelEvents(
      Array.from({ length: 185 }, (_, index) => ({
        kind: "prompt" as const,
        title: `Событие ${index}`,
        text: `payload ${index}`,
      })),
    );

    const events = readDebugPanelEvents();
    expect(events).toHaveLength(180);
    expect(events[0]?.title).toBe("Событие 5");
  });
});
