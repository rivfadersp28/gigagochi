import { beforeEach, describe, expect, it } from "vitest";

import {
  FIRST_SESSION_COPY,
  firstSessionTravelConfirmation,
  firstSessionIntroduction,
  firstSessionReactionReply,
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

  it("keeps the current stage across browser sessions", () => {
    const initial = readLocalPetFirstSession(pet());
    expect(initial?.stage).toBe("awaiting-chat");

    const progressed = updateLocalPetFirstSession(initial!, "awaiting-remedy");
    expect(readLocalPetFirstSession(pet())).toEqual(progressed);

    window.sessionStorage.clear();
    expect(readLocalPetFirstSession(pet())).toEqual(progressed);
  });

  it("keeps chat active for one follow-up answer", () => {
    const initial = restartLocalPetFirstSession(pet().petId)!;

    const followup = updateLocalPetFirstSession(initial, "awaiting-chat-followup");

    expect(isLocalFirstSessionActive(followup)).toBe(true);
    expect(followup.stage).toBe("awaiting-chat-followup");
    expect(readLocalPetFirstSession(pet())).toEqual(followup);
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

  it("enables onboarding when restarting a disabled first session", () => {
    setLocalFirstSessionEnabled(false);

    const restarted = restartLocalPetFirstSession(pet().petId);

    expect(isLocalFirstSessionEnabled()).toBe(true);
    expect(restarted?.stage).toBe("awaiting-chat");
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

  it("invites the owner to help the bat after the remedy", () => {
    expect(FIRST_SESSION_COPY.afterRemedy).toContain("Я увидел летучую мышь");
    expect(FIRST_SESSION_COPY.afterRemedy).toContain("ей нужна помощь");
    expect(FIRST_SESSION_COPY.afterRemedy).not.toContain("отправимся в путешествие");
  });

  it("asks what the owner likes to do after learning their name", () => {
    expect(FIRST_SESSION_COPY.afterName).toBe("А чем ты любишь заниматься?");
  });

  it("keeps only declarative reactions during the scripted dialogue", () => {
    expect(firstSessionReactionReply(
      "Очень приятно! Как прошёл твой день?",
      FIRST_SESSION_COPY.afterNameFallback,
    )).toBe("Очень приятно!");
    expect(firstSessionReactionReply(
      "Чем ещё увлекаешься?",
      FIRST_SESSION_COPY.afterChatFallback,
    )).toBe("Звучит здорово!");
  });

  it("explains coins and invites the owner to dress the pet after the first challenge", () => {
    expect(FIRST_SESSION_COPY.afterFirstChallenge).toBe(
      "За правильные ответы я получаю монетки. Их можно потратить на новый гардероб или внешность. Попробуй меня во что-то нарядить.",
    );
  });
});
