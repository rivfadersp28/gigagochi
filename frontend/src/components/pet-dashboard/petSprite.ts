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

const SAD_ASSET_CACHE_VERSION = "20260710-2";

export function versionedSadAssetUrl(url: string) {
  if (!url || url.includes("sad_asset_v=")) {
    return url;
  }
  return `${url}${url.includes("?") ? "&" : "?"}sad_asset_v=${SAD_ASSET_CACHE_VERSION}`;
}

function assetSetForPet(pet: LocalPetState) {
  return pet.assetSet ?? (process.env.NODE_ENV === "development" ? TEST_PET_ASSET_SET : undefined);
}

export function generatedSpriteUrl(pet: LocalPetState, stage: PetStage, state: PetState) {
  const imageUrl = assetSetForPet(pet)?.images[stage]?.[state] || null;
  return imageUrl && state === "sad" ? versionedSadAssetUrl(imageUrl) : imageUrl;
}

export function generatedSceneVideoUrl(pet: LocalPetState, sad = false) {
  const assetSet = assetSetForPet(pet);
  const videoUrl = (sad ? assetSet?.sadVideoUrl : assetSet?.videoUrl) || null;
  return videoUrl && sad ? versionedSadAssetUrl(videoUrl) : videoUrl;
}

export function isPetInRedZone(pet: LocalPetState) {
  return pet.stats.hunger < 30 || pet.stats.happiness < 30 || pet.stats.energy < 30;
}

export function hasGeneratedSadAssets(pet: LocalPetState) {
  const assetSet = assetSetForPet(pet);
  const sadImageUrl = assetSet?.images[pet.stage]?.sad;
  return Boolean(
    assetSet?.sadVideoUrl
    && sadImageUrl
    && sadImageUrl !== assetSet.images[pet.stage].idle,
  );
}
