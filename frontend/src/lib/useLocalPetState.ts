"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  clearLocalChatHistory,
  commitLocalPetStateMutation,
  createLocalPetState,
  PET_STATE_STORAGE_KEY,
  readLocalPetState,
  resetLocalPetState,
  writeLocalPetState,
  writeLocalPetStateDurably,
  type LocalPetStateMutationOperation,
} from "./localPetStorage";
import { clearLocalPetScopedData } from "./localPetScopedCleanup";
import type { FoodId } from "./localPetFood";
import type { TravelImpactApplication } from "./localPetTravelImpacts";
import {
  clearPendingPushSnapshotReset,
  flushPendingPushSnapshotResets,
  isPendingPushSnapshotResetStorageKey,
  queuePushSnapshotReset,
} from "./pendingPushSnapshotReset";
import type {
  ConversationHappinessDelta,
  InteractiveTravelPart,
  LocalPetAssetSet,
  LocalPetState,
  PetMood,
  PetStatTickMap,
  PetStatsPatch,
} from "./types";

type LocalPetStateStatus = "loading" | "ready" | "empty" | "error";
const LOCAL_PET_TICK_MS = 60_000;
const PENDING_STORAGE_ERROR_MESSAGE = (
  "Не удалось сохранить прогресс. Освободите место на устройстве или разрешите "
  + "приложению хранить данные, затем попробуйте снова."
);

type UseLocalPetStateResult = {
  pet: LocalPetState | null;
  status: LocalPetStateStatus;
  error: string | null;
  create: (description: string, assetSet?: LocalPetAssetSet) => LocalPetState;
  createDurably: (description: string, assetSet?: LocalPetAssetSet) => LocalPetState | null;
  feed: (foodId: FoodId) => LocalPetState | null;
  play: () => LocalPetState | null;
  registerPetTap: () => { pet: LocalPetState; rewarded: boolean } | null;
  reset: (expectedPetId?: string) => boolean;
  resetStats: () => LocalPetState | null;
  kill: () => LocalPetState | null;
  revive: () => LocalPetState | null;
  updateName: (name: string, expectedPetId?: string) => LocalPetState | null;
  applyGeneratedAssets: (
    assetSet: LocalPetAssetSet,
    expectedPetId?: string,
    expectedBaseAssetSet?: LocalPetAssetSet,
  ) => LocalPetState | null;
  applyMoodHint: (
    moodHint?: PetMood,
    storyLibraryPatch?: Record<string, unknown>,
    happinessDelta?: ConversationHappinessDelta,
    expectedPetId?: string,
  ) => LocalPetState | null;
  applyLiteOverlayPatch: (
    patch?: Record<string, unknown>,
    expectedPetId?: string,
  ) => LocalPetState | null;
  applyStoryLibraryPatch: (
    patch?: Record<string, unknown>,
    expectedPetId?: string,
  ) => LocalPetState | null;
  applyRecentStoryEventsPatch: (
    patch?: Record<string, unknown>,
    expectedPetId?: string,
  ) => LocalPetState | null;
  applyInteractiveTravelImpacts: (
    input: {
      travelId: string;
      parts: readonly InteractiveTravelPart[];
      legacyAppliedResultParts?: readonly number[];
    },
    expectedPetId?: string,
  ) => TravelImpactApplication | null;
  applyStatsPatch: (
    patch?: PetStatsPatch,
    expectedPetId?: string,
    expectedBaseStatTicks?: PetStatTickMap,
  ) => LocalPetState | null;
};

