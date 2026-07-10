import type { LocalPetState } from "./types";

const KEY_PREFIX = "tamagochi:v1:introduction-shown:";
const MAX_MESSAGE_LENGTH = 300;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeMessage(value: unknown): string {
  return typeof value === "string"
    ? value.trim().replace(/\s+/g, " ").slice(0, MAX_MESSAGE_LENGTH).trim()
    : "";
}

function firstMessageFromBible(value: unknown): string {
  if (!isRecord(value) || !isRecord(value.openings)) {
    return "";
  }
  return normalizeMessage(value.openings.first_message);
}

export function claimPetIntroduction(pet: LocalPetState): string | null {
  if (typeof window === "undefined" || pet.introductionPending !== true) {
    return null;
  }

  const key = `${KEY_PREFIX}${pet.petId}`;
  if (window.localStorage.getItem(key) === "1") {
    return null;
  }

  const message = firstMessageFromBible(pet.assetSet?.characterBible)
    || firstMessageFromBible(pet.assetSet?.characterTemplate);
  if (!message) {
    return null;
  }

  window.localStorage.setItem(key, "1");
  return message;
}
