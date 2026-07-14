"use client";

/* eslint-disable @next/next/no-img-element */
/* eslint-disable react-hooks/set-state-in-effect -- effects drive the persisted travel state machine */
import { ArrowLeft, Loader2, RotateCcw } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  type CSSProperties,
  type FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  animateInteractiveTravelPart,
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
import { buildMemorySnapshotContext } from "@/lib/localPetMemoryRecall";
import { readLocalPetMemory } from "@/lib/localPetMemoryStorage";
import { readLocalChatHistory } from "@/lib/localPetStorage";
import {
  canUseDebugMenu,
  hapticNotification,
  setTelegramBackgroundColor,
  useTelegramBackButton,
} from "@/lib/telegram";
import type { InteractiveTravelPart } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";

import { ErrorNotice } from "./ErrorNotice";
import { PetSpeechBubble } from "./pet-dashboard/PetSpeechBubble";
import styles from "./InteractiveTravelScreen.module.css";

const DESTINATION_MAX_LENGTH = 500;
const ADVICE_MAX_LENGTH = 1000;
const INTRO_PORTION_DURATION_MS = 3_200;
const MINIMUM_WAIT_DURATION_MS = 1_600;
const FALLBACK_BACKGROUND = "/figma/travel-entry-bg.png";
const ENTRY_BACKGROUND_VIDEO = "/figma/travel-entry-bg.mp4?ping_pong_v=20260714-1";
const SPEECH_BUBBLE_SHAPE = "/figma/speech-bubble-new.svg";
const VIDEO_FILTER = "/figma/video-filter-normal.webp?v=20260713-video-filter-lossless-webp-1";
const GLASS_CONTROL_BACKDROP_STYLE: CSSProperties = {
  backdropFilter: "blur(10px)",
  WebkitBackdropFilter: "blur(10px)",
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
};

function shuffledFallbackDestinations() {
  return [...FALLBACK_DESTINATIONS]
    .sort(() => Math.random() - 0.5)
    .slice(0, 3);
}

function illustrationKey(travelId: string, partNumber: number) {
  return `${travelId}:${partNumber}`;
}

function partByNumber(parts: InteractiveTravelPart[], partNumber: number) {
  return parts.find((part) => part.partNumber === partNumber) ?? null;
}

