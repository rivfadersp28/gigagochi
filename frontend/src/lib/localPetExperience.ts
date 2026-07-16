export const PET_EXPERIENCE_MAX = 3_000;
export const PET_INITIAL_EXPERIENCE = 0;
export const PET_LEGACY_EXPERIENCE = 450;
export const PET_STORY_EXPERIENCE_MAX = 200;
export const PET_OUTFIT_EXPERIENCE_COST = 200;

export function clampPetExperience(value: unknown) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return PET_LEGACY_EXPERIENCE;
  }
  return Math.max(0, Math.min(PET_EXPERIENCE_MAX, Math.floor(value)));
}

export function petExperiencePercent(value: unknown) {
  return (clampPetExperience(value) / PET_EXPERIENCE_MAX) * 100;
}
