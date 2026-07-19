"use client";

/* eslint-disable @next/next/no-img-element */
/* eslint-disable react-hooks/set-state-in-effect -- effects drive the persisted travel state machine */
import { FlaskConical, Play, RotateCcw } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { SmoothBackgroundVideo } from "@/components/SmoothBackgroundVideo";
import { TiltedGlassButton } from "@/components/TiltedGlassButton";
import { XpAmount } from "@/components/XpAmount";

import {
  animateInteractiveTravelPart,
  chooseAutomaticInteractiveStory,
  continueInteractiveTravel,
  getAutomaticInteractiveStory,
  getInteractiveTravelSuggestions,
  getInteractiveTravelStatus,
  illustrateInteractiveTravelPart,
  type AutomaticInteractiveStory,
} from "@/lib/api";
import { presentError, type PresentedError } from "@/lib/errorPresentation";
import { ApiError } from "@/lib/apiTransport";
import {
  currentPendingPart,
  interactiveTravelCharacterImageUrl,
  patchInteractiveTravelPartBackground,
  patchInteractiveTravelPartVideo,
  resolvedResultParagraphs,
  splitInteractiveTravelText,
  storyAndChallengeParagraphs,
} from "@/lib/interactiveTravelPresentation";
import {
  clearLocalInteractiveTravel,
  isLocalInteractiveTravelStorageKey,
  LocalInteractiveTravelMutationLockError,
  readLocalInteractiveTravel,
  withLocalInteractiveTravelMutationLock,
  writeLocalInteractiveTravelDurably,
  type InteractiveTravelPresentation,
  type LocalInteractiveTravel,
} from "@/lib/localInteractiveTravel";
import {
  enqueueInteractiveTravelCancel,
  enqueueInteractiveTravelCapture,
  flushPendingInteractiveTravelOperations,
} from "@/lib/pendingInteractiveTravelOperations";
import {
  getInteractiveTravelDemo,
  resetInteractiveTravelGeneration,
} from "@/lib/interactiveTravelDebugApi";
import { startInteractiveTravelWithRecovery } from "@/lib/interactiveTravelStartRecovery";
import {
  firstSessionTravelConfirmation,
  isLocalFirstSessionEnabled,
  isLocalFirstSessionActive,
  readLocalPetFirstSession,
  restartLocalPetFirstSession,
  setLocalFirstSessionEnabled,
  updateLocalPetFirstSession,
  type LocalPetFirstSession,
} from "@/lib/localPetFirstSession";
import {
  createOnboardingBatStorySession,
  isOnboardingBatTravelId,
  ONBOARDING_BAT_CORRECT_CHOICE,
  resolveOnboardingBatStory,
  retryOnboardingBatStory,
} from "@/lib/localPetOnboardingBatStory";
import {
  hapticNotification,
  setTelegramBackgroundColor,
  useTelegramBackButton,
} from "@/lib/telegram";
import type { InteractiveTravelPart, InteractiveTravelState } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";
import { APP_BACKGROUND_COLOR } from "@/lib/theme";
import {
  playInteractiveTravelSuccessSound,
  primeInteractiveTravelSuccessSound,
} from "@/lib/travelSuccessAudio";
import { useTelegramCapabilities } from "@/lib/useTelegramCapabilities";

import { ErrorNotice } from "./ErrorNotice";
import { PetSpeechBubble } from "./pet-dashboard/PetSpeechBubble";
import { PetThinkingIndicator } from "./pet-dashboard/PetThinkingIndicator";
import styles from "./InteractiveTravelScreen.module.css";

const DESTINATION_MAX_LENGTH = 500;
const INTRO_PORTION_DURATION_MS = 3_200;
const CHARACTER_TRANSITION_DURATION_MS = 500;
const MINIMUM_WAIT_DURATION_MS = 1_600;
const DEMO_WAIT_DURATION_MS = 5_000;
const MAXIMUM_DEPARTURE_WAIT_DURATION_MS = 90_000;
const MEDIA_PATCH_LOCK_ACQUIRE_TIMEOUT_MS = 5 * 60_000 + 10_000;
const MEDIA_RETRY_DELAYS_MS = [2_000, 5_000] as const;
const STORY_CHARACTER_RISE_DURATION_MS = 300;
const STORY_CHARACTER_RISE_STAGGER_MS = 12;
const ENTRY_BACKGROUND_VIDEO = "/figma/travel-entry-bg.mp4?ping_pong_v=20260714-2";
const SPEECH_BUBBLE_SHAPE = "/figma/speech-bubble-new.svg";
const VIDEO_FILTER = "/figma/video-filter-normal.webp?v=20260713-video-filter-lossless-webp-1";
const STORAGE_ERROR: PresentedError = {
  message:
    "Не удалось сохранить прогресс. Освободите место на устройстве или разрешите "
    + "приложению хранить данные, затем попробуйте снова.",
};
const MUTATION_ERROR: PresentedError = {
  message: "Путешествие обновляется в другой вкладке. Подожди немного и попробуй снова.",
};
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
  automaticStoryToken?: string;
};

function interactiveMediaErrorIsRetryable(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status === undefined) {
    return true;
  }
  return error.status === 408
    || error.status === 409
    || error.status === 429
    || error.status >= 500;
}

function waitForInteractiveMediaRetry(delayMs: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, delayMs));
}

function automaticStorySession(story: AutomaticInteractiveStory): LocalInteractiveTravel {
  const selectedIndex = story.selectedChoice
    ? story.choices.indexOf(story.selectedChoice)
    : -1;
  const hasResult = selectedIndex >= 0 && Boolean(story.result);
  const part: InteractiveTravelPart = {
    partNumber: 1,
    title: story.title,
    storyText: story.storyText,
    challenge: story.question,
    actionSuggestions: [...story.choices],
    backgroundImageUrl: hasResult
      ? story.outcomeImageUrls[selectedIndex]
      : story.situationImageUrl,
    backgroundVideoUrl: hasResult
      ? story.outcomeVideoUrls[selectedIndex]
      : story.situationVideoUrl,
    ...(hasResult && story.result
      ? { answer: story.selectedChoice, result: story.result }
      : {}),
  };
  return {
    travel: {
      travelId: story.travelId,
      generatedAt: story.createdAt,
      destination: story.destination,
      overallTitle: story.title,
      generationStatus: "ready",
      plan: null,
      parts: [part],
      completed: hasResult,
      outcomeValence: story.result?.outcomeValence,
      statImpact: story.result?.statImpacts[0],
    },
    appliedResultParts: [],
    presentation: {
      phase: hasResult ? "result" : "story",
      partNumber: 1,
      portionIndex: 0,
    },
  };
}

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

function AnimatedTravelParagraph({
  animationKey,
  baseDelayMs = 0,
  className,
  startIndex,
  text,
}: {
  animationKey: string;
  baseDelayMs?: number;
  className?: string;
  startIndex: number;
  text: string;
}) {
  const tokens = text.split(/(\s+)/u).filter(Boolean);
  const tokenOffsets = tokens.map((_, index) =>
    tokens.slice(0, index).reduce((total, token) => total + Array.from(token).length, 0));
  return (
    <p className={className} aria-label={text}>
      <span className={styles.screenReaderText}>{text}</span>
      {tokens.map((token, tokenIndex) => {
        const isWhitespace = /^\s+$/u.test(token);
        if (isWhitespace) {
          return (
            <span
              key={`${animationKey}:token:${tokenIndex}`}
              aria-hidden="true"
              className={styles.storyAnimatedWhitespace}
            >
              {" "}
            </span>
          );
        }
        return (
          <span
            key={`${animationKey}:token:${tokenIndex}`}
            aria-hidden="true"
            className={styles.storyAnimatedWord}
          >
            {Array.from(token).map((character, characterOffset) => {
              const characterIndex = startIndex + tokenOffsets[tokenIndex] + characterOffset;
              const delay = baseDelayMs
                + characterIndex * STORY_CHARACTER_RISE_STAGGER_MS;
              const key = `${animationKey}:character:${characterIndex}`;
              return (
                <span
                  key={key}
                  className={styles.storyAnimatedCharacter}
                  style={{ animationDelay: `${delay}ms` }}
                >
                  {character}
                </span>
              );
            })}
          </span>
        );
      })}
    </p>
  );
}

