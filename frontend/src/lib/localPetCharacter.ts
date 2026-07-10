import type { LocalPetAssetSet } from "./types";

const CHARACTER_INSTANCE_SCHEMA_VERSION = 1;
const PROMPT_MODEL_SCHEMA_VERSION = 3;
const MUTABLE_EXTENSION_KEYS = [
  "lite_overlay",
  "story_library_overlay",
  "recent_story_events",
  "instance",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function cloneRecord(value: Record<string, unknown>): Record<string, unknown> {
  if (typeof structuredClone === "function") {
    return structuredClone(value);
  }
  return JSON.parse(JSON.stringify(value)) as Record<string, unknown>;
}

function cleanTemplateBible(value: Record<string, unknown>): Record<string, unknown> {
  const template = cloneRecord(value);
  const extensions = isRecord(template.extensions) ? { ...template.extensions } : null;
  if (!extensions) {
    return template;
  }

  for (const key of MUTABLE_EXTENSION_KEYS) {
    delete extensions[key];
  }

  if (Object.keys(extensions).length) {
    template.extensions = extensions;
  } else {
    delete template.extensions;
  }

  return template;
}

function stripPromptScaffolding(value: Record<string, unknown>): Record<string, unknown> {
  const bible = cloneRecord(value);
  delete bible.dialogue_style;

  if (isRecord(bible.lore)) {
    const lore = { ...bible.lore };
    delete lore.voice;
    if (Object.keys(lore).length) {
      bible.lore = lore;
    } else {
      delete bible.lore;
    }
  }

  return bible;
}

function withInstanceMeta(
  bible: Record<string, unknown>,
  assetSet: LocalPetAssetSet,
  createdAt: string,
): Record<string, unknown> {
  const instance = cloneRecord(bible);
  const extensions = isRecord(instance.extensions) ? { ...instance.extensions } : {};
  const currentInstance = isRecord(extensions.instance) ? extensions.instance : {};

  extensions.instance = {
    ...currentInstance,
    schemaVersion: CHARACTER_INSTANCE_SCHEMA_VERSION,
    createdAt:
      typeof currentInstance.createdAt === "string" && currentInstance.createdAt
        ? currentInstance.createdAt
        : createdAt,
    templateAssetSetId: assetSet.assetSetId,
    templateGeneratedAt: assetSet.generatedAt,
    mutableOverlayPaths: {
      liteOverlay: "extensions.lite_overlay",
      storyLibraryOverlay: "extensions.story_library_overlay",
      recentStoryEvents: "extensions.recent_story_events",
    },
    promptModelVersion: PROMPT_MODEL_SCHEMA_VERSION,
    disabledPromptSections: ["characterBible.dialogue_style", "lore.voice"],
  };

  return {
    ...instance,
    extensions,
  };
}

export function normalizeCharacterAssetSet(
  assetSet: LocalPetAssetSet,
  createdAt = new Date().toISOString(),
): LocalPetAssetSet {
  if (!isRecord(assetSet.characterBible)) {
    return assetSet;
  }

  const characterTemplate = isRecord(assetSet.characterTemplate)
    ? cleanTemplateBible(assetSet.characterTemplate)
    : cleanTemplateBible(assetSet.characterBible);

  return {
    ...assetSet,
    characterTemplate,
    characterBible: withInstanceMeta(stripPromptScaffolding(assetSet.characterBible), assetSet, createdAt),
  };
}

function characterNameFromBible(bible: unknown): string | undefined {
  if (!isRecord(bible)) {
    return undefined;
  }

  const identity = isRecord(bible.identity) ? bible.identity : {};
  const rawName = identity.name ?? bible.name;
  if (typeof rawName !== "string") {
    return undefined;
  }

  const name = rawName.trim().replace(/\s+/g, " ").slice(0, 32);
  return name || undefined;
}

export function characterNameFromAssetSet(assetSet?: LocalPetAssetSet): string | undefined {
  return (
    characterNameFromBible(assetSet?.characterBible)
    ?? characterNameFromBible(assetSet?.characterTemplate)
  );
}
