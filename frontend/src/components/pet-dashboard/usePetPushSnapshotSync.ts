"use client";

import { useEffect, useRef, type RefObject } from "react";

import { canUseTmaApi, registerPetPushSnapshot } from "@/lib/api";
import { buildMemorySnapshotContext } from "@/lib/localPetMemoryRecall";
import { readLocalPetMemory } from "@/lib/localPetMemoryStorage";
import { readLocalChatHistory } from "@/lib/localPetStorage";
import type { LocalPetState, PetStatsPatch } from "@/lib/types";

const PUSH_SNAPSHOT_SYNC_MIN_INTERVAL_MS = 10 * 60_000;

type UsePetPushSnapshotSyncOptions = {
  status: "loading" | "ready" | "empty" | "error";
  pet: LocalPetState | null;
  petId: string;
  recentAmbientRepliesRef: RefObject<string[]>;
  applyStatsPatch: (patch?: PetStatsPatch) => LocalPetState | null;
  applyLiteOverlayPatch: (patch?: Record<string, unknown>) => LocalPetState | null;
  applyStoryLibraryPatch: (patch?: Record<string, unknown>) => LocalPetState | null;
  applyRecentStoryEventsPatch: (patch?: Record<string, unknown>) => LocalPetState | null;
};

export function usePetPushSnapshotSync({
  status,
  pet,
  petId,
  recentAmbientRepliesRef,
  applyStatsPatch,
  applyLiteOverlayPatch,
  applyStoryLibraryPatch,
  applyRecentStoryEventsPatch,
}: UsePetPushSnapshotSyncOptions) {
  const lastSyncRef = useRef<{
    petId: string;
    syncedAt: number;
    updatedAt: string;
  } | null>(null);

  useEffect(() => {
    if (status !== "ready" || !pet || pet.petId !== petId || !canUseTmaApi()) {
      return;
    }

    const now = Date.now();
    const lastSync = lastSyncRef.current;
    if (
      lastSync?.petId === pet.petId &&
      lastSync.updatedAt === pet.updatedAt &&
      now - lastSync.syncedAt < PUSH_SNAPSHOT_SYNC_MIN_INTERVAL_MS
    ) {
      return;
    }

    lastSyncRef.current = {
      petId: pet.petId,
      syncedAt: now,
      updatedAt: pet.updatedAt,
    };

    void registerPetPushSnapshot(
      pet,
      buildMemorySnapshotContext(readLocalPetMemory(pet.petId)),
      {
      history: readLocalChatHistory().messages,
      recentAmbientReplies: recentAmbientRepliesRef.current,
      },
    )
      .then((response) => {
        applyStatsPatch(response.statsPatch ?? undefined);
        applyLiteOverlayPatch(response.liteOverlayPatch ?? undefined);
        applyStoryLibraryPatch(response.storyLibraryPatch ?? undefined);
        applyRecentStoryEventsPatch(response.recentStoryEventsPatch ?? undefined);
      })
      .catch(() => undefined);
  }, [
    applyLiteOverlayPatch,
    applyRecentStoryEventsPatch,
    applyStatsPatch,
    applyStoryLibraryPatch,
    pet,
    petId,
    recentAmbientRepliesRef,
    status,
  ]);
}
