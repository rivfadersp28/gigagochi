"use client";

import { useEffect, useRef, useState } from "react";

import { createBulgeRenderer, type BulgeRenderer } from "./petTapBulgeRenderer";
import { shouldReduceMotion } from "./motion";

const DISTORTION_DURATION_MS = 180;
const DISTORTION_ATTACK_MS = 45;
const DISTORTION_HOLD_MS = 80;
const DISTORTION_STRENGTH = 0.18;

type TriggerPetTapBulgeOptions = {
  video: HTMLVideoElement | null;
  mediaWidth: number;
  mediaHeight: number;
  normalizedX: number;
  normalizedY: number;
};

export function usePetTapBulge() {
  const [isBulgeVisible, setIsBulgeVisible] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rendererRef = useRef<BulgeRenderer | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const distortionStartedAtRef = useRef(0);
  const distortionCenterRef = useRef({ x: 0.5, y: 0.5 });
  const distortionDurationRef = useRef(DISTORTION_DURATION_MS);
  const distortionStrengthRef = useRef(DISTORTION_STRENGTH);

  function drawDistortion(now: number) {
    const elapsed = now - distortionStartedAtRef.current;
    const duration = distortionDurationRef.current;
    if (elapsed >= duration) {
      animationFrameRef.current = null;
      setIsBulgeVisible(false);
      return;
    }

    const attack = Math.min(1, elapsed / DISTORTION_ATTACK_MS);
    const release = elapsed <= DISTORTION_HOLD_MS
      ? 1
      : Math.max(0, 1 - (elapsed - DISTORTION_HOLD_MS) / (duration - DISTORTION_HOLD_MS));
    const strength = distortionStrengthRef.current * Math.min(attack, release);
    const video = videoRef.current;
    if (video) {
      rendererRef.current?.render(video, distortionCenterRef.current, strength);
    }
    animationFrameRef.current = window.requestAnimationFrame(drawDistortion);
  }

  function triggerPetTapBulge(options: TriggerPetTapBulgeOptions) {
    const canvas = canvasRef.current;
    if (!canvas || !options.video) {
      return false;
    }

    if (!rendererRef.current) {
      try {
        rendererRef.current = createBulgeRenderer(canvas);
      } catch {
        rendererRef.current = null;
      }
    }
    if (!rendererRef.current) {
      return false;
    }

    videoRef.current = options.video;
    distortionCenterRef.current = {
      x: Math.min(1, Math.max(0, options.normalizedX)),
      y: 1 - Math.min(1, Math.max(0, options.normalizedY)),
    };

    const deviceScale = Math.min(window.devicePixelRatio || 1, 2);
    rendererRef.current.resize(
      Math.round(options.mediaWidth * deviceScale),
      Math.round(options.mediaHeight * deviceScale),
    );

    const reducedMotion = shouldReduceMotion();
    distortionDurationRef.current = reducedMotion ? 100 : DISTORTION_DURATION_MS;
    distortionStrengthRef.current = reducedMotion ? 0.08 : DISTORTION_STRENGTH;
    distortionStartedAtRef.current = performance.now();
    setIsBulgeVisible(true);

    if (animationFrameRef.current === null) {
      animationFrameRef.current = window.requestAnimationFrame(drawDistortion);
    }
    return true;
  }

  useEffect(() => () => {
    if (animationFrameRef.current !== null) {
      window.cancelAnimationFrame(animationFrameRef.current);
    }
    rendererRef.current?.destroy();
    rendererRef.current = null;
  }, []);

  return {
    canvasRef,
    isBulgeVisible,
    triggerPetTapBulge,
  };
}
