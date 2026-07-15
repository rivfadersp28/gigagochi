import type { LocalPetState } from "./types";

export type PetVoiceMode = "local" | "generated" | "system";

const LOCAL_REPLY_MAX_CHARS = 260;
const SYSTEM_REPLY_MAX_CHARS = 180;

function compactSpaces(value: string) {
  return value.replace(/\s+/g, " ").trim();
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

export function applyPetVoice(
  text: string,
  _pet?: LocalPetState | null,
  options: { mode?: PetVoiceMode; maxChars?: number } = {},
): string {
  const mode = options.mode ?? "generated";
  const baseLimit =
    options.maxChars ?? (mode === "system" ? SYSTEM_REPLY_MAX_CHARS : LOCAL_REPLY_MAX_CHARS);
  return clampText(compactSpaces(text), baseLimit);
}
