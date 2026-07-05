import type { PromptLayers } from "./types";

export const defaultPromptLayers: PromptLayers = {
  ageStyle: true,
  moodStyle: true,
  statNeeds: true,
  characterCore: true,
  importedSeedchat: true,
  lore: true,
  characterBook: true,
  memory: true,
  referenceCards: true,
  dialogueMoves: true,
  proactivity: true,
  postHistoryInstructions: true,
};

export const cleanPromptLayers: PromptLayers = {
  ageStyle: false,
  moodStyle: false,
  statNeeds: false,
  characterCore: true,
  importedSeedchat: false,
  lore: false,
  characterBook: false,
  memory: false,
  referenceCards: false,
  dialogueMoves: false,
  proactivity: false,
  postHistoryInstructions: false,
};

export const promptLayerOptions = [
  { key: "ageStyle", label: "Возраст" },
  { key: "moodStyle", label: "Настроение" },
  { key: "statNeeds", label: "Голод/энергия" },
  { key: "importedSeedchat", label: "Seed chat" },
  { key: "lore", label: "Лор" },
  { key: "characterBook", label: "Character book" },
  { key: "memory", label: "Память" },
  { key: "referenceCards", label: "Reference cards" },
  { key: "dialogueMoves", label: "Dialogue moves" },
  { key: "proactivity", label: "Инициатива" },
] satisfies Array<{ key: keyof PromptLayers; label: string }>;

export function normalizePromptLayers(value: unknown): PromptLayers {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return defaultPromptLayers;
  }

  const record = value as Partial<Record<keyof PromptLayers, unknown>>;
  return Object.fromEntries(
    Object.entries(defaultPromptLayers).map(([key, fallback]) => [
      key,
      typeof record[key as keyof PromptLayers] === "boolean"
        ? record[key as keyof PromptLayers]
        : fallback,
    ]),
  ) as PromptLayers;
}
