"use client";

/* eslint-disable @next/next/no-img-element */
/* eslint-disable react-hooks/set-state-in-effect -- effects drive the persisted travel state machine */
import { ArrowLeft, Play, RotateCcw } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { SmoothBackgroundVideo } from "@/components/SmoothBackgroundVideo";

import {
  animateInteractiveTravelPart,
  captureInteractiveTravelFinale,
  continueInteractiveTravel,
  getInteractiveTravelSuggestions,
  illustrateInteractiveTravelPart,
  startInteractiveTravel,
} from "@/lib/api";
import { presentError, type PresentedError } from "@/lib/errorPresentation";
import {
  currentPendingPart,
  interactiveTravelCharacterImageUrl,
  patchInteractiveTravelPartBackground,
  patchInteractiveTravelPartVideo,
  resultPortions,
  splitInteractiveTravelText,
  storyPortions,
} from "@/lib/interactiveTravelPresentation";
import {
  clearLocalInteractiveTravel,
  readLocalInteractiveTravel,
  writeLocalInteractiveTravel,
  type InteractiveTravelPresentation,
  type LocalInteractiveTravel,
} from "@/lib/localInteractiveTravel";
import {
  getInteractiveTravelDemo,
  resetInteractiveTravelGeneration,
} from "@/lib/interactiveTravelDebugApi";
import { buildMemorySnapshotContext } from "@/lib/localPetMemoryRecall";
import { readLocalPetMemory } from "@/lib/localPetMemoryStorage";
import { readLocalChatHistory } from "@/lib/localPetStorage";
import {
  canUseDebugMenu,
  hapticNotification,
  setTelegramBackgroundColor,
  useTelegramBackButton,
} from "@/lib/telegram";
import type { InteractiveTravelPart, InteractiveTravelState } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";

import { ErrorNotice } from "./ErrorNotice";
import { PetSpeechBubble } from "./pet-dashboard/PetSpeechBubble";
import { PetThinkingIndicator } from "./pet-dashboard/PetThinkingIndicator";
import styles from "./InteractiveTravelScreen.module.css";

const DESTINATION_MAX_LENGTH = 500;
const ADVICE_MAX_LENGTH = 1000;
const INTRO_PORTION_DURATION_MS = 3_200;
const CHARACTER_TRANSITION_DURATION_MS = 500;
const MINIMUM_WAIT_DURATION_MS = 1_600;
const DEMO_WAIT_DURATION_MS = 5_000;
const ENTRY_BACKGROUND_VIDEO = "/figma/travel-entry-bg.mp4?ping_pong_v=20260714-2";
const SPEECH_BUBBLE_SHAPE = "/figma/speech-bubble-new.svg";
const VIDEO_FILTER = "/figma/video-filter-normal.webp?v=20260713-video-filter-lossless-webp-1";
const SHOW_LOCAL_CONTROLS = process.env.NODE_ENV !== "production";
const FALLBACK_DESTINATIONS = [
  "в подземелье",
  "на болото",
  "в лес",
  "к маяку",
  "в пещеру",
  "на остров",
];

type InteractiveTravelScreenProps = {
  petId: string;
};

function shuffledFallbackDestinations() {
  return [...FALLBACK_DESTINATIONS]
    .sort(() => Math.random() - 0.5)
    .slice(0, 3);
}

function illustrationKey(travelId: string, partNumber: number) {
  return `${travelId}:${partNumber}`;
}

function fallbackIntroReaction(destination: string | undefined) {
  const place = destination?.trim() || "в путешествие";
  const hasDirectionPreposition = /^(?:в|во|на|к|ко|за|под)\s/iu.test(place);
  return hasDirectionPreposition
    ? `Сейчас подготовлюсь и отправлюсь ${place}!`
    : `Сейчас подготовлюсь и отправлюсь в путь — ${place}!`;
}

function partByNumber(parts: InteractiveTravelPart[], partNumber: number) {
  return parts.find((part) => part.partNumber === partNumber) ?? null;
}

function restoredBackgroundVideo(session: LocalInteractiveTravel | null) {
  if (!session || session.presentation.phase === "introReaction") {
    return null;
  }
  if (session.presentation.phase === "departureWait") {
    return (
      [...session.travel.parts]
        .reverse()
        .find(
          (part) =>
            part.partNumber < session.presentation.partNumber && part.backgroundVideoUrl,
        )?.backgroundVideoUrl ?? null
    );
  }
  return (
    partByNumber(session.travel.parts, session.presentation.partNumber)?.backgroundVideoUrl ??
    null
  );
}

function pendingDemoPart(part: InteractiveTravelPart): InteractiveTravelPart {
  return { ...part, answer: undefined, result: undefined };
}

function initialDemoSession(travel: InteractiveTravelState): LocalInteractiveTravel {
  const firstPart = travel.parts[0];
  if (!firstPart) {
    throw new Error("Demo travel has no parts");
  }
  return {
    travel: {
      ...travel,
      parts: [pendingDemoPart(firstPart)],
      completed: false,
      outcomeValence: undefined,
      statImpact: undefined,
    },
    appliedResultParts: [],
    presentation: {
      phase: "introReaction",
      partNumber: firstPart.partNumber,
      portionIndex: 0,
    },
  };
}

