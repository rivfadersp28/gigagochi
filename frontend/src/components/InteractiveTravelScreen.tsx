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
  continueInteractiveTravel,
  getInteractiveTravelSuggestions,
  illustrateInteractiveTravelPart,
} from "@/lib/api";
import { presentError, type PresentedError } from "@/lib/errorPresentation";
import {
  challengePortions,
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
} from "@/lib/pendingInteractiveTravelOperations";
import {
  getInteractiveTravelDemo,
  resetInteractiveTravelGeneration,
} from "@/lib/interactiveTravelDebugApi";
import { buildMemorySnapshotContext } from "@/lib/localPetMemoryRecall";
import { startInteractiveTravelWithRecovery } from "@/lib/interactiveTravelStartRecovery";
import { readLocalPetMemory } from "@/lib/localPetMemoryStorage";
import { readLocalChatHistory } from "@/lib/localPetStorage";
import {
  hapticNotification,
  setTelegramBackgroundColor,
  useTelegramBackButton,
} from "@/lib/telegram";
import type { InteractiveTravelPart, InteractiveTravelState } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";
import { APP_BACKGROUND_COLOR } from "@/lib/theme";
import { useTelegramCapabilities } from "@/lib/useTelegramCapabilities";

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
const MAXIMUM_DEPARTURE_WAIT_DURATION_MS = 90_000;
const MEDIA_PATCH_LOCK_ACQUIRE_TIMEOUT_MS = 5 * 60_000 + 10_000;
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

