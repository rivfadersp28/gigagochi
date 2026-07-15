"use client";

import { useEffect } from "react";

import { ApiError, refreshPetBackgroundAssets } from "@/lib/api";
import type { LocalPetAssetSet } from "@/lib/types";

import { versionedHappyAssetUrl, versionedSadAssetUrl } from "./petSprite";

const BACKGROUND_ASSET_POLL_DELAYS_MS = [2_000, 4_000, 8_000, 15_000, 30_000, 60_000] as const;

export function legacySadAssetUrls(assetSet: LocalPetAssetSet) {
  const idleUrl = assetSet.images.teen.idle;
  if (!/teen-idle\.png(?:\?|$)/.test(idleUrl)) {
    return null;
  }
  return {
    imageUrl: versionedSadAssetUrl(
      idleUrl.replace(/teen-idle\.png(?=\?|$)/, "teen-sad.png"),
    ),
    videoUrl: versionedSadAssetUrl(
      idleUrl.replace(/teen-idle\.png(?=\?|$)/, "teen-sad.mp4"),
    ),
  };
}

export function legacyHappyAssetUrls(assetSet: LocalPetAssetSet) {
  const idleUrl = assetSet.images.teen.idle;
  if (!/teen-idle\.png(?:\?|$)/.test(idleUrl)) {
    return null;
  }
  return {
    imageUrl: versionedHappyAssetUrl(
      idleUrl.replace(/teen-idle\.png(?=\?|$)/, "teen-happy.png"),
    ),
    videoUrl: versionedHappyAssetUrl(
      idleUrl.replace(/teen-idle\.png(?=\?|$)/, "teen-happy.mp4"),
    ),
  };
}

type UsePetBackgroundAssetsOptions = {
  assetSet?: LocalPetAssetSet;
  petId?: string;
  applyGeneratedAssets: (
    assetSet: LocalPetAssetSet,
    expectedPetId?: string,
    expectedBaseAssetSet?: LocalPetAssetSet,
  ) => unknown;
  derivedAssetsEnabled: boolean;
};

export function shouldPollBackgroundAssets(assetSet?: LocalPetAssetSet): boolean {
  return Boolean(
    assetSet?.generationJobId
    && assetSet.backgroundGenerationStatus === "running",
  );
}

export function backgroundAssetsAfterJobNotFound(
  assetSet: LocalPetAssetSet,
  message: string,
): LocalPetAssetSet {
  return {
    ...assetSet,
    generationJobId: undefined,
    backgroundGenerationStatus: "failed",
    backgroundGenerationPhase: "completed",
    backgroundGenerationError: message,
    backgroundGenerationUpdatedAt: new Date().toISOString(),
  };
}