export function useLocalPetState(): UseLocalPetStateResult {
  const [pet, setPet] = useState<LocalPetState | null>(null);
  const [status, setStatus] = useState<LocalPetStateStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const currentPetRef = useRef<LocalPetState | null>(null);
  const activePetId = pet?.petId;

  const savePet = useCallback((state: LocalPetState) => {
    const nextPet = writeLocalPetState(state);
    currentPetRef.current = nextPet;
    setPet(nextPet);
    return nextPet;
  }, []);

  const commitPetMutation = useCallback((
    operation: LocalPetStateMutationOperation,
    expectedPetId?: string,
  ) => {
    const committed = commitLocalPetStateMutation(operation, expectedPetId);
    if (!committed) {
      return null;
    }
    currentPetRef.current = committed.pet;
    setPet(committed.pet);
    setStatus("ready");
    setError(null);
    if (committed.rejected) {
      return null;
    }
    return committed;
  }, []);

  const readPetForMutation = useCallback((expectedPetId?: string) => {
    // `readLocalPetState` already owns the in-memory fallback for unavailable
    // storage. A hook-local fallback would resurrect a pet deleted by another
    // mounted hook when an async callback for the old pet finishes late.
    const currentPet = readLocalPetState();
    return expectedPetId && currentPet?.petId !== expectedPetId ? null : currentPet;
  }, []);

  const reconcilePetFromStorage = useCallback(() => {
    try {
      const stored = readLocalPetState();
      currentPetRef.current = stored;
      setPet(stored);
      setStatus(stored ? "ready" : "empty");
      setError(null);
      return stored;
    } catch {
      currentPetRef.current = null;
      setPet(null);
      setError("Не удалось прочитать локальный прогресс.");
      setStatus("error");
      return null;
    }
  }, []);

  useEffect(() => {
    const flushPendingResets = () => {
      void flushPendingPushSnapshotResets();
    };
    flushPendingResets();
    window.addEventListener("online", flushPendingResets);
    return () => window.removeEventListener("online", flushPendingResets);
  }, []);

  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (event.key === null || isPendingPushSnapshotResetStorageKey(event.key)) {
        void flushPendingPushSnapshotResets();
      }
      if (
        event.key === null
        || event.key === PET_STATE_STORAGE_KEY
      ) {
        reconcilePetFromStorage();
      }
    };
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, [reconcilePetFromStorage]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      try {
        const stored = readLocalPetState();
        if (!stored) {
          currentPetRef.current = null;
          setPet(null);
          setStatus("empty");
          return;
        }

        const progressed = commitPetMutation(
          { kind: "offline-progress" },
          stored.petId,
        )?.pet ?? stored;
        currentPetRef.current = progressed;
        setPet(progressed);
        setStatus("ready");
      } catch {
        currentPetRef.current = null;
        setPet(null);
        setError("Не удалось прочитать локальный прогресс.");
        setStatus("error");
      }
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, [commitPetMutation]);

  useEffect(() => {
    if (!activePetId) {
      return;
    }

    const intervalId = window.setInterval(() => {
      const currentPet = readLocalPetState();
      if (!currentPet || currentPet.petId !== activePetId) {
        reconcilePetFromStorage();
        return;
      }

      const progressed = commitPetMutation(
        { kind: "offline-progress" },
        activePetId,
      )?.pet;
      if (!progressed) {
        return;
      }
      setStatus("ready");
      setError(null);
    }, LOCAL_PET_TICK_MS);

    return () => window.clearInterval(intervalId);
  }, [activePetId, commitPetMutation, reconcilePetFromStorage]);

  const create = useCallback((description: string, assetSet?: LocalPetAssetSet) => {
    const previousPet = readLocalPetState();
    if (previousPet && !queuePushSnapshotReset(previousPet.petId)) {
      setError(PENDING_STORAGE_ERROR_MESSAGE);
      return previousPet;
    }
    if (previousPet) {
      const previousPetId = previousPet.petId;
      clearLocalPetScopedData(previousPetId);
    }
    clearLocalChatHistory();
    const nextPet = savePet(createLocalPetState(description, assetSet));
    setStatus("ready");
    setError(null);
    void flushPendingPushSnapshotResets();
    return nextPet;
  }, [savePet]);

  const createDurably = useCallback((description: string, assetSet?: LocalPetAssetSet) => {
    const previousPetId = readLocalPetState()?.petId;
    if (previousPetId && !queuePushSnapshotReset(previousPetId)) {
      setStatus("ready");
      setError(PENDING_STORAGE_ERROR_MESSAGE);
      return null;
    }
    const nextPet = writeLocalPetStateDurably(createLocalPetState(description, assetSet));
    if (!nextPet) {
      if (previousPetId) {
        clearPendingPushSnapshotReset(previousPetId);
      }
      setStatus(previousPetId ? "ready" : "empty");
      setError(PENDING_STORAGE_ERROR_MESSAGE);
      return null;
    }
    if (previousPetId) {
      clearLocalPetScopedData(previousPetId);
    }
    clearLocalChatHistory();
    currentPetRef.current = nextPet;
    setPet(nextPet);
    setStatus("ready");
    setError(null);
    void flushPendingPushSnapshotResets();
    return nextPet;
  }, []);

  const feed = useCallback((foodId: FoodId) => {
    if (!pet) {
      return null;
    }
    return commitPetMutation({ kind: "feed", foodId }, pet.petId)?.pet ?? null;
  }, [commitPetMutation, pet]);

  const play = useCallback(() => {
    if (!pet) {
      return null;
    }

    return commitPetMutation({ kind: "play" }, pet.petId)?.pet ?? null;
  }, [commitPetMutation, pet]);

  const registerPetTap = useCallback(() => {
    if (!pet) {
      return null;
    }

    const committed = commitPetMutation({ kind: "tap" }, pet.petId);
    return committed
      ? { pet: committed.pet, rewarded: committed.tapRewarded === true }
      : null;
  }, [commitPetMutation, pet]);

  const reset = useCallback((expectedPetId?: string) => {
    const targetPetId = expectedPetId ?? currentPetRef.current?.petId;
    const currentPet = readPetForMutation(targetPetId);
    if (targetPetId && !currentPet) {
      return false;
    }

    const resetPetId = currentPet?.petId ?? targetPetId;
    if (resetPetId && !queuePushSnapshotReset(resetPetId)) {
      setError(PENDING_STORAGE_ERROR_MESSAGE);
      return false;
    }
    if (resetPetId) {
      clearLocalPetScopedData(resetPetId);
    }
    if (!resetLocalPetState(resetPetId)) {
      if (resetPetId) {
        clearPendingPushSnapshotReset(resetPetId);
      }
      reconcilePetFromStorage();
      return false;
    }
    currentPetRef.current = null;
    setPet(null);
    setStatus("empty");
    setError(null);
    void flushPendingPushSnapshotResets();
    return true;
  }, [readPetForMutation, reconcilePetFromStorage]);

  const resetStats = useCallback(() => {
    if (!pet) {
      return null;
    }
    return commitPetMutation({ kind: "reset-stats" }, pet.petId)?.pet ?? null;
  }, [commitPetMutation, pet]);

  const kill = useCallback(() => {
    if (!pet) {
      return null;
    }
    return commitPetMutation({ kind: "kill" }, pet.petId)?.pet ?? null;
  }, [commitPetMutation, pet]);

  const revive = useCallback(() => {
    if (!pet) {
      return null;
    }
    return commitPetMutation({ kind: "revive" }, pet.petId)?.pet ?? null;
  }, [commitPetMutation, pet]);

  const updateName = useCallback((name: string, expectedPetId?: string) => {
    const normalizedName = name.trim().replace(/\s+/g, " ").slice(0, 32);
    if (!normalizedName) {
      return null;
    }

    return commitPetMutation(
      { kind: "update-name", name: normalizedName },
      expectedPetId ?? pet?.petId,
    )?.pet ?? null;
  }, [commitPetMutation, pet?.petId]);

  const applyGeneratedAssets = useCallback((
    assetSet: LocalPetAssetSet,
    expectedPetId?: string,
    expectedBaseAssetSet?: LocalPetAssetSet,
  ) => {
    if (!pet) {
      return null;
    }

    return commitPetMutation({
      kind: "generated-assets",
      assetSet,
      expectedBaseAssetSet,
    }, expectedPetId ?? pet.petId)?.pet ?? null;
  }, [commitPetMutation, pet]);

  const applyMoodHint = useCallback((
    moodHint?: PetMood,
    storyLibraryPatch?: Record<string, unknown>,
    happinessDelta: ConversationHappinessDelta = 0,
    expectedPetId?: string,
  ) => {
    return commitPetMutation({
      kind: "mood-hint",
      moodHint,
      storyLibraryPatch,
      happinessDelta,
    }, expectedPetId ?? pet?.petId)?.pet ?? null;
  }, [commitPetMutation, pet?.petId]);

  const applyLiteOverlayPatch = useCallback((
    patch?: Record<string, unknown>,
    expectedPetId?: string,
  ) => {
    if (!patch) {
      return null;
    }

    return commitPetMutation(
      { kind: "lite-overlay-patch", patch },
      expectedPetId ?? currentPetRef.current?.petId,
    )?.pet ?? null;
  }, [commitPetMutation]);

  const applyStoryLibraryPatch = useCallback((
    patch?: Record<string, unknown>,
    expectedPetId?: string,
  ) => {
    if (!patch) {
      return null;
    }

    return commitPetMutation(
      { kind: "story-library-patch", patch },
      expectedPetId ?? currentPetRef.current?.petId,
    )?.pet ?? null;
  }, [commitPetMutation]);

  const applyRecentStoryEventsPatch = useCallback((
    patch?: Record<string, unknown>,
    expectedPetId?: string,
  ) => {
    if (!patch) {
      return null;
    }

    return commitPetMutation(
      { kind: "recent-story-events-patch", patch },
      expectedPetId ?? currentPetRef.current?.petId,
    )?.pet ?? null;
  }, [commitPetMutation]);

  const applyInteractiveTravelImpacts = useCallback((
    input: {
      travelId: string;
      parts: readonly InteractiveTravelPart[];
      legacyAppliedResultParts?: readonly number[];
    },
    expectedPetId?: string,
  ): TravelImpactApplication | null => {
    const committed = commitPetMutation({
      kind: "interactive-travel-impacts",
      travelId: input.travelId,
      parts: input.parts,
      legacyAppliedResultParts: input.legacyAppliedResultParts,
    }, expectedPetId ?? currentPetRef.current?.petId);
    if (!committed?.travelApplication) {
      setError(PENDING_STORAGE_ERROR_MESSAGE);
      return null;
    }
    return committed.travelApplication;
  }, [commitPetMutation]);

  const applyStatsPatch = useCallback((
    patch?: PetStatsPatch,
    expectedPetId?: string,
    expectedBaseStatTicks?: PetStatTickMap,
  ) => {
    if (!patch) {
      return null;
    }

    return commitPetMutation({
      kind: "stats-patch",
      patch,
      expectedBaseStatTicks,
    }, expectedPetId ?? currentPetRef.current?.petId)?.pet ?? null;
  }, [commitPetMutation]);

  return {
    pet,
    status,
    error,
    create,
    createDurably,
    feed,
    play,
    registerPetTap,
    reset,
    resetStats,
    kill,
    revive,
    updateName,
    applyGeneratedAssets,
    applyMoodHint,
    applyLiteOverlayPatch,
    applyStoryLibraryPatch,
    applyRecentStoryEventsPatch,
    applyInteractiveTravelImpacts,
    applyStatsPatch,
  };
}
