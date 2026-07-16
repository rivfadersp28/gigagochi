"use client";

import { Bug } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { ApiError, generatePetAssets, resumePetGeneration } from "@/lib/api";
import { presentError, type PresentedError } from "@/lib/errorPresentation";
import { readLocalPetState } from "@/lib/localPetStorage";
import {
  adoptPendingPetGenerationJob,
  clearPendingPetGeneration,
  pendingPetGenerationForDescription,
  readPendingPetGeneration,
  setPendingPetGenerationJobId,
  type PendingPetGeneration,
  writePendingPetGeneration,
} from "@/lib/pendingPetGeneration";
import { hapticNotification } from "@/lib/telegram";
import { TEST_PET_ASSET_SET, TEST_PET_DESCRIPTION } from "@/lib/testPetFixture";
import { useLocalPetState } from "@/lib/useLocalPetState";
import { useTelegramCapabilities } from "@/lib/useTelegramCapabilities";
import { cn } from "@/lib/utils";

import styles from "./CreatePetForm.module.css";
import { ErrorNotice } from "./ErrorNotice";
import { PetThinkingIndicator } from "./pet-dashboard/PetThinkingIndicator";
import { TiltedGlassButton } from "./TiltedGlassButton";

const MAX_PROMPT_LENGTH = 300;
const subscribeToHydration = () => () => {};
const DESCRIPTION_PRESETS = ["Ледяного дракона", "Человек-яблоко", "Водяной дух"];
const CREATION_QUESTIONS = [
  {
    title: "Как его будут звать?",
    options: ["Тото", "Бачок", "Денис"],
  },
  {
    title: "Какой у него характер?",
    options: ["Добрый", "Злой", "Ленивый"],
  },
  {
    title: "Чего он боится?",
    options: ["Пауков", "Бизнесменов", "Людоедов"],
  },
  {
    title: "Какой у него любимый предмет?",
    options: ["Вантуз", "Рулон бумаги", "Кока кола"],
  },
] as const;
const FINAL_FLOW_STEP = CREATION_QUESTIONS.length + 1;
const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";
const CREATION_BACKGROUND_VIDEO = "/creation/clouds-creation-timeline.mp4";
const CREATION_BACKGROUND_MUSIC = "/creation/hopeful-piano-loop.m4a";
const CREATION_BACKGROUND_MUSIC_VOLUME = 0.32;
const CREATION_BUTTON_SOUND = "/sounds/creation-button-plop.wav";
const INITIAL_LOOP_END_SECONDS = 170 / 24;
const FORMED_LOOP_START_SECONDS = 267 / 24;
const FORMED_LOOP_END_SECONDS = 447 / 24;
const VIDEO_FRAME_EPSILON_SECONDS = 1 / 48;
type CreationBackgroundPhase = "initial" | "transition" | "formed";
const PENDING_STORAGE_ERROR: PresentedError = {
  message: (
    "Не удалось сохранить прогресс. Освободите место на устройстве или разрешите "
    + "приложению хранить данные, затем попробуйте снова."
  ),
};

class PendingGenerationPersistenceError extends Error {
  constructor() {
    super(PENDING_STORAGE_ERROR.message);
    this.name = "PendingGenerationPersistenceError";
  }
}

function requestIsStillCurrent(requestKey: string): boolean {
  return Boolean(readPendingPetGeneration(requestKey));
}

type PendingGenerationResult = {
  assetSet: Awaited<ReturnType<typeof generatePetAssets>>;
  effectiveDescription: string;
};

type ReadyGeneration = PendingGenerationResult & {
  requestKey: string;
};

function validatedActiveDescription(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const description = value.trim();
  return description && description.length <= MAX_PROMPT_LENGTH ? description : undefined;
}

