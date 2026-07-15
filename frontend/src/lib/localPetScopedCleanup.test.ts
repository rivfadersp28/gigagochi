import { beforeEach, describe, expect, it } from "vitest";

import { localPetMemoryStorageKey } from "./localPetMemoryStorage";
import { clearLocalPetScopedData } from "./localPetScopedCleanup";
import { writeLocalInteractiveTravel } from "./localInteractiveTravel";
import { PENDING_GENERATION_STORAGE_KEY } from "./pendingPetGeneration";
import {
  clearPendingInteractiveTravelOperationsForTests,
  readPendingInteractiveTravelOperations,
} from "./pendingInteractiveTravelOperations";

describe("local pet scoped cleanup", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    clearPendingInteractiveTravelOperationsForTests();
  });

  it("removes every persisted namespace owned by the active pet", () => {
    const petId = "pet-cleanup";
    const keys = [
      localPetMemoryStorageKey(petId),
      `tamagochi:v1:ambient-replies:${petId}`,
      `tamagochi:v1:introduction-shown:${petId}`,
      `gigagochi.interactive-travel.v2:${petId}`,
      `gigagochi.interactive-travel.v3:${petId}`,
      PENDING_GENERATION_STORAGE_KEY,
      "tamagochi:v1:debug-events",
    ];
    for (const key of keys) {
      window.localStorage.setItem(key, "stale");
    }
    window.localStorage.setItem(
      "tamagochi:v1:compliment-ledger",
      JSON.stringify({ version: 1, pets: { [petId]: ["Старый комплимент"] } }),
    );
    window.sessionStorage.setItem(`tamagochi:pet-tap-thanks:${petId}`, "1");

    clearLocalPetScopedData(petId);

    for (const key of keys) {
      expect(window.localStorage.getItem(key), key).toBeNull();
    }
    expect(window.localStorage.getItem("tamagochi:v1:compliment-ledger")).not.toContain(
      petId,
    );
    expect(window.sessionStorage.getItem(`tamagochi:pet-tap-thanks:${petId}`)).toBeNull();
  });

  it("retains a durable server cancellation after deleting an in-progress pet", () => {
    const petId = "pet-with-travel";
    writeLocalInteractiveTravel(petId, {
      travel: {
        travelId: "interactive-travel-cleanup",
        generatedAt: "2026-07-15T12:00:00Z",
        destination: "маяк",
        overallTitle: "Дорога к маяку",
        arcPlan: { goal: "Зажечь свет" },
        parts: [{
          partNumber: 1,
          title: "У берега",
          storyText: "Персонаж вышел к морю.",
          challenge: "Куда идти?",
          actionSuggestions: ["К маяку"],
        }],
        completed: false,
      },
      appliedResultParts: [],
      presentation: { phase: "choice", partNumber: 1, portionIndex: 0 },
    });

    clearLocalPetScopedData(petId);

    expect(readPendingInteractiveTravelOperations()).toMatchObject([
      { kind: "cancel", travelId: "interactive-travel-cleanup" },
    ]);
    expect(
      window.localStorage.getItem(`gigagochi.interactive-travel.v3:${petId}`),
    ).toBeNull();
  });
});
