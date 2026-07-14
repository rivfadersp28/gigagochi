"use client";

/* eslint-disable @next/next/no-img-element */
import type { CSSProperties } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  PetCharacterMessage,
  type PetMessageLayout,
  type PetReplyMessage,
} from "./PetCharacterMessage";
import styles from "./PetSpeechBubble.module.css";
import { shouldReduceMotion } from "./motion";

const BUBBLE_MIN_HEIGHT = 99;
const BUBBLE_MAX_HEIGHT = 203;
const BUBBLE_HORIZONTAL_PADDING = 90;
const BUBBLE_HEIGHT_TEXT_MULTIPLIER = 2;
const BUBBLE_HEIGHT_BASE = 30;
const BUBBLE_MAX_TEXT_LINES = 2;
const BUBBLE_RESIZE_DURATION_MS = 300;
const BUBBLE_RESIZE_EASING = "cubic-bezier(0.645, 0.045, 0.355, 1)";

type PetSpeechBubbleStyle = CSSProperties & {
  "--main-speech-bubble-width": string;
  "--main-speech-bubble-height": string;
  "--main-speech-bubble-text-bottom-padding": string;
};

type PetSpeechBubbleProps = {
  isVisible: boolean;
  message: PetReplyMessage;
  scaleOrigin: "top" | "bottom";
  shapeSrc: string;
  maxLines?: number;
  minFontSize?: number;
};

function clampedBubbleHeight(contentHeight: number) {
  return Math.min(
    BUBBLE_MAX_HEIGHT,
    Math.max(
      BUBBLE_MIN_HEIGHT,
      Math.round(contentHeight * BUBBLE_HEIGHT_TEXT_MULTIPLIER + BUBBLE_HEIGHT_BASE),
    ),
  );
}

function clampedBubbleWidth(contentWidth: number) {
  return Math.min(360, Math.max(248, Math.round(contentWidth + BUBBLE_HORIZONTAL_PADDING)));
}

function textBottomPadding(bubbleHeight: number) {
  const progress = (bubbleHeight - BUBBLE_MIN_HEIGHT) / (BUBBLE_MAX_HEIGHT - BUBBLE_MIN_HEIGHT);
  return Math.round(5 + Math.max(0, Math.min(1, progress)) * 11);
}

