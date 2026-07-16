import { describe, expect, it } from "vitest";

import {
  createOnboardingBatStorySession,
  isOnboardingBatTravelId,
  ONBOARDING_BAT_CORRECT_CHOICE,
  resolveOnboardingBatStory,
} from "./localPetOnboardingBatStory";

describe("localPetOnboardingBatStory", () => {
  it("builds a fixed pending story with the reviewed situation media", () => {
    const session = createOnboardingBatStorySession("pet-1", "2026-07-16T10:00:00Z");

    expect(isOnboardingBatTravelId(session.travel.travelId)).toBe(true);
    expect(session.presentation.phase).toBe("story");
    expect(session.travel.parts[0]).toMatchObject({
      challenge: "К какой группе относится летучая мышь?",
      backgroundImageUrl: "/static/onboarding/bat-help/situation.png",
      backgroundVideoUrl: "/static/onboarding/bat-help/situation.mp4",
    });
  });

  it("resolves only to the fixed positive outcome and grants standard story EXP", () => {
    const resolved = resolveOnboardingBatStory(
      createOnboardingBatStorySession("pet-1", "2026-07-16T10:00:00Z"),
    );

    expect(resolved.travel.completed).toBe(true);
    expect(resolved.presentation.phase).toBe("result");
    expect(resolved.travel.parts[0]).toMatchObject({
      answer: ONBOARDING_BAT_CORRECT_CHOICE,
      backgroundImageUrl: "/static/onboarding/bat-help/success.png",
      backgroundVideoUrl: "/static/onboarding/bat-help/success.mp4",
      result: {
        outcomeValence: "positive",
        experienceGained: 200,
      },
    });
  });
});