function partByNumber(parts: InteractiveTravelPart[], partNumber: number) {
  return parts.find((part) => part.partNumber === partNumber) ?? null;
}

function isTravelPlanReady(travel: InteractiveTravelState) {
  return (travel.generationStatus ?? "ready") === "ready";
}

function presentationProgress(presentation: InteractiveTravelPresentation): number {
  const phaseOffset = {
    introReaction: 0,
    departureWait: 1,
    story: 2,
    choice: 3,
    result: 4,
    completed: 5,
  }[presentation.phase];
  return presentation.partNumber * 10_000
    + phaseOffset * 1_000
    + presentation.portionIndex;
}

function travelAdvancedPastPart(
  latest: LocalInteractiveTravel,
  expectedTravelId: string,
  expectedPartNumber: number,
): boolean {
  if (latest.travel.travelId !== expectedTravelId) {
    return true;
  }
  return latest.travel.completed
    || Boolean(partByNumber(latest.travel.parts, expectedPartNumber)?.result)
    || (currentPendingPart(latest.travel)?.partNumber ?? Number.POSITIVE_INFINITY)
      > expectedPartNumber;
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
  const pending = { ...part };
  delete pending.answer;
  delete pending.result;
  return pending;
}

function initialDemoSession(travel: InteractiveTravelState): LocalInteractiveTravel {
  const firstPart = travel.parts[0];
  if (!firstPart) {
    throw new Error("Interactive travel demo has no parts");
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
    presentation: { phase: "introReaction", partNumber: 1, portionIndex: 0 },
  };
}

function continueDemoSession(
  session: LocalInteractiveTravel,
  canonical: InteractiveTravelState,
): LocalInteractiveTravel {
  const pendingPart = currentPendingPart(session.travel);
  const canonicalPart = pendingPart
    ? partByNumber(canonical.parts, pendingPart.partNumber)
    : null;
  if (!pendingPart || !canonicalPart?.result || !canonicalPart.answer) {
    throw new Error("Interactive travel demo part is incomplete");
  }
  const nextCanonicalPart = partByNumber(canonical.parts, pendingPart.partNumber + 1);
  const resolvedParts = session.travel.parts.slice(0, -1).concat(canonicalPart);
  const parts = nextCanonicalPart
    ? resolvedParts.concat(pendingDemoPart(nextCanonicalPart))
    : resolvedParts;
  return {
    travel: {
      ...canonical,
      parts,
      completed: !nextCanonicalPart,
      outcomeValence: nextCanonicalPart ? undefined : canonical.outcomeValence,
      statImpact: nextCanonicalPart ? undefined : canonical.statImpact,
    },
    appliedResultParts: [],
    presentation: {
      phase: "result",
      partNumber: pendingPart.partNumber,
      portionIndex: 0,
    },
  };
}

