const PET_TAP_THANKS_SESSION_PREFIX = "tamagochi:pet-tap-thanks";
const claimedPetIds = new Set<string>();

const PET_TAP_THANKS_REPLIES = [
  "Приятно!",
  "Щекотно!",
  "Мне нравится!",
] as const;

export function petTapThanksReply(randomValue = Math.random()) {
  const normalizedRandom = Number.isFinite(randomValue)
    ? Math.max(0, Math.min(0.999_999, randomValue))
    : 0;
  return PET_TAP_THANKS_REPLIES[
    Math.floor(normalizedRandom * PET_TAP_THANKS_REPLIES.length)
  ];
}

export function claimPetTapThanksForSession(petId: string) {
  if (typeof window === "undefined") {
    return false;
  }

  const key = `${PET_TAP_THANKS_SESSION_PREFIX}:${petId}`;
  if (claimedPetIds.has(petId)) {
    return false;
  }
  try {
    if (window.sessionStorage.getItem(key)) {
      return false;
    }
    window.sessionStorage.setItem(key, "1");
    claimedPetIds.add(petId);
    return true;
  } catch {
    claimedPetIds.add(petId);
    return true;
  }
}

export function clearPetTapThanksForSession(petId: string) {
  claimedPetIds.delete(petId);
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.removeItem(`${PET_TAP_THANKS_SESSION_PREFIX}:${petId}`);
  } catch {
    // Session storage is best-effort; the in-memory claim is already removed.
  }
}
