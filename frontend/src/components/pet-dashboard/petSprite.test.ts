import { describe, expect, it } from "vitest";

import type { LocalPetState } from "@/lib/types";

import {
  generatedSceneVideoUrl,
  generatedSpriteUrl,
  hasGeneratedSadAssets,
  isPetInRedZone,
} from "./petSprite";
import { legacySadAssetUrls } from "./usePetBackgroundAssets";

function pet(): LocalPetState {
  const tick = "2026-07-10T10:00:00.000Z";
  return {
    version: 2,
    petId: "pet-1",
    description: "мышонок",
    createdAt: tick,
    updatedAt: tick,
    lastInteractionAt: tick,
    lastStatsTickAt: tick,
    lastStatTickAt: { hunger: tick, happiness: tick, energy: tick },
    stage: "teen",
    mood: "idle",
    stats: { hunger: 80, happiness: 80, energy: 80 },
    assetSet: {
      assetSetId: "asset-1",
      generatedAt: tick,
      videoUrl: "/idle.mp4",
      sadVideoUrl: "/sad.mp4",
      images: {
        baby: { idle: "/idle.png", happy: "/idle.png", hungry: "/idle.png", sad: "/sad.png" },
        teen: { idle: "/idle.png", happy: "/idle.png", hungry: "/idle.png", sad: "/sad.png" },
        adult: { idle: "/idle.png", happy: "/idle.png", hungry: "/idle.png", sad: "/sad.png" },
      },
    },
  };
}

describe("sad pet assets", () => {
  it.each(["hunger", "happiness", "energy"] as const)(
    "treats %s below 30 as a red zone",
    (stat) => {
      const state = pet();
      state.stats[stat] = 29;
      expect(isPetInRedZone(state)).toBe(true);
    },
  );

  it("keeps the exact value 30 outside the red zone", () => {
    const state = pet();
    state.stats = { hunger: 30, happiness: 30, energy: 30 };
    expect(isPetInRedZone(state)).toBe(false);
  });

  it("requires both a distinct sad image and a sad video", () => {
    const state = pet();
    expect(hasGeneratedSadAssets(state)).toBe(true);

    state.assetSet!.images.teen.sad = state.assetSet!.images.teen.idle;
    expect(hasGeneratedSadAssets(state)).toBe(false);
  });

  it("derives legacy sad asset URLs from the generated idle scene", () => {
    const state = pet();
    state.assetSet!.images.teen.idle =
      "https://example.test/static/generated/asset-1/teen-idle.png?v=7";

    expect(legacySadAssetUrls(state.assetSet!)).toEqual({
      imageUrl:
        "https://example.test/static/generated/asset-1/teen-sad.png?v=7&sad_asset_v=20260710-2",
      videoUrl:
        "https://example.test/static/generated/asset-1/teen-sad.mp4?v=7&sad_asset_v=20260710-2",
    });
  });

  it("cache-busts only rendered sad assets", () => {
    const state = pet();

    expect(generatedSpriteUrl(state, "teen", "sad")).toContain("sad_asset_v=20260710-2");
    expect(generatedSceneVideoUrl(state, true)).toContain("sad_asset_v=20260710-2");
    expect(generatedSpriteUrl(state, "teen", "idle")).toBe("/idle.png");
    expect(generatedSceneVideoUrl(state)).toBe("/idle.mp4");
  });
});
