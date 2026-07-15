import { describe, expect, it } from "vitest";

import {
  applyInteractiveTravelImpactsToPet,
  MAX_TRAVEL_IMPACT_RECEIPTS,
  normalizeTravelImpactReceipts,
} from "./localPetTravelImpacts";
import type { InteractiveTravelPart, LocalPetState, PetStatKey } from "./types";

const timestamp = "2026-07-15T12:00:00.000Z";

function petState(overrides: Partial<LocalPetState> = {}): LocalPetState {
  return {
    version: 2,
    petId: "pet-1",
    description: "мышонок",
    createdAt: timestamp,
    updatedAt: timestamp,
    lastInteractionAt: timestamp,
    lastStatsTickAt: timestamp,
    lastStatTickAt: { hunger: timestamp, happiness: timestamp, energy: timestamp },
    stage: "baby",
    mood: "idle",
    stats: { hunger: 50, happiness: 50, energy: 50 },
    ...overrides,
  };
}

function resolvedPart(
  partNumber: number,
  statImpacts: Array<{ stat: PetStatKey; amount: number }>,
): InteractiveTravelPart {
  return {
    partNumber,
    title: `Часть ${partNumber}`,
    storyText: "История",
    transition:
      partNumber === 1
        ? undefined
        : { elapsedHours: 4, summary: "Прошло четыре часа." },
    challenge: "Что делать?",
    actionSuggestions: [],
    answer: "Идти",
    result: {
      text: "Получилось.",
      adviceAssessment: "helpful",
      reaction: "Ура!",
      reactionTone: "enthusiastic",
      consequence: "Путь открыт.",
      outcomeValence: "positive",
      statImpacts: statImpacts.map((impact) => ({ ...impact, reason: "Результат" })),
    },
  };
}

describe("local pet interactive-travel impact receipts", () => {
  it("does not replay an impact after stats and receipt survive a session crash", () => {
    const part = resolvedPart(1, [{ stat: "hunger", amount: 4 }]);
    const first = applyInteractiveTravelImpactsToPet(
      petState(),
      "travel-1",
      [part],
    );
    const replay = applyInteractiveTravelImpactsToPet(
      first.pet,
      "travel-1",
      [part],
    );

    expect(first.pet.stats.hunger).toBe(54);
    expect(first.pet.travelImpactReceipts).toEqual(["travel-1:1"]);
    expect(replay.pet).toBe(first.pet);
    expect(replay.pet.stats.hunger).toBe(54);
    expect(replay.appliedResultParts).toEqual([1]);
    expect(replay.newlyAppliedResultParts).toEqual([]);
  });

  it("commits two sequential result calls from the latest pet state", () => {
    const first = applyInteractiveTravelImpactsToPet(
      petState(),
      "travel-1",
      [resolvedPart(1, [{ stat: "hunger", amount: 4 }])],
    );
    const second = applyInteractiveTravelImpactsToPet(
      first.pet,
      "travel-1",
      [
        resolvedPart(1, [{ stat: "hunger", amount: 4 }]),
        resolvedPart(2, [
          { stat: "hunger", amount: -3 },
          { stat: "energy", amount: 5 },
        ]),
      ],
    );

    expect(second.pet.stats).toEqual({ hunger: 51, happiness: 50, energy: 55 });
    expect(second.pet.travelImpactReceipts).toEqual(["travel-1:1", "travel-1:2"]);
    expect(second.appliedResultParts).toEqual([1, 2]);
    expect(second.newlyAppliedResultParts).toEqual([2]);
  });

  it("migrates legacy session markers into receipts without changing stats", () => {
    const alreadyAppliedState = petState({
      stats: { hunger: 54, happiness: 50, energy: 50 },
    });
    const migrated = applyInteractiveTravelImpactsToPet(
      alreadyAppliedState,
      "travel-legacy",
      [resolvedPart(1, [{ stat: "hunger", amount: 4 }])],
      [1],
    );

    expect(migrated.pet.stats.hunger).toBe(54);
    expect(migrated.pet.travelImpactReceipts).toEqual(["travel-legacy:1"]);
    expect(migrated.newlyAppliedResultParts).toEqual([]);
  });

  it("keeps only the newest bounded valid receipts", () => {
    const receipts = Array.from(
      { length: MAX_TRAVEL_IMPACT_RECEIPTS + 6 },
      (_, index) => `travel-${index}:1`,
    );
    receipts.push("invalid", "travel-69:1");

    const normalized = normalizeTravelImpactReceipts(receipts);

    expect(normalized).toHaveLength(MAX_TRAVEL_IMPACT_RECEIPTS);
    expect(normalized?.[0]).toBe("travel-6:1");
    expect(normalized?.at(-1)).toBe("travel-69:1");
  });
});
