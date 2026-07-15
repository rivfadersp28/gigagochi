import { afterEach, describe, expect, it, vi } from "vitest";

import { TEST_PET_ASSET_SET_ID } from "./testPetFixture";
import {
  currentPendingPart,
  interactiveTravelTextParagraphs,
  interactiveTravelCharacterImageUrl,
  nextResolvedPart,
  patchInteractiveTravelPartBackground,
  patchInteractiveTravelPartVideo,
  resolvedResultParagraphs,
  splitInteractiveTravelText,
  storyAndChallengeParagraphs,
} from "./interactiveTravelPresentation";
import type {
  InteractiveTravelPart,
  InteractiveTravelPlan,
  InteractiveTravelState,
  LocalPetState,
} from "./types";

const now = "2026-07-14T12:00:00.000Z";
const travelPlan: InteractiveTravelPlan = {
  version: "task-bank-location-v4",
  tasks: [1, 2, 3, 4].map((partNumber) => ({
    taskId: `traveler-${partNumber}`,
    leadIn: `У башни ${partNumber}`,
    situation: `Ситуация ${partNumber}.`,
    question: `Вопрос ${partNumber}?`,
    choices: ["Плот", "Лодка", "Мост", "Брод"],
    correctChoice: "Плот",
    explanation: `Объяснение ${partNumber}.`,
  })) as InteractiveTravelPlan["tasks"],
};

afterEach(() => {
  vi.unstubAllEnvs();
});

function part(partNumber: number, resolved = false): InteractiveTravelPart {
  return {
    partNumber,
    title: `Часть ${partNumber}`,
    storyText: "Сначала герой вышел к реке. Потом заметил свет на другом берегу.",
    challenge: "Как перебраться через реку?",
    actionSuggestions: ["Построить плот", "Позвать лодочника", "Переплыть"],
    answer: resolved ? "Построить плот" : undefined,
    result: resolved
      ? {
          text: "Плот выдержал течение. Герой благополучно добрался до берега.",
          adviceAssessment: "helpful",
          reaction: "Вот это отличный план!",
          reactionTone: "enthusiastic",
          consequence: "Переправа удалась.",
          outcomeValence: "positive",
          statImpacts: [],
        }
      : undefined,
  };
}

function travel(parts: InteractiveTravelPart[]): InteractiveTravelState {
  return {
    travelId: "travel-1",
    generatedAt: now,
    destination: "далёкая гора",
    overallTitle: "Путь к вершине",
    plan: travelPlan,
    parts,
    completed: false,
  };
}

function pet(assetSetId: string, idleUrl: string): LocalPetState {
  const images = {
    idle: idleUrl,
    happy: idleUrl,
    hungry: idleUrl,
    sad: idleUrl,
  };
  return {
    version: 2,
    petId: "pet-1",
    name: "Тарас",
    description: "Лесной зверёк",
    createdAt: now,
    updatedAt: now,
    lastInteractionAt: now,
    lastStatsTickAt: now,
    lastStatTickAt: { hunger: now, happiness: now, energy: now },
    stage: "teen",
    mood: "idle",
    stats: { hunger: 50, happiness: 50, energy: 50 },
    assetSet: {
      assetSetId,
      generatedAt: now,
      images: { baby: images, teen: images, adult: images },
    },
  };
}

