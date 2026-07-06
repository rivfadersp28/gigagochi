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
const PROMPT_PLACEHOLDER = "Фиолетовая птичка с шарфом и длинным клювом";

export function CreatePetForm() {
  const router = useRouter();
  const localPet = useLocalPetState();
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const isSubmittingRef = useRef(false);

  useEffect(() => {
    if (localPet.status === "ready" && localPet.pet) {
      router.replace(`/pet/${localPet.pet.petId}`);
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
    <section className="tma-screen relative w-screen overflow-hidden bg-[var(--paper)]" aria-label="Создание питомца">
      <form
        onSubmit={handleSubmit}
        className="mx-auto flex min-h-full w-full max-w-[1440px] flex-col justify-center px-[clamp(24px,8vw,316px)] pb-[var(--tma-safe-bottom)] pt-[var(--tma-safe-top)]"
      >
        <label
          htmlFor="description"
          className="h-[21px] whitespace-nowrap text-[17px] font-normal leading-none text-black/30"
        >
          Кого хотите создать?
        </label>

        <div className="mt-[9px] w-full max-w-[900px]">
          <input
            type="text"
            id="description"
            name="description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            maxLength={MAX_PROMPT_LENGTH}
            autoComplete="off"
            autoCapitalize="sentences"
            inputMode="text"
            enterKeyHint="done"
            spellCheck={false}
            disabled={isSubmitting}
            aria-describedby={error ? "create-pet-error" : undefined}
            placeholder={PROMPT_PLACEHOLDER}
            className="h-[48px] w-full appearance-none border-0 bg-transparent p-0 text-[clamp(27px,7.8vw,33px)] font-normal leading-[40px] text-black caret-black outline-none placeholder:text-black/30 disabled:opacity-60"
          />
        </div>

        {error ? (
          <p
            id="create-pet-error"
            className="mt-[18px] max-w-[680px] whitespace-pre-wrap break-words text-[14px] leading-5 text-[var(--danger)]"
            aria-live="polite"
          >
            {error}
          </p>
        ) : null}
      </form>

      <details className="absolute right-[max(16px,var(--tma-safe-right))] top-[max(16px,var(--tma-safe-top))] z-20">
        <summary
          aria-label="Debug меню создания персонажа"
          className="grid size-10 list-none place-items-center rounded-full border border-black/10 bg-white/80 text-black/48 shadow-[0_8px_24px_rgba(0,0,0,0.08)] backdrop-blur transition-colors hover:bg-white hover:text-black/72 focus:outline-none focus:ring-2 focus:ring-black/15 [&::-webkit-details-marker]:hidden"
        >
          <Bug className="pointer-events-none size-4" aria-hidden="true" />
          <span className="sr-only">Debug меню создания персонажа</span>
        </summary>
        <aside
          aria-label="Debug действия создания персонажа"
          className="absolute right-0 top-[50px] w-[min(320px,calc(100vw-32px))] overflow-hidden rounded-[8px] border border-black/10 bg-white text-black shadow-[0_18px_46px_rgba(0,0,0,0.14)]"
        >
          <header className="border-b border-black/10 px-4 py-3">
            <h2 className="text-[15px] font-semibold leading-none text-black">Debug</h2>
          </header>
          <div className="p-3">
            <button
              type="button"
              onClick={handleCreateTestPet}
              className="flex h-11 w-full items-center justify-center rounded-[8px] bg-black px-4 text-[14px] font-medium leading-none text-white transition-colors hover:bg-black/80 focus:outline-none focus:ring-2 focus:ring-black/20 focus:ring-offset-2"
            >
              Тестовый персонаж
            </button>
          </div>
        </aside>
      </details>
    </section>
  );
}