export function InteractiveTravelScreen({ petId }: InteractiveTravelScreenProps) {
  const router = useRouter();
  const localPet = useLocalPetState();
  const [session, setSession] = useState<LocalInteractiveTravel | null>(null);
  const [isRestored, setIsRestored] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [suggestionsNonce, setSuggestionsNonce] = useState(0);
  const [isLoadingSuggestions, setIsLoadingSuggestions] = useState(false);
  const [showCustomDestination, setShowCustomDestination] = useState(false);
  const [showCustomAction, setShowCustomAction] = useState(false);
  const [destination, setDestination] = useState("");
  const [advice, setAdvice] = useState("");
  const [selectedDestination, setSelectedDestination] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<PresentedError | null>(null);
  const [failedIllustrations, setFailedIllustrations] = useState<string[]>([]);
  const [illustratingKeys, setIllustratingKeys] = useState<string[]>([]);
  const [loadedBackgrounds, setLoadedBackgrounds] = useState<string[]>([]);
  const [waitElapsedKey, setWaitElapsedKey] = useState<string | null>(null);
  const [brokenBackgrounds, setBrokenBackgrounds] = useState<string[]>([]);
  const [brokenVideos, setBrokenVideos] = useState<string[]>([]);
  const [characterImageSrc, setCharacterImageSrc] = useState<string | null>(null);
  const submittingRef = useRef(false);
  const requestEpochRef = useRef(0);
  const illustrationRequestsRef = useRef(new Set<string>());
  const failedIllustrationsRef = useRef(new Set<string>());
  const animationRequestsRef = useRef(new Set<string>());
  const failedAnimationsRef = useRef(new Set<string>());
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

  useTelegramBackButton(goBack, true);

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
      setSession(readLocalInteractiveTravel(petId));
      setIsRestored(true);
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [petId]);

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
    setCharacterImageSrc(interactiveTravelCharacterImageUrl(pet));
  }, [pet]);

  const persistSession = useCallback(
    (next: LocalInteractiveTravel) => {
      writeLocalInteractiveTravel(petId, next);
      setSession(next);
    },
    [petId],
  );

  const applyPartImpacts = useCallback(
    (next: LocalInteractiveTravel) => {
      if (!pet) {
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
    [localPet, pet],
  );

  const updatePresentation = useCallback(
    (presentation: InteractiveTravelPresentation) => {
      setSession((current) => {
        if (!current) {
          return current;
        }
        const next = { ...current, presentation };
        writeLocalInteractiveTravel(petId, next);
        return next;
      });
    },
    [petId],
  );

  useEffect(() => {
    if (!isRestored || session || !pet || localPet.status !== "ready") {
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
  }, [isRestored, localPet.status, pet, session, suggestionsNonce]);

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
      setIllustratingKeys((current) => [...new Set([...current, key])]);
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
        setIllustratingKeys((current) => current.filter((candidate) => candidate !== key));
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
      !activePart ||
      (phase !== "introReaction" && phase !== "departureWait")
    ) {
      return;
    }
    void ensureIllustration(session, activePart);
  }, [activePart, ensureIllustration, phase, session]);

  useEffect(() => {
    if (!session || !activePart?.backgroundImageUrl || activePart.backgroundVideoUrl) {
      return;
    }
    void ensureAnimation(session, activePart);
  }, [activePart, ensureAnimation, session]);

  const introText =
    session?.travel.introReaction?.text ??
    `Вот это маршрут! Отправляюсь в ${session?.travel.destination ?? "путешествие"}.`;
  const introPortions = splitInteractiveTravelText(introText);
  const presentationPortionIndex = session?.presentation.portionIndex ?? 0;

  useEffect(() => {
    if (phase !== "introReaction") {
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
      updatePresentation({
        phase: "departureWait",
        partNumber: activePartNumber,
        portionIndex: 0,
      });
    }, INTRO_PORTION_DURATION_MS);
    return () => window.clearTimeout(timeoutId);
  }, [
    activePartNumber,
    introPortions.length,
    phase,
    presentationPortionIndex,
    updatePresentation,
  ]);

  useEffect(() => {
    if (phase !== "departureWait" || !activeIllustrationKey) {
      return;
    }
    setWaitElapsedKey(null);
    const timeoutId = window.setTimeout(() => {
      setWaitElapsedKey(activeIllustrationKey);
    }, MINIMUM_WAIT_DURATION_MS);
    return () => window.clearTimeout(timeoutId);
  }, [activeIllustrationKey, phase]);

  useEffect(() => {
    if (
      !session ||
      !activePart ||
      phase !== "departureWait" ||
      waitElapsedKey !== activeIllustrationKey
    ) {
      return;
    }
    const illustrationReady =
      Boolean(
        activePart.backgroundImageUrl &&
          loadedBackgrounds.includes(activePart.backgroundImageUrl),
      ) ||
      (activeIllustrationKey ? failedIllustrations.includes(activeIllustrationKey) : false);
    if (illustrationReady) {
      updatePresentation({
        phase: "story",
        partNumber: activePart.partNumber,
        portionIndex: 0,
      });
    }
  }, [
    activeIllustrationKey,
    activePart,
    failedIllustrations,
    loadedBackgrounds,
    phase,
    session,
    updatePresentation,
    waitElapsedKey,
  ]);

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
    setSelectedDestination(value);
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
        setSelectedDestination(null);
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

  function handleNewTravel() {
    requestEpochRef.current += 1;
    submittingRef.current = false;
    illustrationRequestsRef.current.clear();
    failedIllustrationsRef.current.clear();
    clearLocalInteractiveTravel(petId);
    setSession(null);
    setSuggestions([]);
    setSuggestionsNonce((current) => current + 1);
    setShowCustomDestination(false);
    setShowCustomAction(false);
    setDestination("");
    setAdvice("");
    setSelectedDestination(null);
    setIsSubmitting(false);
    setError(null);
    setFailedIllustrations([]);
    setIllustratingKeys([]);
    setLoadedBackgrounds([]);
    setWaitElapsedKey(null);
    setBrokenBackgrounds([]);
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
  const showGeneratedVideo = Boolean(
    activePart?.backgroundVideoUrl &&
      !brokenVideos.includes(activePart.backgroundVideoUrl),
  );
  const showGeneratedScene =
    (showGeneratedVideo ||
      (Boolean(activePart?.backgroundImageUrl) &&
        !brokenBackgrounds.includes(activePart?.backgroundImageUrl ?? ""))) &&
    phase !== "introReaction" &&
    phase !== "departureWait";
  const isChoices = phase === "choice" && !showCustomAction;
  const isNarrative = phase === "story" || phase === "result";
  const isWaitingForIllustration =
    Boolean(activeIllustrationKey) && illustratingKeys.includes(activeIllustrationKey ?? "");

  return (
    <main className={styles.viewport} aria-busy={isLoading || isSubmitting}>
      <section className={styles.scene} aria-label="Интерактивное путешествие">
        {!session ? (
          <video
            src={ENTRY_BACKGROUND_VIDEO}
            poster={FALLBACK_BACKGROUND}
            className={`${styles.background} ${styles.entryBackgroundVideo}`}
            autoPlay
            loop
            muted
            playsInline
            preload="auto"
            aria-hidden="true"
          />
        ) : (
          <img src={FALLBACK_BACKGROUND} alt="" className={styles.background} />
        )}
        {showGeneratedScene && showGeneratedVideo && activePart?.backgroundVideoUrl ? (
          <video
            src={activePart.backgroundVideoUrl}
            poster={activePart.backgroundImageUrl}
            className={`${styles.background} ${styles.generatedBackground} ${
              isChoices ? styles.sharpBackground : ""
            }`}
            autoPlay
            loop
            muted
            playsInline
            preload="auto"
            aria-hidden="true"
            onError={() =>
              setBrokenVideos((current) => [
                ...new Set([...current, activePart.backgroundVideoUrl ?? ""]),
              ])
            }
          />
        ) : showGeneratedScene && activePart?.backgroundImageUrl ? (
          <img
            src={activePart.backgroundImageUrl}
            alt=""
            className={`${styles.background} ${styles.generatedBackground} ${
              isChoices ? styles.sharpBackground : ""
            }`}
            onError={() =>
              setBrokenBackgrounds((current) => [
                ...new Set([...current, activePart.backgroundImageUrl ?? ""]),
              ])
            }
          />
        ) : null}
        {(phase === "introReaction" || phase === "departureWait") &&
        activePart?.backgroundImageUrl ? (
          <img
            src={activePart.backgroundImageUrl}
            alt=""
            aria-hidden="true"
            className={styles.backgroundPreloader}
            onLoad={() =>
              setLoadedBackgrounds((current) => [
                ...new Set([...current, activePart.backgroundImageUrl ?? ""]),
              ])
            }
            onError={() => {
              const imageUrl = activePart.backgroundImageUrl ?? "";
              setBrokenBackgrounds((current) => [...new Set([...current, imageUrl])]);
              if (activeIllustrationKey) {
                failedIllustrationsRef.current.add(activeIllustrationKey);
                setFailedIllustrations((current) => [
                  ...new Set([...current, activeIllustrationKey]),
                ]);
              }
            }}
          />
        ) : null}
        <img src={VIDEO_FILTER} alt="" className={styles.grain} aria-hidden="true" />
        <div className={styles.scrim} />

        <button type="button" onClick={goBack} className={styles.backButton}>
          <ArrowLeft aria-hidden="true" />
          <span className="sr-only">
            {showCustomDestination || showCustomAction
              ? "Вернуться к вариантам"
              : "Вернуться к персонажу"}
          </span>
        </button>

        {(session || isSubmitting) ? (
          <button type="button" onClick={handleNewTravel} className={styles.newStoryButton}>
            <RotateCcw aria-hidden="true" />
            <span>Создать новую историю</span>
          </button>
        ) : null}

        {error ? (
          <ErrorNotice id="interactive-travel-error" error={error} className={styles.error} />
        ) : null}

        {isLoading ? (
          <div className={styles.centerLoader}>
            <Loader2 aria-hidden="true" />
            <span>Готовим дорогу…</span>
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
                style={GLASS_CONTROL_BACKDROP_STYLE}
                maxLength={DESTINATION_MAX_LENGTH}
                placeholder="Свой вариант"
                disabled={isSubmitting}
                autoFocus
                enterKeyHint="go"
              />
              {destination.trim() || isSubmitting ? (
                <button type="submit" className={styles.primaryButton} disabled={isSubmitting}>
                  {isSubmitting ? (
                    <>
                      <Loader2 className={styles.spinner} aria-hidden="true" />
                      <span className="sr-only">Путешествие</span>
                    </>
                  ) : (
                    "Путешествие"
                  )}
                </button>
              ) : null}
            </form>
          ) : (
            <div className={styles.destinationPicker}>
              <h1>Куда отправим {petName}?</h1>
              <div className={styles.destinationOptions}>
                {isLoadingSuggestions ? (
                  <Loader2 className={styles.optionLoader} aria-label="Генерируем варианты" />
                ) : (
                  suggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      className={styles.optionButton}
                      disabled={isSubmitting}
                      onClick={() => void beginTravel(suggestion)}
                    >
                      {isSubmitting && selectedDestination === suggestion ? (
                        <>
                          <Loader2 className={styles.spinner} aria-hidden="true" />
                          <span className="sr-only">{suggestion}</span>
                        </>
                      ) : (
                        suggestion
                      )}
                    </button>
                  ))
                )}
                {!isLoadingSuggestions ? (
                  <button
                    ref={customDestinationTriggerRef}
                    type="button"
                    className={`${styles.optionButton} ${styles.secondaryButton}`}
                    style={GLASS_CONTROL_BACKDROP_STYLE}
                    onClick={() => {
                      setShowCustomDestination(true);
                      setError(null);
                    }}
                    disabled={isSubmitting}
                  >
                    Свой вариант
                  </button>
                ) : null}
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
              style={GLASS_CONTROL_BACKDROP_STYLE}
              maxLength={ADVICE_MAX_LENGTH}
              placeholder="Свой вариант"
              disabled={isSubmitting}
              autoFocus
              enterKeyHint="send"
            />
            {advice.trim() || isSubmitting ? (
              <button type="submit" className={styles.primaryButton} disabled={isSubmitting}>
                {isSubmitting ? (
                  <>
                    <Loader2 className={styles.spinner} aria-hidden="true" />
                    <span className="sr-only">Подсказать</span>
                  </>
                ) : (
                  "Подсказать"
                )}
              </button>
            ) : null}
          </form>
        ) : (
          <>
            {phase === "result" && activePart?.answer ? (
              <p
                className={styles.answerCaption}
                role="region"
                aria-label="Твой ответ"
                tabIndex={0}
              >
                <span aria-hidden="true">Твой ответ</span>
                {activePart.answer}
              </p>
            ) : null}

            <div
              className={`${styles.bubbleAnchor} ${
                phase === "introReaction" || phase === "departureWait"
                  ? styles.bubbleAnchorCenter
                  : styles.bubbleAnchorBottom
              } ${phase === "departureWait" ? styles.mutedBubble : ""}`}
            >
              <PetSpeechBubble
                isVisible
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

            {phase === "introReaction" && characterImageSrc ? (
              <img
                src={characterImageSrc}
                alt={petName}
                className={`${styles.character} ${
                  characterImageSrc === "/test-pet/character-transparent.png"
                    ? styles.transparentCharacter
                    : styles.generatedCharacter
                }`}
                onError={() => setCharacterImageSrc(null)}
              />
            ) : null}

            {isNarrative ? (
              <button
                type="button"
                onClick={handleNext}
                className={`${styles.nextButton} ${styles.storyGlassButton}`}
                style={GLASS_CONTROL_BACKDROP_STYLE}
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
                    style={GLASS_CONTROL_BACKDROP_STYLE}
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

            {phase === "departureWait" && isWaitingForIllustration ? (
              <Loader2 className={styles.waitSpinner} aria-hidden="true" />
            ) : null}

            {isSubmitting ? (
              <div className={styles.submittingOverlay}>
                <Loader2 className={styles.spinner} aria-hidden="true" />
              </div>
            ) : null}
          </>
        )}

      </section>
    </main>
  );
}
