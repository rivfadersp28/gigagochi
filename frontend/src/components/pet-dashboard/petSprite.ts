import type { LocalPetState, PetLifeStage, PetMood } from "@/lib/types";
import { TEST_PET_ASSET_SET } from "@/lib/testPetFixture";

export type PetStage = PetLifeStage;
export type PetState = PetMood;
export type PetVisualMode = "normal" | "sad" | "happy";

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
const HAPPY_ASSET_CACHE_VERSION = "20260710-1";

export function versionedSadAssetUrl(url: string) {
  if (!url || url.includes("sad_asset_v=")) {
    return url;
  }
  return `${url}${url.includes("?") ? "&" : "?"}sad_asset_v=${SAD_ASSET_CACHE_VERSION}`;
}

export function versionedHappyAssetUrl(url: string) {
  if (!url || url.includes("happy_asset_v=")) {
    return url;
  }
  return `${url}${url.includes("?") ? "&" : "?"}happy_asset_v=${HAPPY_ASSET_CACHE_VERSION}`;
}

function assetSetForPet(pet: LocalPetState) {
  return pet.assetSet ?? (process.env.NODE_ENV === "development" ? TEST_PET_ASSET_SET : undefined);
}

export function generatedSpriteUrl(pet: LocalPetState, stage: PetStage, state: PetState) {
  const imageUrl = assetSetForPet(pet)?.images[stage]?.[state] || null;
  if (!imageUrl) {
    return null;
  }
  if (state === "sad") {
    return versionedSadAssetUrl(imageUrl);
  }
  if (state === "happy") {
    return versionedHappyAssetUrl(imageUrl);
  }
  return imageUrl;
}

export function generatedSceneVideoUrl(
  pet: LocalPetState,
  mode: PetVisualMode = "normal",
) {
  const assetSet = assetSetForPet(pet);
  const videoUrl = mode === "sad"
    ? assetSet?.sadVideoUrl
    : mode === "happy"
      ? assetSet?.happyVideoUrl
      : assetSet?.videoUrl;
  if (!videoUrl) {
    return null;
  }
  if (mode === "sad") {
    return versionedSadAssetUrl(videoUrl);
  }
  if (mode === "happy") {
    return versionedHappyAssetUrl(videoUrl);
  }
  return videoUrl;
}

export function isPetInRedZone(pet: LocalPetState) {
  return pet.stats.hunger < 30 || pet.stats.happiness < 30 || pet.stats.energy < 30;
}

export function isPetInGreenZone(pet: LocalPetState) {
  return pet.stats.hunger >= 70 && pet.stats.happiness >= 70 && pet.stats.energy >= 70;
}

export function automaticPetVisualMode(pet: LocalPetState): PetVisualMode {
  if (isPetInRedZone(pet)) {
    return "sad";
  }
  if (isPetInGreenZone(pet)) {
    return "happy";
  }
  return "normal";
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

export function hasGeneratedHappyAssets(pet: LocalPetState) {
  const assetSet = assetSetForPet(pet);
  const happyImageUrl = assetSet?.images[pet.stage]?.happy;
  return Boolean(
    assetSet?.happyVideoUrl
    && happyImageUrl
    && happyImageUrl !== assetSet.images[pet.stage].idle,
  );
}

export function isPetVisualModeReady(pet: LocalPetState, mode: PetVisualMode) {
  if (mode === "sad") {
    return hasGeneratedSadAssets(pet);
  }
  if (mode === "happy") {
    return hasGeneratedHappyAssets(pet);
  }
  return Boolean(assetSetForPet(pet)?.images[pet.stage]?.idle);
}

export function resolvedPetVisualMode(
  pet: LocalPetState,
  override: PetVisualMode | null,
  derivedAssetsEnabled = true,
): PetVisualMode {
  if (!derivedAssetsEnabled) {
    return "normal";
  }
  const requestedMode = override ?? automaticPetVisualMode(pet);
  return isPetVisualModeReady(pet, requestedMode) ? requestedMode : "normal";
}

export function generatedVisualPosterUrl(
  pet: LocalPetState,
  mode: PetVisualMode,
) {
  const state = mode === "normal" ? "idle" : mode;
  return generatedSpriteUrl(pet, pet.stage, state);
}
