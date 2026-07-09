"use client";

import { Bug } from "lucide-react";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useRef, useState } from "react";

import { ApiError, generatePetAssets } from "@/lib/api";
import { hapticNotification } from "@/lib/telegram";
import { TEST_PET_ASSET_SET, TEST_PET_DESCRIPTION } from "@/lib/testPetFixture";
import { useLocalPetState } from "@/lib/useLocalPetState";

import { PetCreatingStage } from "./PetCreatingStage";

const MAX_PROMPT_LENGTH = 300;
const PROMPT_PLACEHOLDER = "Грозовой дракон с добрым характером";

export function CreatePetForm() {
  const router = useRouter();
  const localPet = useLocalPetState();
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const isSubmittingRef = useRef(false);
  const hasDescription = description.trim().length > 0;

  useEffect(() => {
    if (localPet.status === "ready" && localPet.pet) {
      router.replace(`/pet/${localPet.pet.petId}`);
      return;
    }
  }, [localPet.pet, localPet.status, router]);

  function handleCreateTestPet() {
    if (isSubmittingRef.current) {
      return;
    }

    setError(null);
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
      setError("Опишите персонажа перед генерацией.");
      return;
    }

    setError(null);
    isSubmittingRef.current = true;
    setIsSubmitting(true);

    try {
      const assetSet = await generatePetAssets(trimmedDescription);
      const pet = localPet.create(trimmedDescription, assetSet);
      hapticNotification("success");
      router.push(`/pet/${pet.petId}`);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(caught.message);
      } else if (caught instanceof Error) {
        setError(`Не удалось создать питомца.\n${caught.name}: ${caught.message}`);
      } else {
        setError("Не удалось создать питомца. Неизвестная ошибка.");
      }
      hapticNotification("error");
      isSubmittingRef.current = false;
      setIsSubmitting(false);
    }
  }

  if (isSubmitting) {
    return <PetCreatingStage overlay />;
  }

  return (
    <section className="create-pet-stage tma-screen relative w-screen overflow-hidden text-white" aria-label="Создание питомца">
      <form
        onSubmit={handleSubmit}
        className="create-pet-form mx-auto flex min-h-full w-full max-w-[640px] flex-col px-[28px] pb-[calc(var(--tma-safe-bottom)+28px)] pt-[calc(var(--tma-safe-top)+88px)] sm:px-[40px]"
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
            placeholder={PROMPT_PLACEHOLDER}
            className={`create-pet-prompt ${hasDescription ? "create-pet-prompt--filled" : ""}`}
          />
        </div>

        {error ? (
          <p
            id="create-pet-error"
            className="create-pet-error"
            aria-live="polite"
          >
            {error}
          </p>
        ) : null}

        <div className="mt-auto flex min-h-[138px] items-start pb-[calc(var(--tma-safe-bottom)+52px)] pt-[48px]">
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
    </section>
  );
}
