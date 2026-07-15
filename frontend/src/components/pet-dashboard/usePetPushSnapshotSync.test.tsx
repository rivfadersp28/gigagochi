import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PushSnapshotResponse } from "@/lib/apiContracts";
import { markLocalPetSnapshotDirty } from "@/lib/localPetSnapshotRevision";
import type { LocalPetState } from "@/lib/types";

import { usePetPushSnapshotSync } from "./usePetPushSnapshotSync";

const mocks = vi.hoisted(() => ({
  currentPet: null as LocalPetState | null,
  registerPetPushSnapshot: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  canUseTmaApi: () => true,
  registerPetPushSnapshot: (...args: unknown[]) => mocks.registerPetPushSnapshot(...args),
}));

vi.mock("@/lib/localPetMemoryRecall", () => ({
  buildMemorySnapshotContext: () => undefined,
}));

vi.mock("@/lib/localPetMemoryStorage", () => ({
  readLocalPetMemory: () => undefined,
}));

vi.mock("@/lib/localPetStorage", () => ({
  readLocalChatHistory: () => ({ version: 1, messages: [] }),
  readLocalPetState: () => mocks.currentPet,
}));

function petState(petId: string): LocalPetState {
  const now = "2026-07-15T12:00:00.000Z";
  return {
    version: 2,
    petId,
    description: petId,
    createdAt: now,
    updatedAt: now,
    lastInteractionAt: now,
    lastStatsTickAt: now,
    lastStatTickAt: { hunger: now, happiness: now, energy: now },
    stage: "baby",
    mood: "idle",
    stats: { hunger: 100, happiness: 100, energy: 100 },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

function response(patch: Partial<PushSnapshotResponse> = {}): PushSnapshotResponse {
  return {
    registered: true,
    telegramId: 1,
    updatedAt: "2026-07-15T12:00:01.000Z",
    ...patch,
  };
}

describe("usePetPushSnapshotSync", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.currentPet = null;
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("coalesces same-pet mutations into one snapshot per interval", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-15T12:00:00.000Z"));
    mocks.registerPetPushSnapshot.mockResolvedValue(response());
    const callbacks = {
      applyStatsPatch: vi.fn(() => null),
      applyLiteOverlayPatch: vi.fn(() => null),
      applyStoryLibraryPatch: vi.fn(() => null),
      applyRecentStoryEventsPatch: vi.fn(() => null),
      resetPet: vi.fn(),
    };
    const initialPet = petState("pet-a");

    const { rerender } = renderHook(
      ({ pet }: { pet: LocalPetState }) => {
        mocks.currentPet = pet;
        return usePetPushSnapshotSync({
          status: "ready",
          pet,
          petId: pet.petId,
          recentAmbientRepliesRef: { current: [] },
          ...callbacks,
        });
      },
      { initialProps: { pet: initialPet } },
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(1);

    rerender({
      pet: { ...initialPet, updatedAt: "2026-07-15T12:00:01.000Z" },
    });
    rerender({
      pet: { ...initialPet, updatedAt: "2026-07-15T12:00:02.000Z" },
    });
    expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * 60_000 - 1);
    });
    expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(2);
    expect(mocks.registerPetPushSnapshot.mock.calls[1]?.[0].updatedAt).toBe(
      "2026-07-15T12:00:02.000Z",
    );
  });

  it("retries a failed first snapshot after backoff instead of waiting ten minutes", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-15T12:00:00.000Z"));
    mocks.registerPetPushSnapshot
      .mockRejectedValueOnce(new Error("temporary network failure"))
      .mockResolvedValueOnce(response());
    const pet = petState("pet-retry");
    const callbacks = {
      applyStatsPatch: vi.fn(() => null),
      applyLiteOverlayPatch: vi.fn(() => null),
      applyStoryLibraryPatch: vi.fn(() => null),
      applyRecentStoryEventsPatch: vi.fn(() => null),
      resetPet: vi.fn(),
    };

    renderHook(() => {
      mocks.currentPet = pet;
      return usePetPushSnapshotSync({
        status: "ready",
        pet,
        petId: pet.petId,
        recentAmbientRepliesRef: { current: [] },
        ...callbacks,
      });
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(14_999);
    });
    expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(2);
  });

  it("syncs history or memory dirtiness even when pet.updatedAt did not change", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-15T12:00:00.000Z"));
    mocks.registerPetPushSnapshot.mockResolvedValue(response());
    const pet = petState("pet-dirty-snapshot");
    const recentAmbientRepliesRef = { current: [] as string[] };
    const callbacks = {
      applyStatsPatch: vi.fn(() => null),
      applyLiteOverlayPatch: vi.fn(() => null),
      applyStoryLibraryPatch: vi.fn(() => null),
      applyRecentStoryEventsPatch: vi.fn(() => null),
      resetPet: vi.fn(),
    };

    renderHook(() => {
      mocks.currentPet = pet;
      return usePetPushSnapshotSync({
        status: "ready",
        pet,
        petId: pet.petId,
        recentAmbientRepliesRef,
        ...callbacks,
      });
    });
    await act(async () => {
      await Promise.resolve();
    });
    expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(1);

    markLocalPetSnapshotDirty(pet.petId);
    recentAmbientRepliesRef.current = ["Свежая реплика"];
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(10 * 60_000);
    });

    expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(2);
    expect(mocks.registerPetPushSnapshot.mock.calls[1]?.[2]).toMatchObject({
      recentAmbientReplies: ["Свежая реплика"],
    });
  });

  it("ignores pet A patches when its request resolves after pet B", async () => {
    const petAResponse = deferred<PushSnapshotResponse>();
    const petBResponse = deferred<PushSnapshotResponse>();
    mocks.registerPetPushSnapshot.mockImplementation((pet: LocalPetState) =>
      pet.petId === "pet-a" ? petAResponse.promise : petBResponse.promise,
    );
    const applyStatsPatch = vi.fn(() => null);
    const applyLiteOverlayPatch = vi.fn(() => null);
    const applyStoryLibraryPatch = vi.fn(() => null);
    const applyRecentStoryEventsPatch = vi.fn(() => null);
    const resetPet = vi.fn();
    const recentAmbientRepliesRef = { current: [] as string[] };

    const { rerender } = renderHook(
      ({ pet }: { pet: LocalPetState }) => {
        mocks.currentPet = pet;
        return usePetPushSnapshotSync({
          status: "ready",
          pet,
          petId: pet.petId,
          recentAmbientRepliesRef,
          applyStatsPatch,
          applyLiteOverlayPatch,
          applyStoryLibraryPatch,
          applyRecentStoryEventsPatch,
          resetPet,
        });
      },
      { initialProps: { pet: petState("pet-a") } },
    );

    await waitFor(() => expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(1));
    rerender({ pet: petState("pet-b") });
    await waitFor(() => expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(2));

    await act(async () => {
      petBResponse.resolve(response({
        statsPatch: { stats: { energy: 71 } },
        liteOverlayPatch: { facts: ["B"] },
        storyLibraryPatch: { bricks: ["B"] },
        recentStoryEventsPatch: { events: ["B"] },
      }));
      await petBResponse.promise;
    });

    expect(applyStatsPatch).toHaveBeenCalledWith(
      { stats: { energy: 71 } },
      "pet-b",
      petState("pet-b").lastStatTickAt,
    );
    expect(applyLiteOverlayPatch).toHaveBeenCalledWith({ facts: ["B"] }, "pet-b");
    expect(applyStoryLibraryPatch).toHaveBeenCalledWith({ bricks: ["B"] }, "pet-b");
    expect(applyRecentStoryEventsPatch).toHaveBeenCalledWith({ events: ["B"] }, "pet-b");

    await act(async () => {
      petAResponse.resolve(response({ statsPatch: { stats: { energy: 1 } } }));
      await petAResponse.promise;
    });

    expect(applyStatsPatch).toHaveBeenCalledTimes(1);
    expect(resetPet).not.toHaveBeenCalled();
  });

  it("ignores a late reset for pet A after switching to pet B", async () => {
    const petAResponse = deferred<PushSnapshotResponse>();
    const petBResponse = deferred<PushSnapshotResponse>();
    mocks.registerPetPushSnapshot.mockImplementation((pet: LocalPetState) =>
      pet.petId === "pet-a" ? petAResponse.promise : petBResponse.promise,
    );
    const resetPet = vi.fn();
    const callbacks = {
      applyStatsPatch: vi.fn(() => null),
      applyLiteOverlayPatch: vi.fn(() => null),
      applyStoryLibraryPatch: vi.fn(() => null),
      applyRecentStoryEventsPatch: vi.fn(() => null),
    };

    const { rerender } = renderHook(
      ({ pet }: { pet: LocalPetState }) => {
        mocks.currentPet = pet;
        return usePetPushSnapshotSync({
          status: "ready",
          pet,
          petId: pet.petId,
          recentAmbientRepliesRef: { current: [] },
          ...callbacks,
          resetPet,
        });
      },
      { initialProps: { pet: petState("pet-a") } },
    );

    await waitFor(() => expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(1));
    rerender({ pet: petState("pet-b") });
    await waitFor(() => expect(mocks.registerPetPushSnapshot).toHaveBeenCalledTimes(2));

    await act(async () => {
      petBResponse.resolve(response());
      await petBResponse.promise;
      petAResponse.resolve(response({ resetPet: true }));
      await petAResponse.promise;
    });

    expect(resetPet).not.toHaveBeenCalled();
  });
});
