import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearLocalInteractiveTravel,
  LocalInteractiveTravelMutationLockError,
  readLocalInteractiveTravel,
  type LocalInteractiveTravel,
  withLocalInteractiveTravelMutationLock,
  writeLocalInteractiveTravel,
  writeLocalInteractiveTravelDurably,
} from "./localInteractiveTravel";
import type { InteractiveTravelState } from "./types";

function completedTravel(): InteractiveTravelState {
  return {
    travelId: "interactive-travel-seven-parts",
    generatedAt: "2026-07-14T12:00:00Z",
    destination: "облачный город",
    overallTitle: "Часы облачного города",
    arcPlan: { goal: "Запустить часы" },
    parts: Array.from({ length: 7 }, (_, index) => ({
      partNumber: index + 1,
      title: `Часть ${index + 1}`,
      storyText: `Ситуация ${index + 1}.`,
      transition:
        index === 0
          ? undefined
          : {
              elapsedHours: 4,
              summary: "За несколько часов конфликт изменился.",
            },
      challenge: "Что делать дальше?",
      actionSuggestions: ["Идти дальше", "Осмотреться", "Позвать на помощь"],
      answer: `Ответ ${index + 1}`,
      result: {
        text: `Результат ${index + 1}.`,
        adviceAssessment: "helpful" as const,
        reaction: "Так и поступлю!",
        reactionTone: "determined" as const,
        consequence: "История изменилась.",
        outcomeValence: "positive" as const,
        statImpacts: [],
      },
    })),
    completed: true,
    outcomeValence: "positive",
  };
}

function inProgressTravel(): InteractiveTravelState {
  const completed = completedTravel();
  return {
    ...completed,
    travelId: "interactive-travel-in-progress",
    parts: completed.parts.slice(0, 3).map((part, index) =>
      index === 2
        ? {
            ...part,
            storyText: "Первая фраза. Вторая фраза.",
            answer: undefined,
            result: undefined,
          }
        : part,
    ),
    completed: false,
    outcomeValence: undefined,
  };
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

function replaceNavigatorLocks(value: unknown) {
  const descriptor = Object.getOwnPropertyDescriptor(navigator, "locks");
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value,
  });
  return () => {
    if (descriptor) {
      Object.defineProperty(navigator, "locks", descriptor);
    } else {
      Reflect.deleteProperty(navigator, "locks");
    }
  };
}

