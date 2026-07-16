import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LocalPetAssetSet } from "@/lib/types";

import {
  shouldPollBackgroundAssets,
  usePetBackgroundAssets,
} from "./usePetBackgroundAssets";

const mocks = vi.hoisted(() => ({
  refreshPetBackgroundAssets: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    code: string;

    constructor(code: string, message: string) {
      super(message);
      this.code = code;
    }
  },
  refreshPetBackgroundAssets: (...args: unknown[]) =>
    mocks.refreshPetBackgroundAssets(...args),
}));

function assetSet(assetSetId: string, updatedAt: string): LocalPetAssetSet {
  const moods = {
    idle: `/${assetSetId}-idle.png`,
    happy: `/${assetSetId}-happy.png`,
    hungry: `/${assetSetId}-hungry.png`,
    sad: `/${assetSetId}-sad.png`,
  };
  return {
    assetSetId,
    generatedAt: "2026-07-15T12:00:00.000Z",
    generationJobId: `job-${assetSetId}`,
    backgroundGenerationStatus: "running",
    backgroundGenerationUpdatedAt: updatedAt,
    images: {
      baby: { ...moods },
      teen: { ...moods },
      adult: { ...moods },
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

describe("usePetBackgroundAssets", () => {
  let online = true;

  beforeEach(() => {
    vi.clearAllMocks();
    online = true;
    Object.defineProperty(navigator, "onLine", {
      configurable: true,
      get: () => online,
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("does not poll a terminal job when optional comparison assets are disabled", () => {
    const terminal = {
      ...assetSet("assets-terminal", "2026-07-15T12:01:00.000Z"),
      backgroundGenerationStatus: "succeeded" as const,
      backgroundGenerationPhase: "completed" as const,
      kandinskyAssets: undefined,
      comparisonGenerationError: undefined,
    };

    expect(shouldPollBackgroundAssets(terminal)).toBe(false);
  });

  it("reconciles newly attached Kandinsky assets for a completed local job", async () => {
    const currentAssets = {
      ...assetSet("assets-completed", "2026-07-15T12:01:00.000Z"),
      backgroundGenerationStatus: "succeeded" as const,
      backgroundGenerationPhase: "completed" as const,
    };
    const kandinskyAssets = {
      ...assetSet("assets-kandinsky", "2026-07-15T12:02:00.000Z"),
      generationJobId: undefined,
      backgroundGenerationStatus: undefined,
      backgroundGenerationUpdatedAt: undefined,
    };
    const refreshedAssets = {
      ...currentAssets,
      backgroundGenerationUpdatedAt: "2026-07-15T12:03:00.000Z",
      kandinskyAssets,
    };
    const applyGeneratedAssets = vi.fn();
    mocks.refreshPetBackgroundAssets.mockResolvedValue(refreshedAssets);

    renderHook(() =>
      usePetBackgroundAssets({
        assetSet: currentAssets,
        petId: "pet-completed",
        applyGeneratedAssets,
        derivedAssetsEnabled: true,
      }),
    );

    await waitFor(() => expect(mocks.refreshPetBackgroundAssets).toHaveBeenCalledTimes(1));
    expect(applyGeneratedAssets).toHaveBeenCalledWith(
      refreshedAssets,
      "pet-completed",
      currentAssets,
    );
  });

  it("checks a completed job without comparison assets only once", async () => {
    vi.useFakeTimers();
    const currentAssets = {
      ...assetSet("assets-legacy", "2026-07-15T12:01:00.000Z"),
      backgroundGenerationStatus: "succeeded" as const,
      backgroundGenerationPhase: "completed" as const,
    };
    mocks.refreshPetBackgroundAssets.mockResolvedValue(currentAssets);

    renderHook(() =>
      usePetBackgroundAssets({
        assetSet: currentAssets,
        petId: "pet-legacy",
        applyGeneratedAssets: vi.fn(),
        derivedAssetsEnabled: true,
      }),
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.refreshPetBackgroundAssets).toHaveBeenCalledTimes(1);

    await act(async () => vi.advanceTimersByTimeAsync(120_000));
    expect(mocks.refreshPetBackgroundAssets).toHaveBeenCalledTimes(1);
  });

  it("ignores pet A assets resolved after pet B and tags the current write", async () => {
    const petAResponse = deferred<LocalPetAssetSet>();
    const petBResponse = deferred<LocalPetAssetSet>();
    mocks.refreshPetBackgroundAssets.mockImplementation((current: LocalPetAssetSet) =>
      current.assetSetId === "assets-a" ? petAResponse.promise : petBResponse.promise,
    );
    const applyGeneratedAssets = vi.fn();
    const assetA = assetSet("assets-a", "2026-07-15T12:00:00.000Z");
    const assetB = assetSet("assets-b", "2026-07-15T12:00:00.000Z");
    const refreshedA = {
      ...assetA,
      backgroundGenerationStatus: "succeeded" as const,
      backgroundGenerationUpdatedAt: "2026-07-15T12:00:01.000Z",
    };
    const refreshedB = {
      ...assetB,
      backgroundGenerationStatus: "succeeded" as const,
      backgroundGenerationUpdatedAt: "2026-07-15T12:00:02.000Z",
    };

    const { rerender } = renderHook(
      ({ assetSet: currentAssets, petId }: { assetSet: LocalPetAssetSet; petId: string }) =>
        usePetBackgroundAssets({
          assetSet: currentAssets,
          petId,
          applyGeneratedAssets,
          derivedAssetsEnabled: true,
        }),
      { initialProps: { assetSet: assetA, petId: "pet-a" } },
    );

    await waitFor(() => expect(mocks.refreshPetBackgroundAssets).toHaveBeenCalledTimes(1));
    rerender({ assetSet: assetB, petId: "pet-b" });
    await waitFor(() => expect(mocks.refreshPetBackgroundAssets).toHaveBeenCalledTimes(2));

    await act(async () => {
      petBResponse.resolve(refreshedB);
      await petBResponse.promise;
    });
    expect(applyGeneratedAssets).toHaveBeenCalledWith(refreshedB, "pet-b", assetB);

    await act(async () => {
      petAResponse.resolve(refreshedA);
      await petAResponse.promise;
    });
    expect(applyGeneratedAssets).toHaveBeenCalledTimes(1);
  });

  it("pauses while offline and retries immediately after reconnecting", async () => {
    online = false;
    const currentAssets = assetSet("assets-offline", "2026-07-15T12:00:00.000Z");
    mocks.refreshPetBackgroundAssets.mockResolvedValue(currentAssets);

    renderHook(() =>
      usePetBackgroundAssets({
        assetSet: currentAssets,
        petId: "pet-offline",
        applyGeneratedAssets: vi.fn(),
        derivedAssetsEnabled: true,
      }),
    );

    await act(async () => Promise.resolve());
    expect(mocks.refreshPetBackgroundAssets).not.toHaveBeenCalled();

    online = true;
    act(() => window.dispatchEvent(new Event("online")));
    await waitFor(() => expect(mocks.refreshPetBackgroundAssets).toHaveBeenCalledTimes(1));
  });

  it("backs off repeated unchanged job polls", async () => {
    vi.useFakeTimers();
    const currentAssets = assetSet("assets-running", "2026-07-15T12:00:00.000Z");
    mocks.refreshPetBackgroundAssets.mockResolvedValue(currentAssets);

    renderHook(() =>
      usePetBackgroundAssets({
        assetSet: currentAssets,
        petId: "pet-running",
        applyGeneratedAssets: vi.fn(),
        derivedAssetsEnabled: true,
      }),
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.refreshPetBackgroundAssets).toHaveBeenCalledTimes(1);

    await act(async () => vi.advanceTimersByTimeAsync(2_000));
    expect(mocks.refreshPetBackgroundAssets).toHaveBeenCalledTimes(2);

    await act(async () => vi.advanceTimersByTimeAsync(3_999));
    expect(mocks.refreshPetBackgroundAssets).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(mocks.refreshPetBackgroundAssets).toHaveBeenCalledTimes(3);
  });

  it("aborts an in-flight refresh on cleanup", async () => {
    const currentAssets = assetSet("assets-abort", "2026-07-15T12:00:00.000Z");
    const response = deferred<LocalPetAssetSet>();
    let signal: AbortSignal | undefined;
    mocks.refreshPetBackgroundAssets.mockImplementation(
      (_current: LocalPetAssetSet, options: { signal?: AbortSignal }) => {
        signal = options.signal;
        return response.promise;
      },
    );

    const { unmount } = renderHook(() =>
      usePetBackgroundAssets({
        assetSet: currentAssets,
        petId: "pet-abort",
        applyGeneratedAssets: vi.fn(),
        derivedAssetsEnabled: true,
      }),
    );

    await waitFor(() => expect(signal).toBeDefined());
    unmount();
    expect(signal?.aborted).toBe(true);
  });
});
