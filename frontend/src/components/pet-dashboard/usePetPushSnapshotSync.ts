"use client";

import { useCallback, useEffect, useRef, type RefObject } from "react";

import { canUseTmaApi, registerPetPushSnapshot } from "@/lib/api";
import { buildMemorySnapshotContext } from "@/lib/localPetMemoryRecall";
import { readLocalPetMemory } from "@/lib/localPetMemoryStorage";
import {
  readLocalPetSnapshotRevision,
  subscribeLocalPetSnapshotDirty,
} from "@/lib/localPetSnapshotRevision";
import { readLocalChatHistory, readLocalPetState } from "@/lib/localPetStorage";
import type { LocalPetState, PetStatsPatch, PetStatTickMap } from "@/lib/types";

const PUSH_SNAPSHOT_SYNC_MIN_INTERVAL_MS = 10 * 60_000;
const PUSH_SNAPSHOT_RETRY_BASE_MS = 15_000;
const PUSH_SNAPSHOT_RETRY_MAX_MS = 2 * 60_000;

type UsePetPushSnapshotSyncOptions = {
  status: "loading" | "ready" | "empty" | "error";
  pet: LocalPetState | null;
  petId: string;
  recentAmbientRepliesRef: RefObject<string[]>;
  applyStatsPatch: (
    patch?: PetStatsPatch,
    expectedPetId?: string,
    expectedBaseStatTicks?: PetStatTickMap,
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
  resetPet: (expectedPetId?: string) => void;
};

type PendingTimer = {
  handle: ReturnType<typeof setTimeout>;
  petId: string;
  dueAt: number;
};

export function usePetPushSnapshotSync(options: UsePetPushSnapshotSyncOptions) {
  const latestOptionsRef = useRef(options);

  const mountedRef = useRef(false);
  const lastSyncRef = useRef<{
    petId: string;
    syncedAt: number;
    revision: string;
  } | null>(null);
  const inFlightRef = useRef(new Map<string, string>());
  const dirtyPetIdsRef = useRef(new Set<string>());
  const retryNotBeforeRef = useRef(new Map<string, number>());
  const failureCountRef = useRef(new Map<string, number>());
  const pendingTimerRef = useRef<PendingTimer | null>(null);
  const runLatestRef = useRef<() => void>(() => undefined);

  const clearPendingTimer = useCallback((expectedPetId?: string) => {
    const pending = pendingTimerRef.current;
    if (!pending || (expectedPetId && pending.petId !== expectedPetId)) {
      return;
    }
    clearTimeout(pending.handle);
    pendingTimerRef.current = null;
  }, []);

  const scheduleRun = useCallback((petId: string, dueAt: number) => {
    const pending = pendingTimerRef.current;
    if (pending?.petId === petId && pending.dueAt <= dueAt) {
      return;
    }
    clearPendingTimer();
    const handle = setTimeout(() => {
      pendingTimerRef.current = null;
      runLatestRef.current();
    }, Math.max(0, dueAt - Date.now()));
    pendingTimerRef.current = { handle, petId, dueAt };
  }, [clearPendingTimer]);

  const runLatest = useCallback(() => {
    const latest = latestOptionsRef.current;
    const renderedPet = latest.pet;
    const pet = readLocalPetState();
    if (
      !mountedRef.current
      || latest.status !== "ready"
      || !pet
      || renderedPet?.petId !== latest.petId
      || pet.petId !== renderedPet.petId
      || !canUseTmaApi()
    ) {
      clearPendingTimer();
      return;
    }

    const requestedPetId = pet.petId;
    const requestedStatTicks = { ...pet.lastStatTickAt };
    const snapshotRevision = `${pet.updatedAt}:${readLocalPetSnapshotRevision(requestedPetId)}`;
    const inFlightRevision = inFlightRef.current.get(requestedPetId);
    if (inFlightRevision !== undefined) {
      if (inFlightRevision !== snapshotRevision) {
        dirtyPetIdsRef.current.add(requestedPetId);
      }
      return;
    }

    const now = Date.now();
    const lastSync = lastSyncRef.current;
    const sameRevision = (
      lastSync?.petId === requestedPetId
      && lastSync.revision === snapshotRevision
    );
    const needsSync = dirtyPetIdsRef.current.has(requestedPetId) || !sameRevision;
    if (!needsSync) {
      clearPendingTimer(requestedPetId);
      return;
    }
    const retryNotBefore = retryNotBeforeRef.current.get(requestedPetId);
    if (retryNotBefore !== undefined && now < retryNotBefore) {
      scheduleRun(requestedPetId, retryNotBefore);
      return;
    }
    if (
      lastSync?.petId === requestedPetId
      && now - lastSync.syncedAt < PUSH_SNAPSHOT_SYNC_MIN_INTERVAL_MS
    ) {
      scheduleRun(requestedPetId, lastSync.syncedAt + PUSH_SNAPSHOT_SYNC_MIN_INTERVAL_MS);
      return;
    }

    clearPendingTimer(requestedPetId);
    dirtyPetIdsRef.current.delete(requestedPetId);
    inFlightRef.current.set(requestedPetId, snapshotRevision);

    void registerPetPushSnapshot(
      pet,
      buildMemorySnapshotContext(readLocalPetMemory(requestedPetId)),
      {
        history: readLocalChatHistory().messages,
        recentAmbientReplies: latest.recentAmbientRepliesRef.current,
      },
    )
      .then((response) => {
        const current = latestOptionsRef.current;
        if (
          !mountedRef.current
          || current.status !== "ready"
          || current.pet?.petId !== requestedPetId
          || current.petId !== requestedPetId
        ) {
          return;
        }
        lastSyncRef.current = {
          petId: requestedPetId,
          syncedAt: Date.now(),
          revision: snapshotRevision,
        };
        retryNotBeforeRef.current.delete(requestedPetId);
        failureCountRef.current.delete(requestedPetId);
        if (response.resetPet) {
          current.resetPet(requestedPetId);
          return;
        }
        current.applyStatsPatch(
          response.statsPatch ?? undefined,
          requestedPetId,
          requestedStatTicks,
        );
        current.applyLiteOverlayPatch(response.liteOverlayPatch ?? undefined, requestedPetId);
        current.applyStoryLibraryPatch(response.storyLibraryPatch ?? undefined, requestedPetId);
        current.applyRecentStoryEventsPatch(
          response.recentStoryEventsPatch ?? undefined,
          requestedPetId,
        );
      })
      .catch(() => {
        if (latestOptionsRef.current.pet?.petId === requestedPetId) {
          dirtyPetIdsRef.current.add(requestedPetId);
          const failureCount = (failureCountRef.current.get(requestedPetId) ?? 0) + 1;
          failureCountRef.current.set(requestedPetId, failureCount);
          const retryDelay = Math.min(
            PUSH_SNAPSHOT_RETRY_MAX_MS,
            PUSH_SNAPSHOT_RETRY_BASE_MS * 2 ** Math.min(failureCount - 1, 3),
          );
          retryNotBeforeRef.current.set(requestedPetId, Date.now() + retryDelay);
        }
      })
      .finally(() => {
        inFlightRef.current.delete(requestedPetId);
        if (mountedRef.current) {
          runLatestRef.current();
        }
      });
  }, [clearPendingTimer, scheduleRun]);

  useEffect(() => {
    latestOptionsRef.current = options;
    runLatestRef.current = runLatest;
  });

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearPendingTimer();
    };
  }, [clearPendingTimer]);

  useEffect(() => subscribeLocalPetSnapshotDirty((changedPetId) => {
    const latest = latestOptionsRef.current;
    if (latest.petId === changedPetId && latest.pet?.petId === changedPetId) {
      runLatestRef.current();
    }
  }), []);

  useEffect(() => {
    runLatest();
  }, [options.pet, options.petId, options.status, runLatest]);
}
