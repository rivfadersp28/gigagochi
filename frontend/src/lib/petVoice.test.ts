import { describe, expect, it } from "vitest";

import { applyPetVoice } from "./petVoice";
import type { LocalPetState } from "./types";

function petWithCatchphrase(): LocalPetState {
  const now = "2026-07-14T00:00:00.000Z";
  return {
    version: 2,
    petId: "astel",
    description: "ледяной дракон",
    createdAt: now,
    updatedAt: now,
    lastInteractionAt: now,
    lastStatsTickAt: now,
    lastStatTickAt: {
      hunger: now,
      happiness: now,
      energy: now,
    },
    stage: "teen",
    mood: "happy",
    stats: {
      hunger: 100,
      happiness: 100,
      energy: 100,
    },
    assetSet: {
      assetSetId: "astel-assets",
      generatedAt: now,
      images: {
        baby: { idle: "", happy: "", hungry: "", sad: "" },
        teen: { idle: "", happy: "", hungry: "", sad: "" },
        adult: { idle: "", happy: "", hungry: "", sad: "" },
      },
      characterBible: {
        voice: {
          catchphrases: ["Следы не лгут."],
        },
      },
    },
  };
}

describe("applyPetVoice", () => {
  it("does not append a catchphrase to local tap feedback", () => {
    expect(applyPetVoice("Приятно!", petWithCatchphrase(), { mode: "local" })).toBe(
      "Приятно!",
    );
  });

  it("does not append a catchphrase to system fallback text", () => {
    expect(applyPetVoice("…", petWithCatchphrase(), { mode: "system" })).toBe("…");
  });
});