describe("local interactive travel storage", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    clearLocalInteractiveTravel("pet-1");
  });

  it("keeps applied results through the seventh part and ignores invalid indexes", () => {
    writeLocalInteractiveTravel("pet-1", {
      travel: completedTravel(),
      appliedResultParts: [1, 5, 6, 7],
      presentation: { phase: "completed", partNumber: 7, portionIndex: 0 },
    });

    expect(readLocalInteractiveTravel("pet-1")?.appliedResultParts).toEqual([1, 5, 6, 7]);
  });

  it("keeps applied results only for existing resolved parts", () => {
    writeLocalInteractiveTravel("pet-1", {
      travel: inProgressTravel(),
      appliedResultParts: [7, 3, 2, 1, 2],
      presentation: { phase: "choice", partNumber: 3, portionIndex: 0 },
    });

    expect(readLocalInteractiveTravel("pet-1")?.appliedResultParts).toEqual([1, 2]);
  });

  it.each([
    { phase: "introReaction" as const, partNumber: 3 },
    { phase: "departureWait" as const, partNumber: 1 },
    { phase: "story" as const, partNumber: 1 },
    { phase: "result" as const, partNumber: 3 },
    { phase: "choice" as const, partNumber: 2 },
    { phase: "completed" as const, partNumber: 3 },
  ])("does not restore impossible $phase presentation", ({ phase, partNumber }) => {
    writeLocalInteractiveTravel("pet-1", {
      travel: inProgressTravel(),
      appliedResultParts: [],
      presentation: { phase, partNumber, portionIndex: 0 },
    });

    expect(readLocalInteractiveTravel("pet-1")?.presentation).toEqual({
      phase: "choice",
      partNumber: 3,
      portionIndex: 0,
    });
  });

  it("restores only the latest resolved result", () => {
    writeLocalInteractiveTravel("pet-1", {
      travel: inProgressTravel(),
      appliedResultParts: [],
      presentation: { phase: "result", partNumber: 1, portionIndex: 0 },
    });

    expect(readLocalInteractiveTravel("pet-1")?.presentation).toEqual({
      phase: "choice",
      partNumber: 3,
      portionIndex: 0,
    });
  });

  it.each([
    {
      phase: "story" as const,
      partNumber: 3,
      portionIndex: 99,
      expectedPortionIndex: 1,
    },
    {
      phase: "result" as const,
      partNumber: 2,
      portionIndex: 99,
      expectedPortionIndex: 1,
    },
    {
      phase: "choice" as const,
      partNumber: 3,
      portionIndex: 99,
      expectedPortionIndex: 0,
    },
  ])(
    "bounds the $phase portion index to $expectedPortionIndex",
    ({ phase, partNumber, portionIndex, expectedPortionIndex }) => {
      writeLocalInteractiveTravel("pet-1", {
        travel: inProgressTravel(),
        appliedResultParts: [],
        presentation: { phase, partNumber, portionIndex },
      });

      expect(readLocalInteractiveTravel("pet-1")?.presentation.portionIndex).toBe(
        expectedPortionIndex,
      );
    },
  );

  it("does not restore an in-progress phase for a completed travel", () => {
    writeLocalInteractiveTravel("pet-1", {
      travel: completedTravel(),
      appliedResultParts: [],
      presentation: { phase: "choice", partNumber: 7, portionIndex: 0 },
    });

    expect(readLocalInteractiveTravel("pet-1")?.presentation).toEqual({
      phase: "completed",
      partNumber: 7,
      portionIndex: 0,
    });
  });

  it("does not restore an incompatible v1 story", () => {
    window.localStorage.setItem(
      "gigagochi.interactive-travel.v1:pet-1",
      JSON.stringify({ travel: completedTravel(), appliedImpactParts: [1, 2, 3] }),
    );

    expect(readLocalInteractiveTravel("pet-1")).toBeNull();
  });

  it("migrates a v2 story to a safe presentation phase", () => {
    window.localStorage.setItem(
      "gigagochi.interactive-travel.v2:pet-1",
      JSON.stringify({ travel: completedTravel(), appliedResultParts: [1, 2, 3] }),
    );

    expect(readLocalInteractiveTravel("pet-1")?.presentation).toEqual({
      phase: "completed",
      partNumber: 7,
      portionIndex: 0,
    });
  });

  it("keeps an active travel across route reads when localStorage is blocked", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });
    const localTravel: LocalInteractiveTravel = {
      travel: inProgressTravel(),
      appliedResultParts: [1, 2],
      presentation: { phase: "choice", partNumber: 3, portionIndex: 0 },
    };

    writeLocalInteractiveTravel("pet-1", localTravel);
    expect(readLocalInteractiveTravel("pet-1")).toEqual(localTravel);
    getItem.mockRestore();
    setItem.mockRestore();

    expect(readLocalInteractiveTravel("pet-1")).toEqual(localTravel);
    expect(window.localStorage.getItem("gigagochi.interactive-travel.v3:pet-1")).not.toBeNull();
  });

  it("does not expose a memory-only travel when a durable write fails", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });
    const localTravel: LocalInteractiveTravel = {
      travel: inProgressTravel(),
      appliedResultParts: [1, 2],
      presentation: { phase: "choice", partNumber: 3, portionIndex: 0 },
    };

    expect(writeLocalInteractiveTravelDurably("durable-pet", localTravel)).toBeNull();
    expect(readLocalInteractiveTravel("durable-pet")).toBeNull();

    setItem.mockRestore();
    clearLocalInteractiveTravel("durable-pet");
  });

  it("does not roll back a newer value written by another tab", () => {
    const key = "gigagochi.interactive-travel.v3:concurrent-pet";
    const previous: LocalInteractiveTravel = {
      travel: { ...inProgressTravel(), travelId: "interactive-travel-previous" },
      appliedResultParts: [1],
      presentation: { phase: "choice", partNumber: 3, portionIndex: 0 },
    };
    const attempted: LocalInteractiveTravel = {
      ...previous,
      travel: { ...previous.travel, travelId: "interactive-travel-attempted" },
    };
    const concurrent: LocalInteractiveTravel = {
      ...previous,
      travel: { ...previous.travel, travelId: "interactive-travel-concurrent" },
    };
    const concurrentRaw = JSON.stringify(concurrent);
    window.localStorage.setItem(key, JSON.stringify(previous));
    const originalGetItem = Storage.prototype.getItem;
    let reads = 0;
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (
      this: Storage,
      requestedKey: string,
    ) {
      if (requestedKey === key) {
        reads += 1;
        if (reads === 2) {
          Storage.prototype.setItem.call(this, key, concurrentRaw);
        }
      }
      return originalGetItem.call(this, requestedKey);
    });

    expect(writeLocalInteractiveTravelDurably("concurrent-pet", attempted)).toBeNull();
    expect(originalGetItem.call(window.localStorage, key)).toBe(concurrentRaw);
    clearLocalInteractiveTravel("concurrent-pet");
  });

  it("does not resurrect a travel after persistent removal fails", () => {
    writeLocalInteractiveTravel("pet-1", {
      travel: inProgressTravel(),
      appliedResultParts: [1, 2],
      presentation: { phase: "choice", partNumber: 3, portionIndex: 0 },
    });
    const removeItem = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });

    clearLocalInteractiveTravel("pet-1");
    expect(readLocalInteractiveTravel("pet-1")).toBeNull();
    expect(window.localStorage.getItem("gigagochi.interactive-travel.v3:pet-1")).not.toBeNull();
    removeItem.mockRestore();

    expect(readLocalInteractiveTravel("pet-1")).toBeNull();
    expect(window.localStorage.getItem("gigagochi.interactive-travel.v3:pet-1")).toBeNull();
  });

  it("bounds the in-memory fallback cache when storage remains blocked", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });
    const localTravel: LocalInteractiveTravel = {
      travel: inProgressTravel(),
      appliedResultParts: [1, 2],
      presentation: { phase: "choice", partNumber: 3, portionIndex: 0 },
    };

    for (let index = 0; index < 70; index += 1) {
      writeLocalInteractiveTravel(`bounded-pet-${index}`, localTravel);
    }

    expect(readLocalInteractiveTravel("bounded-pet-0")).toBeNull();
    expect(readLocalInteractiveTravel("bounded-pet-69")).toEqual(localTravel);
    setItem.mockRestore();
    for (let index = 0; index < 70; index += 1) {
      clearLocalInteractiveTravel(`bounded-pet-${index}`);
    }
  });
});

