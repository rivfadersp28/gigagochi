import { afterEach, describe, expect, it, vi } from "vitest";

import { TEST_PET_ASSET_SET_ID } from "./testPetFixture";
import {
  currentPendingPart,
  interactiveTravelCharacterImageUrl,
  nextResolvedPart,
  patchInteractiveTravelPartBackground,
  patchInteractiveTravelPartVideo,
  resultPortions,
  splitInteractiveTravelText,
  storyPortions,
} from "./interactiveTravelPresentation";
import type {
  InteractiveTravelPart,
  InteractiveTravelState,
  LocalPetState,
} from "./types";

const now = "2026-07-14T12:00:00.000Z";

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
    arcPlan: {},
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
  it("splits an oversized sentence at word boundaries", () => {
    const text =
      "Персонаж долго пробирался через заросший лес, внимательно прислушиваясь к каждому шороху, чтобы вовремя заметить спрятанную среди деревьев старую каменную башню.";

    const portions = splitInteractiveTravelText(text);

    expect(portions.length).toBeGreaterThan(1);
    expect(portions.every((portion) => Array.from(portion).length <= 80)).toBe(true);
    expect(portions.join(" ")).toBe(text.replace(/\.$/u, ""));
  });

  it("hard-wraps a single oversized token without losing characters", () => {
    const text = "я".repeat(201);

    const portions = splitInteractiveTravelText(text);

    expect(portions.map((portion) => Array.from(portion).length)).toEqual([80, 80, 41]);
    expect(portions.join("")).toBe(text);
  });

  it("builds story portions and puts the character reaction before the result", () => {
    const resolved = part(1, true);

    expect(storyPortions(resolved)).toEqual([
      "Сначала герой вышел к реке",
      "Потом заметил свет на другом берегу",
    ]);
    expect(resultPortions(resolved)).toEqual([
      "Вот это отличный план!",
      "Плот выдержал течение",
      "Герой благополучно добрался до берега",
    ]);
    expect(resultPortions(part(2))).toEqual([]);
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
