"use client";

import type { CSSProperties, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { playPetSpeechAudioSequence } from "@/lib/petSpeechAudio";

import { finishAnimation, shouldReduceMotion } from "./motion";

export type PetReplyMessage = {
  id: number;
  text: string;
  playSpeechAudio: boolean;
};

const PET_REPLY_EXIT_MS = 280;
const PET_REPLY_CROSS_EASING = "cubic-bezier(0.16, 1, 0.3, 1)";
const PET_REPLY_CROSS_EXIT_EASING = "cubic-bezier(0.55, 0, 1, 0.45)";
const PET_CHARACTER_RISE_DURATION_MS = 700;
const PET_CHARACTER_RISE_STAGGER_MS = 24;
const PET_CHARACTER_RISE_EASING = "cubic-bezier(0.2, 0.8, 0.2, 1)";

const petMessageStackStyle: CSSProperties = {
  display: "grid",
  justifyItems: "start",
  alignItems: "start",
  minHeight: 0,
  overflow: "visible",
};

const petMessageLayerStyle: CSSProperties = {
  gridArea: "1 / 1",
  maxWidth: "100%",
  margin: 0,
  overflow: "visible",
  overflowWrap: "normal",
  whiteSpace: "normal",
  wordBreak: "normal",
  transformOrigin: "center top",
};

const petMessageWordStyle: CSSProperties = {
  display: "inline-block",
  whiteSpace: "nowrap",
};

const petMessageUnitStyle: CSSProperties = {
  display: "inline-block",
  backfaceVisibility: "hidden",
  transformOrigin: "50% 55%",
  whiteSpace: "pre",
  willChange: "transform, opacity",
};

function renderPetMessageUnits(text: string, translateY: number): ReactNode[] {
  const nodes: ReactNode[] = [];
  let unitIndex = 0;

  text.split("\n").forEach((line, lineIndex, lines) => {
    line.split(/(\s+)/).forEach((part, partIndex) => {
      if (!part) {
        return;
      }

      if (/^\s+$/.test(part)) {
        nodes.push(
          <span key={`${lineIndex}-${partIndex}-space`} style={{ whiteSpace: "pre-wrap" }}>
            {part}
          </span>,
        );
        return;
      }

      nodes.push(
        <span key={`${lineIndex}-${partIndex}-${part}`} style={petMessageWordStyle}>
          {Array.from(part).map((character) => {
            const key = unitIndex;
            unitIndex += 1;
            return (
              <span
                key={`${key}-${character}`}
                data-pet-message-unit="true"
                style={{
                  ...petMessageUnitStyle,
                  opacity: 0,
                  transform: `translate3d(0, ${translateY}px, 0)`,
                }}
              >
                {character}
              </span>
            );
          })}
        </span>,
      );
    });

    if (lineIndex < lines.length - 1) {
      nodes.push(<br key={`${lineIndex}-line-break`} />);
    }
  });

  return nodes;
}

function PetMessageText({
  text,
  translateY,
  speechEndTrimMs,
  playSpeechAudio,
}: {
  text: string;
  translateY: number;
  speechEndTrimMs: number;
  playSpeechAudio: boolean;
}) {
  const textRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const root = textRef.current;
    if (!root) {
      return;
    }

    const units = Array.from(
      root.querySelectorAll<HTMLElement>("[data-pet-message-unit='true']"),
    );

    if (shouldReduceMotion() || typeof root.animate !== "function") {
      units.forEach((unit) => {
        finishAnimation(unit, {
          opacity: "1",
          transform: "translate3d(0, 0, 0)",
          willChange: "auto",
        });
      });
      return;
    }

    const speechDurationMs =
      PET_CHARACTER_RISE_DURATION_MS +
      Math.max(0, units.length - 1) * PET_CHARACTER_RISE_STAGGER_MS;
    const trimmedSpeechDurationMs = Math.max(0, speechDurationMs - speechEndTrimMs);
    const stopSpeechAudio =
      playSpeechAudio && units.length > 0
        ? playPetSpeechAudioSequence(trimmedSpeechDurationMs)
        : null;

    const animations = units.map((unit, index) => {
      finishAnimation(unit, {
        opacity: "0",
        transform: `translate3d(0, ${translateY}px, 0)`,
        willChange: "transform, opacity",
      });

      const animation = unit.animate(
        [
          {
            opacity: 0,
            transform: `translate3d(0, ${translateY}px, 0)`,
          },
          {
            opacity: 1,
            transform: "translate3d(0, 0, 0)",
          },
        ],
        {
          delay: index * PET_CHARACTER_RISE_STAGGER_MS,
          duration: PET_CHARACTER_RISE_DURATION_MS,
          easing: PET_CHARACTER_RISE_EASING,
          fill: "forwards",
        },
      );

      void animation.finished
        .then(() => {
          if (unit.isConnected) {
            finishAnimation(unit, {
              opacity: "1",
              transform: "translate3d(0, 0, 0)",
              willChange: "auto",
            });
          }
        })
        .catch(() => undefined);

      return animation;
    });

    return () => {
      stopSpeechAudio?.();
      animations.forEach((animation) => animation.cancel());
    };
  }, [playSpeechAudio, speechEndTrimMs, text, translateY]);

  return (
    <span ref={textRef} aria-hidden="true">
      {renderPetMessageUnits(text, translateY)}
    </span>
  );
}

