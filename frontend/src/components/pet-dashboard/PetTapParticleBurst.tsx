"use client";

import { useReward, type AnimationConfig } from "partycles";
import type { RefObject } from "react";
import { useEffect, useRef } from "react";

export const PET_TAP_PARTICLE_FADE_MS = 120;

const petTapParticleConfig = {
  particleCount: 16,
  spread: 92,
  startVelocity: 16,
  elementSize: 28,
  lifetime: 170,
  colors: [
    "rgba(255, 255, 255, 0.78)",
    "rgba(129, 212, 250, 0.58)",
    "rgba(41, 182, 246, 0.48)",
    "rgba(255, 241, 118, 0.5)",
  ],
  physics: {
    gravity: -0.04,
    wind: 0,
    friction: 0.985,
  },
  effects: {
    wobble: true,
  },
} satisfies AnimationConfig;

type PetTapParticleBurstProps = {
  id: number;
  x: number;
  y: number;
  isExiting: boolean;
  onComplete: (id: number) => void;
};

export function PetTapParticleBurst({ id, x, y, isExiting, onComplete }: PetTapParticleBurstProps) {
  const targetRef = useRef<HTMLSpanElement>(null);
  const particleContainerRef = useRef<HTMLElement | null>(null);
  const { reward } = useReward(targetRef as RefObject<HTMLElement>, "hearts", petTapParticleConfig);
  const rewardRef = useRef(reward);

  useEffect(() => {
    rewardRef.current = reward;
  }, [reward]);

  useEffect(() => {
    let shouldComplete = true;
    const timeoutId = window.setTimeout(() => {
      const existingBodyChildren = new Set(document.body.children);
      const rewardPromise = rewardRef.current();
      particleContainerRef.current = Array.from(document.body.children).find(
        (child): child is HTMLElement =>
          child instanceof HTMLElement
          && !existingBodyChildren.has(child)
          && child.style.position === "fixed"
          && child.style.zIndex === "9999",
      ) ?? null;

      void rewardPromise.finally(() => {
        if (shouldComplete) {
          onComplete(id);
        }
      });
    }, 0);

    return () => {
      shouldComplete = false;
      window.clearTimeout(timeoutId);
    };
  }, [id, onComplete]);

  useEffect(() => {
    if (!isExiting) {
      return;
    }

    const container = particleContainerRef.current;
    if (container) {
      container.style.transition = `opacity ${PET_TAP_PARTICLE_FADE_MS}ms ease-out`;
      container.style.opacity = "0";
    }

    const timeoutId = window.setTimeout(() => onComplete(id), PET_TAP_PARTICLE_FADE_MS);
    return () => window.clearTimeout(timeoutId);
  }, [id, isExiting, onComplete]);

  return (
    <span
      ref={targetRef}
      aria-hidden="true"
      style={{
        position: "fixed",
        top: y,
        left: x,
        width: 0,
        height: 0,
        pointerEvents: "none",
      }}
    />
  );
}
