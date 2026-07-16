import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { unregisterPetPushSnapshot } from "./api";
import {
  PET_STATE_STORAGE_KEY,
  readLocalPetState,
  resetLocalPetState,
  writeLocalPetState,
} from "./localPetStorage";
import {
  clearPendingPushSnapshotReset,
  flushPendingPushSnapshotResets,
  PENDING_PUSH_SNAPSHOT_RESET_STORAGE_KEY,
  pendingPushSnapshotResetPetIds,
} from "./pendingPushSnapshotReset";
import type { InteractiveTravelPart, LocalPetAssetSet, LocalPetState } from "./types";
import { useLocalPetState } from "./useLocalPetState";

vi.mock("./api", () => ({
  unregisterPetPushSnapshot: vi.fn(),
}));

const unregisterPushSnapshotMock = vi.mocked(unregisterPetPushSnapshot);

function petState(petId: string): LocalPetState {
  const now = new Date().toISOString();
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

function assetSet(assetSetId: string): LocalPetAssetSet {
  const moods = {
    idle: `/${assetSetId}-idle.png`,
    happy: `/${assetSetId}-happy.png`,
    hungry: `/${assetSetId}-hungry.png`,
    sad: `/${assetSetId}-sad.png`,
  };
  return {
    assetSetId,
    generatedAt: "2026-07-15T12:00:00.000Z",
    images: {
      baby: { ...moods },
      teen: { ...moods },
      adult: { ...moods },
    },
  };
}

function resolvedTravelPart(partNumber = 1): InteractiveTravelPart {
  return {
    partNumber,
    title: "Мост",
    storyText: "Питомец перешёл мост.",
    transition:
      partNumber === 1
        ? undefined
        : { elapsedHours: 4, summary: "Прошло четыре часа." },
    challenge: "Что дальше?",
    actionSuggestions: [],
    answer: "Идти",
    result: {
      text: "Получилось.",
      adviceAssessment: "helpful",
      reaction: "Ура!",
      reactionTone: "enthusiastic",
      consequence: "Путь открыт.",
      outcomeValence: "positive",
      statImpacts: [{ stat: "hunger", amount: 4, reason: "Нашёл яблоко." }],
    },
  };
}

describe("useLocalPetState snapshot CAS", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetLocalPetState();
    window.localStorage.clear();
    for (const petId of pendingPushSnapshotResetPetIds()) {
      clearPendingPushSnapshotReset(petId);
    }
    unregisterPushSnapshotMock.mockReset();
    unregisterPushSnapshotMock.mockResolvedValue({ unregistered: true, petId: "pet-a" });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("does not patch or reset pet B with a response expected for pet A", () => {
    const petB = writeLocalPetState(petState("pet-b"));
    const { result } = renderHook(() => useLocalPetState());

    act(() => {
      expect(
        result.current.applyStatsPatch({ stats: { energy: 1 } }, "pet-a"),
      ).toBeNull();
      result.current.reset("pet-a");
    });

    expect(readLocalPetState()).toEqual(petB);
  });

  it("durably unregisters a reset pet from push snapshots", async () => {
    writeLocalPetState(petState("pet-a"));
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });

    await act(async () => {
      result.current.reset("pet-a");
      expect(pendingPushSnapshotResetPetIds()).toEqual(["pet-a"]);
      await flushPendingPushSnapshotResets();
    });

    expect(readLocalPetState()).toBeNull();
    expect(unregisterPushSnapshotMock).toHaveBeenCalledWith("pet-a", { keepalive: true });
    expect(pendingPushSnapshotResetPetIds()).toEqual([]);
  });

  it("keeps an offline push unregister marker after the local reset", async () => {
    unregisterPushSnapshotMock.mockRejectedValue(new Error("offline"));
    writeLocalPetState(petState("pet-a"));
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });

    await act(async () => {
      result.current.reset("pet-a");
      await flushPendingPushSnapshotResets();
    });

    expect(readLocalPetState()).toBeNull();
    expect(pendingPushSnapshotResetPetIds()).toEqual(["pet-a"]);
  });

  it("does not remove a pet when its push reset cannot be persisted first", () => {
    writeLocalPetState(petState("pet-a"));
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });
    const nativeSetItem = window.localStorage.setItem.bind(window.localStorage);
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation((key, value) => {
      if (key === PENDING_PUSH_SNAPSHOT_RESET_STORAGE_KEY) {
        throw new DOMException("blocked", "QuotaExceededError");
      }
      nativeSetItem(key, value);
    });

    act(() => {
      result.current.reset("pet-a");
    });

    expect(readLocalPetState()?.petId).toBe("pet-a");
    expect(result.current.error).toMatch(/Не удалось сохранить прогресс/);
    setItem.mockRestore();
  });

  it("does not replace a pet when its push reset cannot be persisted first", () => {
    writeLocalPetState(petState("pet-a"));
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });
    const nativeSetItem = window.localStorage.setItem.bind(window.localStorage);
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation((key, value) => {
      if (key === PENDING_PUSH_SNAPSHOT_RESET_STORAGE_KEY) {
        throw new DOMException("blocked", "QuotaExceededError");
      }
      nativeSetItem(key, value);
    });

    let replacement: LocalPetState | null = petState("unexpected");
    act(() => {
      replacement = result.current.createDurably("новый питомец");
    });

    expect(replacement).toBeNull();
    expect(readLocalPetState()?.petId).toBe("pet-a");
    expect(result.current.error).toMatch(/Не удалось сохранить прогресс/);
    setItem.mockRestore();
  });

  it("unregisters the previous push snapshot when create replaces a pet", async () => {
    writeLocalPetState(petState("pet-a"));
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });

    await act(async () => {
      result.current.create("новый питомец");
      await flushPendingPushSnapshotResets();
    });

    expect(readLocalPetState()?.petId).not.toBe("pet-a");
    expect(unregisterPushSnapshotMock).toHaveBeenCalledWith("pet-a", { keepalive: true });
    expect(pendingPushSnapshotResetPetIds()).toEqual([]);
  });

  it("does not resurrect a reset pet from another hook's stale async callback", () => {
    const initialPet = writeLocalPetState({
      ...petState("pet-a"),
      assetSet: assetSet("assets-a"),
    });
    const resetScreen = renderHook(() => useLocalPetState());
    const staleScreen = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });

    act(() => {
      resetScreen.result.current.reset("pet-a");
    });
    let staleResult: LocalPetState | null = initialPet;
    act(() => {
      staleResult = staleScreen.result.current.applyGeneratedAssets(
        { ...assetSet("assets-a"), sadVideoUrl: "/late.mp4" },
        "pet-a",
        initialPet.assetSet,
      );
    });

    expect(staleResult).toBeNull();
    expect(readLocalPetState()).toBeNull();
  });

  it("reconciles a pet removed by another tab from a storage event", () => {
    writeLocalPetState(petState("pet-a"));
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });

    window.localStorage.removeItem(PET_STATE_STORAGE_KEY);
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", {
        key: PET_STATE_STORAGE_KEY,
        oldValue: JSON.stringify(petState("pet-a")),
        newValue: null,
      }));
    });

    expect(result.current.pet).toBeNull();
    expect(result.current.status).toBe("empty");
  });

  it("reconciles a replacement pet from another tab", () => {
    writeLocalPetState(petState("pet-a"));
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });
    writeLocalPetState(petState("pet-b"));

    act(() => {
      window.dispatchEvent(new StorageEvent("storage", {
        key: PET_STATE_STORAGE_KEY,
        newValue: window.localStorage.getItem(PET_STATE_STORAGE_KEY),
      }));
    });

    expect(result.current.pet?.petId).toBe("pet-b");
    expect(result.current.status).toBe("ready");
  });

  it("reconciles a missed cross-tab reset on the next stats interval", () => {
    writeLocalPetState(petState("pet-a"));
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });
    window.localStorage.removeItem(PET_STATE_STORAGE_KEY);

    act(() => {
      vi.advanceTimersByTime(60_000);
    });

    expect(result.current.pet).toBeNull();
    expect(result.current.status).toBe("empty");
  });

  it("atomically commits travel stats and a receipt from the latest stored pet", () => {
    writeLocalPetState({
      ...petState("pet-a"),
      stats: { hunger: 50, happiness: 50, energy: 50 },
    });
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });
    writeLocalPetState({
      ...readLocalPetState()!,
      stats: { hunger: 70, happiness: 65, energy: 44 },
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    setItem.mockClear();

    act(() => {
      result.current.applyInteractiveTravelImpacts({
        travelId: "travel-1",
        parts: [resolvedTravelPart()],
      }, "pet-a");
    });

    expect(readLocalPetState()?.stats).toEqual({ hunger: 74, happiness: 65, energy: 44 });
    expect(readLocalPetState()?.travelImpactReceipts).toEqual(["travel-1:1"]);
    expect(setItem.mock.calls.filter(([key]) => key === PET_STATE_STORAGE_KEY)).toHaveLength(1);

    setItem.mockClear();
    act(() => {
      result.current.applyInteractiveTravelImpacts({
        travelId: "travel-1",
        parts: [resolvedTravelPart()],
      }, "pet-a");
    });
    expect(readLocalPetState()?.stats.hunger).toBe(74);
    expect(setItem).not.toHaveBeenCalled();
  });

  it("does not acknowledge a travel impact when its pet receipt is not durable", () => {
    writeLocalPetState({
      ...petState("pet-a"),
      stats: { hunger: 70, happiness: 65, energy: 44 },
    });
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "QuotaExceededError");
    });
    let application: ReturnType<typeof result.current.applyInteractiveTravelImpacts>;

    act(() => {
      application = result.current.applyInteractiveTravelImpacts({
        travelId: "travel-not-durable",
        parts: [resolvedTravelPart()],
      }, "pet-a");
    });

    expect(application!).toBeNull();
    expect(readLocalPetState()?.stats).toEqual({ hunger: 70, happiness: 65, energy: 44 });
    expect(readLocalPetState()?.travelImpactReceipts).toBeUndefined();
    setItem.mockRestore();
  });

  it("returns the actual LWW winner instead of reporting a lost snapshot write", () => {
    writeLocalPetState({
      ...petState("pet-a"),
      stats: { hunger: 50, happiness: 50, energy: 50 },
    });
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });
    const nativeSetItem = window.localStorage.setItem.bind(window.localStorage);
    let injected = false;
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation((key, value) => {
      nativeSetItem(key, value);
      if (!injected && key === PET_STATE_STORAGE_KEY) {
        injected = true;
        const winner = {
          ...(JSON.parse(value) as LocalPetState),
          stats: { hunger: 12, happiness: 34, energy: 56 },
        };
        nativeSetItem(key, JSON.stringify(winner));
      }
    });
    let feedResult: LocalPetState | null = null;

    act(() => {
      feedResult = result.current.feed("berry-bowl");
    });
    setItem.mockRestore();

    expect(feedResult).toBeNull();
    expect(result.current.pet?.stats).toEqual({ hunger: 12, happiness: 34, energy: 56 });
    expect(readLocalPetState()?.stats).toEqual({ hunger: 12, happiness: 34, energy: 56 });
  });

  it("does not overwrite a stat changed locally while a push snapshot was in flight", () => {
    const baseTick = "2026-07-15T12:00:00.000Z";
    const localTick = "2026-07-15T12:01:00.000Z";
    const serverTick = "2026-07-15T12:02:00.000Z";
    writeLocalPetState({
      ...petState("pet-a"),
      lastStatsTickAt: baseTick,
      lastStatTickAt: { hunger: baseTick, happiness: baseTick, energy: baseTick },
      stats: { hunger: 50, happiness: 60, energy: 70 },
    });
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });
    writeLocalPetState({
      ...readLocalPetState()!,
      lastStatsTickAt: baseTick,
      lastStatTickAt: { hunger: baseTick, happiness: baseTick, energy: localTick },
      stats: { hunger: 50, happiness: 60, energy: 88 },
    });

    act(() => {
      result.current.applyStatsPatch(
        {
          stats: { hunger: 45, energy: 1 },
          lastStatTickAt: { hunger: serverTick, energy: serverTick },
        },
        "pet-a",
        { hunger: baseTick, happiness: baseTick, energy: baseTick },
      );
    });

    expect(readLocalPetState()?.stats).toEqual({ hunger: 45, happiness: 60, energy: 88 });
    expect(readLocalPetState()?.lastStatTickAt).toMatchObject({
      hunger: serverTick,
      energy: localTick,
    });
  });

  it("does not apply stale name, mood, or assets from pet A to pet B", () => {
    const petB = writeLocalPetState({
      ...petState("pet-b"),
      name: "Бета",
      assetSet: assetSet("assets-b"),
    });
    const { result } = renderHook(() => useLocalPetState());

    act(() => {
      expect(result.current.updateName("Альфа", "pet-a")).toBeNull();
      expect(result.current.applyMoodHint("sad", undefined, -20, "pet-a")).toBeNull();
      expect(result.current.applyGeneratedAssets(assetSet("assets-a"), "pet-a")).toBeNull();
    });

    expect(readLocalPetState()).toEqual(petB);
  });

  it("does not let stale dashboard actions mutate or reset a replacement pet", () => {
    writeLocalPetState(petState("pet-a"));
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });
    const petB = writeLocalPetState(petState("pet-b"));

    act(() => {
      expect(result.current.feed("berry-bowl")).toBeNull();
      expect(result.current.play()).toBeNull();
      expect(result.current.resetStats()).toBeNull();
      expect(result.current.kill()).toBeNull();
      result.current.reset();
    });

    expect(readLocalPetState()).toEqual(petB);
  });

  it("preserves rejected revive and stale asset CAS return values", () => {
    writeLocalPetState({
      ...petState("pet-a"),
      assetSet: assetSet("assets-current"),
    });
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });

    act(() => {
      expect(result.current.revive()).toBeNull();
      expect(result.current.applyGeneratedAssets(
        assetSet("assets-stale"),
        "pet-a",
        assetSet("assets-stale"),
      )).toBeNull();
      expect(result.current.applyGeneratedAssets(
        assetSet("outfit-new"),
        "pet-a",
        assetSet("assets-stale"),
        true,
      )).toBeNull();
    });

    expect(readLocalPetState()?.assetSet?.assetSetId).toBe("assets-current");
  });

  it("does not report expected-pet patches as successful when storage is unavailable", () => {
    writeLocalPetState({
      ...petState("pet-a"),
      assetSet: assetSet("assets-a"),
    });
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });

    let statsResult: LocalPetState | null = null;
    let overlayResult: LocalPetState | null = null;
    act(() => {
      statsResult = result.current.applyStatsPatch({ stats: { energy: 17 } }, "pet-a");
      overlayResult = result.current.applyLiteOverlayPatch({
        facts: [{ sphere: "world", kind: "world_fact", text: "Новый факт" }],
      }, "pet-a");
    });

    expect(statsResult).toBeNull();
    expect(overlayResult).toBeNull();
    expect(result.current.pet?.stats.energy).toBe(100);
    expect(JSON.stringify(result.current.pet?.assetSet?.characterBible) ?? "").not.toContain(
      "Новый факт",
    );
    expect(setItem).not.toHaveBeenCalled();
    getItem.mockRestore();
    setItem.mockRestore();
  });

  it("keeps a pet across hook remounts when localStorage is unavailable", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage blocked", "SecurityError");
    });
    const firstScreen = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });

    let createdPetId = "";
    act(() => {
      createdPetId = firstScreen.result.current.create("мышонок").petId;
    });
    firstScreen.unmount();

    const nextScreen = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });

    expect(nextScreen.result.current.status).toBe("ready");
    expect(nextScreen.result.current.pet?.petId).toBe(createdPetId);
    getItem.mockRestore();
    setItem.mockRestore();
  });

  it("keeps the latest character bible while applying a stale background snapshot", () => {
    const baseAssets = {
      ...assetSet("assets-a"),
      characterBible: { extensions: { lite_overlay: { facts: [] } } },
    };
    writeLocalPetState({
      ...petState("pet-a"),
      assetSet: baseAssets,
    });
    const { result } = renderHook(() => useLocalPetState());

    act(() => {
      vi.runOnlyPendingTimers();
    });
    act(() => {
      result.current.applyLiteOverlayPatch(
        { facts: [{ key: "friend", value: "Лис" }] },
        "pet-a",
      );
    });
    const latestBible = readLocalPetState()?.assetSet?.characterBible;
    const refreshedAssets = {
      ...baseAssets,
      sadVideoUrl: "/assets-a-sad.mp4",
      images: {
        baby: { ...baseAssets.images.baby, sad: "/fresh-sad.png" },
        teen: { ...baseAssets.images.teen, sad: "/fresh-sad.png" },
        adult: { ...baseAssets.images.adult, sad: "/fresh-sad.png" },
      },
    };

    act(() => {
      result.current.applyGeneratedAssets(refreshedAssets, "pet-a", baseAssets);
    });

    const storedAssets = readLocalPetState()?.assetSet;
    expect(storedAssets?.characterBible).toEqual(latestBible);
    expect(storedAssets?.sadVideoUrl).toBe("/assets-a-sad.mp4");
    expect(storedAssets?.images.teen.sad).toBe("/fresh-sad.png");
  });

  it("merges independent happy and sad results from the same base snapshot", () => {
    const fallbackAssets = assetSet("assets-a");
    for (const stage of ["baby", "teen", "adult"] as const) {
      fallbackAssets.images[stage].happy = fallbackAssets.images[stage].idle;
      fallbackAssets.images[stage].sad = fallbackAssets.images[stage].idle;
    }
    writeLocalPetState({
      ...petState("pet-a"),
      assetSet: fallbackAssets,
    });
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });

    const sadAssets = {
      ...fallbackAssets,
      sadVideoUrl: "/assets-a-sad.mp4",
      images: {
        baby: { ...fallbackAssets.images.baby, sad: "/assets-a-sad.png" },
        teen: { ...fallbackAssets.images.teen, sad: "/assets-a-sad.png" },
        adult: { ...fallbackAssets.images.adult, sad: "/assets-a-sad.png" },
      },
    };
    const happyAssets = {
      ...fallbackAssets,
      happyVideoUrl: "/assets-a-happy.mp4",
      images: {
        baby: { ...fallbackAssets.images.baby, happy: "/assets-a-happy.png" },
        teen: { ...fallbackAssets.images.teen, happy: "/assets-a-happy.png" },
        adult: { ...fallbackAssets.images.adult, happy: "/assets-a-happy.png" },
      },
    };

    act(() => {
      result.current.applyGeneratedAssets(sadAssets, "pet-a", fallbackAssets);
      result.current.applyGeneratedAssets(happyAssets, "pet-a", fallbackAssets);
    });

    const storedAssets = readLocalPetState()?.assetSet;
    expect(storedAssets?.images.teen.sad).toBe("/assets-a-sad.png");
    expect(storedAssets?.sadVideoUrl).toBe("/assets-a-sad.mp4");
    expect(storedAssets?.images.teen.happy).toBe("/assets-a-happy.png");
    expect(storedAssets?.happyVideoUrl).toBe("/assets-a-happy.mp4");
  });

  it("atomically replaces an expected base asset set with a new outfit asset set", () => {
    const baseAssets = assetSet("assets-a");
    const outfitAssets = assetSet("outfit-assets-b");
    writeLocalPetState({
      ...petState("pet-a"),
      assetSet: baseAssets,
    });
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });
    act(() => {
      result.current.applyGeneratedAssets(
        outfitAssets,
        "pet-a",
        baseAssets,
        true,
      );
    });

    expect(readLocalPetState()?.assetSet?.assetSetId).toBe("outfit-assets-b");
  });

  it("does not roll back newer nested comparison assets with a stale equivalent snapshot", () => {
    const comparisonBase = {
      ...assetSet("comparison-a"),
      videoUrl: "/comparison-old.mp4",
    };
    const baseAssets = {
      ...assetSet("assets-a"),
      kandinskyAssets: comparisonBase,
    };
    writeLocalPetState({
      ...petState("pet-a"),
      assetSet: baseAssets,
    });
    const { result } = renderHook(() => useLocalPetState());
    act(() => {
      vi.runOnlyPendingTimers();
    });
    writeLocalPetState({
      ...petState("pet-a"),
      assetSet: {
        ...baseAssets,
        kandinskyAssets: {
          ...comparisonBase,
          videoUrl: "/comparison-new.mp4",
        },
      },
    });

    act(() => {
      result.current.applyGeneratedAssets(
        {
          ...baseAssets,
          kandinskyAssets: { ...comparisonBase },
          sadVideoUrl: "/assets-a-sad.mp4",
        },
        "pet-a",
        baseAssets,
      );
    });

    expect(readLocalPetState()?.assetSet?.kandinskyAssets?.videoUrl).toBe(
      "/comparison-new.mp4",
    );
    expect(readLocalPetState()?.assetSet?.sadVideoUrl).toBe("/assets-a-sad.mp4");
  });
});
