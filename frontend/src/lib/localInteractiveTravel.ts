import { parseInteractiveTravelResponse } from "./apiContracts";
import {
  challengePortions,
  resultPortions,
  splitInteractiveTravelText,
  storyPortions,
} from "./interactiveTravelPresentation";
import {
  LocalPetMutationLockError,
  withLocalPetMutationLock,
  type LocalPetMutationGuard,
} from "./localPetMutationLock";
import type { InteractiveTravelPart, InteractiveTravelState } from "./types";

const STORAGE_PREFIX = "gigagochi.interactive-travel.v3";
const LEGACY_STORAGE_PREFIX = "gigagochi.interactive-travel.v2";
const MAX_SESSION_PETS = 64;
const MUTATION_LOCK_ACQUIRE_TIMEOUT_MS = 10_000;
const MAX_MUTATION_LOCK_ACQUIRE_TIMEOUT_MS = 10 * 60_000;

type SessionTravelEntry = {
  value: LocalInteractiveTravel | null;
  persistence: "write" | "clear" | null;
};

const sessionTravelByPetId = new Map<string, SessionTravelEntry>();

function rememberSessionTravel(petId: string, entry: SessionTravelEntry) {
  sessionTravelByPetId.delete(petId);
  sessionTravelByPetId.set(petId, entry);
  while (sessionTravelByPetId.size > MAX_SESSION_PETS) {
    const oldestPetId = sessionTravelByPetId.keys().next().value;
    if (typeof oldestPetId !== "string") {
      break;
    }
    sessionTravelByPetId.delete(oldestPetId);
  }
}

export type InteractiveTravelPresentationPhase =
  | "introReaction"
  | "departureWait"
  | "story"
  | "result"
  | "choice"
  | "completed";

export type InteractiveTravelPresentation = {
  phase: InteractiveTravelPresentationPhase;
  partNumber: number;
  portionIndex: number;
};

export type LocalInteractiveTravel = {
  travel: InteractiveTravelState;
  appliedResultParts: number[];
  presentation: InteractiveTravelPresentation;
};

const PRESENTATION_PHASES = new Set<InteractiveTravelPresentationPhase>([
  "introReaction",
  "departureWait",
  "story",
  "result",
  "choice",
  "completed",
]);

function storageKey(petId: string, prefix = STORAGE_PREFIX) {
  return `${prefix}:${petId}`;
}

export function isLocalInteractiveTravelStorageKey(
  key: string | null,
  petId: string,
): boolean {
  return key === null
    || key === storageKey(petId)
    || key === storageKey(petId, LEGACY_STORAGE_PREFIX);
}

function localStorage(): Storage | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export type LocalInteractiveTravelMutationGuard = LocalPetMutationGuard;

export class LocalInteractiveTravelMutationLockError extends Error {
  constructor(message = "Interactive travel is being updated in another tab") {
    super(message);
    this.name = "LocalInteractiveTravelMutationLockError";
  }
}

export async function withLocalInteractiveTravelMutationLock<T>(
  petId: string,
  mutation: (guard: LocalInteractiveTravelMutationGuard) => Promise<T> | T,
  options: { acquireTimeoutMs?: number } = {},
): Promise<T> {
  const requestedTimeout = options.acquireTimeoutMs ?? MUTATION_LOCK_ACQUIRE_TIMEOUT_MS;
  const acquireTimeoutMs = Number.isFinite(requestedTimeout) && requestedTimeout > 0
    ? Math.min(requestedTimeout, MAX_MUTATION_LOCK_ACQUIRE_TIMEOUT_MS)
    : MUTATION_LOCK_ACQUIRE_TIMEOUT_MS;
  try {
    return await withLocalPetMutationLock(
      petId,
      "travel",
      mutation,
      { acquireTimeoutMs },
    );
  } catch (error) {
    if (error instanceof LocalPetMutationLockError) {
      throw new LocalInteractiveTravelMutationLockError(error.message);
    }
    throw error;
  }
}

