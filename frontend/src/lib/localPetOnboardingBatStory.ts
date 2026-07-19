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

const ONBOARDING_BAT_INCORRECT_RESULTS: Record<string, string> = {
  "Птицы":
    "Крылья есть и у птиц, и у летучих мышей, но летучие мыши не птицы. Их малыши рождаются живыми и пьют материнское молоко.",
  "Насекомые":
    "Летучая мышь гораздо ближе к зверям, чем к насекомым. У неё есть позвоночник, шерсть, а детёныша мама кормит молоком.",
  "Пресмыкающиеся":
    "Летучая мышь не пресмыкающееся. Она теплокровная, покрыта шерстью и кормит своего малыша молоком.",
};

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
  selectedChoice: string,
): LocalInteractiveTravel {
  const part = session.travel.parts[0];
  if (!part || !isOnboardingBatTravelId(session.travel.travelId)) {
    return session;
  }
  const isCorrect = selectedChoice === ONBOARDING_BAT_CORRECT_CHOICE;
  const incorrectResult = ONBOARDING_BAT_INCORRECT_RESULTS[selectedChoice];
  if (!isCorrect && !incorrectResult) {
    return session;
  }
  return {
    ...session,
    travel: {
      ...session.travel,
      parts: [{
        ...part,
        answer: selectedChoice,
        backgroundImageUrl: isCorrect
          ? ONBOARDING_BAT_SUCCESS_IMAGE
          : ONBOARDING_BAT_SITUATION_IMAGE,
        backgroundVideoUrl: isCorrect
          ? ONBOARDING_BAT_SUCCESS_VIDEO
          : ONBOARDING_BAT_SITUATION_VIDEO,
        result: {
          text: isCorrect
            ? "Летучая мышь относится к млекопитающим. Мама добралась до малыша, согрела его и накормила молоком."
            : incorrectResult,
          adviceAssessment: isCorrect ? "helpful" : "harmful",
          reaction: isCorrect
            ? "Получилось! Малыш снова рядом с мамой."
            : "Похоже, этот ответ не подходит. Давай попробуем ещё раз.",
          reactionTone: isCorrect ? "enthusiastic" : "worried",
          consequence: isCorrect
            ? "Малыш в безопасности рядом с мамой."
            : "Нужно выбрать другой ответ.",
          outcomeValence: isCorrect ? "positive" : "negative",
          experienceGained: isCorrect ? 200 : 0,
          statImpacts: [],
        },
      }],
      completed: isCorrect,
      outcomeValence: isCorrect ? "positive" : undefined,
    },
    presentation: { phase: "result", partNumber: 1, portionIndex: 0 },
  };
}

export function retryOnboardingBatStory(
  session: LocalInteractiveTravel,
): LocalInteractiveTravel {
  const part = session.travel.parts[0];
  if (
    !part
    || session.travel.completed
    || !isOnboardingBatTravelId(session.travel.travelId)
  ) {
    return session;
  }
  const pendingPart = { ...part };
  delete pendingPart.answer;
  delete pendingPart.result;
  pendingPart.backgroundImageUrl = ONBOARDING_BAT_SITUATION_IMAGE;
  pendingPart.backgroundVideoUrl = ONBOARDING_BAT_SITUATION_VIDEO;
  return {
    ...session,
    travel: {
      ...session.travel,
      parts: [pendingPart],
      completed: false,
      outcomeValence: undefined,
    },
    presentation: { phase: "story", partNumber: 1, portionIndex: 0 },
  };
}