describe("interactive travel text portions", () => {
  it("keeps a long sentence in one portion for font fitting", () => {
    const text =
      "Персонаж долго пробирался через заросший лес, внимательно прислушиваясь к каждому шороху, чтобы вовремя заметить спрятанную среди деревьев старую каменную башню.";

    const portions = splitInteractiveTravelText(text);

    expect(portions).toEqual([text.replace(/\.$/u, "")]);
  });

  it("splits only at sentence boundaries without a one-word tail", () => {
    const text = "Сначала герой долго идёт через лес. Затем замечает башню у реки.";

    const portions = splitInteractiveTravelText(text);

    expect(portions).toEqual([
      "Сначала герой долго идёт через лес",
      "Затем замечает башню у реки",
    ]);
  });

  it("shows the whole story and exact question as punctuation-preserving paragraphs", () => {
    const pending = part(1);
    pending.challenge = "Кто образует лишайник?";

    expect(storyAndChallengeParagraphs(pending)).toEqual([
      "Сначала герой вышел к реке.",
      "Потом заметил свет на другом берегу.",
      "Кто образует лишайник?",
    ]);
  });

  it("keeps explicit paragraphs and exposes a resolved result all at once", () => {
    expect(interactiveTravelTextParagraphs("Первая строка.\n\nВторая строка!"))
      .toEqual(["Первая строка.", "Вторая строка!"]);
    expect(resolvedResultParagraphs(part(1, true))).toEqual([
      "Плот выдержал течение.",
      "Герой благополучно добрался до берега.",
    ]);
    expect(resolvedResultParagraphs(part(2))).toEqual([]);
  });

  it("finds the current pending part and the next resolved part", () => {
    const state = travel([part(1, true), part(2, true), part(3)]);

    expect(currentPendingPart(state)?.partNumber).toBe(3);
    expect(nextResolvedPart(state)?.partNumber).toBe(1);
    expect(nextResolvedPart(state, 1)?.partNumber).toBe(2);
    expect(nextResolvedPart(state, 2)).toBeNull();
  });
});

describe("interactive travel assets", () => {
  it("prefers the persisted transparent character asset", () => {
    const generatedPet = pet("generated-pet", "/generated/teen-idle.png");
    generatedPet.assetSet!.characterImageUrl = "/generated/teen-idle-foreground.png";

    expect(interactiveTravelCharacterImageUrl(generatedPet)).toBe(
      "/generated/teen-idle-foreground.png",
    );
  });

  it("derives a sibling transparent foreground URL and preserves its query", () => {
    expect(
      interactiveTravelCharacterImageUrl(
        pet("generated-pet", "https://cdn.example/pets/abc/teen-idle.png?token=one&v=2"),
      ),
    ).toBe(
      "https://cdn.example/pets/abc/teen-idle-foreground.png?token=one&v=2&travel_foreground_v=20260714-1",
    );
  });

  it("uses the dedicated transparent test-pet asset", () => {
    expect(
      interactiveTravelCharacterImageUrl(
        pet(TEST_PET_ASSET_SET_ID, "/test-pet/openai-normal.png"),
      ),
    ).toBe("/test-pet/character-transparent.png");
  });

  it("uses the development test-pet character when the local pet has no assets yet", () => {
    vi.stubEnv("NODE_ENV", "development");
    const petWithoutAssets = pet("missing-assets", "");
    petWithoutAssets.assetSet = undefined;

    expect(interactiveTravelCharacterImageUrl(petWithoutAssets)).toBe(
      "/test-pet/character-transparent.png",
    );
  });
});

describe("patchInteractiveTravelPartBackground", () => {
  it("patches one part without mutating the travel state", () => {
    const first = part(1, true);
    const second = part(2);
    const state = travel([first, second]);

    const patched = patchInteractiveTravelPartBackground(state, 2, "/travel/scene-2.png");

    expect(patched).not.toBe(state);
    expect(patched.parts).not.toBe(state.parts);
    expect(patched.parts[0]).toBe(first);
    expect(patched.parts[1]).not.toBe(second);
    expect(patched.parts[1]).toMatchObject({ backgroundImageUrl: "/travel/scene-2.png" });
    expect(second).not.toHaveProperty("backgroundImageUrl");
  });

  it("patches the generated video without replacing its poster", () => {
    const state = patchInteractiveTravelPartBackground(
      travel([part(1, true), part(2)]),
      2,
      "/travel/scene-2.png",
    );

    const patched = patchInteractiveTravelPartVideo(state, 2, "/travel/scene-2.mp4");

    expect(patched.parts[1]).toMatchObject({
      backgroundImageUrl: "/travel/scene-2.png",
      backgroundVideoUrl: "/travel/scene-2.mp4",
    });
  });
});
