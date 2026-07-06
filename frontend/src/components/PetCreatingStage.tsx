"use client";

/* eslint-disable @next/next/no-img-element */
import Link from "next/link";

type PetCreatingStageProps = {
  overlay?: boolean;
};

export function PetCreatingStage({ overlay = false }: PetCreatingStageProps) {
  return (
    <section
      className={
        overlay
          ? "tma-screen fixed inset-0 z-50 w-screen overflow-hidden bg-[var(--paper)]"
          : "tma-screen relative w-screen overflow-hidden bg-[var(--paper)]"
      }
      aria-busy="true"
      aria-label="Создаем друга"
    >
      <div className="absolute inset-0 grid place-items-center pb-[calc(var(--tma-safe-bottom)+170px)] pt-[var(--tma-safe-top)]">
        <p className="sr-only" aria-live="polite">
          Создаем друга
        </p>

        <p className="creating-friend-shimmer whitespace-nowrap text-[17px] font-normal leading-none">
          Создаем друга
        </p>
      </div>

      <div className="absolute bottom-[max(26px,calc(var(--tma-safe-bottom)+16px))] left-1/2 z-10 flex w-[min(405px,calc(100vw-40px))] -translate-x-1/2 flex-col items-center gap-[32px] sm:gap-[38px]">
        <div className="relative h-[108px] w-full overflow-hidden rounded-[40px] border-[0.5px] border-[rgba(0,0,0,0.07)] bg-[#f5f5f5] sm:h-[116px]">
          <p className="absolute left-[27.5px] top-[27.5px] whitespace-nowrap text-[17px] font-normal leading-none text-black/30">
            Как у тебя дела?
          </p>
          <img
            src="/figma/send-button.svg"
            alt=""
            aria-hidden="true"
            className="absolute bottom-[16.5px] right-[16.5px] size-[36px] max-w-none"
            draggable={false}
          />
        </div>

        <div className="flex items-center gap-[20px] sm:gap-[28px]">
          <div className="inline-flex h-[55px] items-center justify-center gap-[10px] rounded-[40px] bg-white p-[17px] text-[17px] font-normal leading-none text-black drop-shadow-[0_4px_14px_rgba(0,0,0,0.1)]">
            <img
              src="/figma/feed-icon.svg"
              alt=""
              aria-hidden="true"
              className="h-[21.411px] w-[12.684px] max-w-none"
              draggable={false}
            />
            <span className="whitespace-nowrap">Покормить</span>
          </div>

          <div className="inline-flex h-[55px] items-center justify-center gap-[10px] rounded-[40px] bg-white p-[17px] text-[17px] font-normal leading-none text-black drop-shadow-[0_4px_14px_rgba(0,0,0,0.1)]">
            <img
              src="/figma/play-icon.svg"
              alt=""
              aria-hidden="true"
              className="h-[15.041px] w-[23.964px] max-w-none"
              draggable={false}
            />
            <span className="whitespace-nowrap">Поиграть</span>
          </div>
        </div>
      </div>

      <Link
        href="/"
        className="absolute right-[max(20px,var(--tma-safe-right))] top-[max(24px,calc(var(--tma-safe-top)+16px))] z-10 inline-flex h-[21px] items-center justify-center gap-[12px] text-[17px] font-normal leading-none text-black/30 transition-colors hover:text-black/50 focus:outline-none focus:ring-2 focus:ring-black/10 sm:bottom-[max(32px,calc(var(--tma-safe-bottom)+32px))] sm:right-[clamp(24px,4.86vw,70px)] sm:top-auto"
      >
        <span
          aria-hidden="true"
          className="h-[16.925px] w-[13.92px] bg-current"
          style={{
            WebkitMask: "url('/figma/new-pet-icon.svg') center / contain no-repeat",
            mask: "url('/figma/new-pet-icon.svg') center / contain no-repeat",
          }}
        />
        <span className="whitespace-nowrap">Создать нового друга</span>
      </Link>
    </section>
  );
}