export function usePetBackgroundAssets({
  assetSet,
  petId,
  applyGeneratedAssets,
  derivedAssetsEnabled,
}: UsePetBackgroundAssetsOptions) {
  const jobId = assetSet?.generationJobId;
  const status = assetSet?.backgroundGenerationStatus;
  const updatedAt = assetSet?.backgroundGenerationUpdatedAt;

  useEffect(() => {
    if (!derivedAssetsEnabled || !assetSet || !petId || !shouldPollBackgroundAssets(assetSet)) {
      return;
    }

    let cancelled = false;
    let timeoutId: number | undefined;
    let controller: AbortController | undefined;
    let pollInFlight = false;
    let pollAttempt = 0;
    let terminal = false;

    const canPoll = () => navigator.onLine !== false && document.visibilityState !== "hidden";

    const clearScheduledPoll = () => {
      if (timeoutId !== undefined) {
        window.clearTimeout(timeoutId);
        timeoutId = undefined;
      }
    };

    const scheduleNextPoll = () => {
      if (cancelled || terminal || pollInFlight || !canPoll()) {
        return;
      }
      const delay = BACKGROUND_ASSET_POLL_DELAYS_MS[
        Math.min(pollAttempt, BACKGROUND_ASSET_POLL_DELAYS_MS.length - 1)
      ];
      pollAttempt += 1;
      timeoutId = window.setTimeout(() => {
        timeoutId = undefined;
        void poll();
      }, delay);
    };

    const poll = async () => {
      if (cancelled || terminal || pollInFlight || !canPoll()) {
        return;
      }
      pollInFlight = true;
      const currentController = new AbortController();
      controller = currentController;
      try {
        const refreshed = await refreshPetBackgroundAssets(assetSet, {
          signal: currentController.signal,
        });
        if (cancelled) {
          return;
        }
        if (
          refreshed.backgroundGenerationUpdatedAt !== updatedAt
          || refreshed.backgroundGenerationStatus !== status
          || refreshed.kandinskyAssets?.assetSetId !== assetSet.kandinskyAssets?.assetSetId
        ) {
          terminal = true;
          applyGeneratedAssets(refreshed, petId, assetSet);
          return;
        }
      } catch (error) {
        if (cancelled || currentController.signal.aborted) {
          return;
        }
        if (error instanceof ApiError && error.code === "GENERATION_JOB_NOT_FOUND") {
          terminal = true;
          applyGeneratedAssets(
            backgroundAssetsAfterJobNotFound(assetSet, error.message),
            petId,
            assetSet,
          );
          return;
        }
      } finally {
        pollInFlight = false;
        if (controller === currentController) {
          controller = undefined;
        }
        scheduleNextPoll();
      }
    };

    const handlePollingAvailabilityChange = () => {
      clearScheduledPoll();
      if (!canPoll() || pollInFlight) {
        return;
      }
      pollAttempt = 0;
      void poll();
    };

    document.addEventListener("visibilitychange", handlePollingAvailabilityChange);
    window.addEventListener("online", handlePollingAvailabilityChange);
    window.addEventListener("offline", handlePollingAvailabilityChange);
    void poll();
    return () => {
      cancelled = true;
      terminal = true;
      clearScheduledPoll();
      controller?.abort();
      document.removeEventListener("visibilitychange", handlePollingAvailabilityChange);
      window.removeEventListener("online", handlePollingAvailabilityChange);
      window.removeEventListener("offline", handlePollingAvailabilityChange);
    };
  }, [applyGeneratedAssets, assetSet, derivedAssetsEnabled, jobId, petId, status, updatedAt]);

  useEffect(() => {
    if (!derivedAssetsEnabled || !assetSet || !petId || jobId || status || assetSet.sadVideoUrl) {
      return;
    }
    const sadAssets = legacySadAssetUrls(assetSet);
    if (!sadAssets) {
      return;
    }

    let cancelled = false;
    const discover = async () => {
      try {
        const responses = await Promise.all([
          fetch(sadAssets.imageUrl, { method: "HEAD" }),
          fetch(sadAssets.videoUrl, { method: "HEAD" }),
        ]);
        if (cancelled || responses.some((response) => !response.ok)) {
          return;
        }
        applyGeneratedAssets(
          {
            ...assetSet,
            images: {
              baby: { ...assetSet.images.baby, sad: sadAssets.imageUrl },
              teen: { ...assetSet.images.teen, sad: sadAssets.imageUrl },
              adult: { ...assetSet.images.adult, sad: sadAssets.imageUrl },
            },
            sadVideoUrl: sadAssets.videoUrl,
            backgroundGenerationStatus: "succeeded",
            backgroundGenerationPhase: "completed",
            backgroundGenerationUpdatedAt: new Date().toISOString(),
          },
          petId,
          assetSet,
        );
      } catch {
        // Legacy assets are optional; a missing file keeps the current idle fallback.
      }
    };

    void discover();
    return () => {
      cancelled = true;
    };
  }, [applyGeneratedAssets, assetSet, derivedAssetsEnabled, jobId, petId, status]);

  useEffect(() => {
    if (
      !derivedAssetsEnabled
      || !assetSet
      || !petId
      || status === "running"
      || assetSet.happyVideoUrl
    ) {
      return;
    }
    const happyAssets = legacyHappyAssetUrls(assetSet);
    if (!happyAssets) {
      return;
    }

    let cancelled = false;
    const discover = async () => {
      try {
        const responses = await Promise.all([
          fetch(happyAssets.imageUrl, { method: "HEAD" }),
          fetch(happyAssets.videoUrl, { method: "HEAD" }),
        ]);
        if (cancelled || responses.some((response) => !response.ok)) {
          return;
        }
        applyGeneratedAssets(
          {
            ...assetSet,
            images: {
              baby: { ...assetSet.images.baby, happy: happyAssets.imageUrl },
              teen: { ...assetSet.images.teen, happy: happyAssets.imageUrl },
              adult: { ...assetSet.images.adult, happy: happyAssets.imageUrl },
            },
            happyVideoUrl: happyAssets.videoUrl,
            backgroundGenerationUpdatedAt: new Date().toISOString(),
          },
          petId,
          assetSet,
        );
      } catch {
        // A missing derived asset keeps the current normal fallback.
      }
    };

    void discover();
    return () => {
      cancelled = true;
    };
  }, [applyGeneratedAssets, assetSet, derivedAssetsEnabled, petId, status]);
}
