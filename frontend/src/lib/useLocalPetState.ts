"use client";

import { useCallback, useEffect, useState } from "react";

import {
  applyOfflineProgress,
  applyLiteOverlayPatch as applyStoredLiteOverlayPatch,
  applyRecentStoryEventsPatch as applyStoredRecentStoryEventsPatch,
  applyStoryLibraryPatch as applyStoredStoryLibraryPatch,
  calculatePetMood,
  calculatePetStage,
  clearLocalChatHistory,
  createLocalPetState,
  readLocalPetState,
  resetLocalPetState,
  withPetInteraction,
  writeLocalPetState,
} from "./localPetStorage";
import { resetLocalPetMemory } from "./localPetMemoryStorage";
import type { LocalPetAssetSet, LocalPetState, PetMood } from "./types";

type LocalPetStateStatus = "loading" | "ready" | "empty" | "error";
const LOCAL_PET_TICK_MS = 60_000;

type UseLocalPetStateResult = {
  pet: LocalPetState | null;
  status: LocalPetStateStatus;
  error: string | null;
  create: (description: string, assetSet?: LocalPetAssetSet) => LocalPetState;
  feed: () => LocalPetState | null;
  play: () => LocalPetState | null;
  reset: () => void;
  resetStats: () => LocalPetState | null;
  updateName: (name: string) => LocalPetState | null;
  applyGeneratedAssets: (assetSet: LocalPetAssetSet) => LocalPetState | null;
  applyMoodHint: (
    moodHint?: PetMood,
    liteOverlayPatch?: Record<string, unknown>,
    storyLibraryPatch?: Record<string, unknown>,
  ) => LocalPetState | null;
  applyLiteOverlayPatch: (patch?: Record<string, unknown>) => LocalPetState | null;
  applyStoryLibraryPatch: (patch?: Record<string, unknown>) => LocalPetState | null;
  applyRecentStoryEventsPatch: (patch?: Record<string, unknown>) => LocalPetState | null;
};

function saveAndReturn(state: LocalPetState) {
  return writeLocalPetState(state);
}

function readProgressedPet(fallback: LocalPetState | null) {
  const currentPet = readLocalPetState() ?? fallback;
  return currentPet ? applyOfflineProgress(currentPet) : null;
}

