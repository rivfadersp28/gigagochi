import { clampStat } from "./localPetStats";
import type { LocalPetState, PetStatKey } from "./types";

export const FOOD_IDS = ["berry-bowl", "leaf-crunch"] as const;

export type FoodId = (typeof FOOD_IDS)[number];

const FOOD_STAT_DELTA: Record<FoodId, Partial<Record<PetStatKey, number>>> = {
  "berry-bowl": { hunger: 25 },
  "leaf-crunch": { happiness: -25, energy: 25 },
};

const FOOD_REACTION_PROMPT = {
  "berry-bowl":
    "Отреагируй на еду живо и очень коротко: 1–3 слова. Настроение реплики — довольное чавканье, например: «Ням-ням!». Каждый раз формулируй немного по-разному. Верни только реплику персонажа без объяснений.",
  "leaf-crunch":
    "Одной короткой живой фразой скажи, что тебе стало легче, но еда была отвратительной. Ориентир: «Мне легче, но это ужасная гадость!!». Каждый раз формулируй немного по-разному. Верни только реплику персонажа без объяснений.",
} satisfies Record<FoodId, string>;

export function applyFoodEffect(
  stats: LocalPetState["stats"],
  foodId: FoodId,
): LocalPetState["stats"] {
  const delta = FOOD_STAT_DELTA[foodId];

  return {
    hunger: clampStat(stats.hunger + (delta.hunger ?? 0)),
    happiness: clampStat(stats.happiness + (delta.happiness ?? 0)),
    energy: clampStat(stats.energy + (delta.energy ?? 0)),
  };
}

export function foodReactionPrompt(foodId: FoodId): string {
  return FOOD_REACTION_PROMPT[foodId];
}
