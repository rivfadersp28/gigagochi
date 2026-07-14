import { beforeEach, describe, expect, it } from "vitest";

import { readLocalInteractiveTravel, writeLocalInteractiveTravel } from "./localInteractiveTravel";
import type { InteractiveTravelState } from "./types";

function completedTravel(): InteractiveTravelState {
  return {
    travelId: "travel-six-parts",
    generatedAt: "2026-07-14T12:00:00Z",
    destination: "облачный город",
    overallTitle: "Часы облачного города",
    arcPlan: { goal: "Запустить часы" },
    parts: Array.from({ length: 6 }, (_, index) => ({
      partNumber: index + 1,
      title: `Часть ${index + 1}`,
      storyText: `Ситуация ${index + 1}.`,
      transition:
        index === 0
          ? undefined
          : {
              elapsedHours: 4,
              summary: "За несколько часов конфликт изменился.",
            },
      challenge: "Что делать дальше?",
      actionSuggestions: ["Идти дальше", "Осмотреться", "Позвать на помощь"],
      answer: `Ответ ${index + 1}`,
      result: {
        text: `Результат ${index + 1}.`,
        adviceAssessment: "helpful" as const,
        reaction: "Так и поступлю!",
        reactionTone: "determined" as const,
        consequence: "История изменилась.",
        outcomeValence: "positive" as const,
        statImpacts: [],
      },
    })),
    completed: true,
    outcomeValence: "positive",
  };
}

describe("local interactive travel storage", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("keeps applied results through the sixth part and ignores invalid indexes", () => {
    writeLocalInteractiveTravel("pet-1", {
      travel: completedTravel(),
      appliedResultParts: [1, 5, 6, 7],
      presentation: { phase: "completed", partNumber: 6, portionIndex: 0 },
    });

    expect(readLocalInteractiveTravel("pet-1")?.appliedResultParts).toEqual([1, 5, 6]);
  });

  it("does not restore an incompatible v1 story", () => {
    window.localStorage.setItem(
      "gigagochi.interactive-travel.v1:pet-1",
      JSON.stringify({ travel: completedTravel(), appliedImpactParts: [1, 2, 3] }),
    );

    expect(readLocalInteractiveTravel("pet-1")).toBeNull();
  });

  it("migrates a v2 story to a safe presentation phase", () => {
    window.localStorage.setItem(
      "gigagochi.interactive-travel.v2:pet-1",
      JSON.stringify({ travel: completedTravel(), appliedResultParts: [1, 2, 3] }),
    );

    expect(readLocalInteractiveTravel("pet-1")?.presentation).toEqual({
      phase: "completed",
      partNumber: 6,
      portionIndex: 0,
    });
  });
});
