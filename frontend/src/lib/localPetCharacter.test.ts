import { describe, expect, it } from "vitest";

import { characterNameFromAssetSet } from "./localPetCharacter";
import type { LocalPetAssetSet } from "./types";

const moods = {
  idle: "idle.png",
  happy: "happy.png",
  hungry: "hungry.png",
  sad: "sad.png",
};

function assetSet(
  characterBible?: Record<string, unknown>,
  characterTemplate?: Record<string, unknown>,
): LocalPetAssetSet {
  return {
    assetSetId: "asset-1",
    generatedAt: "2026-07-10T06:00:00.000Z",
    characterBible,
    characterTemplate,
    images: {
      baby: { ...moods },
      teen: { ...moods },
      adult: { ...moods },
    },
  };
}

describe("characterNameFromAssetSet", () => {
  it("prefers the mutable character name", () => {
    expect(
      characterNameFromAssetSet(
        assetSet(
          { identity: { name: " Мира " } },
          { identity: { name: "Луна" } },
        ),
      ),
    ).toBe("Мира");
  });

  it("falls back to the immutable template when the mutable bible is overlay-only", () => {
    expect(
      characterNameFromAssetSet(
        assetSet(
          { extensions: { lite_overlay: { facts: [] } } },
          { identity: { name: "  Луна   Ночная  " } },
        ),
      ),
    ).toBe("Луна Ночная");
  });

  it("supports a legacy top-level template name", () => {
    expect(characterNameFromAssetSet(assetSet(undefined, { name: "Искра" }))).toBe("Искра");
  });
});