export function PetCharacterMessage({
  message,
  textTranslateY,
  speechEndTrimMs,
}: {
  message: PetReplyMessage;
  textTranslateY: number;
  speechEndTrimMs: number;
}) {
  const [currentMessage, setCurrentMessage] = useState(message);
  const [previousMessage, setPreviousMessage] = useState<PetReplyMessage | null>(null);
  const currentMessageRef = useRef(message);
  const currentLayerRef = useRef<HTMLParagraphElement>(null);
  const previousLayerRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    if (message.id === currentMessageRef.current.id) {
      return;
    }

    const outgoingMessage = currentMessageRef.current;
    currentMessageRef.current = message;

    setPreviousMessage(outgoingMessage);
    setCurrentMessage(message);

    const timeoutId = window.setTimeout(() => {
      setPreviousMessage((storedMessage) =>
        storedMessage?.id === outgoingMessage.id ? null : storedMessage,
      );
    }, PET_REPLY_EXIT_MS);

    return () => window.clearTimeout(timeoutId);
  }, [message]);

  useEffect(() => {
    const currentLayer = currentLayerRef.current;
    if (!currentLayer) {
      return;
    }

    if (shouldReduceMotion() || typeof currentLayer.animate !== "function") {
      finishAnimation(currentLayer, {
        opacity: "1",
        transform: "scale(1)",
      });
      return;
    }

    finishAnimation(currentLayer, {
      opacity: "0",
      transform: "scale(1.035)",
    });

    const animation = currentLayer.animate(
      [
        { opacity: 0, transform: "scale(1.035)" },
        { opacity: 1, transform: "scale(1)" },
      ],
      {
        duration: PET_REPLY_EXIT_MS,
        easing: PET_REPLY_CROSS_EASING,
        fill: "forwards",
      },
    );

    void animation.finished
      .then(() => {
        if (currentLayer.isConnected) {
          finishAnimation(currentLayer, {
            opacity: "1",
            transform: "scale(1)",
          });
        }
      })
      .catch(() => undefined);

    return () => animation.cancel();
  }, [currentMessage.id]);

  useEffect(() => {
    const previousLayer = previousLayerRef.current;
    if (!previousMessage || !previousLayer) {
      return;
    }

    if (shouldReduceMotion() || typeof previousLayer.animate !== "function") {
      finishAnimation(previousLayer, {
        opacity: "0",
        transform: "scale(0.965)",
      });
      return;
    }

    finishAnimation(previousLayer, {
      opacity: "1",
      transform: "scale(1)",
    });

    const animation = previousLayer.animate(
      [
        { opacity: 1, transform: "scale(1)" },
        { opacity: 0, transform: "scale(0.965)" },
      ],
      {
        duration: PET_REPLY_EXIT_MS,
        easing: PET_REPLY_CROSS_EXIT_EASING,
        fill: "forwards",
      },
    );

    void animation.finished
      .then(() => {
        if (previousLayer.isConnected) {
          finishAnimation(previousLayer, {
            opacity: "0",
            transform: "scale(0.965)",
          });
        }
      })
      .catch(() => undefined);

    return () => animation.cancel();
  }, [previousMessage]);

  return (
    <div
      className="main-speech-bubble__message text-center text-[20.357px] font-bold leading-[normal] text-white"
      style={petMessageStackStyle}
      aria-live="polite"
    >
      {previousMessage ? (
        <p
          key={`previous-${previousMessage.id}`}
          ref={previousLayerRef}
          style={{
            ...petMessageLayerStyle,
            opacity: 1,
            transform: "scale(1)",
          }}
          aria-hidden="true"
        >
          {previousMessage.text}
        </p>
      ) : null}
      <p
        key={`current-${currentMessage.id}`}
        ref={currentLayerRef}
        style={{
          ...petMessageLayerStyle,
          position: "relative",
          zIndex: 1,
          opacity: 0,
          transform: "scale(1.035)",
        }}
        aria-label={currentMessage.text}
      >
        <PetMessageText
          text={currentMessage.text}
          translateY={textTranslateY}
          speechEndTrimMs={speechEndTrimMs}
          playSpeechAudio={currentMessage.playSpeechAudio}
        />
      </p>
    </div>
  );
}
