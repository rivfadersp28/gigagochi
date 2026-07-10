import { describe, expect, it } from "vitest";

import type { LocalPetState } from "@/lib/types";
import { TEST_PET_ASSET_SET, TEST_PET_DESCRIPTION } from "@/lib/testPetFixture";

import { storyHistoryFromPet } from "./storyHistory";

function petWithEvents(events: unknown[]): LocalPetState {
  return {
    version: 2,
    petId: "pet-1",
    description: "кот",
    createdAt: "2026-07-01T00:00:00Z",
    updatedAt: "2026-07-10T00:00:00Z",
    lastInteractionAt: "2026-07-10T00:00:00Z",
    lastStatsTickAt: "2026-07-10T00:00:00Z",
    lastStatTickAt: {
      hunger: "2026-07-10T00:00:00Z",
      happiness: "2026-07-10T00:00:00Z",
      energy: "2026-07-10T00:00:00Z",
    },
    stage: "baby",
    mood: "idle",
    stats: { hunger: 80, happiness: 70, energy: 60 },
    assetSet: {
      assetSetId: "asset-1",
      generatedAt: "2026-07-01T00:00:00Z",
      images: {
        baby: { idle: "", happy: "", hungry: "", sad: "" },
        teen: { idle: "", happy: "", hungry: "", sad: "" },
        adult: { idle: "", happy: "", hungry: "", sad: "" },
      },
      characterBible: {
        extensions: { recent_story_events: events },
      },
    },
  };
}

describe("storyHistoryFromPet", () => {
  it("returns every valid event with the newest one first", () => {
    const history = storyHistoryFromPet(petWithEvents([
      {
        id: "old",
        title: "Старая история",
        summary: "Короткое описание",
        createdAt: "2026-07-08T00:00:00Z",
      },
      {
        id: "new",
        title: "Новая история",
        storyText: "Полный текст события",
        imageUrl: "/static/generated/pet-1/story.png",
        generatedAt: "2026-07-09T00:00:00Z",
      },
      { title: "Пустая" },
    ]));

    expect(history).toEqual([
      {
        id: "new",
        title: "Новая история",
        text: "Полный текст события",
        imageUrl: "/static/generated/pet-1/story.png",
        generatedAt: "2026-07-09T00:00:00Z",
      },
      {
        id: "old",
        title: "Старая история",
        text: "Короткое описание",
        imageUrl: undefined,
        generatedAt: "2026-07-08T00:00:00Z",
      },
    ]);
  });

  it("seeds every new test pet with three illustrated stories", () => {
    const history = storyHistoryFromPet({
      ...petWithEvents([]),
      description: TEST_PET_DESCRIPTION,
      assetSet: TEST_PET_ASSET_SET,
    });

    expect(history).toHaveLength(3);
    expect(history.map((item) => item.title)).toEqual([
      "Кочка под туманом",
      "Вмятина от серого града",
      "Трещина в старом свете",
    ]);
    expect(history.every((item) => Boolean(item.imageUrl))).toBe(true);
  });

  it("restores the three illustrated stories for an existing test pet", () => {
    const history = storyHistoryFromPet({
      ...petWithEvents([]),
      description: TEST_PET_DESCRIPTION,
      assetSet: {
        ...TEST_PET_ASSET_SET,
        characterBible: {
          extensions: {
            recent_story_events: [
              {
                id: "legacy-fallback",
                title: "Кочка под туманом",
                summary: "Короткая серверная копия без картинки",
              },
            ],
          },
        },
      },
    });

    expect(history).toHaveLength(3);
    expect(history.every((item) => Boolean(item.imageUrl))).toBe(true);
    expect(history[0]?.text).toContain("На туманном лугу");
  });
});
