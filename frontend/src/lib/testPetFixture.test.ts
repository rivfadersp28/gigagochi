import { describe, expect, it } from "vitest";

import {
  refreshedTestPetAssetSet,
  TEST_PET_ASSET_SET,
} from "./testPetFixture";

describe("refreshedTestPetAssetSet", () => {
  it("adds Kandinsky assets to a stale test-pet asset set", () => {
    const characterBible = { identity: { name: "Локальное имя" } };
    const staleAssetSet = {
      ...TEST_PET_ASSET_SET,
      characterBible,
      kandinskyAssets: undefined,
    };

    const refreshed = refreshedTestPetAssetSet(staleAssetSet);

    expect(refreshed?.kandinskyAssets).toEqual(TEST_PET_ASSET_SET.kandinskyAssets);
    expect(refreshed?.characterBible).toBe(characterBible);
  });

  it("does not refresh the current test-pet asset set", () => {
    expect(refreshedTestPetAssetSet(TEST_PET_ASSET_SET)).toBeNull();
  });
});
