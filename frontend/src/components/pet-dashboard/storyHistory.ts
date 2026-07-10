import type { LocalPetState } from "@/lib/types";
import { TEST_PET_ASSET_SET, TEST_PET_ASSET_SET_ID } from "@/lib/testPetFixture";

export type StoryHistoryItem = {
  id: string;
  title: string;
  text: string;
  imageUrl?: string;
  generatedAt?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function storyHistoryFromPet(pet: LocalPetState): StoryHistoryItem[] {
  const bible = pet.assetSet?.characterBible;
  const extensions = isRecord(bible) && isRecord(bible.extensions) ? bible.extensions : null;
  const rawEvents = extensions?.recent_story_events;
  const fixtureBible = TEST_PET_ASSET_SET.characterBible;
  const fixtureExtensions = isRecord(fixtureBible) && isRecord(fixtureBible.extensions)
    ? fixtureBible.extensions
    : null;
  const fixtureEvents = fixtureExtensions?.recent_story_events;

  function normalizeEvents(events: unknown): StoryHistoryItem[] {
    if (!Array.isArray(events)) {
      return [];
    }

    return events
      .map((value, index): StoryHistoryItem | null => {
        if (!isRecord(value)) {
          return null;
        }

        const storyText = text(value.storyText) || text(value.summary) || text(value.compactText);
        if (!storyText) {
          return null;
        }

        const generatedAt = text(value.generatedAt) || text(value.createdAt);
        const title = text(value.title) || "История питомца";
        return {
          id: text(value.id) || generatedAt || `${title}-${index}`,
          title,
          text: storyText,
          imageUrl: text(value.imageUrl) || undefined,
          generatedAt: generatedAt || undefined,
        };
      })
      .filter((item): item is StoryHistoryItem => Boolean(item));
  }

  const currentHistory = normalizeEvents(rawEvents);
  if (pet.assetSet?.assetSetId !== TEST_PET_ASSET_SET_ID) {
    return currentHistory.reverse();
  }

  const seededHistory = normalizeEvents(fixtureEvents);
  const seededTitles = new Set(seededHistory.map((item) => item.title.toLocaleLowerCase("ru")));
  const additionalHistory = currentHistory.filter(
    (item) => !seededTitles.has(item.title.toLocaleLowerCase("ru")),
  );

  return [...seededHistory, ...additionalHistory].reverse();
}