export function InteractiveTravelScreen({ petId }: InteractiveTravelScreenProps) {
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
  const travelResetInFlightRef = useRef(false);
  const demoAutoStartAttemptedRef = useRef(false);
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

  useTelegramBackButton(goBack);

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
    const wasOpen = wasCustomActionOpenRef.current;
    wasCustomActionOpenRef.current = showCustomAction;
    if (wasOpen && !showCustomAction) {
      customActionTriggerRef.current?.focus();
    }
  }, [showCustomAction]);

  useEffect(() => {
    requestEpochRef.current += 1;
    activeTravelIdRef.current = null;
    queuedFinaleTravelIdRef.current = null;
    submittingRef.current = false;
    illustrationRequestsRef.current.clear();
    failedIllustrationsRef.current.clear();
    animationRequestsRef.current.clear();
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
    setWaitElapsedKey(null);
    setBrokenVideos([]);
    setVisibleBackgroundVideoUrl(null);
    setIsCharacterEntranceComplete(false);
    setExitingCharacterTravelId(null);
    return () => {
      requestEpochRef.current += 1;
      activeTravelIdRef.current = null;
      submittingRef.current = false;
    };
  }, [petId]);

  useEffect(() => {
    if (isRestored || localPet.status !== "ready") {
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
  }, [isRestored, localPet, petId]);

  useEffect(() => {
    const travel = session?.travel;
    if (
      !demoTravel
      && travel?.completed
      && queuedFinaleTravelIdRef.current !== travel.travelId
      && enqueueInteractiveTravelCapture(travel)
    ) {
      queuedFinaleTravelIdRef.current = travel.travelId;
    }
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
      if (demoTravel) {
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
    [demoTravel, petId],
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
      if (demoTravel) {
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
  }, [adoptStoredSession, demoTravel, petId]);

  useEffect(() => {
    if (!isRestored || session || !pet || localPet.status !== "ready") {
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
  }, [canShowDebugMenu, isRestored, localPet.status, pet, session, suggestionsNonce]);

  const ensureAnimation = useCallback(
    async (travelSession: LocalInteractiveTravel, part: InteractiveTravelPart) => {
      if (demoTravel) {
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
      } catch {
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
      if (demoTravel) {
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
      } catch {
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
      phase !== "departureWait"
    ) {
      return;
    }
    void ensureIllustration(session, activePart);
  }, [activePart, ensureIllustration, phase, session]);

  useEffect(() => {
    if (
      !session ||
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
    if (demoTravel || !activePart || phase !== "departureWait") {
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
  }, [activePart, demoTravel, phase, updatePresentation]);

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
      setBrokenVideos([]);
      setFailedIllustrations([]);
      setFailedAnimations([]);
      setShowCustomDestination(false);
      setShowCustomAction(false);
      setDestination("");
      setAdvice("");
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
          history: readLocalChatHistory().messages,
          memoryContext: buildMemorySnapshotContext(readLocalPetMemory(pet.petId)),
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
            phase: "introReaction",
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
          setAdvice("");
          setShowCustomAction(false);
          return;
        }
        const latestPendingPart = currentPendingPart(latest.travel);
        if (!latestPendingPart || latestPendingPart.partNumber !== pendingPart.partNumber) {
          adoptStoredSession(latest);
          return;
        }
        const response = await continueInteractiveTravel(latest.travel, value, pet, {
          includeDebug: canShowDebugMenu,
          history: readLocalChatHistory().messages,
          memoryContext: buildMemorySnapshotContext(readLocalPetMemory(pet.petId)),
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
        const nextPendingPart = currentPendingPart(next.travel);
        if (nextPendingPart) {
          void ensureIllustration(next, nextPendingPart);
        }
        setAdvice("");
        setShowCustomAction(false);
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
    if (phase === "choice") {
      const portions = challengePortions(activePart);
      const nextIndex = session.presentation.portionIndex + 1;
      if (nextIndex < portions.length) {
        updatePresentation({
          phase: "choice",
          partNumber: activePart.partNumber,
          portionIndex: nextIndex,
        });
      }
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

  function handleSkipDepartureWait() {
    if (!activePart || phase !== "departureWait") {
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
  const challengeTextPortions = activePart ? challengePortions(activePart) : [];
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
      return challengeTextPortions[Math.min(portionIndex, challengeTextPortions.length - 1)]
        ?? challenge;
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
  const isChallengeComplete =
    phase === "choice" && portionIndex >= Math.max(0, challengeTextPortions.length - 1);
  const isChoices = isChallengeComplete && !showCustomAction;
  const isChallengeContext = phase === "choice" && !isChallengeComplete;
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

            {(session && !session.travel.completed) || isSubmitting ? (
              <button
                type="button"
                onClick={() => void handleNewTravel()}
                className={styles.newStoryButton}
                disabled={isResettingTravel}
              >
                <RotateCcw aria-hidden="true" />
                <span>Создать новую историю</span>
              </button>
            ) : null}
          </>
        ) : null}

        {canShowDebugMenu && !isTravelLoading ? (
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
            <h1 className="text-balance">Что подсказать {petName}?</h1>
            <label className="sr-only" htmlFor="travel-advice">
              Свой вариант действия
            </label>
            <input
              id="travel-advice"
              aria-describedby="travel-advice-requirement"
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
            <p id="travel-advice-requirement" className="sr-only">
              Напиши совет, чтобы подсказать действие
            </p>
            <button
              type="submit"
              className={styles.primaryButton}
              disabled={isSubmitting || !advice.trim()}
            >
              Подсказать
            </button>
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
                    (phase === "choice" && portionIndex < challengeTextPortions.length - 1) ||
                    (phase === "result" && portionIndex < resultTextPortions.length - 1),
                }}
                scaleOrigin="bottom"
                shapeSrc={SPEECH_BUBBLE_SHAPE}
                maxLines={3}
                minFontSize={14}
              />
            </div>

            {isNarrative || isChallengeContext ? (
              <button
                type="button"
                onClick={handleNext}
                className={`${styles.nextButton} ${styles.storyGlassButton}`}
              >
                Далее
              </button>
            ) : null}

            {phase === "departureWait" && !demoTravel ? (
              <button
                type="button"
                onClick={handleSkipDepartureWait}
                className={`${styles.nextButton} ${styles.storyGlassButton}`}
              >
                Продолжить без видео
              </button>
            ) : null}

            {isChoices && activePart ? (
              <div
                className={styles.actionsScroll}
                role="group"
                aria-label="Варианты действий"
              >
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
              <div className={styles.completionActions}>
                <button type="button" onClick={goBack} className={styles.completionButton}>
                  К персонажу
                </button>
                <button
                  type="button"
                  onClick={() => void handleNewTravel()}
                  className={styles.completionButton}
                  disabled={isResettingTravel}
                >
                  Новое путешествие
                </button>
              </div>
            ) : null}
          </>
        )}
      </section>
    </main>
  );
}
