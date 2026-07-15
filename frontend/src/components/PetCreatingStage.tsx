"use client";

import dynamic from "next/dynamic";
import { useSyncExternalStore } from "react";

const ImageGeneration = dynamic(
  () => import("img-fx").then((module) => module.ImageGeneration),
  {
    ssr: false,
    loading: () => <div style={{ width: 280, height: 280, borderRadius: 16 }} />,
  },
);

type PetCreatingStageProps = {
  overlay?: boolean;
};

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function subscribeReducedMotion(onStoreChange: () => void) {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return () => undefined;
  }
  const query = window.matchMedia(REDUCED_MOTION_QUERY);
  if (typeof query.addEventListener === "function") {
    query.addEventListener("change", onStoreChange);
    return () => query.removeEventListener("change", onStoreChange);
  }
  query.addListener(onStoreChange);
  return () => query.removeListener(onStoreChange);
}

function reducedMotionSnapshot() {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

function CreationLoaderCard() {
  const reducedMotion = useSyncExternalStore(
    subscribeReducedMotion,
    reducedMotionSnapshot,
    () => false,
  );

  if (reducedMotion) {
    return (
      <div
        className="creation-loader-card"
        data-creation-loader-static="true"
        style={{
          width: 280,
          height: 280,
          borderRadius: 16,
          background: "center / contain no-repeat url('/figma/main-pet.png')",
        }}
        aria-hidden="true"
      />
    );
  }

  return (
    <ImageGeneration
      preset="pixels-organic"
      strength={1.0}
      images={["/figma/main-pet.png", "/figma/pet.png"]}
      className="creation-loader-card"
      paused={false}
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

      <p className="creation-loader-notice absolute inset-x-[28px] bottom-[calc(var(--tma-safe-bottom)+32px)] z-10 text-center text-[15px] leading-[20px] text-pretty text-white/50">
        Мы пришлем уведомление, когда персонаж будет готов
      </p>
    </section>
  );
}
