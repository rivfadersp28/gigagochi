"use client";

import { useEffect } from "react";

import { ApiError, refreshPetBackgroundAssets } from "@/lib/api";
import type { LocalPetAssetSet } from "@/lib/types";

import { versionedHappyAssetUrl, versionedSadAssetUrl } from "./petSprite";

const BACKGROUND_ASSET_POLL_MS = 2_000;

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
  applyGeneratedAssets: (assetSet: LocalPetAssetSet) => unknown;
  derivedAssetsEnabled: boolean;
};

export function shouldPollBackgroundAssets(assetSet?: LocalPetAssetSet): boolean {
  if (!assetSet?.generationJobId || assetSet.comparisonGenerationError) {
    return false;
  }
  return assetSet.backgroundGenerationStatus === "running" || !assetSet.kandinskyAssets;
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
  applyGeneratedAssets,
  derivedAssetsEnabled,
}: UsePetBackgroundAssetsOptions) {
  const jobId = assetSet?.generationJobId;
  const status = assetSet?.backgroundGenerationStatus;
  const updatedAt = assetSet?.backgroundGenerationUpdatedAt;

  useEffect(() => {
    if (!derivedAssetsEnabled || !assetSet || !shouldPollBackgroundAssets(assetSet)) {
      return;
    }

    let cancelled = false;
    let timeoutId: number | undefined;

    const poll = async () => {
      try {
        const refreshed = await refreshPetBackgroundAssets(assetSet);
        if (cancelled) {
          return;
        }
        if (
          refreshed.backgroundGenerationUpdatedAt !== updatedAt
          || refreshed.backgroundGenerationStatus !== status
          || refreshed.kandinskyAssets?.assetSetId !== assetSet.kandinskyAssets?.assetSetId
        ) {
          applyGeneratedAssets(refreshed);
          return;
        }
      } catch (error) {
        if (cancelled) {
          return;
        }
        if (error instanceof ApiError && error.code === "GENERATION_JOB_NOT_FOUND") {
          applyGeneratedAssets(backgroundAssetsAfterJobNotFound(assetSet, error.message));
          return;
        }
      }
      timeoutId = window.setTimeout(poll, BACKGROUND_ASSET_POLL_MS);
    };

    void poll();
    return () => {
      cancelled = true;
      if (timeoutId !== undefined) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [applyGeneratedAssets, assetSet, derivedAssetsEnabled, jobId, status, updatedAt]);

  useEffect(() => {
    if (!derivedAssetsEnabled || !assetSet || jobId || status || assetSet.sadVideoUrl) {
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
        applyGeneratedAssets({
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
        });
      } catch {
        // Legacy assets are optional; a missing file keeps the current idle fallback.
      }
    };

    void discover();
    return () => {
      cancelled = true;
    };
  }, [applyGeneratedAssets, assetSet, derivedAssetsEnabled, jobId, status]);

  useEffect(() => {
    if (
      !derivedAssetsEnabled
      || !assetSet
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
        applyGeneratedAssets({
          ...assetSet,
          images: {
            baby: { ...assetSet.images.baby, happy: happyAssets.imageUrl },
            teen: { ...assetSet.images.teen, happy: happyAssets.imageUrl },
            adult: { ...assetSet.images.adult, happy: happyAssets.imageUrl },
          },
          happyVideoUrl: happyAssets.videoUrl,
          backgroundGenerationUpdatedAt: new Date().toISOString(),
        });
      } catch {
        // A missing derived asset keeps the current normal fallback.
      }
    };

    void discover();
    return () => {
      cancelled = true;
    };
  }, [applyGeneratedAssets, assetSet, derivedAssetsEnabled, status]);
}
