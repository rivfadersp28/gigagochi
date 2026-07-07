import storyConstructorData from "@/data/storyConstructor.json";

import type { LocalPetState } from "./types";

export type StoryConstructorMainScreenKind = "event" | "question" | "joke";

export type StoryConstructorMainScreenTemplate = {
  id: string;
  kind: StoryConstructorMainScreenKind;
  text: (pet?: LocalPetState | null) => string;
};

type StoryConstructorData = typeof storyConstructorData;
type StoryPools = StoryConstructorData["pools"];

const FALLBACK_ITEM = "маленькая находка";
const FALLBACK_CREATURE = {
  name: "светлячок-проводник",
  detail: "мигает, когда кто-то говорит правду",
};
const FALLBACK_LOCATION = {
  name: "теплая тропинка",
  habitat: "home",
  detail: "там тихо и немного странно",
};
const FALLBACK_NEIGHBOR = {
  name: "тихий сосед",
  trait: "замечает то, что остальные пропускают",
};
const FALLBACK_THREAT = {
  name: "шорох из темного угла",
  danger: "low",
  detail: "пугает только в первые три секунды",
};

function pickValue<T>(values: readonly T[], fallback: T): T {
  if (!values.length) {
    return fallback;
  }
  return values[Math.floor(Math.random() * values.length)] ?? fallback;
}

function pickSecondValue(values: readonly string[], first: string, fallback: string): string {
  const candidates = values.filter((value) => value !== first);
  return pickValue(candidates, fallback);
}

function petName(pet?: LocalPetState | null) {
  return pet?.name?.trim() || "я";
}

function buildRenderContext(pet?: LocalPetState | null): Record<string, string> {
  const pools: StoryPools = storyConstructorData.pools;
  const item = pickValue(pools.items, FALLBACK_ITEM);
  const item2 = pickSecondValue(pools.items, item, "вторая маленькая находка");
  const creature = pickValue(pools.creatures, FALLBACK_CREATURE);
  const location = pickValue(pools.locations, FALLBACK_LOCATION);
  const neighbor = pickValue(pools.neighbors, FALLBACK_NEIGHBOR);
  const threat = pickValue(pools.threats, FALLBACK_THREAT);

  return {
    pet: petName(pet),
    item,
    item2,
    creature: creature.name,
    creatureDetail: creature.detail,
    location: location.name,
    locationDetail: location.detail,
    neighbor: neighbor.name,
    neighborTrait: neighbor.trait,
    threat: threat.name,
    threatDetail: threat.detail,
  };
}

export function renderStoryConstructorText(
  text: string,
  pet?: LocalPetState | null,
): string {
  const context = buildRenderContext(pet);
  return text.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, key: string) => context[key] || match);
}

function templatesForKind(kind: StoryConstructorMainScreenKind) {
  if (kind === "event") {
    return storyConstructorData.mainScreen.eventTemplates;
  }
  if (kind === "question") {
    return storyConstructorData.mainScreen.questionTemplates;
  }
  return storyConstructorData.mainScreen.jokeTemplates;
}

export function storyConstructorMainScreenTemplates(
  kind: StoryConstructorMainScreenKind,
): StoryConstructorMainScreenTemplate[] {
  return templatesForKind(kind).map((template) => ({
    id: template.id,
    kind,
    text: (pet) => renderStoryConstructorText(template.text, pet),
  }));
}
