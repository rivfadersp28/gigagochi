import type { LocalPetState, PetLifeStage, PetMood } from "./types";

export type PetVoiceMode = "local" | "generated" | "system";

type PetVoiceProfile = {
  stage: PetLifeStage;
  mood: PetMood;
  catchphrases: string[];
  sampleReplies: string[];
  avoidPatterns: string[];
};

const LOCAL_REPLY_MAX_CHARS = 260;
const SYSTEM_REPLY_MAX_CHARS = 180;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function compactSpaces(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

function textValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? compactSpaces(value) : null;
}

function stringList(value: unknown): string[] {
  if (typeof value === "string") {
    return [compactSpaces(value)].filter(Boolean);
  }
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    const text = textValue(item);
    return text ? [text] : [];
  });
}

function recordAt(record: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = record[key];
  return isRecord(value) ? value : {};
}

function uniqueStrings(values: string[], limit: number) {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const normalized = value.toLocaleLowerCase("ru-RU");
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    result.push(value);
    if (result.length >= limit) {
      break;
    }
  }
  return result;
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function hashText(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) | 0;
  }
  return Math.abs(hash);
}

export function buildPetVoiceProfile(pet?: LocalPetState | null): PetVoiceProfile {
  const bible = isRecord(pet?.assetSet?.characterBible) ? pet.assetSet.characterBible : {};
  const template = isRecord(pet?.assetSet?.characterTemplate) ? pet.assetSet.characterTemplate : {};
  const voice = {
    ...recordAt(template, "voice"),
    ...recordAt(bible, "voice"),
  };
  const dialogueStyle = recordAt(bible, "dialogue_style");
  const lore = recordAt(bible, "lore");
  const templateLore = recordAt(template, "lore");
  const loreVoice = {
    ...recordAt(templateLore, "voice"),
    ...recordAt(lore, "voice"),
  };

  return {
    stage: pet?.stage ?? "baby",
    mood: pet?.mood ?? "idle",
    catchphrases: uniqueStrings(
      [
        ...stringList(voice.catchphrases),
        ...stringList(loreVoice.favorite_phrases),
      ],
      4,
    ),
    sampleReplies: uniqueStrings(
      [
        ...stringList(voice.sample_replies),
        ...stringList(dialogueStyle.sample_replies),
      ],
      6,
    ),
    avoidPatterns: uniqueStrings(
      [
        ...stringList(voice.avoid),
        ...stringList(voice.avoid_patterns),
        ...stringList(dialogueStyle.avoid_patterns),
        ...stringList(loreVoice.avoid_saying),
      ],
      10,
    ),
  };
}

function stripAvoidPatterns(text: string, avoidPatterns: string[]) {
  let result = text;
  for (const pattern of avoidPatterns) {
    const normalized = compactSpaces(pattern);
    if (normalized.length < 3 || normalized.length > 48) {
      continue;
    }
    if (/(тон|объяснен|паттерн|длинн)/i.test(normalized)) {
      continue;
    }
    result = result.replace(new RegExp(escapeRegExp(normalized), "giu"), "");
  }
  return compactSpaces(result);
}

function clampText(text: string, limit: number) {
  if (text.length <= limit) {
    return text;
  }
  const clipped = text.slice(0, limit - 1).trimEnd();
  const sentenceEnd = Math.max(
    clipped.lastIndexOf("."),
    clipped.lastIndexOf("?"),
    clipped.lastIndexOf("!"),
  );
  if (sentenceEnd >= Math.floor(limit * 0.55)) {
    return clipped.slice(0, sentenceEnd + 1);
  }
  return `${clipped}…`;
}

function localStagePass(text: string, profile: PetVoiceProfile) {
  if (profile.stage === "baby") {
    return clampText(text, 210);
  }
  if (profile.stage === "teen") {
    return clampText(text, 240);
  }
  return clampText(text, LOCAL_REPLY_MAX_CHARS);
}

function selectSignature(text: string, profile: PetVoiceProfile) {
  const candidates = profile.catchphrases.filter((phrase) => {
    const normalized = phrase.toLocaleLowerCase("ru-RU");
    return normalized.length >= 2 && normalized.length <= 24;
  });
  if (!candidates.length) {
    return null;
  }
  return candidates[hashText(`${profile.stage}:${profile.mood}:${text}`) % candidates.length] ?? null;
}

function shouldAddSignature(text: string, signature: string, mode: PetVoiceMode) {
  if (mode === "generated") {
    return false;
  }
  if (text.toLocaleLowerCase("ru-RU").includes(signature.toLocaleLowerCase("ru-RU"))) {
    return false;
  }
  if (text.length > 170) {
    return false;
  }
  return true;
}

function addSignature(text: string, profile: PetVoiceProfile, mode: PetVoiceMode) {
  const signature = selectSignature(text, profile);
  if (!signature || !shouldAddSignature(text, signature, mode)) {
    return text;
  }
  if (text.endsWith("?") || hashText(text) % 2 === 0) {
    return `${signature}. ${text}`;
  }
  return `${text} ${signature}.`;
}

export function applyPetVoice(
  text: string,
  pet?: LocalPetState | null,
  options: { mode?: PetVoiceMode; maxChars?: number } = {},
): string {
  const mode = options.mode ?? "generated";
  const profile = buildPetVoiceProfile(pet);
  const baseLimit =
    options.maxChars ?? (mode === "system" ? SYSTEM_REPLY_MAX_CHARS : LOCAL_REPLY_MAX_CHARS);
  let result = compactSpaces(text);

  result = stripAvoidPatterns(result, profile.avoidPatterns);
  if (mode === "local") {
    result = localStagePass(result, profile);
  }
  result = addSignature(result, profile, mode);

  return clampText(result, baseLimit);
}
