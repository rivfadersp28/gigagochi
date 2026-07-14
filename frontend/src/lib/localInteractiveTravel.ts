import { parseInteractiveTravelResponse } from "./apiContracts";
import type { InteractiveTravelState } from "./types";

const STORAGE_PREFIX = "gigagochi.interactive-travel.v3";
const LEGACY_STORAGE_PREFIX = "gigagochi.interactive-travel.v2";

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

function storageKey(petId: string, prefix = STORAGE_PREFIX) {
  return `${prefix}:${petId}`;
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

function parsedPresentation(
  value: unknown,
  travel: InteractiveTravelState,
): InteractiveTravelPresentation {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return defaultPresentation(travel);
  }
  const raw = value as Record<string, unknown>;
  const phases = new Set<InteractiveTravelPresentationPhase>([
    "introReaction",
    "departureWait",
    "story",
    "result",
    "choice",
    "completed",
  ]);
  if (
    typeof raw.phase !== "string" ||
    !phases.has(raw.phase as InteractiveTravelPresentationPhase) ||
    !Number.isInteger(raw.partNumber) ||
    Number(raw.partNumber) < 1 ||
    Number(raw.partNumber) > 7 ||
    !Number.isInteger(raw.portionIndex) ||
    Number(raw.portionIndex) < 0
  ) {
    return defaultPresentation(travel);
  }
  return {
    phase: raw.phase as InteractiveTravelPresentationPhase,
    partNumber: Number(raw.partNumber),
    portionIndex: Number(raw.portionIndex),
  };
}

function parseStoredTravel(raw: string): LocalInteractiveTravel {
  const parsed = JSON.parse(raw) as Record<string, unknown>;
  const response = parseInteractiveTravelResponse({ travel: parsed.travel });
  return {
    travel: response.travel,
    appliedResultParts: Array.isArray(parsed.appliedResultParts)
      ? parsed.appliedResultParts.filter(
          (value): value is number => Number.isInteger(value) && value >= 1 && value <= 7,
        )
      : [],
    presentation: parsedPresentation(parsed.presentation, response.travel),
  };
}

export function readLocalInteractiveTravel(petId: string): LocalInteractiveTravel | null {
  try {
    const raw =
      window.localStorage.getItem(storageKey(petId)) ??
      window.localStorage.getItem(storageKey(petId, LEGACY_STORAGE_PREFIX));
    return raw ? parseStoredTravel(raw) : null;
  } catch {
    return null;
  }
}

export function writeLocalInteractiveTravel(
  petId: string,
  value: LocalInteractiveTravel,
): LocalInteractiveTravel {
  try {
    window.localStorage.setItem(storageKey(petId), JSON.stringify(value));
  } catch {
    // The current screen can keep working if WebView storage is unavailable.
  }
  return value;
}

export function clearLocalInteractiveTravel(petId: string) {
  try {
    window.localStorage.removeItem(storageKey(petId));
    window.localStorage.removeItem(storageKey(petId, LEGACY_STORAGE_PREFIX));
  } catch {
    // WebView storage can be unavailable in restricted browser modes.
  }
}
