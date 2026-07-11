import { describe, expect, it } from "vitest";

import { applyOfflineProgress, applyStatsPatch } from "./localPetStats";
import type { LocalPetState } from "./types";

function petState(): LocalPetState {
  const tick = "2026-07-09T06:00:00.000Z";
  return {
    version: 2,
    petId: "pet-1",
    description: "листолицый питомец",
    createdAt: "2026-07-01T06:00:00.000Z",
    updatedAt: tick,
    lastInteractionAt: tick,
    lastStatsTickAt: tick,
    lastStatTickAt: {
      hunger: tick,
      happiness: tick,
      energy: tick,
    },
    stage: "baby",
    mood: "happy",
    stats: {
      hunger: 80,
      happiness: 80,
      energy: 80,
    },
  };
}

describe("localPetStats", () => {
  it("decays each stat from its own tick and advances the life stage", () => {
    const progressed = applyOfflineProgress(
      petState(),
      new Date("2026-07-10T06:00:00.000Z"),
    );

    expect(progressed.stats).toEqual({ hunger: 0, happiness: 0, energy: 0 });
    expect(progressed.stage).toBe("adult");
    expect(progressed.mood).toBe("hungry");
  });

  it("applies a partial server patch without resetting unrelated stat ticks", () => {
    const state = petState();
    const updated = applyStatsPatch(state, {
      stats: { energy: 35 },
      lastStatTickAt: { energy: "2026-07-09T10:00:00.000Z" },
    });

    expect(updated.stats).toEqual({ hunger: 80, happiness: 80, energy: 35 });
    expect(updated.lastStatTickAt.hunger).toBe(state.lastStatTickAt.hunger);
    expect(updated.lastStatTickAt.energy).toBe("2026-07-09T10:00:00.000Z");
  });

  it("dies only after spending more than 24 continuous hours at zero", () => {
    const zeroThreshold = new Date("2026-07-11T01:12:00.000Z");
    const atThreshold = applyOfflineProgress(petState(), zeroThreshold);

    expect(atThreshold.stats.hunger).toBe(0);
    expect(atThreshold.zeroStatSinceAt?.hunger).toBe("2026-07-10T01:12:00.000Z");
    expect(atThreshold.diedAt).toBeUndefined();

    const afterThreshold = applyOfflineProgress(
      atThreshold,
      new Date("2026-07-11T01:12:00.001Z"),
    );
    expect(afterThreshold.diedAt).toBe("2026-07-11T01:12:00.000Z");
  });

  it("clears a stale zero timer when the parameter is restored", () => {
    const state = {
      ...petState(),
      zeroStatSinceAt: { hunger: "2026-07-01T00:00:00.000Z" },
    };

    const progressed = applyOfflineProgress(
      state,
      new Date("2026-07-09T06:00:00.000Z"),
    );

    expect(progressed.zeroStatSinceAt?.hunger).toBeUndefined();
    expect(progressed.diedAt).toBeUndefined();
  });
});
