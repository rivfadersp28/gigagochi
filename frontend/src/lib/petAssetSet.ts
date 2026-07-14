import { TEST_PET_ASSET_SET } from "./testPetFixture";
import type { LocalPetAssetSet, LocalPetState } from "./types";

export function assetSetForPet(pet: LocalPetState): LocalPetAssetSet | undefined {
  return pet.assetSet ??
    (process.env.NODE_ENV === "development" ? TEST_PET_ASSET_SET : undefined);
}
