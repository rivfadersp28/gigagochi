import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  LocalPetMutationLockError,
  withLocalPetMutationLock,
} from "./localPetMutationLock";

function replaceNavigatorLocks(value: unknown) {
  const descriptor = Object.getOwnPropertyDescriptor(navigator, "locks");
  Object.defineProperty(navigator, "locks", { configurable: true, value });
  return () => {
    if (descriptor) {
      Object.defineProperty(navigator, "locks", descriptor);
    } else {
      Reflect.deleteProperty(navigator, "locks");
    }
  };
}

describe("local pet cross-tab mutation lock", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("uses an exclusive scoped Web Lock", async () => {
    const request = vi.fn(async (
      _name: string,
      _options: LockOptions,
      callback: () => Promise<string>,
    ) => callback());
    const restoreLocks = replaceNavigatorLocks({ request });
    try {
      await expect(withLocalPetMutationLock(
        "pet-1",
        "conversation",
        async ({ assertOwned }) => {
          assertOwned();
          return "ok";
        },
      )).resolves.toBe("ok");
      expect(request).toHaveBeenCalledWith(
        "tamagochi:v1:pet-mutation:conversation:pet-1",
        expect.objectContaining({ mode: "exclusive", signal: expect.any(AbortSignal) }),
        expect.any(Function),
      );
    } finally {
      restoreLocks();
    }
  });

  it("keeps the single-view flow working when Web Locks are unavailable", async () => {
    const restoreLocks = replaceNavigatorLocks(undefined);
    const mutation = vi.fn(({ assertOwned }: { assertOwned: () => void }) => {
      assertOwned();
      return "fallback";
    });
    try {
      await expect(withLocalPetMutationLock(
        "pet-1",
        "conversation",
        mutation,
      )).resolves.toBe("fallback");
      expect(mutation).toHaveBeenCalledOnce();
    } finally {
      restoreLocks();
    }
  });

  it("bounds Web Lock acquisition and never runs an unowned mutation", async () => {
    vi.useFakeTimers();
    const mutation = vi.fn();
    const request = vi.fn((
      _name: string,
      options: LockOptions,
      callback: () => unknown,
    ) => {
      void callback;
      return new Promise<never>((_resolve, reject) => {
        options.signal?.addEventListener("abort", () => reject(options.signal?.reason));
      });
    });
    const restoreLocks = replaceNavigatorLocks({ request });
    const result = withLocalPetMutationLock("pet-1", "conversation", mutation);
    const rejection = expect(result).rejects.toBeInstanceOf(LocalPetMutationLockError);
    try {
      await vi.advanceTimersByTimeAsync(10_000);
      await rejection;
      expect(mutation).not.toHaveBeenCalled();
    } finally {
      restoreLocks();
    }
  });

});
