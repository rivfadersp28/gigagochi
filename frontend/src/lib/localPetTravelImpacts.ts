import { applyStatsPatch } from "./localPetStats";
import {
  clampPetExperience,
  PET_STORY_EXPERIENCE_MAX,
} from "./localPetExperience";
import type {
  InteractiveTravelPart,
  LocalPetState,
  PetStatKey,
  PetStatsPatch,
} from "./types";

export const MAX_TRAVEL_IMPACT_RECEIPTS = 64;
const MAX_TRAVEL_ID_LENGTH = 160;
const MIN_TRAVEL_PART_NUMBER = 1;
const MAX_TRAVEL_PART_NUMBER = 7;

export type TravelImpactApplication = {
  pet: LocalPetState;
  appliedResultParts: number[];
  newlyAppliedResultParts: number[];
};

export function travelImpactReceiptKey(
  travelId: string,
  partNumber: number,
): string | null {
  const normalizedTravelId = travelId.trim();
  if (
    !normalizedTravelId
    || normalizedTravelId.length > MAX_TRAVEL_ID_LENGTH
    || !Number.isSafeInteger(partNumber)
    || partNumber < MIN_TRAVEL_PART_NUMBER
    || partNumber > MAX_TRAVEL_PART_NUMBER
  ) {
    return null;
  }
  return `${normalizedTravelId}:${partNumber}`;
}

function normalizedReceipt(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const candidate = value.trim();
  const separatorIndex = candidate.lastIndexOf(":");
  if (separatorIndex <= 0) {
    return null;
  }
  const travelId = candidate.slice(0, separatorIndex);
  const partNumber = Number(candidate.slice(separatorIndex + 1));
  return travelImpactReceiptKey(travelId, partNumber);
}

export function normalizeTravelImpactReceipts(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const seen = new Set<string>();
  const newestFirst: string[] = [];
  for (let index = value.length - 1; index >= 0; index -= 1) {
    const receipt = normalizedReceipt(value[index]);
    if (!receipt || seen.has(receipt)) {
      continue;
    }
    seen.add(receipt);
    newestFirst.push(receipt);
    if (newestFirst.length >= MAX_TRAVEL_IMPACT_RECEIPTS) {
      break;
    }
  }
  return newestFirst.length > 0 ? newestFirst.reverse() : undefined;
}

export function applyInteractiveTravelImpactsToPet(
  state: LocalPetState,
  travelId: string,
  parts: readonly InteractiveTravelPart[],
  legacyAppliedResultParts: readonly number[] = [],
  now = new Date(),
): TravelImpactApplication {
  const currentReceipts = normalizeTravelImpactReceipts(state.travelImpactReceipts) ?? [];
  const receiptSet = new Set(currentReceipts);
  const legacyApplied = new Set(
    legacyAppliedResultParts.filter(
      (partNumber) =>
        Number.isSafeInteger(partNumber)
        && partNumber >= MIN_TRAVEL_PART_NUMBER
        && partNumber <= MAX_TRAVEL_PART_NUMBER,
    ),
  );
  const nextReceipts = [...currentReceipts];
  const appliedResultParts = new Set<number>();
  const newlyAppliedResultParts: number[] = [];
  const patchedStats: NonNullable<PetStatsPatch["stats"]> = {};
  let nextExperience = clampPetExperience(state.experience);

  for (const part of parts) {
    if (!part.result) {
      continue;
    }
    const receipt = travelImpactReceiptKey(travelId, part.partNumber);
    if (!receipt) {
      continue;
    }
    appliedResultParts.add(part.partNumber);
    if (receiptSet.has(receipt)) {
      continue;
    }

    receiptSet.add(receipt);
    nextReceipts.push(receipt);
    if (legacyApplied.has(part.partNumber)) {
      continue;
    }

    newlyAppliedResultParts.push(part.partNumber);
    const experienceGained = part.result.experienceGained;
    if (typeof experienceGained === "number" && Number.isFinite(experienceGained)) {
      nextExperience = clampPetExperience(
        nextExperience + Math.max(0, Math.min(PET_STORY_EXPERIENCE_MAX, experienceGained)),
      );
    }
    for (const impact of part.result.statImpacts) {
      if (
        !(["hunger", "happiness", "energy"] as const).includes(impact.stat)
        || !Number.isFinite(impact.amount)
        || impact.amount === 0
      ) {
        continue;
      }
      const stat = impact.stat as PetStatKey;
      const currentValue = patchedStats[stat] ?? state.stats[stat];
      patchedStats[stat] = currentValue + Math.max(-15, Math.min(15, impact.amount));
    }
  }

  if (nextReceipts.length === currentReceipts.length) {
    return {
      pet: state,
      appliedResultParts: [...appliedResultParts].sort((left, right) => left - right),
      newlyAppliedResultParts,
    };
  }

  const stateWithStats = Object.keys(patchedStats).length > 0
    ? applyStatsPatch(state, { stats: patchedStats }, now)
    : state;
  return {
    pet: {
      ...stateWithStats,
      experience: nextExperience,
      travelImpactReceipts: nextReceipts.slice(-MAX_TRAVEL_IMPACT_RECEIPTS),
    },
    appliedResultParts: [...appliedResultParts].sort((left, right) => left - right),
    newlyAppliedResultParts,
  };
}
