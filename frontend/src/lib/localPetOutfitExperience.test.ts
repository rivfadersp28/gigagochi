import { describe, expect, it } from "vitest";

import {
  adoptChargedOutfitExperience,
  chargeOutfitExperience,
  refundOutfitExperience,
} from "./localPetOutfitExperience";
import type { LocalPetState } from "./types";

const pet: LocalPetState = {
  version: 2,
  petId: "pet-1",
  description: "мышонок",
  experience: 500,
  createdAt: "2026-07-16T10:00:00.000Z",
  updatedAt: "2026-07-16T10:00:00.000Z",
  lastInteractionAt: "2026-07-16T10:00:00.000Z",
  lastStatsTickAt: "2026-07-16T10:00:00.000Z",
  lastStatTickAt: {
    hunger: "2026-07-16T10:00:00.000Z",
    happiness: "2026-07-16T10:00:00.000Z",
    energy: "2026-07-16T10:00:00.000Z",
  },
  stage: "baby",
  mood: "idle",
  stats: { hunger: 100, happiness: 100, energy: 100 },
};

describe("outfit experience ledger", () => {
  it("charges and refunds exactly once for one request key", () => {
    const charged = chargeOutfitExperience(pet, "outfit-request-0001", 200).pet;
    expect(charged.experience).toBe(300);
    expect(chargeOutfitExperience(charged, "outfit-request-0001", 200).pet.experience).toBe(300);

    const refunded = refundOutfitExperience(charged, "outfit-request-0001", 200).pet;
    expect(refunded.experience).toBe(500);
    expect(refundOutfitExperience(refunded, "outfit-request-0001", 200).pet.experience).toBe(500);
    expect(chargeOutfitExperience(refunded, "outfit-request-0001", 200).rejected).toBe(true);
  });

  it("adopts a legacy charged job without charging it twice", () => {
    const adopted = adoptChargedOutfitExperience(pet, "outfit-request-legacy-1", 200).pet;
    expect(adopted.experience).toBe(500);
    expect(refundOutfitExperience(adopted, "outfit-request-legacy-1", 200).pet.experience)
      .toBe(700);
  });
});
