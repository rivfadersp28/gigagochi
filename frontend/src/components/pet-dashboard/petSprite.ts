import type { LocalPetState, PetLifeStage, PetMood } from "@/lib/types";
import { TEST_PET_ASSET_SET } from "@/lib/testPetFixture";

export type PetStage = PetLifeStage;
export type PetState = PetMood;

export const stageLabels: Record<PetStage, string> = {
  baby: "Baby",
  teen: "Teen",
  adult: "Adult",
};

export const stateLabels: Record<PetState, string> = {
  idle: "Idle",
  happy: "Happy",
  sad: "Sad",
  hungry: "Hungry",
};

function assetSetForPet(pet: LocalPetState) {
  return pet.assetSet ?? (process.env.NODE_ENV === "development" ? TEST_PET_ASSET_SET : undefined);
}

export function generatedSpriteUrl(pet: LocalPetState, stage: PetStage, state: PetState) {
  return assetSetForPet(pet)?.images[stage]?.[state] || null;
}

export function generatedSceneVideoUrl(pet: LocalPetState) {
  return assetSetForPet(pet)?.videoUrl || null;
}