function flushSessionTravel(petId: string, store: Storage | null): boolean {
  const entry = sessionTravelByPetId.get(petId);
  if (!entry?.persistence) {
    return true;
  }
  if (!store) {
    return false;
  }

  let succeeded = true;
  if (entry.persistence === "write" && entry.value) {
    try {
      store.setItem(storageKey(petId), JSON.stringify(entry.value));
    } catch {
      succeeded = false;
    }
    // The v3 value is authoritative. Failure to remove a legacy value must not
    // turn an otherwise durable write into a retry of a paid workflow.
    try {
      store.removeItem(storageKey(petId, LEGACY_STORAGE_PREFIX));
    } catch {}
  } else {
    for (const key of [storageKey(petId), storageKey(petId, LEGACY_STORAGE_PREFIX)]) {
      try {
        store.removeItem(key);
      } catch {
        succeeded = false;
      }
    }
  }
  if (succeeded) {
    if (entry.persistence === "clear") {
      sessionTravelByPetId.delete(petId);
    } else {
      entry.persistence = null;
    }
  }
  return succeeded;
}

function defaultPresentation(travel: InteractiveTravelState): InteractiveTravelPresentation {
  if (travel.completed) {
    return {
      phase: "completed",
      partNumber: travel.parts.at(-1)?.partNumber ?? 1,
      portionIndex: 0,
    };
  }
  const pendingPart = travel.parts.find((part) => !part.result) ?? travel.parts.at(-1);
  return {
    phase: "choice",
    partNumber: pendingPart?.partNumber ?? 1,
    portionIndex: 0,
  };
}

function expectedPartForPhase(
  phase: InteractiveTravelPresentationPhase,
  travel: InteractiveTravelState,
): InteractiveTravelPart | null {
  const pendingPart = travel.parts.find((part) => !part.result) ?? null;
  const latestResolvedPart = travel.parts.findLast((part) => Boolean(part.result)) ?? null;

  if (phase === "introReaction") {
    return pendingPart === travel.parts[0] ? pendingPart : null;
  }
  if (phase === "departureWait" || phase === "story" || phase === "choice") {
    return pendingPart;
  }
  if (phase === "result") {
    return latestResolvedPart;
  }
  return travel.completed ? (travel.parts.at(-1) ?? null) : null;
}

function maximumPortionIndex(
  phase: InteractiveTravelPresentationPhase,
  part: InteractiveTravelPart,
  travel: InteractiveTravelState,
): number {
  if (phase === "introReaction") {
    return Math.max(
      0,
      splitInteractiveTravelText(travel.introReaction?.text ?? "").length - 1,
    );
  }
  if (phase === "story") {
    return Math.max(0, storyPortions(part).length - 1);
  }
  if (phase === "choice") {
    return Math.max(0, challengePortions(part).length - 1);
  }
  if (phase === "result") {
    return Math.max(0, resultPortions(part).length - 1);
  }
  return 0;
}

function parsedPresentation(
  value: unknown,
  travel: InteractiveTravelState,
): InteractiveTravelPresentation {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return defaultPresentation(travel);
  }
  const raw = value as Record<string, unknown>;
  if (
    typeof raw.phase !== "string" ||
    !PRESENTATION_PHASES.has(raw.phase as InteractiveTravelPresentationPhase) ||
    !Number.isSafeInteger(raw.partNumber) ||
    Number(raw.partNumber) < 1 ||
    Number(raw.partNumber) > 7 ||
    !Number.isSafeInteger(raw.portionIndex) ||
    Number(raw.portionIndex) < 0
  ) {
    return defaultPresentation(travel);
  }
  const phase = raw.phase as InteractiveTravelPresentationPhase;
  const part = expectedPartForPhase(phase, travel);
  if (!part || part.partNumber !== Number(raw.partNumber)) {
    return defaultPresentation(travel);
  }
  return {
    phase,
    partNumber: part.partNumber,
    portionIndex: Math.min(
      Number(raw.portionIndex),
      maximumPortionIndex(phase, part, travel),
    ),
  };
}

