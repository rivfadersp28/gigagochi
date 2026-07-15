import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { claimPetIntroduction, clearPetIntroduction } from "./localPetIntroduction";
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

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns a neutral introduction exactly once per pet", () => {
    expect(claimPetIntroduction(pet())).toBe("Я Листик. Как тебя зовут?");
    expect(claimPetIntroduction(pet())).toBeNull();
    expect(claimPetIntroduction(pet({ petId: "pet-2" }))).toBe(
      "Я Листик. Как тебя зовут?",
    );
  });

  it("falls back to the immutable template name", () => {
    const state = pet({ name: undefined });
    if (!state.assetSet) throw new Error("assetSet is required by the fixture");
    state.assetSet.characterBible = {};
    state.assetSet.characterTemplate = {
      identity: { name: "Листик" },
      openings: { first_message: "Я Листик. Кажется, я только что появился" },
    };

    expect(claimPetIntroduction(state)).toBe("Я Листик. Как тебя зовут?");
  });

  it("uses a neutral nameless introduction when no name exists", () => {
    const state = pet({ name: undefined });
    if (!state.assetSet) throw new Error("assetSet is required by the fixture");
    state.assetSet.characterBible = {};

    expect(claimPetIntroduction(state)).toBe("Как тебя зовут?");
    expect(claimPetIntroduction(state)).toBeNull();
  });

  it("treats pets saved before the feature as already introduced", () => {
    expect(claimPetIntroduction(pet({ introductionPending: undefined }))).toBeNull();
  });

  it("falls back to a session one-shot when WebView storage is unavailable", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });
    const state = pet({ petId: "storage-blocked-pet" });

    expect(claimPetIntroduction(state)).toBe("Я Листик. Как тебя зовут?");
    expect(claimPetIntroduction(state)).toBeNull();

    getItem.mockRestore();
    setItem.mockRestore();
    expect(claimPetIntroduction(state)).toBeNull();
    expect(
      window.localStorage.getItem("tamagochi:v1:introduction-shown:storage-blocked-pet"),
    ).toBe("1");
  });

  it("removes a persisted introduction claim during pet cleanup", () => {
    const state = pet({ petId: "pet-cleanup" });
    expect(claimPetIntroduction(state)).not.toBeNull();

    clearPetIntroduction(state.petId);

    expect(
      window.localStorage.getItem("tamagochi:v1:introduction-shown:pet-cleanup"),
    ).toBeNull();
  });
});