async function runPendingGeneration(
  pending: PendingPetGeneration,
  signal: AbortSignal,
): Promise<PendingGenerationResult> {
  try {
    const assetSet = pending.jobId
      ? await resumePetGeneration(pending.jobId, { signal })
      : await generatePetAssets(pending.description, {
          requestKey: pending.requestKey,
          signal,
          onJobQueued: (jobId) => {
            setPendingPetGenerationJobId(pending.requestKey, jobId);
          },
        });
    return { assetSet, effectiveDescription: pending.description };
  } catch (caught) {
    const activeJobId = caught instanceof ApiError
      && caught.code === "GENERATION_ALREADY_ACTIVE"
      ? caught.activeJobId
      : undefined;
    const effectiveDescription = caught instanceof ApiError
      ? validatedActiveDescription(caught.activeDescription)
      : undefined;
    if (!activeJobId || !effectiveDescription) {
      throw caught;
    }
    const adoptionPersisted = adoptPendingPetGenerationJob(
      pending.requestKey,
      activeJobId,
      effectiveDescription,
    );
    if (!adoptionPersisted) {
      throw new PendingGenerationPersistenceError();
    }
    const adopted = readPendingPetGeneration(pending.requestKey);
    if (
      adopted?.jobId !== activeJobId
      || adopted.description !== effectiveDescription
    ) {
      throw caught;
    }
    // One bounded recovery hop: never repeat the paid POST. A failure from this
    // GET/poll propagates to the caller and is retried only after reload/action.
    const assetSet = await resumePetGeneration(activeJobId, { signal });
    return { assetSet, effectiveDescription };
  }
}

type CreatePetFormProps = {
  redirectExistingPet?: boolean;
};

