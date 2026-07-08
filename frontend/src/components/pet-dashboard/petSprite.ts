import type { LocalPetState, PetLifeStage, PetMood } from "@/lib/types";

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

export const spriteStageOptions = [
  { value: "baby", label: "Малой" },
  { value: "teen", label: "Подросток" },
  { value: "adult", label: "Взрослый" },
] satisfies Array<{ value: PetStage; label: string }>;

export const spriteStateOptions = [
  { value: "idle", label: "Норм" },
  { value: "hungry", label: "Голодный" },
  { value: "sad", label: "Грустный" },
  { value: "happy", label: "Счастливый" },
] satisfies Array<{ value: PetState; label: string }>;

export function generatedSpriteUrl(pet: LocalPetState, stage: PetStage, state: PetState) {
  return pet.assetSet?.images[stage]?.[state] || null;
}
