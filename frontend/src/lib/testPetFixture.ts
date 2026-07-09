import type { LocalPetAssetSet, PetMood } from "./types";

const TEST_PET_IMAGE_BASE = "/test-pet";
const TEST_PET_SCENE_URL = `${TEST_PET_IMAGE_BASE}/scene.png`;

const sameSceneForEveryMood: Record<PetMood, string> = {
  idle: TEST_PET_SCENE_URL,
  happy: TEST_PET_SCENE_URL,
  hungry: TEST_PET_SCENE_URL,
  sad: TEST_PET_SCENE_URL,
};

export const TEST_PET_DESCRIPTION =
  "Маленький меланхоличный мышонок-исследователь в сшитом шлеме с компасом и старым ручным фонарем";

export const TEST_PET_ASSET_SET: LocalPetAssetSet = {
  assetSetId: "debug-test-pet-seedance-forest-mouse-v1",
  generatedAt: "2026-07-09T15:04:31.000Z",
  videoUrl: `${TEST_PET_IMAGE_BASE}/scene.mp4`,
  images: {
    baby: { ...sameSceneForEveryMood },
    teen: { ...sameSceneForEveryMood },
    adult: { ...sameSceneForEveryMood },
  },
};