function parseStoredTravel(raw: string): LocalInteractiveTravel {
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  const response = parseInteractiveTravelResponse({ travel: parsed.travel });
  const resolvedPartNumbers = new Set(
    response.travel.parts.filter((part) => part.result).map((part) => part.partNumber),
  );
  return {
    travel: response.travel,
    appliedResultParts: Array.isArray(parsed.appliedResultParts)
      ? [
          ...new Set(
            parsed.appliedResultParts.filter(
              (value): value is number =>
                typeof value === "number" &&
                Number.isSafeInteger(value) &&
                resolvedPartNumbers.has(value),
            ),
          ),
        ].sort((left, right) => left - right)
      : [],
    presentation: parsedPresentation(parsed.presentation, response.travel),
  };
}

export function readLocalInteractiveTravel(petId: string): LocalInteractiveTravel | null {
  const store = localStorage();
  flushSessionTravel(petId, store);
  const sessionEntry = sessionTravelByPetId.get(petId);
  if (sessionEntry?.persistence || !store) {
    return sessionEntry?.value ?? null;
  }
  try {
    const raw =
      store.getItem(storageKey(petId)) ??
      store.getItem(storageKey(petId, LEGACY_STORAGE_PREFIX));
    const value = raw ? parseStoredTravel(raw) : null;
    rememberSessionTravel(petId, { value, persistence: null });
    return value;
  } catch {
    return sessionEntry?.value ?? null;
  }
}

export function writeLocalInteractiveTravel(
  petId: string,
  value: LocalInteractiveTravel,
): LocalInteractiveTravel {
  rememberSessionTravel(petId, { value, persistence: "write" });
  flushSessionTravel(petId, localStorage());
  return value;
}

export function writeLocalInteractiveTravelDurably(
  petId: string,
  value: LocalInteractiveTravel,
): LocalInteractiveTravel | null {
  const store = localStorage();
  if (!store) {
    return null;
  }

  const previousEntry = sessionTravelByPetId.get(petId);
  const previousSession = previousEntry ? { ...previousEntry } : null;
  let previousRaw: string | null;
  let serialized: string;
  try {
    previousRaw = store.getItem(storageKey(petId));
    serialized = JSON.stringify(value);
  } catch {
    return null;
  }

  const restoreSession = () => {
    if (previousSession) {
      rememberSessionTravel(petId, previousSession);
    } else {
      sessionTravelByPetId.delete(petId);
    }
  };
  const restorePersistentValue = () => {
    try {
      // Another tab may have committed a newer value after our read-back. Only
      // roll back the exact value written by this attempt.
      if (store.getItem(storageKey(petId)) !== serialized) {
        return;
      }
      if (previousRaw === null) {
        store.removeItem(storageKey(petId));
      } else {
        store.setItem(storageKey(petId), previousRaw);
      }
    } catch {}
  };

  rememberSessionTravel(petId, { value, persistence: "write" });
  if (!flushSessionTravel(petId, store)) {
    restoreSession();
    return null;
  }

  try {
    const storedRaw = store.getItem(storageKey(petId));
    if (storedRaw !== serialized) {
      throw new Error("interactive travel storage read-back mismatch");
    }
    const stored = parseStoredTravel(storedRaw);
    if (stored.travel.travelId !== value.travel.travelId) {
      throw new Error("interactive travel identity read-back mismatch");
    }
    rememberSessionTravel(petId, { value: stored, persistence: null });
    return stored;
  } catch {
    restorePersistentValue();
    restoreSession();
    return null;
  }
}

export function clearLocalInteractiveTravel(petId: string) {
  rememberSessionTravel(petId, { value: null, persistence: "clear" });
  flushSessionTravel(petId, localStorage());
}
