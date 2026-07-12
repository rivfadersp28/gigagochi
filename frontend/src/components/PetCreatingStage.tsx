"use client";

import { ImageGeneration, type ImageGenerationHandle } from "img-fx";
import { useRef } from "react";

type PetCreatingStageProps = {
  overlay?: boolean;
};

function CreationLoaderCard() {
  const ref = useRef<ImageGenerationHandle>(null);

  return (
    <ImageGeneration
      ref={ref}
      preset="pixels-organic"
      strength={1.0}
      images={["/figma/main-pet.png", "/figma/pet.png"]}
      className="creation-loader-card"
      aria-hidden="true"
    >
      <div style={{ width: 280, height: 280, borderRadius: 16 }} />
    </ImageGeneration>
  );
}

export function PetCreatingStage({ overlay = false }: PetCreatingStageProps) {
  return (
    <section
      className={
        overlay
          ? "creation-loader-screen tma-screen fixed inset-0 z-50 w-screen overflow-hidden"
          : "creation-loader-screen tma-screen relative w-screen overflow-hidden"
      }
      aria-busy="true"
      aria-label="Создаем друга"
    >
      <div className="creation-loader-center absolute inset-0 flex flex-col items-center justify-center gap-[22px] px-[28px] pb-[calc(var(--tma-safe-bottom)+196px)] pt-[var(--tma-safe-top)]">
        <p className="sr-only" aria-live="polite">
          Создаем друга
        </p>

        <CreationLoaderCard />

        <p className="creating-friend-shimmer whitespace-nowrap text-[17px] font-semibold leading-none">
          Создаем друга
        </p>
      </div>

      <p className="creation-loader-notice absolute inset-x-[28px] bottom-[calc(var(--tma-safe-bottom)+32px)] z-[1] text-center text-[15px] leading-[20px] text-white/50">
        Мы пришлем уведомление, когда персонаж будет готов
      </p>
    </section>
  );
}
