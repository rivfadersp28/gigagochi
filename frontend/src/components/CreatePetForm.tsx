"use client";

import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import { ApiError, generatePetAssets } from "@/lib/api";
import { hapticNotification } from "@/lib/telegram";
import { useLocalPetState } from "@/lib/useLocalPetState";

import { PetCreatingStage } from "./PetCreatingStage";

const MAX_PROMPT_LENGTH = 300;
const PROMPT_PLACEHOLDER = "Фиолетовая птичка с шарфом и длинным клювом";

export function CreatePetForm() {
  const router = useRouter();
  const localPet = useLocalPetState();
  const [description, setDescription] = useState("");
  const [useTemplatePresets, setUseTemplatePresets] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (localPet.status === "ready" && localPet.pet) {
      router.replace(`/pet/${localPet.pet.petId}`);
    }
  }, [localPet.pet, localPet.status, router]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedDescription = description.trim();

    if (!trimmedDescription) {
      setError("Опишите персонажа перед генерацией.");
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const assetSet = await generatePetAssets(trimmedDescription, { useTemplatePresets });
      const pet = localPet.create(trimmedDescription, assetSet);
      hapticNotification("success");
      router.push(`/pet/${pet.petId}`);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(caught.message);
      } else {
        setError("Не удалось создать питомца через AI. Проверьте backend и ключ OpenAI.");
      }
      hapticNotification("error");
      setIsSubmitting(false);
    }
  }

  if (isSubmitting) {
    return <PetCreatingStage overlay />;
  }

  return (
    <section className="tma-screen relative w-screen overflow-hidden bg-white" aria-label="Создание питомца">
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

        <div className="mt-[18px] flex w-full max-w-[900px] items-center justify-between gap-4">
          <span className="text-[14px] font-normal leading-none text-black/45">
            Использовать заготовки
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={useTemplatePresets}
            disabled={isSubmitting}
            onClick={() => setUseTemplatePresets((current) => !current)}
            className={[
              "relative h-[28px] w-[50px] shrink-0 rounded-full border transition-colors",
              "focus:outline-none focus:ring-2 focus:ring-black/10 disabled:cursor-not-allowed disabled:opacity-50",
              useTemplatePresets
                ? "border-black bg-black"
                : "border-black/10 bg-black/[0.06] hover:bg-black/[0.09]",
            ].join(" ")}
          >
            <span
              className={[
                "absolute left-0 top-1/2 size-[22px] -translate-y-1/2 rounded-full bg-white shadow-[0_1px_4px_rgba(0,0,0,0.18)] transition-transform",
                useTemplatePresets ? "translate-x-[24px]" : "translate-x-[3px]",
              ].join(" ")}
              aria-hidden="true"
            />
          </button>
        </div>

        {error ? (
          <p
            id="create-pet-error"
            className="mt-[18px] max-w-[680px] text-[14px] leading-5 text-[var(--danger)]"
            aria-live="polite"
          >
            {error}
          </p>
        ) : null}
      </form>
    </section>
  );
}