export function useLocalPetState(): UseLocalPetStateResult {
  const [pet, setPet] = useState<LocalPetState | null>(null);
  const [status, setStatus] = useState<LocalPetStateStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const activePetId = pet?.petId;

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      try {
        const stored = readLocalPetState();
        if (!stored) {
          setPet(null);
          setStatus("empty");
          return;
        }

        const progressed = applyOfflineProgress(stored);
        saveAndReturn(progressed);
        setPet(progressed);
        setStatus("ready");
      } catch {
        setPet(null);
        setError("Не удалось прочитать локальный прогресс.");
        setStatus("error");
      }
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, []);

  useEffect(() => {
    if (!activePetId) {
      return;
    }

    const intervalId = window.setInterval(() => {
      const currentPet = readLocalPetState();
      if (!currentPet) {
        return;
      }

      const progressed = applyOfflineProgress(currentPet);
      saveAndReturn(progressed);
      setPet(progressed);
      setStatus("ready");
      setError(null);
    }, LOCAL_PET_TICK_MS);

    return () => window.clearInterval(intervalId);
  }, [activePetId]);

  const create = useCallback((description: string, assetSet?: LocalPetAssetSet) => {
    clearLocalChatHistory();
    const nextPet = saveAndReturn(createLocalPetState(description, assetSet));
    setPet(nextPet);
    setStatus("ready");
    setError(null);
    return nextPet;
  }, []);

  const feed = useCallback(() => {
    if (!pet) {
      return null;
    }

    const currentPet = readProgressedPet(pet);
    if (!currentPet) {
      return null;
    }

    const nextPet = saveAndReturn(
      withPetInteraction(currentPet, (stats) => ({
        ...stats,
        hunger: stats.hunger + 25,
        happiness: stats.happiness + 4,
      })),
    );
    setPet(nextPet);
    return nextPet;
  }, [pet]);

  const play = useCallback(() => {
    if (!pet) {
      return null;
    }

    const currentPet = readProgressedPet(pet);
    if (!currentPet) {
      return null;
    }

    const nextPet = saveAndReturn(
      withPetInteraction(currentPet, (stats) => ({
        ...stats,
        happiness: stats.happiness + 18,
        energy: stats.energy - 8,
        hunger: stats.hunger - 4,
      })),
    );
    setPet(nextPet);
    return nextPet;
  }, [pet]);

  const reset = useCallback(() => {
    resetLocalPetMemory(pet?.petId);
    resetLocalPetState();
    setPet(null);
    setStatus("empty");
    setError(null);
  }, [pet?.petId]);

  const resetStats = useCallback(() => {
    const currentPet = readProgressedPet(pet);
    if (!currentPet) {
      return null;
    }

    const now = new Date();
    const stats = {
      hunger: 0,
      happiness: 0,
      energy: 0,
    };
    const nextPet = saveAndReturn({
      ...currentPet,
      updatedAt: now.toISOString(),
      lastInteractionAt: now.toISOString(),
      lastStatsTickAt: now.toISOString(),
      stage: calculatePetStage(currentPet.createdAt, now),
      mood: calculatePetMood(stats),
      stats,
    });
    setPet(nextPet);
    setStatus("ready");
    setError(null);
    return nextPet;
  }, [pet]);

  const updateName = useCallback((name: string) => {
    const normalizedName = name.trim().replace(/\s+/g, " ").slice(0, 32);
    if (!normalizedName) {
      return null;
    }

    const currentPet = readProgressedPet(pet);
    if (!currentPet) {
      return null;
    }

    const now = new Date();
    const nextPet = saveAndReturn({
      ...currentPet,
      name: normalizedName,
      updatedAt: now.toISOString(),
      stage: calculatePetStage(currentPet.createdAt, now),
    });
    setPet(nextPet);
    setStatus("ready");
    setError(null);
    return nextPet;
  }, [pet]);

  const applyGeneratedAssets = useCallback((assetSet: LocalPetAssetSet) => {
    if (!pet) {
      return null;
    }

    const currentPet = readProgressedPet(pet);
    if (!currentPet) {
      return null;
    }

    const now = new Date();
    const nextPet = saveAndReturn({
      ...currentPet,
      assetSet,
      updatedAt: now.toISOString(),
      stage: calculatePetStage(currentPet.createdAt, now),
      mood: calculatePetMood(currentPet.stats),
    });
    setPet(nextPet);
    return nextPet;
  }, [pet]);

  const applyMoodHint = useCallback((
    moodHint?: PetMood,
    liteOverlayPatch?: Record<string, unknown>,
    storyLibraryPatch?: Record<string, unknown>,
  ) => {
    const currentPet = readProgressedPet(pet);
    if (!currentPet) {
      return null;
    }

    const now = new Date();
    const stats = {
      ...currentPet.stats,
      happiness: Math.min(100, currentPet.stats.happiness + 5),
    };
    const nextState = {
      ...currentPet,
      updatedAt: now.toISOString(),
      lastInteractionAt: now.toISOString(),
      lastStatsTickAt: now.toISOString(),
      stage: calculatePetStage(currentPet.createdAt, now),
      mood: moodHint ?? calculatePetMood(stats),
      stats,
    };
    const withLiteOverlay = liteOverlayPatch
      ? applyStoredLiteOverlayPatch(nextState, liteOverlayPatch)
      : nextState;
    const withStoryLibrary = storyLibraryPatch
      ? applyStoredStoryLibraryPatch(withLiteOverlay, storyLibraryPatch)
      : withLiteOverlay;
    const nextPet = saveAndReturn(withStoryLibrary);
    setPet(nextPet);
    return nextPet;
  }, [pet]);

  const applyLiteOverlayPatch = useCallback((patch?: Record<string, unknown>) => {
    if (!patch) {
      return null;
    }

    const currentPet = readLocalPetState();
    if (!currentPet) {
      return null;
    }

    const nextPet = saveAndReturn(applyStoredLiteOverlayPatch(currentPet, patch));
    setPet(nextPet);
    return nextPet;
  }, []);

  const applyStoryLibraryPatch = useCallback((patch?: Record<string, unknown>) => {
    if (!patch) {
      return null;
    }

    const currentPet = readLocalPetState();
    if (!currentPet) {
      return null;
    }

    const nextPet = saveAndReturn(applyStoredStoryLibraryPatch(currentPet, patch));
    setPet(nextPet);
    return nextPet;
  }, []);

  const applyRecentStoryEventsPatch = useCallback((patch?: Record<string, unknown>) => {
    if (!patch) {
      return null;
    }

    const currentPet = readLocalPetState();
    if (!currentPet) {
      return null;
    }

    const nextPet = saveAndReturn(applyStoredRecentStoryEventsPatch(currentPet, patch));
    setPet(nextPet);
    return nextPet;
  }, []);

  return {
    pet,
    status,
    error,
    create,
    feed,
    play,
    reset,
    resetStats,
    updateName,
    applyGeneratedAssets,
    applyMoodHint,
    applyLiteOverlayPatch,
    applyStoryLibraryPatch,
    applyRecentStoryEventsPatch,
  };
}