export function InteractiveTravelScreen({
  petId,
  automaticStoryToken,
}: InteractiveTravelScreenProps) {
  const router = useRouter();
  const localPet = useLocalPetState();
  const canShowDebugMenu = useTelegramCapabilities().debugMenu;
  const [session, setSession] = useState<LocalInteractiveTravel | null>(null);
  const [demoTravel, setDemoTravel] = useState<InteractiveTravelState | null>(null);
  const activeTravelIdRef = useRef<string | null>(null);
  const queuedFinaleTravelIdRef = useRef<string | null>(null);
  const [isRestored, setIsRestored] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggestionsNonce, setSuggestionsNonce] = useState(0);
  const [isLoadingSuggestions, setIsLoadingSuggestions] = useState(true);
  const [showCustomDestination, setShowCustomDestination] = useState(false);
  const [destination, setDestination] = useState("");
  const [firstSession, setFirstSession] = useState<LocalPetFirstSession | null>(null);
  const [firstSessionConfirmationPortion, setFirstSessionConfirmationPortion] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<PresentedError | null>(null);
  const [failedIllustrations, setFailedIllustrations] = useState<string[]>([]);
  const [failedAnimations, setFailedAnimations] = useState<string[]>([]);
  const [loadedVideos, setLoadedVideos] = useState<string[]>([]);
  const [readyStoryVideos, setReadyStoryVideos] = useState<string[]>([]);
  const [waitElapsedKey, setWaitElapsedKey] = useState<string | null>(null);
  const [brokenVideos, setBrokenVideos] = useState<string[]>([]);
  const [visibleBackgroundVideoUrl, setVisibleBackgroundVideoUrl] = useState<string | null>(
    null,
  );
  const [characterImageSrc, setCharacterImageSrc] = useState<string | null>(null);
  const [isCharacterEntranceComplete, setIsCharacterEntranceComplete] = useState(false);
  const [exitingCharacterTravelId, setExitingCharacterTravelId] = useState<string | null>(null);
  const [isResettingTravel, setIsResettingTravel] = useState(false);
  const [isFinalActionReady, setIsFinalActionReady] = useState(false);
  const travelResetInFlightRef = useRef(false);
  const demoAutoStartAttemptedRef = useRef(false);
  const submittingRef = useRef(false);
  const requestEpochRef = useRef(0);
  const illustrationRequestsRef = useRef(new Set<string>());
  const failedIllustrationsRef = useRef(new Set<string>());
  const animationRequestsRef = useRef(new Set<string>());
  const generationStatusRequestRef = useRef<string | null>(null);
  const failedAnimationsRef = useRef(new Set<string>());
  const backgroundTransitionsRef = useRef(new Set<string>());
  const customDestinationTriggerRef = useRef<HTMLButtonElement>(null);
  const wasCustomDestinationOpenRef = useRef(false);

  const pet = localPet.pet;
  const petName = pet?.name?.trim() || "Персонаж";
  const currentPetId = pet?.petId;
  const currentPetIntroductionPending = pet?.introductionPending;
  const isOnboardingBatSession = isOnboardingBatTravelId(session?.travel.travelId);
  const firstSessionEnabled = isLocalFirstSessionEnabled();

  function restartFirstSessionFlow() {
    if (!pet) {
      return;
    }
    restartLocalPetFirstSession(pet.petId);
    router.push(`/pet/${pet.petId}`);
  }

  function toggleFirstSessionFlow() {
    if (!pet) {
      return;
    }
    if (firstSessionEnabled) {
      setLocalFirstSessionEnabled(false);
      setFirstSession(null);
    } else {
      restartLocalPetFirstSession(pet.petId);
    }
    router.push(`/pet/${pet.petId}`);
  }

  useEffect(() => {
    let cancelled = false;
    window.queueMicrotask(() => {
      if (cancelled) {
        return;
      }
      if (!currentPetId || currentPetId !== petId) {
        setFirstSession(null);
        return;
      }
      const restored = readLocalPetFirstSession({
        petId: currentPetId,
        introductionPending: currentPetIntroductionPending,
      });
      setFirstSession(restored);
      setFirstSessionConfirmationPortion(0);
    });
    return () => {
      cancelled = true;
    };
  }, [currentPetId, currentPetIntroductionPending, petId]);

  const clearTravelHistory = useCallback(() => {
    if (automaticStoryToken) {
      setSession(null);
      return;
    }
    if (isOnboardingBatSession) {
      activeTravelIdRef.current = null;
      setSession(null);
      return;
    }
    const latest = readLocalInteractiveTravel(petId);
    if (!demoTravel) {
      const travelId = latest?.travel.travelId ?? activeTravelIdRef.current;
      const queued = latest?.travel.completed
        ? enqueueInteractiveTravelCapture(latest.travel)
        : travelId
          ? enqueueInteractiveTravelCancel(travelId)
          : false;
      if (queued) {
        void flushPendingInteractiveTravelOperations().catch(() => undefined);
      }
    }
    requestEpochRef.current += 1;
    activeTravelIdRef.current = null;
    clearLocalInteractiveTravel(petId);
    setSession(null);
    setDemoTravel(null);
  }, [automaticStoryToken, demoTravel, isOnboardingBatSession, petId]);

  const goBack = useCallback(() => {
    if (showCustomDestination) {
      setShowCustomDestination(false);
      setError(null);
      return;
    }
    if (!automaticStoryToken) {
      clearTravelHistory();
    }
    router.push(`/pet/${petId}`);
  }, [automaticStoryToken, clearTravelHistory, petId, router, showCustomDestination]);

  useTelegramBackButton(goBack);

  useEffect(() => {
    if (automaticStoryToken) {
      return;
    }
    const handlePageHide = () => clearTravelHistory();
    window.addEventListener("pagehide", handlePageHide);
    return () => window.removeEventListener("pagehide", handlePageHide);
  }, [automaticStoryToken, clearTravelHistory]);

  useEffect(() => {
    setTelegramBackgroundColor("#000000");
    return () => setTelegramBackgroundColor(APP_BACKGROUND_COLOR);
  }, []);

  useEffect(() => {
    const wasOpen = wasCustomDestinationOpenRef.current;
    wasCustomDestinationOpenRef.current = showCustomDestination;
    if (wasOpen && !showCustomDestination) {
      customDestinationTriggerRef.current?.focus();
    }
  }, [showCustomDestination]);

  useEffect(() => {
    requestEpochRef.current += 1;
    activeTravelIdRef.current = null;
    queuedFinaleTravelIdRef.current = null;
    submittingRef.current = false;
    illustrationRequestsRef.current.clear();
    failedIllustrationsRef.current.clear();
    animationRequestsRef.current.clear();
    generationStatusRequestRef.current = null;
    failedAnimationsRef.current.clear();
    backgroundTransitionsRef.current.clear();
    setIsRestored(false);
    setSession(null);
    setDemoTravel(null);
    setSuggestions([]);
    setIsLoadingSuggestions(true);
    setIsSubmitting(false);
    setError(null);
    setFailedIllustrations([]);
    setFailedAnimations([]);
    setLoadedVideos([]);
    setReadyStoryVideos([]);
    setWaitElapsedKey(null);
    setBrokenVideos([]);
    setVisibleBackgroundVideoUrl(null);
    setIsCharacterEntranceComplete(false);
    setExitingCharacterTravelId(null);
    return () => {
      requestEpochRef.current += 1;
      activeTravelIdRef.current = null;
      submittingRef.current = false;
      generationStatusRequestRef.current = null;
    };
  }, [automaticStoryToken, petId]);

  useEffect(() => {
    if (
      automaticStoryToken
      || firstSession?.stage !== "awaiting-travel"
      || isOnboardingBatSession
      || localPet.status !== "ready"
      || !pet
    ) {
      return;
    }
    const next = createOnboardingBatStorySession(pet.petId);
    activeTravelIdRef.current = next.travel.travelId;
    setSession(next);
    setVisibleBackgroundVideoUrl(next.travel.parts[0]?.backgroundVideoUrl ?? null);
    setIsLoadingSuggestions(false);
    setIsRestored(true);
  }, [automaticStoryToken, firstSession, isOnboardingBatSession, localPet.status, pet]);

  useEffect(() => {
    if (
      !automaticStoryToken
      || isRestored
      || localPet.status !== "ready"
      || !pet
    ) {
      return;
    }
    const requestEpoch = ++requestEpochRef.current;
    setIsLoadingSuggestions(false);
    setError(null);
    void getAutomaticInteractiveStory(automaticStoryToken)
      .then((story) => {
        if (requestEpoch !== requestEpochRef.current) {
          return;
        }
        const next = automaticStorySession(story);
        if (next.travel.parts.some((part) => part.result)) {
          const applied = localPet.applyInteractiveTravelImpacts(
            { travelId: next.travel.travelId, parts: next.travel.parts },
            pet.petId,
          );
          if (!applied) {
            setError(STORAGE_ERROR);
          }
        }
        activeTravelIdRef.current = next.travel.travelId;
        setSession(next);
        setVisibleBackgroundVideoUrl(next.travel.parts[0]?.backgroundVideoUrl ?? null);
        setIsRestored(true);
      })
      .catch((caught) => {
        if (requestEpoch === requestEpochRef.current) {
          setError(presentError(caught, "Не удалось открыть эту историю."));
          setIsRestored(true);
        }
      });
  }, [automaticStoryToken, isRestored, localPet, pet]);

  useEffect(() => {
    if (isRestored || localPet.status !== "ready") {
      return;
    }
    if (automaticStoryToken) {
      return;
    }
    let cancelled = false;
    const timeoutId = window.setTimeout(() => {
      void withLocalInteractiveTravelMutationLock(petId, () => {
        if (cancelled) {
          return;
        }
        let restoredSession = readLocalInteractiveTravel(petId);
        if (restoredSession?.travel.parts.some((part) => part.result)) {
          const restoredPet = localPet.pet;
          const applied = restoredPet
            ? localPet.applyInteractiveTravelImpacts(
                {
                  travelId: restoredSession.travel.travelId,
                  parts: restoredSession.travel.parts,
                  legacyAppliedResultParts: restoredSession.appliedResultParts,
                },
                restoredPet.petId,
              )
            : null;
          if (!applied) {
            setError(STORAGE_ERROR);
            setIsLoadingSuggestions(false);
            setIsRestored(true);
            return;
          }
          const receiptsChanged =
            applied.appliedResultParts.length !== restoredSession.appliedResultParts.length
            || applied.appliedResultParts.some(
              (partNumber, index) =>
                partNumber !== restoredSession?.appliedResultParts[index],
            );
          if (receiptsChanged) {
            restoredSession = writeLocalInteractiveTravelDurably(petId, {
              ...restoredSession,
              appliedResultParts: applied.appliedResultParts,
            });
            if (!restoredSession) {
              setError(STORAGE_ERROR);
              setIsLoadingSuggestions(false);
              setIsRestored(true);
              return;
            }
          }
        }
        if (cancelled) {
          return;
        }
        activeTravelIdRef.current = restoredSession?.travel.travelId ?? null;
        setSession(restoredSession);
        setVisibleBackgroundVideoUrl(restoredBackgroundVideo(restoredSession));
        setIsLoadingSuggestions(restoredSession === null);
        setIsRestored(true);
      }).catch(() => {
        if (!cancelled) {
          setError(STORAGE_ERROR);
          setIsLoadingSuggestions(false);
          setIsRestored(true);
        }
      });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [automaticStoryToken, isRestored, localPet, petId]);

  useEffect(() => {
    const travel = session?.travel;
    if (automaticStoryToken) {
      return;
    }
    if (
      !demoTravel
      && !isOnboardingBatTravelId(travel?.travelId)
      && travel?.completed
      && queuedFinaleTravelIdRef.current !== travel.travelId
      && enqueueInteractiveTravelCapture(travel)
    ) {
      queuedFinaleTravelIdRef.current = travel.travelId;
    }
  }, [automaticStoryToken, demoTravel, session]);

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

  const applyPartImpacts = useCallback(
    (next: LocalInteractiveTravel): LocalInteractiveTravel | null => {
      if (demoTravel || !pet || !next.travel.parts.some((part) => part.result)) {
        return next;
      }
      const applied = localPet.applyInteractiveTravelImpacts(
        {
          travelId: next.travel.travelId,
          parts: next.travel.parts,
          legacyAppliedResultParts: next.appliedResultParts,
        },
        pet.petId,
      );
      if (!applied) {
        return null;
      }
      if (
        applied.appliedResultParts.length === next.appliedResultParts.length
        && applied.appliedResultParts.every(
          (partNumber, index) => partNumber === next.appliedResultParts[index],
        )
      ) {
        return next;
      }
      return { ...next, appliedResultParts: applied.appliedResultParts };
    },
    [demoTravel, localPet, pet],
  );

  const persistSession = useCallback(
    (next: LocalInteractiveTravel) => {
      const preliminary = writeLocalInteractiveTravelDurably(petId, next);
      if (!preliminary) {
        setError(STORAGE_ERROR);
        return false;
      }
      const reconciled = applyPartImpacts(preliminary);
      if (!reconciled) {
        setError(STORAGE_ERROR);
        return false;
      }
      const stored = reconciled === preliminary
        ? preliminary
        : writeLocalInteractiveTravelDurably(petId, reconciled);
      if (!stored) {
        setError(STORAGE_ERROR);
        return false;
      }
      activeTravelIdRef.current = stored.travel.travelId;
      setSession(stored);
      return true;
    },
    [applyPartImpacts, petId],
  );

  const adoptStoredSession = useCallback(
    (storedSession: LocalInteractiveTravel): LocalInteractiveTravel | null => {
      const reconciled = applyPartImpacts(storedSession);
      if (!reconciled) {
        setError(STORAGE_ERROR);
        return null;
      }
      const stored = reconciled === storedSession
        ? storedSession
        : writeLocalInteractiveTravelDurably(petId, reconciled);
      if (!stored) {
        setError(STORAGE_ERROR);
        return null;
      }
      activeTravelIdRef.current = stored.travel.travelId;
      setSession(stored);
      setVisibleBackgroundVideoUrl(restoredBackgroundVideo(stored));
      setError(null);
      return stored;
    },
    [applyPartImpacts, petId],
  );

  const updatePresentation = useCallback(
    (presentation: InteractiveTravelPresentation) => {
      if (demoTravel || automaticStoryToken || isOnboardingBatSession) {
        setSession((current) => current ? { ...current, presentation } : current);
        return;
      }
      const expectedTravelId = activeTravelIdRef.current;
      void withLocalInteractiveTravelMutationLock(petId, ({ assertOwned }) => {
        const latest = readLocalInteractiveTravel(petId);
        if (!latest || latest.travel.travelId !== expectedTravelId) {
          return;
        }
        if (presentationProgress(presentation) < presentationProgress(latest.presentation)) {
          setSession(latest);
          return;
        }
        assertOwned();
        const stored = writeLocalInteractiveTravelDurably(petId, {
          ...latest,
          presentation,
        });
        if (!stored) {
          throw new Error("Interactive travel presentation was not stored durably");
        }
        setSession(stored);
      }).catch((caught) => {
        if (activeTravelIdRef.current === expectedTravelId) {
          setError(
            caught instanceof LocalInteractiveTravelMutationLockError
              ? MUTATION_ERROR
              : STORAGE_ERROR,
          );
        }
      });
    },
    [automaticStoryToken, demoTravel, isOnboardingBatSession, petId],
  );

  const patchLatestMedia = useCallback(
    async (
      travelId: string,
      partNumber: number,
      media: { imageUrl: string } | { videoUrl: string },
    ): Promise<LocalInteractiveTravel | null> =>
      withLocalInteractiveTravelMutationLock(petId, ({ assertOwned }) => {
        const latest = readLocalInteractiveTravel(petId);
        if (
          !latest
          || latest.travel.travelId !== travelId
          || activeTravelIdRef.current !== travelId
        ) {
          return null;
        }
        const latestPart = partByNumber(latest.travel.parts, partNumber);
        if (!latestPart) {
          return null;
        }
        const alreadyPatched = "imageUrl" in media
          ? Boolean(latestPart.backgroundImageUrl)
          : Boolean(latestPart.backgroundVideoUrl);
        const next = alreadyPatched
          ? latest
          : {
              ...latest,
              travel: "imageUrl" in media
                ? patchInteractiveTravelPartBackground(
                    latest.travel,
                    partNumber,
                    media.imageUrl,
                  )
                : patchInteractiveTravelPartVideo(
                    latest.travel,
                    partNumber,
                    media.videoUrl,
                  ),
            };
        if (next === latest) {
          setSession(latest);
          return latest;
        }
        assertOwned();
        const stored = writeLocalInteractiveTravelDurably(petId, next);
        if (!stored) {
          throw new Error("Interactive travel media URL was not stored durably");
        }
        setSession(stored);
        return stored;
      }, { acquireTimeoutMs: MEDIA_PATCH_LOCK_ACQUIRE_TIMEOUT_MS }),
    [petId],
  );

  useEffect(() => {
    const reconcileFromStorage = (event: StorageEvent) => {
      if (demoTravel || automaticStoryToken || isOnboardingBatSession) {
        return;
      }
      if (!isLocalInteractiveTravelStorageKey(event.key, petId)) {
        return;
      }
      requestEpochRef.current += 1;
      submittingRef.current = false;
      setIsSubmitting(false);
      void withLocalInteractiveTravelMutationLock(petId, () => {
        const latest = readLocalInteractiveTravel(petId);
        if (latest) {
          adoptStoredSession(latest);
          return;
        }
        activeTravelIdRef.current = null;
        setSession(null);
        setVisibleBackgroundVideoUrl(null);
        setSuggestions([]);
        setIsLoadingSuggestions(true);
      }).catch(() => undefined);
    };
    window.addEventListener("storage", reconcileFromStorage);
    return () => window.removeEventListener("storage", reconcileFromStorage);
  }, [adoptStoredSession, automaticStoryToken, demoTravel, isOnboardingBatSession, petId]);

  useEffect(() => {
    if (
      automaticStoryToken
      || !isRestored
      || session
      || !pet
      || localPet.status !== "ready"
    ) {
      return;
    }
    const requestEpoch = ++requestEpochRef.current;
    setIsLoadingSuggestions(true);
    setError(null);
    void getInteractiveTravelSuggestions(pet, { includeDebug: canShowDebugMenu })
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
  }, [automaticStoryToken, canShowDebugMenu, isRestored, localPet.status, pet, session, suggestionsNonce]);

  const sessionTravelId = session?.travel.travelId;
  const sessionGenerationStatus = session?.travel.generationStatus;
  const sessionPlanReady = Boolean(session && isTravelPlanReady(session.travel));

  useEffect(() => {
    if (!sessionTravelId || demoTravel || sessionGenerationStatus !== "generating") {
      return;
    }
    const travelId = sessionTravelId;
    let cancelled = false;
    let timeoutId: number | null = null;

    const scheduleNext = (delay: number) => {
      timeoutId = window.setTimeout(() => void poll(), delay);
    };
    const poll = async () => {
      if (cancelled || generationStatusRequestRef.current === travelId) {
        return;
      }
      generationStatusRequestRef.current = travelId;
      try {
        const response = await getInteractiveTravelStatus(travelId);
        if (cancelled || activeTravelIdRef.current !== travelId) {
          return;
        }
        if (response.travel.generationStatus === "failed") {
          await withLocalInteractiveTravelMutationLock(petId, () => {
            const latest = readLocalInteractiveTravel(petId);
            if (latest?.travel.travelId !== travelId) {
              return;
            }
            clearLocalInteractiveTravel(petId);
            activeTravelIdRef.current = null;
            setSession(null);
            setSuggestions(shuffledFallbackDestinations());
            setIsLoadingSuggestions(false);
            setError({
              message: response.travel.generationError
                ?? "Не получилось подготовить путешествие. Попробуй ещё раз.",
            });
          });
          return;
        }
        await withLocalInteractiveTravelMutationLock(petId, ({ assertOwned }) => {
          const latest = readLocalInteractiveTravel(petId);
          if (!latest || latest.travel.travelId !== travelId) {
            return;
          }
          assertOwned();
          const stored = writeLocalInteractiveTravelDurably(petId, {
            ...latest,
            travel: response.travel,
          });
          if (!stored) {
            throw new Error("Interactive travel generation status was not stored durably");
          }
          setSession(stored);
        });
        if (!cancelled && response.travel.generationStatus === "generating") {
          scheduleNext(1_000);
        }
      } catch {
        if (!cancelled) {
          scheduleNext(2_000);
        }
      } finally {
        if (generationStatusRequestRef.current === travelId) {
          generationStatusRequestRef.current = null;
        }
      }
    };

    scheduleNext(0);
    return () => {
      cancelled = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
      if (generationStatusRequestRef.current === travelId) {
        generationStatusRequestRef.current = null;
      }
    };
  }, [demoTravel, petId, sessionGenerationStatus, sessionTravelId]);

  const ensureAnimation = useCallback(
    async (travelSession: LocalInteractiveTravel, part: InteractiveTravelPart) => {
      if (demoTravel || !isTravelPlanReady(travelSession.travel)) {
        return;
      }
      const travelId = travelSession.travel.travelId;
      if (!part.backgroundImageUrl || part.backgroundVideoUrl) {
        return;
      }
      if (activeTravelIdRef.current !== travelId) {
        return;
      }
      const key = illustrationKey(travelId, part.partNumber);
      if (animationRequestsRef.current.has(key) || failedAnimationsRef.current.has(key)) {
        return;
      }
      animationRequestsRef.current.add(key);
      try {
        for (let attempt = 0; attempt <= MEDIA_RETRY_DELAYS_MS.length; attempt += 1) {
          try {
            const response = await animateInteractiveTravelPart(travelSession.travel, part);
            if (activeTravelIdRef.current !== travelId) {
              return;
            }
            if (response.partNumber !== part.partNumber) {
              throw new Error("Animation part mismatch");
            }
            await patchLatestMedia(travelId, part.partNumber, {
              videoUrl: response.videoUrl,
            });
            return;
          } catch (error) {
            const retryDelay = MEDIA_RETRY_DELAYS_MS[attempt];
            if (
              retryDelay === undefined
              || !interactiveMediaErrorIsRetryable(error)
              || activeTravelIdRef.current !== travelId
            ) {
              break;
            }
            await waitForInteractiveMediaRetry(retryDelay);
          }
        }
        if (activeTravelIdRef.current === travelId) {
          failedAnimationsRef.current.add(key);
          setFailedAnimations((current) => [...new Set([...current, key])]);
        }
      } finally {
        animationRequestsRef.current.delete(key);
      }
    },
    [demoTravel, patchLatestMedia],
  );

  const ensureIllustration = useCallback(
    async (travelSession: LocalInteractiveTravel, part: InteractiveTravelPart) => {
      if (demoTravel || !isTravelPlanReady(travelSession.travel)) {
        return;
      }
      const travelId = travelSession.travel.travelId;
      if (!pet || part.backgroundImageUrl) {
        return;
      }
      if (activeTravelIdRef.current !== travelId) {
        return;
      }
      const key = illustrationKey(travelId, part.partNumber);
      if (
        illustrationRequestsRef.current.has(key) ||
        failedIllustrationsRef.current.has(key)
      ) {
        return;
      }
      illustrationRequestsRef.current.add(key);
      try {
        for (let attempt = 0; attempt <= MEDIA_RETRY_DELAYS_MS.length; attempt += 1) {
          try {
            const response = await illustrateInteractiveTravelPart(
              travelSession.travel,
              part,
              pet,
            );
            if (activeTravelIdRef.current !== travelId) {
              return;
            }
            if (response.partNumber !== part.partNumber) {
              throw new Error("Illustration part mismatch");
            }
            const patched = await patchLatestMedia(travelId, part.partNumber, {
              imageUrl: response.imageUrl,
            });
            const illustratedPart = patched
              ? partByNumber(patched.travel.parts, part.partNumber)
              : null;
            if (patched && illustratedPart) {
              void ensureAnimation(patched, illustratedPart);
            }
            return;
          } catch (error) {
            const retryDelay = MEDIA_RETRY_DELAYS_MS[attempt];
            if (
              retryDelay === undefined
              || !interactiveMediaErrorIsRetryable(error)
              || activeTravelIdRef.current !== travelId
            ) {
              break;
            }
            await waitForInteractiveMediaRetry(retryDelay);
          }
        }
        if (activeTravelIdRef.current === travelId) {
          failedIllustrationsRef.current.add(key);
          setFailedIllustrations((current) => [...new Set([...current, key])]);
        }
      } finally {
        illustrationRequestsRef.current.delete(key);
      }
    },
    [demoTravel, ensureAnimation, patchLatestMedia, pet],
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
      !activePart ||
      !isTravelPlanReady(session.travel) ||
      phase !== "departureWait"
    ) {
      return;
    }
    void ensureIllustration(session, activePart);
  }, [activePart, ensureIllustration, phase, session]);

  useEffect(() => {
    if (
      !session ||
      !isTravelPlanReady(session.travel) ||
      phase === "introReaction" ||
      !activePart?.backgroundImageUrl ||
      activePart.backgroundVideoUrl
    ) {
      return;
    }
    void ensureAnimation(session, activePart);
  }, [activePart, ensureAnimation, phase, session]);

  const introText =
    session?.travel.introReaction?.text ??
    fallbackIntroReaction(session?.travel.destination);
  const introPortions = splitInteractiveTravelText(introText);
  const presentationPortionIndex = session?.presentation.portionIndex ?? 0;
  const introCharacterTravelId =
    phase === "introReaction" ? session?.travel.travelId ?? null : null;
  const isFirstSessionConfirmation = Boolean(
    !session
    && firstSession?.stage === "confirming-travel"
    && firstSession.selectedDestination,
  );
  const showTravelCharacter = Boolean(
    characterImageSrc && (introCharacterTravelId || isFirstSessionConfirmation),
  );
  const isIntroCharacterEntranceComplete =
    introCharacterTravelId === null || !showTravelCharacter || isCharacterEntranceComplete;
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
    if (
      demoTravel
      || !sessionPlanReady
      || phase !== "departureWait"
      || !activeIllustrationKey
    ) {
      return;
    }
    setWaitElapsedKey(null);
    const timeoutId = window.setTimeout(() => {
      setWaitElapsedKey(activeIllustrationKey);
    }, MINIMUM_WAIT_DURATION_MS);
    return () => window.clearTimeout(timeoutId);
  }, [activeIllustrationKey, demoTravel, phase, sessionPlanReady]);

  useEffect(() => {
    if (
      demoTravel
      || !sessionPlanReady
      || !activePart
      || phase !== "departureWait"
    ) {
      return;
    }
    const timeoutId = window.setTimeout(() => {
      updatePresentation({
        phase: "story",
        partNumber: activePart.partNumber,
        portionIndex: 0,
      });
    }, MAXIMUM_DEPARTURE_WAIT_DURATION_MS);
    return () => window.clearTimeout(timeoutId);
  }, [activePart, demoTravel, phase, sessionPlanReady, updatePresentation]);

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
      const next = initialDemoSession(response.travel);
      activeTravelIdRef.current = response.travel.travelId;
      setDemoTravel(response.travel);
      setSession(next);
      setIsLoadingSuggestions(false);
      setVisibleBackgroundVideoUrl(null);
      setLoadedVideos([]);
      setReadyStoryVideos([]);
      setBrokenVideos([]);
      setFailedIllustrations([]);
      setFailedAnimations([]);
      setShowCustomDestination(false);
      setDestination("");
      hapticNotification("success");
    } catch (caught) {
      if (requestEpoch === requestEpochRef.current) {
        setIsLoadingSuggestions(false);
        setError(presentError(caught, "Не получилось запустить демо-историю."));
        hapticNotification("error");
      }
    } finally {
      if (requestEpoch === requestEpochRef.current) {
        submittingRef.current = false;
        setIsSubmitting(false);
      }
    }
  }

  useEffect(() => {
    if (
      demoAutoStartAttemptedRef.current
      || !canShowDebugMenu
      || !isRestored
      || new URLSearchParams(window.location.search).get("demo") !== "1"
    ) {
      return;
    }
    demoAutoStartAttemptedRef.current = true;
    void handleStartDemo();
  }, [canShowDebugMenu, isRestored]);

  function selectFirstSessionDestination(rawDestination: string) {
    const value = rawDestination.trim();
    if (!value) {
      setError({ message: "Напиши, куда отправить персонажа" });
      return;
    }
    if (!firstSession || !isLocalFirstSessionActive(firstSession)) {
      void beginTravel(value);
      return;
    }
    const next = updateLocalPetFirstSession(
      firstSession,
      "confirming-travel",
      value,
    );
    setFirstSession(next);
    setFirstSessionConfirmationPortion(0);
    setShowCustomDestination(false);
    setDestination("");
    setError(null);
    hapticNotification("success");
  }

  async function beginTravel(
    rawDestination: string,
    options: { completeFirstSession?: boolean } = {},
  ) {
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
      await withLocalInteractiveTravelMutationLock(petId, async ({ assertOwned }) => {
        if (requestEpoch !== requestEpochRef.current) {
          return;
        }
        const latest = readLocalInteractiveTravel(petId);
        if (latest) {
          adoptStoredSession(latest);
          setShowCustomDestination(false);
          setDestination("");
          return;
        }
        const response = await startInteractiveTravelWithRecovery(value, pet, {
          includeDebug: canShowDebugMenu,
        });
        assertOwned();
        if (requestEpoch !== requestEpochRef.current) {
          return;
        }
        const firstPart = response.travel.parts[0];
        const next: LocalInteractiveTravel = {
          travel: response.travel,
          appliedResultParts: [],
          presentation: {
            phase: options.completeFirstSession ? "departureWait" : "introReaction",
            partNumber: firstPart?.partNumber ?? 1,
            portionIndex: 0,
          },
        };
        setVisibleBackgroundVideoUrl(null);
        if (!persistSession(next)) {
          hapticNotification("error");
          return;
        }
        setShowCustomDestination(false);
        setDestination("");
        if (options.completeFirstSession && firstSession) {
          setFirstSession(updateLocalPetFirstSession(firstSession, "completed"));
        }
        hapticNotification("success");
      });
    } catch (caught) {
      if (requestEpoch !== requestEpochRef.current) {
        return;
      }
      setError(
        caught instanceof LocalInteractiveTravelMutationLockError
          ? MUTATION_ERROR
          : presentError(caught, "Не получилось начать путешествие. Попробуй ещё раз."),
      );
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
    if (isOnboardingBatSession) {
      const next = resolveOnboardingBatStory(session, value);
      if (value === ONBOARDING_BAT_CORRECT_CHOICE) {
        primeInteractiveTravelSuccessSound();
        const applied = localPet.applyInteractiveTravelImpacts(
          { travelId: next.travel.travelId, parts: next.travel.parts },
          pet.petId,
        );
        if (!applied) {
          setError(STORAGE_ERROR);
          hapticNotification("error");
          return;
        }
        setSession({ ...next, appliedResultParts: applied.appliedResultParts });
        void playInteractiveTravelSuccessSound();
        hapticNotification("success");
      } else {
        setSession(next);
        hapticNotification("warning");
      }
      setVisibleBackgroundVideoUrl(next.travel.parts[0]?.backgroundVideoUrl ?? null);
      return;
    }
    primeInteractiveTravelSuccessSound();
    if (automaticStoryToken) {
      submittingRef.current = true;
      const requestEpoch = ++requestEpochRef.current;
      setIsSubmitting(true);
      setError(null);
      try {
        const story = await chooseAutomaticInteractiveStory(automaticStoryToken, value);
        if (requestEpoch !== requestEpochRef.current) {
          return;
        }
        const next = automaticStorySession(story);
        const applied = localPet.applyInteractiveTravelImpacts(
          { travelId: next.travel.travelId, parts: next.travel.parts },
          pet.petId,
        );
        if (!applied) {
          setError(STORAGE_ERROR);
          hapticNotification("error");
          return;
        }
        activeTravelIdRef.current = next.travel.travelId;
        setSession(next);
        setVisibleBackgroundVideoUrl(next.travel.parts[0]?.backgroundVideoUrl ?? null);
        if (story.result?.outcomeValence === "positive") {
          void playInteractiveTravelSuccessSound();
        }
        hapticNotification("success");
      } catch (caught) {
        if (requestEpoch === requestEpochRef.current) {
          setError(presentError(caught, "Не получилось продолжить историю. Попробуй ещё раз."));
          hapticNotification("error");
        }
      } finally {
        if (requestEpoch === requestEpochRef.current) {
          submittingRef.current = false;
          setIsSubmitting(false);
        }
      }
      return;
    }
    if (demoTravel) {
      submittingRef.current = true;
      setIsSubmitting(true);
      setError(null);
      try {
        const next = continueDemoSession(session, demoTravel);
        setSession(next);
        const resolvedPart = next.travel.parts.find(
          (part) => part.partNumber === pendingPart.partNumber,
        );
        if (resolvedPart?.result?.outcomeValence === "positive") {
          void playInteractiveTravelSuccessSound();
        }
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
      await withLocalInteractiveTravelMutationLock(petId, async ({ assertOwned }) => {
        if (requestEpoch !== requestEpochRef.current) {
          return;
        }
        const latest = readLocalInteractiveTravel(petId);
        if (!latest) {
          activeTravelIdRef.current = null;
          setSession(null);
          return;
        }
        if (
          travelAdvancedPastPart(
            latest,
            session.travel.travelId,
            pendingPart.partNumber,
          )
        ) {
          adoptStoredSession(latest);
          return;
        }
        const latestPendingPart = currentPendingPart(latest.travel);
        if (!latestPendingPart || latestPendingPart.partNumber !== pendingPart.partNumber) {
          adoptStoredSession(latest);
          return;
        }
        const response = await continueInteractiveTravel(latest.travel, value, pet, {
          includeDebug: canShowDebugMenu,
        });
        assertOwned();
        if (requestEpoch !== requestEpochRef.current) {
          return;
        }
        const next: LocalInteractiveTravel = {
          travel: response.travel,
          appliedResultParts: latest.appliedResultParts,
          presentation: {
            phase: "result",
            partNumber: latestPendingPart.partNumber,
            portionIndex: 0,
          },
        };
        if (!persistSession(next)) {
          hapticNotification("error");
          return;
        }
        const resolvedPart = next.travel.parts.find(
          (part) => part.partNumber === latestPendingPart.partNumber,
        );
        if (resolvedPart?.result?.outcomeValence === "positive") {
          void playInteractiveTravelSuccessSound();
        }
        const nextPendingPart = currentPendingPart(next.travel);
        if (nextPendingPart) {
          void ensureIllustration(next, nextPendingPart);
        }
        hapticNotification("success");
      });
    } catch (caught) {
      if (requestEpoch !== requestEpochRef.current) {
        return;
      }
      setError(
        caught instanceof LocalInteractiveTravelMutationLockError
          ? MUTATION_ERROR
          : presentError(caught, "Не получилось продолжить историю. Попробуй ещё раз."),
      );
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
    activeTravelIdRef.current = null;
    submittingRef.current = false;
    illustrationRequestsRef.current.clear();
    failedIllustrationsRef.current.clear();
    animationRequestsRef.current.clear();
    failedAnimationsRef.current.clear();
    backgroundTransitionsRef.current.clear();
    clearLocalInteractiveTravel(petId);
    setSession(null);
    setSuggestions([]);
    setSuggestionsNonce((current) => current + 1);
    setIsLoadingSuggestions(true);
    setShowCustomDestination(false);
    setDestination("");
    setIsSubmitting(false);
    setError(null);
    setFailedIllustrations([]);
    setFailedAnimations([]);
    setLoadedVideos([]);
    setReadyStoryVideos([]);
    setWaitElapsedKey(null);
    setBrokenVideos([]);
    setVisibleBackgroundVideoUrl(null);
    setIsCharacterEntranceComplete(false);
  }

  async function handleNewTravel() {
    if (travelResetInFlightRef.current) {
      return;
    }
    if (demoTravel) {
      requestEpochRef.current += 1;
      activeTravelIdRef.current = null;
      setDemoTravel(null);
      setSession(null);
      setVisibleBackgroundVideoUrl(null);
      setIsRestored(false);
      setIsLoadingSuggestions(true);
      setError(null);
      return;
    }
    const visibleTravel = session?.travel;
    if (!visibleTravel) {
      resetLocalTravel();
      return;
    }

    travelResetInFlightRef.current = true;
    setIsResettingTravel(true);
    setError(null);
    try {
      await withLocalInteractiveTravelMutationLock(petId, async ({ assertOwned }) => {
        const latest = readLocalInteractiveTravel(petId);
        if (!latest) {
          resetLocalTravel();
          return;
        }
        const travel = latest.travel;
        const queued = travel.completed
          ? enqueueInteractiveTravelCapture(travel)
          : enqueueInteractiveTravelCancel(travel.travelId);
        if (!queued) {
          throw new Error("interactive travel operation was not persisted");
        }
        assertOwned();
        resetLocalTravel();
      });
    } catch (caught) {
      activeTravelIdRef.current = visibleTravel.travelId;
      setError(
        caught instanceof LocalInteractiveTravelMutationLockError
          ? MUTATION_ERROR
          : presentError(
              caught,
              "Не получилось безопасно завершить текущее путешествие. Попробуй ещё раз.",
            ),
      );
      hapticNotification("error");
    } finally {
      travelResetInFlightRef.current = false;
      setIsResettingTravel(false);
    }
  }

  async function handleDebugRestart() {
    if (travelResetInFlightRef.current) {
      return;
    }
    if (demoTravel) {
      const next = initialDemoSession(demoTravel);
      activeTravelIdRef.current = demoTravel.travelId;
      setSession(next);
      setVisibleBackgroundVideoUrl(null);
      setReadyStoryVideos([]);
      setError(null);
      setIsCharacterEntranceComplete(false);
      setExitingCharacterTravelId(null);
      hapticNotification("success");
      return;
    }
    const travelId = session?.travel.travelId;
    travelResetInFlightRef.current = true;
    requestEpochRef.current += 1;
    activeTravelIdRef.current = null;
    setIsResettingTravel(true);
    setError(null);
    try {
      await withLocalInteractiveTravelMutationLock(petId, async ({ assertOwned }) => {
        const latestTravelId = readLocalInteractiveTravel(petId)?.travel.travelId ?? travelId;
        if (latestTravelId) {
          await resetInteractiveTravelGeneration(latestTravelId);
        }
        assertOwned();
        resetLocalTravel();
      });
      hapticNotification("success");
    } catch (caught) {
      activeTravelIdRef.current = travelId ?? null;
      setError(
        caught instanceof LocalInteractiveTravelMutationLockError
          ? MUTATION_ERROR
          : presentError(caught, "Не получилось перезапустить путешествие."),
      );
      hapticNotification("error");
    } finally {
      travelResetInFlightRef.current = false;
      setIsResettingTravel(false);
    }
  }

  function handleNext() {
    if (!session || !activePart || phase !== "result") {
      return;
    }
    if (isOnboardingBatSession && !session.travel.completed) {
      const next = retryOnboardingBatStory(session);
      setSession(next);
      setVisibleBackgroundVideoUrl(next.travel.parts[0]?.backgroundVideoUrl ?? null);
      return;
    }
    if (session.travel.completed) {
      if (isOnboardingBatSession) {
        if (firstSession) {
          setFirstSession(updateLocalPetFirstSession(
            firstSession,
            "awaiting-completion-message",
          ));
        }
        router.replace(`/pet/${petId}`);
        return;
      }
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

  function handleSkipDepartureWait() {
    if (
      !activePart
      || (session && !isTravelPlanReady(session.travel))
      || phase !== "departureWait"
    ) {
      return;
    }
    updatePresentation({
      phase: "story",
      partNumber: activePart.partNumber,
      portionIndex: 0,
    });
  }

  function handleDestinationSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    selectFirstSessionDestination(destination);
  }

  const isLoading = localPet.status === "loading" || !isRestored;
  const isTravelLoading =
    isLoading || isLoadingSuggestions || isSubmitting || isResettingTravel;
  const portionIndex = session?.presentation.portionIndex ?? 0;
  const messageText = (() => {
    if (!session) {
      return "";
    }
    if (phase === "introReaction") {
      return introPortions[Math.min(portionIndex, introPortions.length - 1)] ?? introText;
    }
    if (phase === "departureWait") {
      if (session.travel.generationStatus === "generating") {
        return `${petName} уже отправился в путь. Готовлю первую часть приключения...`;
      }
      return (
        activePart?.transition?.departureHook ??
        (activePartNumber === 1
          ? `${petName} отправился в путь, дорога займет какое-то время...`
          : `${petName} продолжает путешествие. Дорога займет какое-то время...`)
      );
    }
    return "Я устал — на сегодня хватит. Отправляюсь домой.";
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
  const isStoryQuestionPhase = phase === "story" || phase === "choice";
  const isStoryLayout = isStoryQuestionPhase || phase === "result";
  const storyParagraphs = activePart ? storyAndChallengeParagraphs(activePart) : [];
  const resultParagraphs = activePart ? resolvedResultParagraphs(activePart) : [];
  const visibleStoryParagraphs = phase === "result" ? resultParagraphs : storyParagraphs;
  const resultImpact = phase === "result" ? activePart?.result?.statImpacts[0] : undefined;
  const resultImpactLabel = resultImpact?.stat === "happiness"
    ? "Настроение"
    : resultImpact?.stat === "energy"
      ? "Здоровье"
      : "Голод";
  const storyRevealKey = isStoryLayout && activePart
    ? `${session?.travel.travelId ?? "travel"}:${activePart.partNumber}:${
        phase === "result" ? "result" : "question"
      }`
    : null;
  const storyRevealCharacterCount = visibleStoryParagraphs.reduce(
    (total, paragraph) => total + Array.from(paragraph).length,
    0,
  );
  const storyRevealDuration = storyRevealCharacterCount === 0
    ? 0
    : STORY_CHARACTER_RISE_DURATION_MS
      + (storyRevealCharacterCount - 1) * STORY_CHARACTER_RISE_STAGGER_MS;
  const resultReaction = phase === "result" ? activePart?.result?.reaction ?? "" : "";
  const resultReactionCharacterCount = Array.from(resultReaction).length;
  const resultReactionDuration = resultReactionCharacterCount === 0
    ? 0
    : STORY_CHARACTER_RISE_DURATION_MS
      + (resultReactionCharacterCount - 1) * STORY_CHARACTER_RISE_STAGGER_MS;
  const resultOutcomeDelay = storyRevealDuration + resultReactionDuration;
  useEffect(() => {
    if (phase !== "result" || !session?.travel.completed) {
      setIsFinalActionReady(false);
      return;
    }

    setIsFinalActionReady(false);
    const timeoutId = window.setTimeout(() => {
      setIsFinalActionReady(true);
    }, resultOutcomeDelay);
    return () => window.clearTimeout(timeoutId);
  }, [phase, resultOutcomeDelay, session?.travel.completed, storyRevealKey]);
  const animatedStoryParagraphs = visibleStoryParagraphs.map((paragraph, index) => {
    const startIndex = visibleStoryParagraphs
      .slice(0, index)
      .reduce((total, item) => total + Array.from(item).length, 0);
    return (
      <AnimatedTravelParagraph
        key={`${storyRevealKey ?? "story"}:${index}`}
        animationKey={`${storyRevealKey ?? "story"}:${index}`}
        startIndex={startIndex}
        text={paragraph}
      />
    );
  });
  const storyVideoSrc = activePart?.backgroundVideoUrl
    && !brokenVideos.includes(activePart.backgroundVideoUrl)
    ? activePart.backgroundVideoUrl
    : null;
  const isStoryVideoReady = Boolean(
    storyVideoSrc && readyStoryVideos.includes(storyVideoSrc),
  );
  const firstSessionConfirmationText = firstSession?.selectedDestination
    ? firstSessionTravelConfirmation(firstSession.selectedDestination)
    : "";
  const firstSessionConfirmationPortions = splitInteractiveTravelText(
    firstSessionConfirmationText,
  );
  const firstSessionConfirmationTextPortion = firstSessionConfirmationPortions[
    Math.min(
      firstSessionConfirmationPortion,
      Math.max(0, firstSessionConfirmationPortions.length - 1),
    )
  ] ?? firstSessionConfirmationText;
  const isFirstSessionConfirmationComplete = firstSessionConfirmationPortion
    >= Math.max(0, firstSessionConfirmationPortions.length - 1);
  return (
    <main
      className={styles.viewport}
      aria-busy={isTravelLoading}
    >
      <section
        className={`${styles.scene} ${isStoryLayout ? styles.storyScene : ""}`}
        aria-label="Интерактивное путешествие"
      >
        {isStoryLayout ? (
          activePart?.backgroundImageUrl ? (
            <img
              src={activePart.backgroundImageUrl}
              alt=""
              className={styles.storyBackdrop}
              aria-hidden="true"
            />
          ) : null
        ) : (
          <>
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
          </>
        )}

        {canShowDebugMenu && !isTravelLoading ? (
          <div className={styles.debugMenu} aria-label="Debug путешествия">
            <span>Debug</span>
            <button type="button" onClick={toggleFirstSessionFlow}>
              <FlaskConical aria-hidden="true" />
              <span>Первая сессия: {firstSessionEnabled ? "вкл" : "выкл"}</span>
            </button>
            <button type="button" onClick={restartFirstSessionFlow}>
              <RotateCcw aria-hidden="true" />
              <span>Перезапустить первую сессию</span>
            </button>
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
        ) : isFirstSessionConfirmation && firstSession?.selectedDestination ? (
          <>
            <div className={`${styles.bubbleAnchor} ${styles.bubbleAnchorCenter}`}>
              <PetSpeechBubble
                isVisible
                message={{
                  id: 7000 + firstSessionConfirmationPortion,
                  text: firstSessionConfirmationTextPortion,
                  playSpeechAudio: false,
                  hasNextPortion: !isFirstSessionConfirmationComplete,
                }}
                scaleOrigin="bottom"
                shapeSrc={SPEECH_BUBBLE_SHAPE}
                maxLines={3}
                minFontSize={14}
              />
            </div>
            {!isFirstSessionConfirmationComplete ? (
              <button
                type="button"
                onClick={() => setFirstSessionConfirmationPortion((current) => current + 1)}
                className={`${styles.nextButton} ${styles.storyGlassButton}`}
              >
                Далее
              </button>
            ) : (
              <div className={styles.completionActions}>
                <button
                  type="button"
                  onClick={() => {
                    setFirstSession(updateLocalPetFirstSession(
                      firstSession,
                      "awaiting-travel",
                    ));
                    setFirstSessionConfirmationPortion(0);
                  }}
                  className={`${styles.completionButton} ${styles.storyGlassButton}`}
                >
                  Изменить
                </button>
                <button
                  type="button"
                  onClick={() => void beginTravel(firstSession.selectedDestination ?? "", {
                    completeFirstSession: true,
                  })}
                  className={styles.completionButton}
                  disabled={isSubmitting}
                >
                  Подтвердить
                </button>
              </div>
            )}
          </>
        ) : !session ? (
          showCustomDestination ? (
            <form className={styles.customForm} onSubmit={handleDestinationSubmit}>
              <h1 className="text-balance">Куда отправим {petName}?</h1>
              <label className="sr-only" htmlFor="travel-destination">
                Свой вариант путешествия
              </label>
              <input
                id="travel-destination"
                aria-describedby="travel-destination-requirement"
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
              <p id="travel-destination-requirement" className="sr-only">
                Напиши место, чтобы начать путешествие
              </p>
              <button
                type="submit"
                className={styles.primaryButton}
                disabled={isSubmitting || !destination.trim()}
              >
                Путешествие
              </button>
            </form>
          ) : (
            <div className={styles.destinationPicker}>
              <h1 className="text-balance">Куда отправим {petName}?</h1>
              <div className={styles.destinationOptions}>
                {suggestions.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    className={styles.optionButton}
                    disabled={isSubmitting}
                    onClick={() => selectFirstSessionDestination(suggestion)}
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
        ) : isStoryLayout && activePart ? (
          <div className={styles.storyContent}>
            <div className={styles.storyMediaFrame} aria-hidden="true">
              {activePart.backgroundImageUrl && !isStoryVideoReady ? (
                <img
                  src={activePart.backgroundImageUrl}
                  alt=""
                  className={styles.storyPoster}
                />
              ) : null}
              {storyVideoSrc ? (
                <SmoothBackgroundVideo
                  src={storyVideoSrc}
                  className={`${styles.storyVideo} ${
                    isStoryVideoReady ? "" : styles.storyVideoPending
                  }`}
                  onReady={(readySrc) => {
                    setLoadedVideos((current) => [...new Set([...current, readySrc])]);
                    setReadyStoryVideos((current) => [
                      ...new Set([...current, readySrc]),
                    ]);
                  }}
                  onError={(brokenSrc) => {
                    setBrokenVideos((current) => [...new Set([...current, brokenSrc])]);
                  }}
                />
              ) : null}
            </div>

            <div
              className={styles.storyTextFrame}
              aria-label={phase === "result" ? "Результат выбора" : "История и вопрос"}
            >
              {animatedStoryParagraphs}
              {phase === "result" && activePart.result ? (
                <div className={styles.storyResultMeta}>
                  <AnimatedTravelParagraph
                    animationKey={`${storyRevealKey ?? "story"}:reaction`}
                    baseDelayMs={storyRevealDuration}
                    className={styles.storyCharacterReaction}
                    startIndex={0}
                    text={activePart.result.reaction}
                  />
                  {activePart.result.outcomeValence === "positive" ? (
                    <div
                      className={styles.storyOutcomeAnimated}
                      style={{ animationDelay: `${resultOutcomeDelay}ms` }}
                    >
                      <XpAmount
                        text={`+${activePart.result.experienceGained ?? 0}`}
                        ariaLabel={`Получено ${activePart.result.experienceGained ?? 0} единиц опыта`}
                        className={`${styles.storyOutcomeDelta} ${styles.storyOutcomePositive}`}
                      />
                    </div>
                  ) : resultImpact ? (
                    <div
                      className={styles.storyOutcomeAnimated}
                      style={{ animationDelay: `${resultOutcomeDelay}ms` }}
                    >
                      <p className={`${styles.storyOutcomeDelta} ${styles.storyOutcomeNegative}`}>
                        −{Math.abs(resultImpact.amount)} {resultImpactLabel}
                      </p>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>

            {isStoryQuestionPhase ? (
              <div
                className={styles.storyAnswersScroll}
                role="group"
                aria-label="Варианты ответа"
              >
                <div className={styles.storyAnswersRow}>
                  {activePart.actionSuggestions.slice(0, 4).map((suggestion, index) => (
                    <TiltedGlassButton
                      key={suggestion}
                      index={index}
                      delayMs={index * 200}
                      disabled={isSubmitting}
                      onClick={() => void submitAdvice(suggestion)}
                    >
                      {suggestion}
                    </TiltedGlassButton>
                  ))}
                </div>
              </div>
            ) : (
              <div className={styles.storyResultActions}>
                {!session.travel.completed || isFinalActionReady ? (
                  <TiltedGlassButton
                    animate={session.travel.completed}
                    onClick={handleNext}
                    className={styles.storyResultButton}
                    disabled={isSubmitting}
                  >
                    {isOnboardingBatSession && !session.travel.completed
                      ? "Попробовать ещё раз"
                      : session.travel.completed ? "Завершить" : "Далее"}
                  </TiltedGlassButton>
                ) : null}
              </div>
            )}
          </div>
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
                  hasNextPortion: false,
                }}
                scaleOrigin="bottom"
                shapeSrc={SPEECH_BUBBLE_SHAPE}
                maxLines={3}
                minFontSize={14}
              />
            </div>

            {phase === "departureWait"
              && !demoTravel
              && isTravelPlanReady(session.travel) ? (
              <button
                type="button"
                onClick={handleSkipDepartureWait}
                className={`${styles.nextButton} ${styles.storyGlassButton}`}
              >
                Продолжить без видео
              </button>
            ) : null}

            {phase === "completed" ? (
              <div className={styles.completionActions}>
                <button type="button" onClick={goBack} className={styles.completionButton}>
                  К персонажу
                </button>
                {!isOnboardingBatSession ? (
                  <button
                    type="button"
                    onClick={() => void handleNewTravel()}
                    className={styles.completionButton}
                    disabled={isResettingTravel}
                  >
                    Новое путешествие
                  </button>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </section>
    </main>
  );
}
