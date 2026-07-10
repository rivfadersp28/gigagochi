import { beforeEach, describe, expect, it } from "vitest";

import { claimPetIntroduction } from "./localPetIntroduction";
import type { LocalPetState } from "./types";

function pet(overrides: Partial<LocalPetState> = {}): LocalPetState {
  const now = "2026-07-10T12:00:00.000Z";
  return {
    version: 2,
    petId: "pet-1",
    introductionPending: true,
    name: "Листик",
    description: "серый зверёк с листом вместо лица",
    createdAt: now,
    updatedAt: now,
    lastInteractionAt: now,
    lastStatsTickAt: now,
    lastStatTickAt: { hunger: now, happiness: now, energy: now },
    stage: "baby",
    mood: "idle",
    stats: { hunger: 80, happiness: 80, energy: 80 },
    assetSet: {
      assetSetId: "asset-1",
      generatedAt: now,
      characterBible: {
        openings: { first_message: "  Привет,   я Листик. Побудешь рядом?  " },
      },
      images: {
        baby: { idle: "", happy: "", hungry: "", sad: "" },
        teen: { idle: "", happy: "", hungry: "", sad: "" },
        adult: { idle: "", happy: "", hungry: "", sad: "" },
      },
    },
    ...overrides,
  };
}

describe("claimPetIntroduction", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("returns the generated introduction exactly once per pet", () => {
    expect(claimPetIntroduction(pet())).toBe("Привет, я Листик. Побудешь рядом?");
    expect(claimPetIntroduction(pet())).toBeNull();
    expect(claimPetIntroduction(pet({ petId: "pet-2" }))).toBe(
      "Привет, я Листик. Побудешь рядом?",
    );
  });

  it("falls back to the immutable template opening", () => {
    const state = pet();
    if (!state.assetSet) throw new Error("assetSet is required by the fixture");
    state.assetSet.characterBible = {};
    state.assetSet.characterTemplate = {
      openings: { first_message: "Я Листик. Кажется, я только что появился" },
    };

    expect(claimPetIntroduction(state)).toBe("Я Листик. Кажется, я только что появился");
  });

  it("does not consume the marker when no generated opening exists", () => {
    const state = pet();
    if (!state.assetSet) throw new Error("assetSet is required by the fixture");
    state.assetSet.characterBible = {};

    expect(claimPetIntroduction(state)).toBeNull();
    state.assetSet.characterBible = { openings: { first_message: "Я здесь" } };
    expect(claimPetIntroduction(state)).toBe("Я здесь");
  });

  it("treats pets saved before the feature as already introduced", () => {
    expect(claimPetIntroduction(pet({ introductionPending: undefined }))).toBeNull();
  });
});