export function PetSpeechBubble({
  isVisible,
  message,
  scaleOrigin,
  shapeSrc,
  maxLines = BUBBLE_MAX_TEXT_LINES,
  minFontSize,
}: PetSpeechBubbleProps) {
  const [contentLayout, setContentLayout] = useState<PetMessageLayout>({
    width: 158,
    height: 32,
  });
  const bubbleRef = useRef<HTMLDivElement>(null);
  const shapeRef = useRef<HTMLImageElement>(null);
  const previousSizeRef = useRef<{ width: number; height: number } | null>(null);
  const previousMessageTopRef = useRef<number | null>(null);
  const shapeAnimationRef = useRef<Animation | null>(null);
  const messageShiftAnimationRef = useRef<Animation | null>(null);
  const bubbleWidth = clampedBubbleWidth(contentLayout.width);
  const bubbleHeight = clampedBubbleHeight(contentLayout.height);
  const style: PetSpeechBubbleStyle = {
    "--main-speech-bubble-width": `${bubbleWidth}px`,
    "--main-speech-bubble-height": `${bubbleHeight}px`,
    "--main-speech-bubble-text-bottom-padding": `${textBottomPadding(bubbleHeight)}px`,
  };

  const handleCurrentMessageLayout = useCallback((layout: PetMessageLayout) => {
    if (
      !Number.isFinite(layout.width) ||
      !Number.isFinite(layout.height) ||
      layout.width <= 0 ||
      layout.height <= 0
    ) {
      return;
    }
    setContentLayout((currentLayout) =>
      Math.abs(currentLayout.width - layout.width) < 0.5 &&
      Math.abs(currentLayout.height - layout.height) < 0.5
        ? currentLayout
        : layout,
    );
  }, []);

  useEffect(() => {
    const bubble = bubbleRef.current;
    const shape = shapeRef.current;
    if (!bubble || !shape || typeof ResizeObserver !== "function") {
      return;
    }

    const observer = new ResizeObserver(([entry]) => {
      if (!entry) {
        return;
      }

      const messageLayer = bubble.querySelector<HTMLElement>(".main-speech-bubble__message");
      const borderBox = entry.borderBoxSize?.[0];
      const nextSize = borderBox
        ? { width: borderBox.inlineSize, height: borderBox.blockSize }
        : { width: bubble.offsetWidth, height: bubble.offsetHeight };
      const previousSize = previousSizeRef.current;
      const nextMessageTop = messageLayer?.getBoundingClientRect().top ?? null;
      const previousMessageTop = previousMessageTopRef.current;
      previousSizeRef.current = nextSize;
      previousMessageTopRef.current = nextMessageTop;

      if (
        !previousSize ||
        shouldReduceMotion() ||
        typeof shape.animate !== "function" ||
        (Math.abs(previousSize.width - nextSize.width) < 0.5 &&
          Math.abs(previousSize.height - nextSize.height) < 0.5)
      ) {
        return;
      }

      shapeAnimationRef.current?.cancel();
      shape.style.willChange = "transform";

      const shapeAnimation = shape.animate(
        [
          {
            transform: `scale(${previousSize.width / nextSize.width}, ${previousSize.height / nextSize.height})`,
          },
          { transform: "scale(1, 1)" },
        ],
        {
          duration: BUBBLE_RESIZE_DURATION_MS,
          easing: BUBBLE_RESIZE_EASING,
        },
      );
      shapeAnimationRef.current = shapeAnimation;

      if (
        messageLayer &&
        previousMessageTop !== null &&
        nextMessageTop !== null &&
        Math.abs(previousMessageTop - nextMessageTop) >= 0.5
      ) {
        messageShiftAnimationRef.current?.cancel();
        messageLayer.style.willChange = "transform";

        const messageShiftAnimation = messageLayer.animate(
          [
            {
              transform: `translate3d(0, ${previousMessageTop - nextMessageTop}px, 0)`,
            },
            { transform: "translate3d(0, 0, 0)" },
          ],
          {
            duration: BUBBLE_RESIZE_DURATION_MS,
            easing: BUBBLE_RESIZE_EASING,
          },
        );
        messageShiftAnimationRef.current = messageShiftAnimation;

        void messageShiftAnimation.finished
          .then(() => {
            if (messageShiftAnimationRef.current === messageShiftAnimation) {
              messageLayer.style.willChange = "auto";
              messageShiftAnimationRef.current = null;
            }
          })
          .catch(() => undefined);
      }

      void shapeAnimation.finished
        .then(() => {
          if (shapeAnimationRef.current === shapeAnimation) {
            shape.style.willChange = "auto";
            shapeAnimationRef.current = null;
          }
        })
        .catch(() => undefined);
    });

    observer.observe(bubble);
    return () => {
      observer.disconnect();
      shapeAnimationRef.current?.cancel();
      messageShiftAnimationRef.current?.cancel();
      shape.style.willChange = "auto";
      const messageLayer = bubble.querySelector<HTMLElement>(".main-speech-bubble__message");
      if (messageLayer) {
        messageLayer.style.willChange = "auto";
      }
    };
  }, []);

  return (
    <div
      ref={bubbleRef}
      className={`main-speech-bubble ${styles.scaleTransition} ${
        isVisible ? "" : styles.hidden
      } ${scaleOrigin === "top" ? styles.originTop : styles.originBottom}`}
      style={style}
    >
      <img
        ref={shapeRef}
        src={shapeSrc}
        alt=""
        className="main-speech-bubble__shape"
        aria-hidden="true"
        draggable={false}
      />
      <PetCharacterMessage
        message={message}
        textTranslateY={0}
        speechEndTrimMs={0}
        onCurrentMessageLayout={handleCurrentMessageLayout}
        maxCurrentMessageLines={maxLines}
        minCurrentMessageFontSize={minFontSize}
      />
    </div>
  );
}
