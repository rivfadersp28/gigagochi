"use client";

import { useReward, type AnimationConfig } from "partycles";
import type { RefObject } from "react";
import { useEffect, useRef } from "react";

const petTapParticleConfig = {
  particleCount: 24,
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
  targetRef: RefObject<HTMLElement | null>;
  onComplete: (id: number) => void;
};

export function PetTapParticleBurst({ id, targetRef, onComplete }: PetTapParticleBurstProps) {
  const { reward } = useReward(targetRef as RefObject<HTMLElement>, "bubbles", petTapParticleConfig);
  const rewardRef = useRef(reward);

  useEffect(() => {
    rewardRef.current = reward;
  }, [reward]);

  useEffect(() => {
    let shouldComplete = true;
    const timeoutId = window.setTimeout(() => {
      void rewardRef.current().finally(() => {
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

  return null;
}
