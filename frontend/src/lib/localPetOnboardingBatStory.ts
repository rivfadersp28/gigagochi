import type { LocalInteractiveTravel } from "./localInteractiveTravel";

export const ONBOARDING_BAT_CORRECT_CHOICE = "Млекопитающие";
export const ONBOARDING_BAT_CHOICES = [
  "Птицы",
  ONBOARDING_BAT_CORRECT_CHOICE,
  "Насекомые",
  "Пресмыкающиеся",
] as const;

const ONBOARDING_BAT_TRAVEL_ID_PREFIX = "onboarding-bat-help-v1-";
const ONBOARDING_BAT_SITUATION_IMAGE = "/static/onboarding/bat-help/situation.png";
const ONBOARDING_BAT_SITUATION_VIDEO = "/static/onboarding/bat-help/situation.mp4";
const ONBOARDING_BAT_SUCCESS_IMAGE = "/static/onboarding/bat-help/success.png";
const ONBOARDING_BAT_SUCCESS_VIDEO = "/static/onboarding/bat-help/success.mp4";

export function isOnboardingBatTravelId(travelId: string | null | undefined) {
  return Boolean(travelId?.startsWith(ONBOARDING_BAT_TRAVEL_ID_PREFIX));
}

export function createOnboardingBatStorySession(
  petId: string,
  generatedAt = new Date().toISOString(),
): LocalInteractiveTravel {
  return {
    travel: {
      travelId: `${ONBOARDING_BAT_TRAVEL_ID_PREFIX}${petId}`.slice(0, 160),
      generatedAt,
      destination: "старый чердак",
      overallTitle: "Малыш летучей мыши",
      generationStatus: "ready",
      plan: null,
      parts: [{
        partNumber: 1,
        title: "Малыш летучей мыши",
        storyText:
          "Ой, что это? Под крышей пищит детёныш летучей мыши. Его мама рядом и хочет его накормить. Чтобы понять, чем она кормит малыша, нужно узнать, к какой группе относится летучая мышь.",
        challenge: "К какой группе относится летучая мышь?",
        actionSuggestions: [...ONBOARDING_BAT_CHOICES],
        backgroundImageUrl: ONBOARDING_BAT_SITUATION_IMAGE,
        backgroundVideoUrl: ONBOARDING_BAT_SITUATION_VIDEO,
      }],
      completed: false,
    },
    appliedResultParts: [],
    presentation: { phase: "story", partNumber: 1, portionIndex: 0 },
  };
}

export function resolveOnboardingBatStory(
  session: LocalInteractiveTravel,
): LocalInteractiveTravel {
  const part = session.travel.parts[0];
  if (!part || !isOnboardingBatTravelId(session.travel.travelId)) {
    return session;
  }
  return {
    ...session,
    travel: {
      ...session.travel,
      parts: [{
        ...part,
        answer: ONBOARDING_BAT_CORRECT_CHOICE,
        backgroundImageUrl: ONBOARDING_BAT_SUCCESS_IMAGE,
        backgroundVideoUrl: ONBOARDING_BAT_SUCCESS_VIDEO,
        result: {
          text:
            "Летучая мышь относится к млекопитающим. Мама добралась до малыша, согрела его и накормила молоком.",
          adviceAssessment: "helpful",
          reaction: "Получилось! Малыш снова рядом с мамой.",
          reactionTone: "enthusiastic",
          consequence: "Малыш в безопасности рядом с мамой.",
          outcomeValence: "positive",
          experienceGained: 200,
          statImpacts: [],
        },
      }],
      completed: true,
      outcomeValence: "positive",
    },
    presentation: { phase: "result", partNumber: 1, portionIndex: 0 },
  };
}
