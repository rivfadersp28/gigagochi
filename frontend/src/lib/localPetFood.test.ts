import { describe, expect, it } from "vitest";

import { applyFoodEffect, foodReactionPrompt, type FoodId } from "./localPetFood";

describe("localPetFood", () => {
  it.each([
    ["berry-bowl", { hunger: 65, happiness: 40, energy: 40 }],
    ["leaf-crunch", { hunger: 40, happiness: 15, energy: 65 }],
  ] satisfies [FoodId, { hunger: number; happiness: number; energy: number }][])(
    "applies the %s effect",
    (foodId, expectedStats) => {
      expect(
        applyFoodEffect({ hunger: 40, happiness: 40, energy: 40 }, foodId),
      ).toEqual(expectedStats);
    },
  );

  it("clamps changed stats to their valid range", () => {
    expect(
      applyFoodEffect({ hunger: 90, happiness: 10, energy: 90 }, "leaf-crunch"),
    ).toEqual({ hunger: 90, happiness: 0, energy: 100 });
  });

  it.each([
    ["berry-bowl", "Ням-ням!"],
    ["leaf-crunch", "Мне легче, но это ужасная гадость!!"],
  ] satisfies [FoodId, string][])(
    "provides a distinct reaction prompt for %s",
    (foodId, expectedText) => {
      expect(foodReactionPrompt(foodId)).toContain(expectedText);
    },
  );
});
