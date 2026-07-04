"use client";

import { useCallback, useEffect, useState } from "react";

import {
  applyOfflineProgress,
  applyMemoryPatch,
  calculatePetMood,
  calculatePetStage,
  clearLocalChatHistory,
  createLocalPetState,
  mergeLoreMemories,
  readLocalPetState,
  resetLocalPetState,
  withPetInteraction,
  writeLocalPetState,
} from "./localPetStorage";
import type { LocalPetAssetSet, LocalPetState, PetMemoryPatch, PetMood } from "./types";

type LocalPetStateStatus = "loading" | "ready" | "empty" | "error";

type UseLocalPetStateResult = {
  pet: LocalPetState | null;
  status: LocalPetStateStatus;
  error: string | null;
  create: (description: string, assetSet?: LocalPetAssetSet) => LocalPetState;
  feed: () => LocalPetState | null;
  play: () => LocalPetState | null;
  reset: () => void;
  applyGeneratedAssets: (assetSet: LocalPetAssetSet) => LocalPetState | null;
  applyMoodHint: (
    moodHint?: PetMood,
    loreMemoriesToSave?: string[],
    memoryPatch?: PetMemoryPatch,
  ) => LocalPetState | null;
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
    resetLocalPetState();
    setPet(null);
    setStatus("empty");
    setError(null);
  }, []);

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
    loreMemoriesToSave?: string[],
    memoryPatch?: PetMemoryPatch,
  ) => {
    if (!pet) {
      return null;
    }

    const now = new Date();
    const stats = {
      ...pet.stats,
      happiness: Math.min(100, pet.stats.happiness + 5),
    };
    const nextPet = saveAndReturn({
      ...pet,
      updatedAt: now.toISOString(),
      lastInteractionAt: now.toISOString(),
      stage: calculatePetStage(pet.createdAt, now),
      mood: moodHint ?? calculatePetMood(stats),
      stats,
      memory: applyMemoryPatch(pet.memory, memoryPatch),
      loreMemories: mergeLoreMemories(pet.loreMemories, loreMemoriesToSave),
    });
    setPet(nextPet);
    return nextPet;
  }, [pet]);

  return {
    pet,
    status,
    error,
    create,
    feed,
    play,
    reset,
    applyGeneratedAssets,
    applyMoodHint,
  };
}
