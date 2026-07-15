import type { LocalPetState } from "./types";

const KEY_PREFIX = "tamagochi:v1:introduction-shown:";
const MAX_MESSAGE_LENGTH = 120;
const MAX_PENDING_SESSION_CLAIMS = 256;
const MAX_PET_ID_LENGTH = 120;
const pendingSessionClaims = new Set<string>();
const pendingSessionClears = new Set<string>();

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeMessage(value: unknown): string {
  return typeof value === "string"
    ? value.trim().replace(/\s+/g, " ").slice(0, MAX_MESSAGE_LENGTH).trim()
    : "";
}

function nameFromBible(value: unknown): string {
  if (!isRecord(value) || !isRecord(value.identity)) {
    return "";
  }
  return normalizeMessage(value.identity.name).slice(0, 40).trim();
}

function neutralIntroduction(pet: LocalPetState): string {
  const name = normalizeMessage(pet.name).slice(0, 40).trim()
    || nameFromBible(pet.assetSet?.characterBible)
    || nameFromBible(pet.assetSet?.characterTemplate);
  return name ? `Я ${name}. Как тебя зовут?` : "Как тебя зовут?";
}

function storage(): Storage | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function rememberPendingClaim(petId: string) {
  pendingSessionClears.delete(petId);
  pendingSessionClaims.delete(petId);
  pendingSessionClaims.add(petId);
  while (pendingSessionClaims.size > MAX_PENDING_SESSION_CLAIMS) {
    const oldestPetId = pendingSessionClaims.values().next().value;
    if (typeof oldestPetId !== "string") {
      break;
    }
    pendingSessionClaims.delete(oldestPetId);
  }
}

function rememberPendingClear(petId: string) {
  pendingSessionClaims.delete(petId);
  pendingSessionClears.delete(petId);
  pendingSessionClears.add(petId);
  while (pendingSessionClears.size > MAX_PENDING_SESSION_CLAIMS) {
    const oldestPetId = pendingSessionClears.values().next().value;
    if (typeof oldestPetId !== "string") {
      break;
    }
    pendingSessionClears.delete(oldestPetId);
  }
}

function flushPendingClaim(petId: string, store: Storage | null) {
  if (!pendingSessionClaims.has(petId) || !store) {
    return;
  }
  try {
    store.setItem(`${KEY_PREFIX}${petId}`, "1");
    pendingSessionClaims.delete(petId);
  } catch {
    // Keep the session claim so a stale missing marker cannot replay the intro.
  }
}

function flushPendingClear(petId: string, store: Storage | null) {
  if (!pendingSessionClears.has(petId) || !store) {
    return;
  }
  try {
    store.removeItem(`${KEY_PREFIX}${petId}`);
    pendingSessionClears.delete(petId);
  } catch {
    // Keep the tombstone so a stale marker cannot become authoritative again.
  }
}

export function claimPetIntroduction(pet: LocalPetState): string | null {
  if (typeof window === "undefined" || pet.introductionPending !== true) {
    return null;
  }

  const petId = pet.petId.trim().slice(0, MAX_PET_ID_LENGTH);
  if (!petId) {
    return null;
  }
  const store = storage();
  flushPendingClear(petId, store);
  if (pendingSessionClaims.has(petId)) {
    flushPendingClaim(petId, store);
    return null;
  }
  const key = `${KEY_PREFIX}${petId}`;
  try {
    if (!pendingSessionClears.has(petId) && store?.getItem(key) === "1") {
      return null;
    }
  } catch {
    // Fall through to an in-memory one-shot when WebView storage is unavailable.
  }

  const message = neutralIntroduction(pet);

  if (!store) {
    rememberPendingClaim(petId);
    return message;
  }
  pendingSessionClears.delete(petId);
  try {
    store.setItem(key, "1");
  } catch {
    rememberPendingClaim(petId);
  }
  return message;
}

export function clearPetIntroduction(petId: string) {
  const normalizedPetId = petId.trim().slice(0, MAX_PET_ID_LENGTH);
  if (!normalizedPetId) {
    return;
  }
  rememberPendingClear(normalizedPetId);
  flushPendingClear(normalizedPetId, storage());
}
