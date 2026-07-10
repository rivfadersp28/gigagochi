import { describe, expect, it } from "vitest";

import { characterBibleForApi } from "./api";
import type { LocalPetState } from "./types";

function petWithBibles(
  characterTemplate: Record<string, unknown>,
  characterBible: Record<string, unknown>,
): LocalPetState {
  return {
    version: 2,
    petId: "pet-1",
    description: "медный зверёк",
    stage: "adult",
    mood: "idle",
    stats: { hunger: 80, happiness: 80, energy: 80 },
    createdAt: "2026-07-10T06:00:00.000Z",
    updatedAt: "2026-07-10T06:00:00.000Z",
    lastInteractionAt: "2026-07-10T06:00:00.000Z",
    lastStatsTickAt: "2026-07-10T06:00:00.000Z",
    lastStatTickAt: {
      hunger: "2026-07-10T06:00:00.000Z",
      happiness: "2026-07-10T06:00:00.000Z",
      energy: "2026-07-10T06:00:00.000Z",
    },
    assetSet: {
      assetSetId: "asset-1",
      generatedAt: "2026-07-10T06:00:00.000Z",
      characterTemplate,
      characterBible,
      images: {
        baby: { idle: "", happy: "", hungry: "", sad: "" },
        teen: { idle: "", happy: "", hungry: "", sad: "" },
        adult: { idle: "", happy: "", hungry: "", sad: "" },
      },
    },
  };
}

describe("characterBibleForApi", () => {
  it("restores immutable voice and identity fields around a sparse instance", () => {
    const result = characterBibleForApi(
      petWithBibles(
        {
          identity: { name: "Медирог", species: "медный зверёк", role: "слухач дождя" },
          voice: { rules: ["говорит медленно"] },
          extensions: { generation: { pipeline: "v4" } },
        },
        {
          identity: { name: "Звон" },
          extensions: { lite_overlay: { facts: [] } },
        },
      ),
    );

    expect(result?.identity).toEqual({
      name: "Звон",
      species: "медный зверёк",
      role: "слухач дождя",
    });
    expect(result?.voice).toEqual({ rules: ["говорит медленно"] });
    expect(result?.extensions).toEqual({
      generation: { pipeline: "v4" },
      lite_overlay: { facts: [] },
    });
  });
});