describe("interactive travel cross-tab mutation lock", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("uses the browser exclusive lock when Web Locks is available", async () => {
    const request = vi.fn(async (
      _name: string,
      _options: LockOptions,
      callback: () => Promise<string>,
    ) => callback());
    const restoreLocks = replaceNavigatorLocks({ request });

    try {
      await expect(
        withLocalInteractiveTravelMutationLock("pet-web-lock", async ({ assertOwned }) => {
          assertOwned();
          return "committed";
        }),
      ).resolves.toBe("committed");
      expect(request).toHaveBeenCalledWith(
        "tamagochi:v1:pet-mutation:travel:pet-web-lock",
        expect.objectContaining({ mode: "exclusive", signal: expect.any(AbortSignal) }),
        expect.any(Function),
      );
    } finally {
      restoreLocks();
    }
  });

  it("bounds Web Lock acquisition without running an unowned mutation", async () => {
    vi.useFakeTimers();
    const mutation = vi.fn();
    const request = vi.fn((
      _name: string,
      options: LockOptions,
      _callback: () => unknown,
    ) => {
      void _callback;
      return new Promise<never>((_resolve, reject) => {
        options.signal?.addEventListener("abort", () => reject(options.signal?.reason));
      });
    });
    const restoreLocks = replaceNavigatorLocks({ request });
    const result = withLocalInteractiveTravelMutationLock("pet-web-timeout", mutation);
    const rejection = expect(result).rejects.toBeInstanceOf(
      LocalInteractiveTravelMutationLockError,
    );

    try {
      await vi.advanceTimersByTimeAsync(10_000);
      await rejection;
      expect(mutation).not.toHaveBeenCalled();
    } finally {
      restoreLocks();
    }
  });

  it("stops the acquisition timer after Web Lock ownership is granted", async () => {
    vi.useFakeTimers();
    const gate = deferred();
    const expected = new Error("provider failed after lock acquisition");
    let receivedSignal: AbortSignal | undefined;
    const request = vi.fn(async (
      _name: string,
      options: LockOptions,
      callback: () => Promise<void>,
    ) => {
      receivedSignal = options.signal;
      return callback();
    });
    const restoreLocks = replaceNavigatorLocks({ request });
    const result = withLocalInteractiveTravelMutationLock(
      "pet-web-owned",
      async () => {
        await gate.promise;
        throw expected;
      },
    );
    const rejection = expect(result).rejects.toBe(expected);

    try {
      await vi.advanceTimersByTimeAsync(10_000);
      expect(receivedSignal?.aborted).toBe(false);
      gate.resolve();
      await rejection;
    } finally {
      gate.resolve();
      restoreLocks();
    }
  });

  it("uses the honest LWW path when Web Locks are unavailable", async () => {
    const restoreLocks = replaceNavigatorLocks(undefined);
    const mutation = vi.fn(({ assertOwned }) => {
      assertOwned();
      return "committed";
    });

    try {
      await expect(
        withLocalInteractiveTravelMutationLock("pet-lww", mutation),
      ).resolves.toBe("committed");
      expect(mutation).toHaveBeenCalledOnce();
      expect(window.localStorage.length).toBe(0);
    } finally {
      restoreLocks();
    }
  });
});