function continueDemoSession(
  session: LocalInteractiveTravel,
  canonicalTravel: InteractiveTravelState,
): LocalInteractiveTravel {
  const pendingPart = currentPendingPart(session.travel);
  if (!pendingPart) {
    throw new Error("Demo travel has no pending part");
  }
  const completedPart = partByNumber(canonicalTravel.parts, pendingPart.partNumber);
  if (!completedPart?.result || !completedPart.answer) {
    throw new Error("Demo travel part is incomplete");
  }
  const nextCanonicalPart = partByNumber(canonicalTravel.parts, pendingPart.partNumber + 1);
  const parts = session.travel.parts.map((part) =>
    part.partNumber === pendingPart.partNumber ? completedPart : part,
  );
  if (nextCanonicalPart) {
    parts.push(pendingDemoPart(nextCanonicalPart));
  }
  return {
    ...session,
    travel: {
      ...session.travel,
      parts,
      completed: !nextCanonicalPart,
      outcomeValence: nextCanonicalPart ? undefined : canonicalTravel.outcomeValence,
      statImpact: nextCanonicalPart ? undefined : canonicalTravel.statImpact,
    },
    presentation: {
      phase: "result",
      partNumber: pendingPart.partNumber,
      portionIndex: 0,
    },
  };
}

