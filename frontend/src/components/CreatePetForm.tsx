"use client";

import { Bug } from "lucide-react";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

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

import { PetCreatingStage } from "./PetCreatingStage";
import { ErrorNotice } from "./ErrorNotice";

const MAX_PROMPT_LENGTH = 300;
const PROMPT_PLACEHOLDER = "Грозовой дракон с добрым характером";
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

export function CreatePetForm() {
  const router = useRouter();
  const localPet = useLocalPetState();
  const createLocalPetDurably = localPet.createDurably;
  const currentPet = localPet.pet;
  const localPetStatus = localPet.status;
  const [description, setDescription] = useState("");
  const [error, setError] = useState<PresentedError | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [keyboardInset, setKeyboardInset] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);
  const isSubmittingRef = useRef(false);
  const resumeAttemptedRef = useRef(false);
  const activeGenerationAbortRef = useRef<AbortController | null>(null);
  const hasDescription = description.trim().length > 0;
  const isKeyboardRaised = keyboardInset > 120;
  const canShowDebugMenu = useTelegramCapabilities().debugMenu;

  useEffect(() => {
    if (localPet.status === "ready" && localPet.pet) {
      clearPendingPetGeneration();
      router.replace(`/pet/${localPet.pet.petId}`);
      return;
    }
  }, [localPet.pet, localPet.status, router]);

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
    if (existingPet) {
      clearPendingPetGeneration();
      router.replace(`/pet/${existingPet.petId}`);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    activeGenerationAbortRef.current = controller;
    const timeoutId = window.setTimeout(() => {
      if (cancelled) {
        return;
      }
      setDescription(pending.description);
      isSubmittingRef.current = true;
      setIsSubmitting(true);
      const generation = runPendingGeneration(pending, controller.signal);
      void generation
        .then(({ assetSet, effectiveDescription }) => {
          if (cancelled) {
            return;
          }
          if (!requestIsStillCurrent(pending.requestKey)) {
            isSubmittingRef.current = false;
            setIsSubmitting(false);
            return;
          }
          const existingPet = readLocalPetState();
          if (existingPet) {
            clearPendingPetGeneration();
            router.replace(`/pet/${existingPet.petId}`);
            return;
          }
          const pet = createLocalPetDurably(effectiveDescription, assetSet);
          if (!pet) {
            setError(PENDING_STORAGE_ERROR);
            isSubmittingRef.current = false;
            setIsSubmitting(false);
            return;
          }
          clearPendingPetGeneration(pending.requestKey);
          router.push(`/pet/${pet.petId}`);
        })
        .catch((caught) => {
          if (cancelled) {
            return;
          }
          const latestPending = readPendingPetGeneration(pending.requestKey);
          if (latestPending) {
            setDescription(latestPending.description);
          }
          if (
            caught instanceof ApiError
            && (
              caught.code === "GENERATION_JOB_NOT_FOUND"
              || caught.generationTerminal
            )
          ) {
            clearPendingPetGeneration(pending.requestKey);
          }
          setError(
            caught instanceof PendingGenerationPersistenceError
              ? PENDING_STORAGE_ERROR
              : presentError(
                  caught,
                  "Не получилось продолжить создание питомца. Попробуйте ещё раз.",
                ),
          );
          isSubmittingRef.current = false;
          setIsSubmitting(false);
        })
        .finally(() => {
          if (activeGenerationAbortRef.current === controller) {
            activeGenerationAbortRef.current = null;
          }
        });
    }, 0);
    return () => {
      cancelled = true;
      controller.abort();
      if (activeGenerationAbortRef.current === controller) {
        activeGenerationAbortRef.current = null;
      }
      window.clearTimeout(timeoutId);
    };
  }, [createLocalPetDurably, currentPet, localPetStatus, router]);

  useEffect(() => () => {
    activeGenerationAbortRef.current?.abort();
    activeGenerationAbortRef.current = null;
  }, []);

  useEffect(() => {
    let animationFrameId: number | null = null;

    function updateKeyboardInset() {
      animationFrameId = null;
      const formHeight = formRef.current?.getBoundingClientRect().height ?? window.innerHeight;
      const visualViewport = window.visualViewport;
      const visibleBottom = visualViewport
        ? visualViewport.height + visualViewport.offsetTop
        : window.innerHeight;
      const nextInset = Math.max(0, Math.round(formHeight - visibleBottom));

      setKeyboardInset((currentInset) =>
        currentInset === nextInset ? currentInset : nextInset,
      );
    }

    function scheduleKeyboardInsetUpdate() {
      if (animationFrameId === null) {
        animationFrameId = window.requestAnimationFrame(updateKeyboardInset);
      }
    }

    scheduleKeyboardInsetUpdate();
    window.addEventListener("resize", scheduleKeyboardInsetUpdate);
    window.visualViewport?.addEventListener("resize", scheduleKeyboardInsetUpdate);
    window.visualViewport?.addEventListener("scroll", scheduleKeyboardInsetUpdate);

    return () => {
      if (animationFrameId !== null) {
        window.cancelAnimationFrame(animationFrameId);
      }
      window.removeEventListener("resize", scheduleKeyboardInsetUpdate);
      window.visualViewport?.removeEventListener("resize", scheduleKeyboardInsetUpdate);
      window.visualViewport?.removeEventListener("scroll", scheduleKeyboardInsetUpdate);
    };
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

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmittingRef.current) {
      return;
    }

    const trimmedDescription = description.trim();

    if (!trimmedDescription) {
      setError({ message: "Опишите персонажа" });
      return;
    }

    setError(null);
    isSubmittingRef.current = true;
    setIsSubmitting(true);
    const pending = pendingPetGenerationForDescription(trimmedDescription);
    const markerPersisted = writePendingPetGeneration(pending);
    if (!markerPersisted) {
      setError(PENDING_STORAGE_ERROR);
      hapticNotification("error");
      isSubmittingRef.current = false;
      setIsSubmitting(false);
      return;
    }
    const controller = new AbortController();
    activeGenerationAbortRef.current?.abort();
    activeGenerationAbortRef.current = controller;

    try {
      const { assetSet, effectiveDescription } = await runPendingGeneration(
        pending,
        controller.signal,
      );
      if (!requestIsStillCurrent(pending.requestKey)) {
        isSubmittingRef.current = false;
        setIsSubmitting(false);
        return;
      }
      const existingPet = readLocalPetState();
      if (existingPet) {
        clearPendingPetGeneration();
        router.replace(`/pet/${existingPet.petId}`);
        return;
      }
      const pet = createLocalPetDurably(effectiveDescription, assetSet);
      if (!pet) {
        setError(PENDING_STORAGE_ERROR);
        hapticNotification("error");
        isSubmittingRef.current = false;
        setIsSubmitting(false);
        return;
      }
      clearPendingPetGeneration(pending.requestKey);
      hapticNotification("success");
      router.push(`/pet/${pet.petId}`);
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
        && (
          caught.code === "GENERATION_JOB_NOT_FOUND"
          || caught.generationTerminal
        )
      ) {
        clearPendingPetGeneration(pending.requestKey);
      }
      setError(
        caught instanceof PendingGenerationPersistenceError
          ? PENDING_STORAGE_ERROR
          : presentError(caught, "Не получилось создать питомца. Попробуйте ещё раз."),
      );
      hapticNotification("error");
      isSubmittingRef.current = false;
      setIsSubmitting(false);
    } finally {
      if (activeGenerationAbortRef.current === controller) {
        activeGenerationAbortRef.current = null;
      }
    }
  }

  if (isSubmitting) {
    return <PetCreatingStage overlay />;
  }

  return (
    <section className="create-pet-stage tma-screen relative w-screen overflow-hidden text-white" aria-label="Создание питомца">
      <form
        ref={formRef}
        onSubmit={handleSubmit}
        className={cn(
          "create-pet-form mx-auto flex min-h-full w-full max-w-[640px] flex-col px-[28px] pb-[calc(var(--tma-safe-bottom)+28px)] pt-[calc(var(--tma-safe-top)+88px)] sm:px-[40px]",
          isKeyboardRaised && "create-pet-form--keyboard",
        )}
        style={{ "--create-pet-keyboard-inset": `${keyboardInset}px` } as CSSProperties}
      >
        <div className="create-pet-prompt-block">
          <label htmlFor="description" className="create-pet-label">
            Опиши своего друга
          </label>

          <textarea
            id="description"
            name="description"
            value={description}
            onChange={(event) => {
              setDescription(event.target.value);
              if (error) {
                setError(null);
              }
            }}
            maxLength={MAX_PROMPT_LENGTH}
            rows={4}
            autoFocus
            autoComplete="off"
            autoCapitalize="sentences"
            inputMode="text"
            enterKeyHint="done"
            spellCheck={false}
            disabled={isSubmitting}
            aria-describedby={error ? "create-pet-error" : undefined}
            aria-invalid={Boolean(error)}
            placeholder={PROMPT_PLACEHOLDER}
            className={`create-pet-prompt ${hasDescription ? "create-pet-prompt--filled" : ""}`}
          />

        </div>

        {error ? (
          <ErrorNotice
            error={error}
            id="create-pet-error"
            className="create-pet-error"
          />
        ) : null}

        <div className="create-pet-actions mt-auto flex min-h-[138px] items-start pb-[calc(var(--tma-safe-bottom)+52px)] pt-[48px]">
          {hasDescription ? (
            <button
              type="submit"
              disabled={isSubmitting}
              className="create-pet-submit"
              aria-label="Создать персонажа"
            >
              Создать
            </button>
          ) : null}
        </div>
      </form>

      {canShowDebugMenu ? (
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
    </section>
  );
}
