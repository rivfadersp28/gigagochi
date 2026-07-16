type PendingTravelVideoPrototype = {
  version: 1;
  petId: string;
  prompt: string;
  requestKey: string;
  createdAt: number;
};

const MAX_AGE_MS = 24 * 60 * 60 * 1000;
const REQUEST_KEY_PATTERN =
  /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/u;

function storageKey(petId: string) {
  return `travel-video-prototype:${petId}:pending-request`;
}

function readPending(petId: string): PendingTravelVideoPrototype | null {
  try {
    const raw = window.localStorage.getItem(storageKey(petId));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<PendingTravelVideoPrototype>;
    if (
      value.version !== 1 ||
      value.petId !== petId ||
      typeof value.prompt !== "string" ||
      typeof value.requestKey !== "string" ||
      !REQUEST_KEY_PATTERN.test(value.requestKey) ||
      typeof value.createdAt !== "number" ||
      Date.now() - value.createdAt > MAX_AGE_MS
    ) {
      window.localStorage.removeItem(storageKey(petId));
      return null;
    }
    return value as PendingTravelVideoPrototype;
  } catch {
    return null;
  }
}

export function prepareTravelVideoPrototypeRequest(petId: string, prompt: string): string {
  const normalizedPrompt = prompt.trim();
  const pending = readPending(petId);
  if (pending?.prompt === normalizedPrompt) {
    return pending.requestKey;
  }
  const requestKey = window.crypto.randomUUID();
  const next: PendingTravelVideoPrototype = {
    version: 1,
    petId,
    prompt: normalizedPrompt,
    requestKey,
    createdAt: Date.now(),
  };
  window.localStorage.setItem(storageKey(petId), JSON.stringify(next));
  return requestKey;
}

export function clearTravelVideoPrototypeRequest(petId: string, requestKey: string): void {
  const pending = readPending(petId);
  if (pending?.requestKey === requestKey) {
    window.localStorage.removeItem(storageKey(petId));
  }
}
