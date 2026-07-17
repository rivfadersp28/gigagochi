export type PendingTravelVideoPrototype = {
  version: 1;
  petId: string;
  prompt: string;
  requestKey: string;
  jobId?: string;
  createdAt: number;
};

const MAX_AGE_MS = 24 * 60 * 60 * 1000;
const REQUEST_KEY_PATTERN =
  /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/u;
const JOB_ID_PATTERN = /^travel-video-prototype-[a-f0-9]{32}$/u;

function storageKey(petId: string) {
  return `travel-video-prototype:${petId}:pending-request`;
}

export function readPendingTravelVideoPrototype(
  petId: string,
): PendingTravelVideoPrototype | null {
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
      (value.jobId !== undefined && (
        typeof value.jobId !== "string" || !JOB_ID_PATTERN.test(value.jobId)
      )) ||
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
  const pending = readPendingTravelVideoPrototype(petId);
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

export function setPendingTravelVideoPrototypeJobId(
  petId: string,
  requestKey: string,
  jobId: string,
): PendingTravelVideoPrototype | null {
  const pending = readPendingTravelVideoPrototype(petId);
  if (!pending || pending.requestKey !== requestKey || !JOB_ID_PATTERN.test(jobId)) {
    return null;
  }
  const next = { ...pending, jobId };
  try {
    window.localStorage.setItem(storageKey(petId), JSON.stringify(next));
    return readPendingTravelVideoPrototype(petId)?.jobId === jobId ? next : null;
  } catch {
    return null;
  }
}

export function clearTravelVideoPrototypeRequest(petId: string, requestKey: string): void {
  const pending = readPendingTravelVideoPrototype(petId);
  if (pending?.requestKey === requestKey) {
    window.localStorage.removeItem(storageKey(petId));
  }
}