export function CreatePetForm({ redirectExistingPet = true }: CreatePetFormProps) {
  const router = useRouter();
  const localPet = useLocalPetState();
  const createLocalPetDurably = localPet.createDurably;
  const currentPet = localPet.pet;
  const localPetStatus = localPet.status;
  const [description, setDescription] = useState("");
  const [flowStep, setFlowStep] = useState(0);
  const [customInputStep, setCustomInputStep] = useState<number | null>(null);
  const [customValue, setCustomValue] = useState("");
  const [error, setError] = useState<PresentedError | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [generationResult, setGenerationResult] = useState<ReadyGeneration | null>(null);
  const isMounted = useSyncExternalStore(
    subscribeToHydration,
    () => true,
    () => false,
  );
  const [backgroundPhase, setBackgroundPhase] = useState<CreationBackgroundPhase>("initial");
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false);
  const isSubmittingRef = useRef(false);
  const resumeAttemptedRef = useRef(false);
  const finalizeAttemptedRef = useRef(false);
  const activeGenerationAbortRef = useRef<AbortController | null>(null);
  const backgroundVideoRef = useRef<HTMLVideoElement | null>(null);
  const backgroundMusicRef = useRef<HTMLAudioElement | null>(null);
  const canShowDebugMenu = useTelegramCapabilities().debugMenu;
  const activeQuestion = flowStep > 0 && flowStep < FINAL_FLOW_STEP
    ? CREATION_QUESTIONS[flowStep - 1]
    : null;
  const isCustomInputOpen = customInputStep === flowStep;

  useEffect(() => {
    if (backgroundMusicRef.current) {
      backgroundMusicRef.current.volume = CREATION_BACKGROUND_MUSIC_VOLUME;
    }
  }, []);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const query = window.matchMedia(REDUCED_MOTION_QUERY);
    const handleChange = () => {
      const matches = query.matches;
      setPrefersReducedMotion(matches);
      if (matches) {
        setBackgroundPhase((phase) => phase === "transition" ? "formed" : phase);
      }
    };
    handleChange();
    query.addEventListener("change", handleChange);
    return () => query.removeEventListener("change", handleChange);
  }, []);

  useEffect(() => {
    const video = backgroundVideoRef.current;
    if (!video) {
      return;
    }

    const seekToPhase = () => {
      if (backgroundPhase === "transition") {
        video.currentTime = INITIAL_LOOP_END_SECONDS;
      } else if (
        backgroundPhase === "formed"
        && (
          video.currentTime < FORMED_LOOP_START_SECONDS
          || video.currentTime >= FORMED_LOOP_END_SECONDS
        )
      ) {
        video.currentTime = FORMED_LOOP_START_SECONDS;
      }
    };

    if (video.readyState >= HTMLMediaElement.HAVE_METADATA) {
      seekToPhase();
    }
    if (prefersReducedMotion) {
      video.pause();
      return;
    }
    if (typeof video.requestVideoFrameCallback !== "function") {
      return;
    }

    let active = true;
    let frameCallbackId = 0;
    const monitorTimeline: VideoFrameRequestCallback = (_now, metadata) => {
      if (!active) {
        return;
      }
      const mediaTime = metadata.mediaTime;
      if (
        backgroundPhase === "initial"
        && mediaTime >= INITIAL_LOOP_END_SECONDS - VIDEO_FRAME_EPSILON_SECONDS
      ) {
        video.currentTime = 0;
      } else if (
        backgroundPhase === "transition"
        && mediaTime >= FORMED_LOOP_START_SECONDS - VIDEO_FRAME_EPSILON_SECONDS
      ) {
        setBackgroundPhase("formed");
      } else if (
        backgroundPhase === "formed"
        && mediaTime >= FORMED_LOOP_END_SECONDS - VIDEO_FRAME_EPSILON_SECONDS
      ) {
        video.currentTime = FORMED_LOOP_START_SECONDS;
      }
      frameCallbackId = video.requestVideoFrameCallback(monitorTimeline);
    };
    frameCallbackId = video.requestVideoFrameCallback(monitorTimeline);
    return () => {
      active = false;
      video.cancelVideoFrameCallback(frameCallbackId);
    };
  }, [backgroundPhase, prefersReducedMotion]);

  const startPendingGeneration = useCallback(async (pending: PendingPetGeneration) => {
    isSubmittingRef.current = true;
    setIsSubmitting(true);
    setGenerationResult(null);
    setError(null);

    const controller = new AbortController();
    activeGenerationAbortRef.current?.abort();
    activeGenerationAbortRef.current = controller;

    try {
      const result = await runPendingGeneration(pending, controller.signal);
      if (!requestIsStillCurrent(pending.requestKey)) {
        return;
      }
      setGenerationResult({ ...result, requestKey: pending.requestKey });
    } catch (caught) {
      if (controller.signal.aborted) {
        return;
      }
      const latestPending = readPendingPetGeneration(pending.requestKey);
      if (latestPending) {
        setDescription(latestPending.description);
      }
      if (
        caught instanceof ApiError
        && (caught.code === "GENERATION_JOB_NOT_FOUND" || caught.generationTerminal)
      ) {
        clearPendingPetGeneration(pending.requestKey);
      }
      setError(
        caught instanceof PendingGenerationPersistenceError
          ? PENDING_STORAGE_ERROR
          : presentError(caught, "Не получилось создать питомца. Попробуйте ещё раз."),
      );
    } finally {
      if (activeGenerationAbortRef.current === controller) {
        activeGenerationAbortRef.current = null;
      }
      isSubmittingRef.current = false;
      setIsSubmitting(false);
    }
  }, []);

  useEffect(() => {
    if (redirectExistingPet && localPet.status === "ready" && localPet.pet) {
      clearPendingPetGeneration();
      router.replace(`/pet/${localPet.pet.petId}`);
      return;
    }
  }, [localPet.pet, localPet.status, redirectExistingPet, router]);

  useEffect(() => {
    if (resumeAttemptedRef.current || localPetStatus === "loading" || currentPet) {
      return;
    }
    resumeAttemptedRef.current = true;
    const pending = readPendingPetGeneration();
    if (!pending) {
      return;
    }
    const existingPet = readLocalPetState();
    if (redirectExistingPet && existingPet) {
      clearPendingPetGeneration();
      router.replace(`/pet/${existingPet.petId}`);
      return;
    }
    const timeoutId = window.setTimeout(() => {
      setDescription(pending.description);
      setFlowStep(FINAL_FLOW_STEP);
      setBackgroundPhase("formed");
      void startPendingGeneration(pending);
    }, 0);
    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [currentPet, localPetStatus, redirectExistingPet, router, startPendingGeneration]);

  useEffect(() => {
    if (
      flowStep !== FINAL_FLOW_STEP
      || !generationResult
      || finalizeAttemptedRef.current
    ) {
      return;
    }
    finalizeAttemptedRef.current = true;

    if (!requestIsStillCurrent(generationResult.requestKey)) {
      return;
    }
    const existingPet = readLocalPetState();
    if (redirectExistingPet && existingPet) {
      clearPendingPetGeneration();
      router.replace(`/pet/${existingPet.petId}`);
      return;
    }
    const pet = createLocalPetDurably(
      generationResult.effectiveDescription,
      generationResult.assetSet,
    );
    if (!pet) {
      const timeoutId = window.setTimeout(() => setError(PENDING_STORAGE_ERROR), 0);
      return () => window.clearTimeout(timeoutId);
    }
    clearPendingPetGeneration(generationResult.requestKey);
    hapticNotification("success");
    router.push(`/pet/${pet.petId}`);
  }, [createLocalPetDurably, flowStep, generationResult, redirectExistingPet, router]);

  useEffect(() => () => {
    activeGenerationAbortRef.current?.abort();
    activeGenerationAbortRef.current = null;
  }, []);

  function handleCreateTestPet() {
    if (isSubmittingRef.current) {
      return;
    }

    setError(null);
    clearPendingPetGeneration();
    const pet = localPet.create(TEST_PET_DESCRIPTION, TEST_PET_ASSET_SET);
    hapticNotification("success");
    router.push(`/pet/${pet.petId}`);
  }

  function beginGeneration(rawDescription: string) {
    if (isSubmittingRef.current) {
      return;
    }

    const trimmedDescription = rawDescription.trim();

    if (!trimmedDescription) {
      setError({ message: "Опишите персонажа" });
      return;
    }

    const pending = pendingPetGenerationForDescription(trimmedDescription);
    const markerPersisted = writePendingPetGeneration(pending);
    if (!markerPersisted) {
      setError(PENDING_STORAGE_ERROR);
      hapticNotification("error");
      return;
    }

    setDescription(trimmedDescription);
    setCustomInputStep(null);
    setCustomValue("");
    setFlowStep(1);
    setBackgroundPhase(prefersReducedMotion ? "formed" : "transition");
    void startPendingGeneration(pending);
  }

  function handleCustomSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = customValue.trim();
    if (!value) {
      setError({ message: "Введите свой вариант" });
      return;
    }
    if (flowStep === 0) {
      beginGeneration(value);
      return;
    }
    setError(null);
    setCustomValue("");
    setCustomInputStep(null);
    setFlowStep((current) => Math.min(current + 1, FINAL_FLOW_STEP));
  }

  function handleCustomKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  function retryGeneration() {
    const pending = readPendingPetGeneration()
      ?? pendingPetGenerationForDescription(description);
    if (!writePendingPetGeneration(pending)) {
      setError(PENDING_STORAGE_ERROR);
      return;
    }
    finalizeAttemptedRef.current = false;
    void startPendingGeneration(pending);
  }

  const questionTitle = flowStep === 0
    ? "Кого хочешь создать?"
    : activeQuestion?.title ?? "";
  const questionOptions = flowStep === 0
    ? DESCRIPTION_PRESETS
    : activeQuestion?.options ?? [];
  function restartFormedBackground(video: HTMLVideoElement) {
    video.currentTime = FORMED_LOOP_START_SECONDS;
    if (!prefersReducedMotion && video.paused) {
      void video.play().catch(() => undefined);
    }
  }

  function startBackgroundMusic() {
    const audio = backgroundMusicRef.current;
    if (!audio || !audio.paused) {
      return;
    }
    void audio.play().catch(() => undefined);
  }

  function handleBackgroundTimeUpdate(video: HTMLVideoElement) {
    if (typeof video.requestVideoFrameCallback === "function") {
      return;
    }
    if (
      backgroundPhase === "initial"
      && video.currentTime >= INITIAL_LOOP_END_SECONDS
    ) {
      video.currentTime = 0;
    } else if (
      backgroundPhase === "transition"
      && video.currentTime >= FORMED_LOOP_START_SECONDS
    ) {
      setBackgroundPhase("formed");
    } else if (
      backgroundPhase === "formed"
      && video.currentTime >= FORMED_LOOP_END_SECONDS
    ) {
      restartFormedBackground(video);
    }
  }

  return (
    <section
      className={styles.screen}
      aria-label="Создание питомца"
      data-button-press-sound-src={CREATION_BUTTON_SOUND}
      onPointerDownCapture={startBackgroundMusic}
      onClickCapture={startBackgroundMusic}
      onKeyDownCapture={startBackgroundMusic}
    >
      <div className={styles.scene}>
        <audio
          ref={backgroundMusicRef}
          src={CREATION_BACKGROUND_MUSIC}
          autoPlay
          loop
          preload="auto"
          aria-hidden="true"
        />
        <audio src={CREATION_BUTTON_SOUND} preload="auto" aria-hidden="true" />
        <video
          ref={backgroundVideoRef}
          className={cn(
            styles.background,
            isCustomInputOpen && styles.dimmed,
          )}
          data-background-phase={backgroundPhase}
          aria-hidden="true"
          autoPlay={!prefersReducedMotion}
          muted
          playsInline
          preload="auto"
          poster="/creation/clouds-empty.png"
          disablePictureInPicture
          onLoadedMetadata={(event) => {
            const video = event.currentTarget;
            if (backgroundPhase === "transition") {
              video.currentTime = INITIAL_LOOP_END_SECONDS;
            } else if (backgroundPhase === "formed") {
              video.currentTime = FORMED_LOOP_START_SECONDS;
            }
          }}
          onTimeUpdate={(event) => {
            handleBackgroundTimeUpdate(event.currentTarget);
          }}
          onEnded={(event) => {
            if (backgroundPhase === "formed") {
              restartFormedBackground(event.currentTarget);
            }
          }}
        >
          <source src={CREATION_BACKGROUND_VIDEO} type="video/mp4" />
        </video>
        <img
          src="/figma/video-filter-normal.webp?v=20260713-video-filter-lossless-webp-1"
          alt=""
          className={styles.grain}
          draggable={false}
          aria-hidden="true"
        />

        {isCustomInputOpen ? (
          <form className={styles.customForm} onSubmit={handleCustomSubmit}>
            <h1 className={styles.customTitle}>{questionTitle}</h1>
            <label className="sr-only" htmlFor="creation-custom-value">
              {flowStep === 0 ? "Свой вариант персонажа" : `Свой вариант: ${questionTitle}`}
            </label>
            <textarea
              id="creation-custom-value"
              name="creation-custom-value"
              value={customValue}
              onChange={(event) => {
                setCustomValue(event.target.value);
                setError(null);
              }}
              onKeyDown={handleCustomKeyDown}
              className={styles.customInput}
              maxLength={MAX_PROMPT_LENGTH}
              rows={3}
              autoFocus
              autoComplete="off"
              autoCapitalize="sentences"
              enterKeyHint="done"
              spellCheck={false}
              placeholder="Свой вариант"
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "create-pet-error" : undefined}
            />
            {customValue.trim() ? (
              <TiltedGlassButton
                type="submit"
                animate={false}
                index={1}
                className={styles.customNextButton}
                style={{
                  backdropFilter: "blur(10px)",
                  WebkitBackdropFilter: "blur(10px)",
                }}
              >
                Далее
              </TiltedGlassButton>
            ) : null}
          </form>
        ) : flowStep === FINAL_FLOW_STEP ? (
          <div className={styles.finalPanel} aria-busy={isSubmitting}>
            <div className={styles.finalContent}>
              {!error && isSubmitting ? <PetThinkingIndicator /> : null}
              <h1 className={styles.finalTitle}>Персонаж формируется...</h1>
              {error ? (
                <>
                  <ErrorNotice error={error} id="create-pet-error" className={styles.error} />
                  {!isSubmitting ? (
                    <TiltedGlassButton
                      animate={false}
                      className={styles.retryButton}
                      onClick={retryGeneration}
                    >
                      Попробовать снова
                    </TiltedGlassButton>
                  ) : null}
                </>
              ) : (
                <p className={styles.finalCopy}>
                  Это может занять несколько минут. Можешь пока пойти по своим делам,
                  мы тебя позовем
                </p>
              )}
            </div>
          </div>
        ) : (
          <div className={styles.questionPanel}>
            <h1 className={styles.title}>{questionTitle}</h1>
            <div className={styles.options} role="group" aria-label={questionTitle}>
              {questionOptions.map((option, index) => (
                <TiltedGlassButton
                  key={option}
                  index={index}
                  delayMs={index * 80}
                  onClick={() => {
                    setError(null);
                    if (flowStep === 0) {
                      beginGeneration(option);
                    } else {
                      setFlowStep((current) => Math.min(current + 1, FINAL_FLOW_STEP));
                    }
                  }}
                >
                  {option}
                </TiltedGlassButton>
              ))}
              <TiltedGlassButton
                key={`custom-option-${flowStep}`}
                index={questionOptions.length}
                delayMs={questionOptions.length * 80}
                onClick={() => {
                  setCustomValue("");
                  setCustomInputStep(flowStep);
                  setError(null);
                }}
              >
                Свой вариант
              </TiltedGlassButton>
            </div>
          </div>
        )}

        {error && flowStep !== FINAL_FLOW_STEP ? (
          <ErrorNotice error={error} id="create-pet-error" className={styles.error} />
        ) : null}

      {isMounted && canShowDebugMenu && flowStep === 0 && !isCustomInputOpen ? (
        <details className="absolute right-[max(16px,var(--tma-safe-right))] top-[max(16px,var(--tma-safe-top))] z-20 w-[min(320px,calc(100vw-32px))]">
          <summary
            aria-label="Debug меню создания персонажа"
            className="ml-auto grid size-10 list-none place-items-center rounded-full border border-white/10 bg-[rgba(255,255,255,0.08)] text-[rgba(255,255,255,0.48)] shadow-[0_8px_24px_rgba(0,0,0,0.32)] backdrop-blur transition-colors hover:bg-[rgba(255,255,255,0.12)] hover:text-[rgba(255,255,255,0.72)] focus:outline-none focus:ring-2 focus:ring-[rgba(255,255,255,0.15)] [&::-webkit-details-marker]:hidden"
          >
            <Bug className="pointer-events-none size-4" aria-hidden="true" />
            <span className="sr-only">Debug меню создания персонажа</span>
          </summary>
          <aside
            aria-label="Debug действия создания персонажа"
            className="mt-2 w-full overflow-hidden rounded-[10px] border border-white/10 bg-[#121212] text-white shadow-[0_18px_46px_rgba(0,0,0,0.4)]"
          >
            <header className="border-b border-white/10 px-4 py-3">
              <h2 className="text-[15px] font-semibold leading-none text-white">Debug</h2>
            </header>
            <div className="p-3">
              <button
                type="button"
                onClick={handleCreateTestPet}
                className="flex h-11 w-full items-center justify-center rounded-[8px] bg-white px-4 text-[14px] font-medium leading-none text-black transition-colors hover:bg-[rgba(255,255,255,0.86)] focus:outline-none focus:ring-2 focus:ring-white/20 focus:ring-offset-2 focus:ring-offset-[#121212]"
              >
                Тестовый персонаж
              </button>
            </div>
          </aside>
        </details>
      ) : null}
      </div>
    </section>
  );
}
