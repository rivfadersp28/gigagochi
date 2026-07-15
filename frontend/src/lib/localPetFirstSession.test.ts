import { beforeEach, describe, expect, it } from "vitest";

import {
  firstSessionTravelConfirmation,
  firstSessionIntroduction,
  isLocalFirstSessionActive,
  isLocalFirstSessionEnabled,
  readLocalPetFirstSession,
  restartLocalPetFirstSession,
  setLocalFirstSessionEnabled,
  updateLocalPetFirstSession,
} from "./localPetFirstSession";
import type { LocalPetState } from "./types";

function pet(): LocalPetState {
  const now = "2026-07-15T10:00:00.000Z";
  return {
    version: 2,
    petId: "pet-first-session",
    introductionPending: true,
    name: "Листик",
    description: "лесной зверёк",
    createdAt: now,
    updatedAt: now,
    lastInteractionAt: now,
    lastStatsTickAt: now,
    lastStatTickAt: { hunger: now, happiness: now, energy: now },
    stage: "baby",
    mood: "idle",
    stats: { hunger: 100, happiness: 100, energy: 100 },
  };
}

describe("localPetFirstSession", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    setLocalFirstSessionEnabled(true);
  });

  it("keeps onboarding disabled until it is explicitly enabled", () => {
    window.localStorage.clear();

    expect(isLocalFirstSessionEnabled()).toBe(false);
    expect(readLocalPetFirstSession(pet())).toBeNull();
  });

  it("replays from the first step only once per browser session", () => {
    const initial = readLocalPetFirstSession(pet());
    expect(initial?.stage).toBe("awaiting-chat");

    const progressed = updateLocalPetFirstSession(initial!, "awaiting-remedy");
    expect(readLocalPetFirstSession(pet())).toEqual(progressed);

    window.sessionStorage.clear();
    expect(readLocalPetFirstSession(pet())?.stage).toBe("awaiting-chat");
  });

  it("persists a selected destination and completes the flow", () => {
    const initial = restartLocalPetFirstSession(pet().petId)!;
    const confirming = updateLocalPetFirstSession(
      initial,
      "confirming-travel",
      "в горы",
    );
    expect(confirming.selectedDestination).toBe("в горы");

    const completed = updateLocalPetFirstSession(confirming, "completed");
    expect(isLocalFirstSessionActive(completed)).toBe(false);
    expect(completed.selectedDestination).toBeUndefined();
  });

  it("describes the future timed delivery contract before confirmation", () => {
    expect(firstSessionTravelConfirmation("в горы")).toContain("несколько часов");
    expect(firstSessionTravelConfirmation("в горы")).toContain("четыре части истории");
    expect(firstSessionTravelConfirmation("в горы")).toContain("следующий уровень");
  });

  it("introduces the generated pet name and asks for the owner name", () => {
    expect(firstSessionIntroduction(pet())).toBe(
      "Привет, меня зовут Листик. Давай познакомимся. Как тебя зовут?",
    );
  });
});
