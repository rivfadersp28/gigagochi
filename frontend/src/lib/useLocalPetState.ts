"use client";

import { useCallback, useEffect, useState } from "react";

import {
  applyOfflineProgress,
  applyLiteOverlayPatch as applyStoredLiteOverlayPatch,
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

type UseLocalPetStateResult = {
  pet: LocalPetState | null;
  status: LocalPetStateStatus;
  error: string | null;
  create: (description: string, assetSet?: LocalPetAssetSet) => LocalPetState;
  feed: () => LocalPetState | null;
  play: () => LocalPetState | null;
  reset: () => void;
  updateName: (name: string) => LocalPetState | null;
  applyGeneratedAssets: (assetSet: LocalPetAssetSet) => LocalPetState | null;
  applyMoodHint: (
    moodHint?: PetMood,
    liteOverlayPatch?: Record<string, unknown>,
    storyLibraryPatch?: Record<string, unknown>,
  ) => LocalPetState | null;
  applyLiteOverlayPatch: (patch?: Record<string, unknown>) => LocalPetState | null;
  applyStoryLibraryPatch: (patch?: Record<string, unknown>) => LocalPetState | null;
};

function saveAndReturn(state: LocalPetState) {
  writeLocalPetState(state);
  return state;
}

export function useLocalPetState(): UseLocalPetStateResult {
  const [pet, setPet] = useState<LocalPetState | null>(null);
  const [status, setStatus] = useState<LocalPetStateStatus>("loading");
  const [error, setError] = useState<string | null>(null);

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

    const nextPet = saveAndReturn(
      withPetInteraction(pet, (stats) => ({
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

    const nextPet = saveAndReturn(
      withPetInteraction(pet, (stats) => ({
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

  const updateName = useCallback((name: string) => {
    const normalizedName = name.trim().replace(/\s+/g, " ").slice(0, 32);
    if (!normalizedName) {
      return null;
    }

    const currentPet = readLocalPetState() ?? pet;
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

    const now = new Date();
    const nextPet = saveAndReturn({
      ...pet,
      assetSet,
      updatedAt: now.toISOString(),
      stage: calculatePetStage(pet.createdAt, now),
      mood: calculatePetMood(pet.stats),
    });
    setPet(nextPet);
    return nextPet;
  }, [pet]);

  const applyMoodHint = useCallback((
    moodHint?: PetMood,
    liteOverlayPatch?: Record<string, unknown>,
    storyLibraryPatch?: Record<string, unknown>,
  ) => {
    const currentPet = readLocalPetState() ?? pet;
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

  return {
    pet,
    status,
    error,
    create,
    feed,
    play,
    reset,
    updateName,
    applyGeneratedAssets,
    applyMoodHint,
    applyLiteOverlayPatch,
    applyStoryLibraryPatch,
  };
}