export function InteractiveTravelScreen({ petId }: InteractiveTravelScreenProps) {
  const router = useRouter();
  const localPet = useLocalPetState();
  const [session, setSession] = useState<LocalInteractiveTravel | null>(null);
  const [demoTravel, setDemoTravel] = useState<InteractiveTravelState | null>(null);
  const capturedFinaleTravelIdRef = useRef<string | null>(null);
  const [isRestored, setIsRestored] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggestionsNonce, setSuggestionsNonce] = useState(0);
  const [isLoadingSuggestions, setIsLoadingSuggestions] = useState(false);
  const [showCustomDestination, setShowCustomDestination] = useState(false);
  const [showCustomAction, setShowCustomAction] = useState(false);
  const [destination, setDestination] = useState("");
  const [advice, setAdvice] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<PresentedError | null>(null);
  const [failedIllustrations, setFailedIllustrations] = useState<string[]>([]);
  const [failedAnimations, setFailedAnimations] = useState<string[]>([]);
  const [loadedVideos, setLoadedVideos] = useState<string[]>([]);
  const [waitElapsedKey, setWaitElapsedKey] = useState<string | null>(null);
  const [brokenVideos, setBrokenVideos] = useState<string[]>([]);
  const [visibleBackgroundVideoUrl, setVisibleBackgroundVideoUrl] = useState<string | null>(
    null,
  );
  const [characterImageSrc, setCharacterImageSrc] = useState<string | null>(null);
  const [isCharacterEntranceComplete, setIsCharacterEntranceComplete] = useState(false);
  const [exitingCharacterTravelId, setExitingCharacterTravelId] = useState<string | null>(null);
  const [isResettingTravel, setIsResettingTravel] = useState(false);
  const submittingRef = useRef(false);
  const requestEpochRef = useRef(0);
  const illustrationRequestsRef = useRef(new Set<string>());
  const failedIllustrationsRef = useRef(new Set<string>());
  const animationRequestsRef = useRef(new Set<string>());
  const failedAnimationsRef = useRef(new Set<string>());
  const backgroundTransitionsRef = useRef(new Set<string>());
  const customDestinationTriggerRef = useRef<HTMLButtonElement>(null);
  const customActionTriggerRef = useRef<HTMLButtonElement>(null);
  const wasCustomDestinationOpenRef = useRef(false);
  const wasCustomActionOpenRef = useRef(false);
  const demoAutoStartAttemptedRef = useRef(false);

  const pet = localPet.pet;
  const petName = pet?.name?.trim() || "Персонаж";

  const goBack = useCallback(() => {
    if (showCustomAction) {
      setShowCustomAction(false);
      setError(null);
      return;
    }
    if (showCustomDestination) {
      setShowCustomDestination(false);
      setError(null);
      return;
    }
    router.push(`/pet/${petId}`);
  }, [petId, router, showCustomAction, showCustomDestination]);

  useTelegramBackButton(goBack, SHOW_LOCAL_CONTROLS);

  useEffect(() => {
    setTelegramBackgroundColor("#000000");
  }, []);

  useEffect(() => {
    const wasOpen = wasCustomDestinationOpenRef.current;
    wasCustomDestinationOpenRef.current = showCustomDestination;
    if (wasOpen && !showCustomDestination) {
      customDestinationTriggerRef.current?.focus();
    }
  }, [showCustomDestination]);

  useEffect(() => {
    const wasOpen = wasCustomActionOpenRef.current;
    wasCustomActionOpenRef.current = showCustomAction;
    if (wasOpen && !showCustomAction) {
      customActionTriggerRef.current?.focus();
    }
  }, [showCustomAction]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      const restoredSession = readLocalInteractiveTravel(petId);
      setSession(restoredSession);
      setVisibleBackgroundVideoUrl(restoredBackgroundVideo(restoredSession));
      setIsRestored(true);
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [petId]);

  useEffect(() => {
    const travel = session?.travel;
    if (
      demoTravel ||
      !travel?.completed ||
      capturedFinaleTravelIdRef.current === travel.travelId
    ) {
      return;
    }
    capturedFinaleTravelIdRef.current = travel.travelId;
    void captureInteractiveTravelFinale(travel).catch(() => {
      capturedFinaleTravelIdRef.current = null;
    });
  }, [demoTravel, session]);

  useEffect(() => {
    if (localPet.status === "empty") {
      router.replace("/");
      return;
    }
    if (localPet.status === "ready" && pet?.petId !== petId) {
      router.replace(pet ? `/pet/${pet.petId}` : "/");
    }
  }, [localPet.status, pet, petId, router]);

  useEffect(() => {
    if (!pet) {
      setCharacterImageSrc(null);
      return;
    }
    const nextCharacterImageSrc = interactiveTravelCharacterImageUrl(pet);
    setCharacterImageSrc(nextCharacterImageSrc);
    if (nextCharacterImageSrc) {
      const preload = new Image();
      preload.src = nextCharacterImageSrc;
    }
  }, [pet]);

  const persistSession = useCallback(
    (next: LocalInteractiveTravel) => {
      if (!demoTravel) {
        writeLocalInteractiveTravel(petId, next);
      }
      setSession(next);
    },
    [demoTravel, petId],
  );

  const applyPartImpacts = useCallback(
    (next: LocalInteractiveTravel) => {
      if (demoTravel || !pet) {
        return next;
      }
      const applied = new Set(next.appliedResultParts);
      const freshParts = next.travel.parts.filter(
        (part) => part.result && !applied.has(part.partNumber),
      );
      if (freshParts.length === 0) {
        return next;
      }
      const stats = { ...pet.stats };
      let hasStatsChange = false;
      for (const part of freshParts) {
        for (const impact of part.result?.statImpacts ?? []) {
          stats[impact.stat] += impact.amount;
          hasStatsChange = true;
        }
        applied.add(part.partNumber);
      }
      if (hasStatsChange) {
        localPet.applyStatsPatch({ stats });
      }
      return { ...next, appliedResultParts: [...applied].sort() };
    },
    [demoTravel, localPet, pet],
  );

  const updatePresentation = useCallback(
    (presentation: InteractiveTravelPresentation) => {
      setSession((current) => {
        if (!current) {
          return current;
        }
        const next = { ...current, presentation };
        if (!demoTravel) {
          writeLocalInteractiveTravel(petId, next);
        }
        return next;
      });
    },
    [demoTravel, petId],
  );

  useEffect(() => {
    if (!isRestored || session || demoTravel || !pet || localPet.status !== "ready") {
      return;
    }
    const requestEpoch = ++requestEpochRef.current;
    setIsLoadingSuggestions(true);
    setError(null);
    void getInteractiveTravelSuggestions(pet, { includeDebug: canUseDebugMenu() })
      .then((response) => {
        if (requestEpoch === requestEpochRef.current) {
          setSuggestions(response.destinations);
        }
      })
      .catch((caught) => {
        if (requestEpoch !== requestEpochRef.current) {
          return;
        }
        setSuggestions(shuffledFallbackDestinations());
        setError(presentError(caught, "Не получилось обновить варианты. Можно выбрать любой из этих."));
      })
      .finally(() => {
        if (requestEpoch === requestEpochRef.current) {
          setIsLoadingSuggestions(false);
        }
      });
  }, [demoTravel, isRestored, localPet.status, pet, session, suggestionsNonce]);

  const ensureAnimation = useCallback(
    async (travelSession: LocalInteractiveTravel, part: InteractiveTravelPart) => {
      if (!part.backgroundImageUrl || part.backgroundVideoUrl) {
        return;
      }
      const key = illustrationKey(travelSession.travel.travelId, part.partNumber);
      if (animationRequestsRef.current.has(key) || failedAnimationsRef.current.has(key)) {
        return;
      }
      animationRequestsRef.current.add(key);
      const requestEpoch = requestEpochRef.current;
      try {
        const response = await animateInteractiveTravelPart(travelSession.travel, part);
        if (requestEpoch !== requestEpochRef.current) {
          return;
        }
        if (response.partNumber !== part.partNumber) {
          throw new Error("Animation part mismatch");
        }
        setSession((current) => {
          if (!current || current.travel.travelId !== travelSession.travel.travelId) {
            return current;
          }
          const next = {
            ...current,
            travel: patchInteractiveTravelPartVideo(
              current.travel,
              part.partNumber,
              response.videoUrl,
            ),
          };
          writeLocalInteractiveTravel(petId, next);
          return next;
        });
      } catch {
        if (requestEpoch === requestEpochRef.current) {
          failedAnimationsRef.current.add(key);
          setFailedAnimations((current) => [...new Set([...current, key])]);
        }
      } finally {
        animationRequestsRef.current.delete(key);
      }
    },
    [petId],
  );

  const ensureIllustration = useCallback(
    async (travelSession: LocalInteractiveTravel, part: InteractiveTravelPart) => {
      if (!pet || part.backgroundImageUrl) {
        return;
      }
      const key = illustrationKey(travelSession.travel.travelId, part.partNumber);
      if (
        illustrationRequestsRef.current.has(key) ||
        failedIllustrationsRef.current.has(key)
      ) {
        return;
      }
      illustrationRequestsRef.current.add(key);
      const requestEpoch = requestEpochRef.current;
      try {
        const response = await illustrateInteractiveTravelPart(
          travelSession.travel,
          part,
          pet,
        );
        if (requestEpoch !== requestEpochRef.current) {
          return;
        }
        if (response.partNumber !== part.partNumber) {
          throw new Error("Illustration part mismatch");
        }
        setSession((current) => {
          if (!current || current.travel.travelId !== travelSession.travel.travelId) {
            return current;
          }
          const next = {
            ...current,
            travel: patchInteractiveTravelPartBackground(
              current.travel,
              part.partNumber,
              response.imageUrl,
            ),
          };
          writeLocalInteractiveTravel(petId, next);
          return next;
        });
      } catch {
        if (requestEpoch === requestEpochRef.current) {
          failedIllustrationsRef.current.add(key);
          setFailedIllustrations((current) => [...new Set([...current, key])]);
        }
      } finally {
        illustrationRequestsRef.current.delete(key);
      }
    },
    [pet, petId],
  );

  const phase = session?.presentation.phase;
  const activePartNumber = session?.presentation.partNumber ?? 1;
  const activePart = session
    ? partByNumber(session.travel.parts, activePartNumber)
    : null;
  const activeIllustrationKey = session
    ? illustrationKey(session.travel.travelId, activePartNumber)
    : null;

  useEffect(() => {
    if (
      !session ||
      demoTravel ||
      !activePart ||
      phase !== "departureWait"
    ) {
      return;
    }
    void ensureIllustration(session, activePart);
  }, [activePart, demoTravel, ensureIllustration, phase, session]);

  useEffect(() => {
    if (
      !session ||
      demoTravel ||
      phase === "introReaction" ||
      !activePart?.backgroundImageUrl ||
      activePart.backgroundVideoUrl
    ) {
      return;
    }
    void ensureAnimation(session, activePart);
  }, [activePart, demoTravel, ensureAnimation, phase, session]);

  const introText =
    session?.travel.introReaction?.text ??
    fallbackIntroReaction(session?.travel.destination);
  const introPortions = splitInteractiveTravelText(introText);
  const presentationPortionIndex = session?.presentation.portionIndex ?? 0;
  const introCharacterTravelId =
    phase === "introReaction" ? session?.travel.travelId ?? null : null;
  const showTravelCharacter = Boolean(
    characterImageSrc && introCharacterTravelId,
  );
  const isIntroCharacterEntranceComplete =
    introCharacterTravelId === null || isCharacterEntranceComplete;
  const isIntroCharacterExiting =
    introCharacterTravelId !== null && exitingCharacterTravelId === introCharacterTravelId;

  useEffect(() => {
    if (!showTravelCharacter) {
      setIsCharacterEntranceComplete(false);
      return;
    }
    const timeoutId = window.setTimeout(() => {
      setIsCharacterEntranceComplete(true);
    }, CHARACTER_TRANSITION_DURATION_MS);
    return () => window.clearTimeout(timeoutId);
  }, [showTravelCharacter]);

  useEffect(() => {
    if (
      phase !== "introReaction" ||
      !isIntroCharacterEntranceComplete ||
      isIntroCharacterExiting
    ) {
      return;
    }
    const timeoutId = window.setTimeout(() => {
      const nextIndex = presentationPortionIndex + 1;
      if (nextIndex < Math.max(1, introPortions.length)) {
        updatePresentation({
          phase: "introReaction",
          partNumber: activePartNumber,
          portionIndex: nextIndex,
        });
        return;
      }
      if (introCharacterTravelId) {
        setExitingCharacterTravelId(introCharacterTravelId);
      }
    }, INTRO_PORTION_DURATION_MS);
    return () => window.clearTimeout(timeoutId);
  }, [
    activePartNumber,
    introPortions.length,
    introCharacterTravelId,
    isIntroCharacterEntranceComplete,
    isIntroCharacterExiting,
    phase,
    presentationPortionIndex,
    updatePresentation,
  ]);

  useEffect(() => {
    if (!isIntroCharacterExiting) {
      return;
    }
    const timeoutId = window.setTimeout(() => {
      updatePresentation({
        phase: "departureWait",
        partNumber: activePartNumber,
        portionIndex: 0,
      });
    }, CHARACTER_TRANSITION_DURATION_MS);
    return () => window.clearTimeout(timeoutId);
  }, [activePartNumber, isIntroCharacterExiting, updatePresentation]);

  useEffect(() => {
    if (demoTravel || phase !== "departureWait" || !activeIllustrationKey) {
      return;
    }
    setWaitElapsedKey(null);
    const timeoutId = window.setTimeout(() => {
      setWaitElapsedKey(activeIllustrationKey);
    }, MINIMUM_WAIT_DURATION_MS);
    return () => window.clearTimeout(timeoutId);
  }, [activeIllustrationKey, demoTravel, phase]);

  useEffect(() => {
    if (
      !session ||
      demoTravel ||
      !activePart ||
      phase !== "departureWait" ||
      waitElapsedKey !== activeIllustrationKey
    ) {
      return;
    }
    const backgroundVideoUrl = activePart.backgroundVideoUrl;
    const videoReady = Boolean(
      backgroundVideoUrl && loadedVideos.includes(backgroundVideoUrl),
    );
    const videoFailed = Boolean(
      backgroundVideoUrl && brokenVideos.includes(backgroundVideoUrl),
    );
    const mediaFailed = activeIllustrationKey
      ? failedIllustrations.includes(activeIllustrationKey) ||
        failedAnimations.includes(activeIllustrationKey)
      : false;
    if (videoReady && backgroundVideoUrl) {
      const transitionKey = `${activeIllustrationKey}:${backgroundVideoUrl}`;
      if (backgroundTransitionsRef.current.has(transitionKey)) {
        return;
      }
      backgroundTransitionsRef.current.add(transitionKey);
      if (visibleBackgroundVideoUrl === backgroundVideoUrl) {
        updatePresentation({
          phase: "story",
          partNumber: activePart.partNumber,
          portionIndex: 0,
        });
        return;
      }
      setVisibleBackgroundVideoUrl(backgroundVideoUrl);
      updatePresentation({
        phase: "story",
        partNumber: activePart.partNumber,
        portionIndex: 0,
      });
      return;
    }
    if (videoFailed || mediaFailed) {
      updatePresentation({
        phase: "story",
        partNumber: activePart.partNumber,
        portionIndex: 0,
      });
    }
  }, [
    activeIllustrationKey,
    activePart,
    brokenVideos,
    demoTravel,
    failedAnimations,
    failedIllustrations,
    loadedVideos,
    phase,
    session,
    updatePresentation,
    visibleBackgroundVideoUrl,
    waitElapsedKey,
  ]);

  useEffect(() => {
    if (!demoTravel || !activePart || phase !== "departureWait") {
      return;
    }
    setVisibleBackgroundVideoUrl(activePart.backgroundVideoUrl ?? null);
    const timeoutId = window.setTimeout(() => {
      updatePresentation({
        phase: "story",
        partNumber: activePart.partNumber,
        portionIndex: 0,
      });
    }, DEMO_WAIT_DURATION_MS);
    return () => window.clearTimeout(timeoutId);
  }, [activePart, demoTravel, phase, updatePresentation]);

  async function handleStartDemo() {
    if (submittingRef.current) {
      return;
    }
    submittingRef.current = true;
    const requestEpoch = ++requestEpochRef.current;
    setIsSubmitting(true);
    setError(null);
    try {
      const response = await getInteractiveTravelDemo();
      if (requestEpoch !== requestEpochRef.current) {
        return;
      }
      setDemoTravel(response.travel);
      setSession(initialDemoSession(response.travel));
      setVisibleBackgroundVideoUrl(null);
      setShowCustomDestination(false);
      setShowCustomAction(false);
      setDestination("");
      setAdvice("");
      setIsLoadingSuggestions(false);
      hapticNotification("success");
    } catch (caught) {
      if (requestEpoch !== requestEpochRef.current) {
        return;
      }
      setIsLoadingSuggestions(false);
      setError(presentError(caught, "Не получилось запустить демо-историю."));
      hapticNotification("error");
    } finally {
      if (requestEpoch === requestEpochRef.current) {
        submittingRef.current = false;
        setIsSubmitting(false);
      }
    }
  }

  useEffect(() => {
    if (
      demoAutoStartAttemptedRef.current ||
      !canUseDebugMenu() ||
      !isRestored ||
      new URLSearchParams(window.location.search).get("demo") !== "1"
    ) {
      return;
    }
    demoAutoStartAttemptedRef.current = true;
    void handleStartDemo();
  }, [isRestored]);

  async function beginTravel(rawDestination: string) {
    const value = rawDestination.trim();
    if (!pet || submittingRef.current) {
      return;
    }
    if (!value) {
      setError({ message: "Напиши, куда отправить персонажа" });
      return;
    }
    submittingRef.current = true;
    const requestEpoch = ++requestEpochRef.current;
    setIsSubmitting(true);
    setError(null);
    try {
      const response = await startInteractiveTravel(value, pet, {
        includeDebug: canUseDebugMenu(),
        history: readLocalChatHistory().messages,
        memoryContext: buildMemorySnapshotContext(readLocalPetMemory(pet.petId)),
      });
      if (requestEpoch !== requestEpochRef.current) {
        return;
      }
      const firstPart = response.travel.parts[0];
      const next = applyPartImpacts({
        travel: response.travel,
        appliedResultParts: [],
        presentation: {
          phase: "introReaction",
          partNumber: firstPart?.partNumber ?? 1,
          portionIndex: 0,
        },
      });
      setVisibleBackgroundVideoUrl(null);
      persistSession(next);
      setShowCustomDestination(false);
      setDestination("");
      hapticNotification("success");
    } catch (caught) {
      if (requestEpoch !== requestEpochRef.current) {
        return;
      }
      setError(presentError(caught, "Не получилось начать путешествие. Попробуй ещё раз."));
      hapticNotification("error");
    } finally {
      if (requestEpoch === requestEpochRef.current) {
        submittingRef.current = false;
        setIsSubmitting(false);
      }
    }
  }

  async function submitAdvice(rawAdvice: string) {
    const value = rawAdvice.trim();
    if (!pet || !session || session.travel.completed || submittingRef.current) {
      return;
    }
    if (!value) {
      setError({ message: "Напиши, как персонажу поступить" });
      return;
    }
    const pendingPart = currentPendingPart(session.travel);
    if (!pendingPart) {
      return;
    }
    if (demoTravel) {
      submittingRef.current = true;
      setIsSubmitting(true);
      setError(null);
      try {
        setSession(continueDemoSession(session, demoTravel));
        setAdvice("");
        setShowCustomAction(false);
        hapticNotification("success");
      } catch (caught) {
        setError(presentError(caught, "Не получилось продолжить демо-историю."));
        hapticNotification("error");
      } finally {
        submittingRef.current = false;
        setIsSubmitting(false);
      }
      return;
    }
    submittingRef.current = true;
    const requestEpoch = ++requestEpochRef.current;
    setIsSubmitting(true);
    setError(null);
    try {
      const response = await continueInteractiveTravel(session.travel, value, pet, {
        includeDebug: canUseDebugMenu(),
        history: readLocalChatHistory().messages,
        memoryContext: buildMemorySnapshotContext(readLocalPetMemory(pet.petId)),
      });
      if (requestEpoch !== requestEpochRef.current) {
        return;
      }
      const next = applyPartImpacts({
        travel: response.travel,
        appliedResultParts: session.appliedResultParts,
        presentation: {
          phase: "result",
          partNumber: pendingPart.partNumber,
          portionIndex: 0,
        },
      });
      persistSession(next);
      const nextPendingPart = currentPendingPart(next.travel);
      if (nextPendingPart) {
        void ensureIllustration(next, nextPendingPart);
      }
      setAdvice("");
      setShowCustomAction(false);
      hapticNotification("success");
    } catch (caught) {
      if (requestEpoch !== requestEpochRef.current) {
        return;
      }
      setError(presentError(caught, "Не получилось продолжить историю. Попробуй ещё раз."));
      hapticNotification("error");
    } finally {
      if (requestEpoch === requestEpochRef.current) {
        submittingRef.current = false;
        setIsSubmitting(false);
      }
    }
  }

  function resetLocalTravel() {
    requestEpochRef.current += 1;
    submittingRef.current = false;
    illustrationRequestsRef.current.clear();
    failedIllustrationsRef.current.clear();
    animationRequestsRef.current.clear();
    failedAnimationsRef.current.clear();
    backgroundTransitionsRef.current.clear();
    clearLocalInteractiveTravel(petId);
    setDemoTravel(null);
    setSession(null);
    setSuggestions([]);
    setSuggestionsNonce((current) => current + 1);
    setShowCustomDestination(false);
    setShowCustomAction(false);
    setDestination("");
    setAdvice("");
    setIsSubmitting(false);
    setError(null);
    setFailedIllustrations([]);
    setFailedAnimations([]);
    setLoadedVideos([]);
    setWaitElapsedKey(null);
    setBrokenVideos([]);
    setVisibleBackgroundVideoUrl(null);
    setIsCharacterEntranceComplete(false);
  }

  function handleNewTravel() {
    if (demoTravel) {
      requestEpochRef.current += 1;
      const restoredSession = readLocalInteractiveTravel(petId);
      setDemoTravel(null);
      setSession(restoredSession);
      setVisibleBackgroundVideoUrl(restoredBackgroundVideo(restoredSession));
      setShowCustomAction(false);
      setAdvice("");
      setError(null);
      if (!restoredSession) {
        setSuggestions([]);
        setSuggestionsNonce((current) => current + 1);
      }
      return;
    }
    resetLocalTravel();
  }

  async function handleDebugRestart() {
    if (isResettingTravel) {
      return;
    }
    if (demoTravel) {
      setSession(initialDemoSession(demoTravel));
      setVisibleBackgroundVideoUrl(null);
      setError(null);
      setIsCharacterEntranceComplete(false);
      setExitingCharacterTravelId(null);
      hapticNotification("success");
      return;
    }
    const travelId = session?.travel.travelId;
    requestEpochRef.current += 1;
    setIsResettingTravel(true);
    setError(null);
    try {
      if (travelId) {
        await resetInteractiveTravelGeneration(travelId);
      }
      resetLocalTravel();
      hapticNotification("success");
    } catch (caught) {
      setError(presentError(caught, "Не получилось перезапустить путешествие."));
      hapticNotification("error");
    } finally {
      setIsResettingTravel(false);
    }
  }

  function handleNext() {
    if (!session || !activePart) {
      return;
    }
    if (phase === "story") {
      const portions = storyPortions(activePart);
      const nextIndex = session.presentation.portionIndex + 1;
      updatePresentation(
        nextIndex < portions.length
          ? { phase: "story", partNumber: activePart.partNumber, portionIndex: nextIndex }
          : { phase: "choice", partNumber: activePart.partNumber, portionIndex: 0 },
      );
      return;
    }
    if (phase !== "result") {
      return;
    }
    const portions = resultPortions(activePart);
    const nextIndex = session.presentation.portionIndex + 1;
    if (nextIndex < portions.length) {
      updatePresentation({
        phase: "result",
        partNumber: activePart.partNumber,
        portionIndex: nextIndex,
      });
      return;
    }
    if (session.travel.completed) {
      updatePresentation({
        phase: "completed",
        partNumber: activePart.partNumber,
        portionIndex: 0,
      });
      return;
    }
    const nextPart = currentPendingPart(session.travel);
    updatePresentation({
      phase: nextPart ? "departureWait" : "completed",
      partNumber: nextPart?.partNumber ?? activePart.partNumber,
      portionIndex: 0,
    });
  }

  function handleDestinationSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void beginTravel(destination);
  }

  function handleAdviceSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitAdvice(advice);
  }

  const isLoading = localPet.status === "loading" || !isRestored;
  const isTravelLoading =
    isLoading || isLoadingSuggestions || isSubmitting || isResettingTravel;
  const storyTextPortions = activePart ? storyPortions(activePart) : [];
  const resultTextPortions = activePart ? resultPortions(activePart) : [];
  const portionIndex = session?.presentation.portionIndex ?? 0;
  const messageText = (() => {
    if (!session) {
      return "";
    }
    if (phase === "introReaction") {
      return introPortions[Math.min(portionIndex, introPortions.length - 1)] ?? introText;
    }
    if (phase === "departureWait") {
      return (
        activePart?.transition?.departureHook ??
        (activePartNumber === 1
          ? `${petName} отправился в путь, дорога займет какое-то время...`
          : `${petName} продолжает путешествие. Дорога займет какое-то время...`)
      );
    }
    if (phase === "story") {
      return storyTextPortions[Math.min(portionIndex, storyTextPortions.length - 1)] ?? "";
    }
    if (phase === "result") {
      return resultTextPortions[Math.min(portionIndex, resultTextPortions.length - 1)] ?? "";
    }
    if (phase === "choice") {
      const challenge = activePart?.challenge ?? "Что делать дальше?";
      return splitInteractiveTravelText(challenge)[0] ?? challenge;
    }
    return `${petName} вернулся домой. Путешествие завершено!`;
  })();

  const messageId =
    activePartNumber * 10_000 +
    ({
      introReaction: 1_000,
      departureWait: 2_000,
      story: 3_000,
      result: 4_000,
      choice: 5_000,
      completed: 6_000,
    }[
      phase ?? "story"
    ] ?? 0) +
    portionIndex;
  const preloadBackgroundVideoUrl =
    activePart?.backgroundVideoUrl &&
    activePart.backgroundVideoUrl !== visibleBackgroundVideoUrl &&
    !brokenVideos.includes(activePart.backgroundVideoUrl)
      ? activePart.backgroundVideoUrl
      : null;
  const backgroundVideoSrc =
    preloadBackgroundVideoUrl ?? visibleBackgroundVideoUrl ?? ENTRY_BACKGROUND_VIDEO;
  const shouldRevealBackgroundVideo =
    backgroundVideoSrc === ENTRY_BACKGROUND_VIDEO
    || backgroundVideoSrc === visibleBackgroundVideoUrl;
  const isChoices = phase === "choice" && !showCustomAction;
  const isNarrative = phase === "story" || phase === "result";
  return (
    <main
      className={styles.viewport}
      aria-busy={isTravelLoading}
    >
      <section className={styles.scene} aria-label="Интерактивное путешествие">
        <SmoothBackgroundVideo
          src={backgroundVideoSrc}
          className={`${styles.background} ${styles.backgroundVideo}`}
          revealWhenReady={shouldRevealBackgroundVideo}
          onReady={(readySrc) => {
            if (readySrc === ENTRY_BACKGROUND_VIDEO) {
              return;
            }
            setLoadedVideos((current) => [...new Set([...current, readySrc])]);
          }}
          onError={(brokenSrc) => {
            setBrokenVideos((current) => [...new Set([...current, brokenSrc])]);
            if (visibleBackgroundVideoUrl === brokenSrc) {
              setVisibleBackgroundVideoUrl(null);
            }
          }}
        />
        <img src={VIDEO_FILTER} alt="" className={styles.grain} aria-hidden="true" />

        {SHOW_LOCAL_CONTROLS && !isTravelLoading ? (
          <>
            <button type="button" onClick={goBack} className={styles.backButton}>
              <ArrowLeft aria-hidden="true" />
              <span className="sr-only">
                {showCustomDestination || showCustomAction
                  ? "Вернуться к вариантам"
                  : "Вернуться к персонажу"}
              </span>
            </button>

            {session || isSubmitting ? (
              <button type="button" onClick={handleNewTravel} className={styles.newStoryButton}>
                <RotateCcw aria-hidden="true" />
                <span>Создать новую историю</span>
              </button>
            ) : null}
          </>
        ) : null}

        {canUseDebugMenu() && !isTravelLoading ? (
          <div className={styles.debugMenu} aria-label="Debug путешествия">
            <span>Debug</span>
            {!demoTravel ? (
              <button
                type="button"
                onClick={() => void handleStartDemo()}
                disabled={isResettingTravel}
              >
                <Play aria-hidden="true" />
                <span>Запустить демо-историю</span>
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => void handleDebugRestart()}
              disabled={isResettingTravel}
            >
              <RotateCcw aria-hidden="true" />
              <span>{demoTravel ? "Перезапустить демо" : "Перезапустить путешествие"}</span>
            </button>
          </div>
        ) : null}

        {error && !isTravelLoading ? (
          <ErrorNotice id="interactive-travel-error" error={error} className={styles.error} />
        ) : null}

        {!isTravelLoading && showTravelCharacter && characterImageSrc ? (
          <img
            src={characterImageSrc}
            alt={petName}
            className={`${styles.character} ${
              characterImageSrc === "/test-pet/character-transparent.png"
                ? styles.transparentCharacter
                : styles.generatedCharacter
            } ${isIntroCharacterExiting ? styles.characterExiting : ""}`}
            onError={() => setCharacterImageSrc(null)}
          />
        ) : null}

        {isTravelLoading ? (
          <div className={styles.travelLoadingIndicator}>
            <PetThinkingIndicator />
          </div>
        ) : !session ? (
          showCustomDestination ? (
            <form className={styles.customForm} onSubmit={handleDestinationSubmit}>
              <h1>Куда отправим {petName}?</h1>
              <label className="sr-only" htmlFor="travel-destination">
                Свой вариант путешествия
              </label>
              <input
                id="travel-destination"
                value={destination}
                onChange={(event) => {
                  setDestination(event.target.value);
                  setError(null);
                }}
                className={styles.promptInput}
                maxLength={DESTINATION_MAX_LENGTH}
                placeholder="Свой вариант"
                disabled={isSubmitting}
                autoFocus
                enterKeyHint="go"
              />
              {destination.trim() ? (
                <button type="submit" className={styles.primaryButton} disabled={isSubmitting}>
                  Путешествие
                </button>
              ) : null}
            </form>
          ) : (
            <div className={styles.destinationPicker}>
              <h1>Куда отправим {petName}?</h1>
              <div className={styles.destinationOptions}>
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    className={styles.optionButton}
                    disabled={isSubmitting}
                    onClick={() => void beginTravel(suggestion)}
                  >
                    {suggestion}
                  </button>
                ))}
                <button
                  ref={customDestinationTriggerRef}
                  type="button"
                  className={`${styles.optionButton} ${styles.secondaryButton}`}
                  onClick={() => {
                    setShowCustomDestination(true);
                    setError(null);
                  }}
                  disabled={isSubmitting}
                >
                  Свой вариант
                </button>
              </div>
            </div>
          )
        ) : showCustomAction && phase === "choice" ? (
          <form className={styles.customForm} onSubmit={handleAdviceSubmit}>
            <h1>Что подсказать {petName}?</h1>
            <label className="sr-only" htmlFor="travel-advice">
              Свой вариант действия
            </label>
            <input
              id="travel-advice"
              value={advice}
              onChange={(event) => {
                setAdvice(event.target.value);
                setError(null);
              }}
              className={styles.promptInput}
              maxLength={ADVICE_MAX_LENGTH}
              placeholder="Свой вариант"
              disabled={isSubmitting}
              autoFocus
              enterKeyHint="send"
            />
            {advice.trim() ? (
              <button type="submit" className={styles.primaryButton} disabled={isSubmitting}>
                Подсказать
              </button>
            ) : null}
          </form>
        ) : (
          <>
            <div
              className={`${styles.bubbleAnchor} ${
                phase === "introReaction" || phase === "departureWait"
                  ? styles.bubbleAnchorCenter
                  : styles.bubbleAnchorBottom
              } ${phase === "departureWait" ? styles.mutedBubble : ""}`}
            >
              <PetSpeechBubble
                isVisible={isIntroCharacterEntranceComplete && !isIntroCharacterExiting}
                message={{
                  id: messageId,
                  text: messageText,
                  playSpeechAudio: false,
                  hasNextPortion:
                    (phase === "story" && portionIndex < storyTextPortions.length - 1) ||
                    (phase === "result" && portionIndex < resultTextPortions.length - 1),
                }}
                scaleOrigin="bottom"
                shapeSrc={SPEECH_BUBBLE_SHAPE}
                maxLines={3}
                minFontSize={20}
              />
            </div>

            {isNarrative ? (
              <button
                type="button"
                onClick={handleNext}
                className={`${styles.nextButton} ${styles.storyGlassButton}`}
              >
                Далее
              </button>
            ) : null}

            {isChoices && activePart ? (
              <div className={styles.actionsScroll} aria-label="Варианты действий">
                <div className={styles.actionsRow}>
                  {activePart.actionSuggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      className={`${styles.actionButton} ${styles.storyGlassButton}`}
                      disabled={isSubmitting}
                      onClick={() => void submitAdvice(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                  <button
                    ref={customActionTriggerRef}
                    type="button"
                    className={`${styles.actionButton} ${styles.storyGlassButton}`}
                    disabled={isSubmitting}
                    onClick={() => {
                      setShowCustomAction(true);
                      setError(null);
                    }}
                  >
                    Свой вариант
                  </button>
                </div>
              </div>
            ) : null}

            {phase === "completed" ? (
              <button type="button" onClick={goBack} className={styles.completionButton}>
                К персонажу
              </button>
            ) : null}
          </>
        )}
      </section>
    </main>
  );
}
