import { clampPetExperience, PET_EXPERIENCE_MAX } from "./localPetExperience";
import type { LocalPetState, OutfitExperienceReceipt } from "./types";

const MAX_OUTFIT_EXPERIENCE_RECEIPTS = 64;
const REQUEST_KEY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,95}$/u;

function normalizeReceipt(value: unknown): OutfitExperienceReceipt | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const receipt = value as Partial<OutfitExperienceReceipt>;
  if (
    typeof receipt.requestKey !== "string"
    || !REQUEST_KEY_PATTERN.test(receipt.requestKey)
    || (receipt.status !== "charged" && receipt.status !== "refunded")
    || typeof receipt.amount !== "number"
    || !Number.isSafeInteger(receipt.amount)
    || receipt.amount <= 0
    || receipt.amount > PET_EXPERIENCE_MAX
  ) return null;
  return {
    requestKey: receipt.requestKey,
    status: receipt.status,
    amount: receipt.amount,
  };
}

export function normalizeOutfitExperienceReceipts(
  value: unknown,
): OutfitExperienceReceipt[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const byKey = new Map<string, OutfitExperienceReceipt>();
  for (const valueReceipt of value) {
    const receipt = normalizeReceipt(valueReceipt);
    if (!receipt) continue;
    byKey.delete(receipt.requestKey);
    byKey.set(receipt.requestKey, receipt);
  }
  const bounded = [...byKey.values()].slice(-MAX_OUTFIT_EXPERIENCE_RECEIPTS);
  return bounded.length ? bounded : undefined;
}

export function chargeOutfitExperience(
  state: LocalPetState,
  requestKey: string,
  amount: number,
  now = new Date(),
): { pet: LocalPetState; rejected?: true } {
  const normalized = normalizeReceipt({ requestKey, amount, status: "charged" });
  if (!normalized) return { pet: state, rejected: true };
  const receipts = normalizeOutfitExperienceReceipts(state.outfitExperienceReceipts) ?? [];
  const existing = receipts.find((receipt) => receipt.requestKey === requestKey);
  if (existing?.status === "charged" && existing.amount === amount) return { pet: state };
  if (existing || clampPetExperience(state.experience) < amount) {
    return { pet: state, rejected: true };
  }
  return {
    pet: {
      ...state,
      experience: clampPetExperience(state.experience) - amount,
      outfitExperienceReceipts: [...receipts, normalized].slice(
        -MAX_OUTFIT_EXPERIENCE_RECEIPTS,
      ),
      updatedAt: now.toISOString(),
    },
  };
}

export function adoptChargedOutfitExperience(
  state: LocalPetState,
  requestKey: string,
  amount: number,
  now = new Date(),
): { pet: LocalPetState; rejected?: true } {
  const normalized = normalizeReceipt({ requestKey, amount, status: "charged" });
  if (!normalized) return { pet: state, rejected: true };
  const receipts = normalizeOutfitExperienceReceipts(state.outfitExperienceReceipts) ?? [];
  const existing = receipts.find((receipt) => receipt.requestKey === requestKey);
  if (existing?.status === "charged" && existing.amount === amount) return { pet: state };
  if (existing) return { pet: state, rejected: true };
  return {
    pet: {
      ...state,
      outfitExperienceReceipts: [...receipts, normalized].slice(
        -MAX_OUTFIT_EXPERIENCE_RECEIPTS,
      ),
      updatedAt: now.toISOString(),
    },
  };
}

export function refundOutfitExperience(
  state: LocalPetState,
  requestKey: string,
  amount: number,
  now = new Date(),
): { pet: LocalPetState; rejected?: true } {
  const receipts = normalizeOutfitExperienceReceipts(state.outfitExperienceReceipts) ?? [];
  const index = receipts.findIndex((receipt) => receipt.requestKey === requestKey);
  const existing = index >= 0 ? receipts[index] : undefined;
  if (!existing || existing.amount !== amount) return { pet: state, rejected: true };
  if (existing.status === "refunded") return { pet: state };
  const nextReceipts = [...receipts];
  nextReceipts[index] = { ...existing, status: "refunded" };
  return {
    pet: {
      ...state,
      experience: Math.min(PET_EXPERIENCE_MAX, clampPetExperience(state.experience) + amount),
      outfitExperienceReceipts: nextReceipts,
      updatedAt: now.toISOString(),
    },
  };
}
