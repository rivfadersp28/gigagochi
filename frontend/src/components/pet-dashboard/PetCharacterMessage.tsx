"use client";

import type { CSSProperties, ReactNode } from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { playPetSpeechAudioSequence } from "@/lib/petSpeechAudio";
import { hapticImpact } from "@/lib/telegram";

import { finishAnimation, shouldReduceMotion } from "./motion";
import styles from "./PetCharacterMessage.module.css";

export type PetReplyMessage = {
  id: number;
  text: string;
  playSpeechAudio: boolean;
  hasNextPortion: boolean;
};

export type PetMessageLayout = {
  width: number;
  height: number;
};

const PET_REPLY_ENTER_MS = 300;
const PET_REPLY_EXIT_MS = 100;
const PET_REPLY_CROSS_EASING = "cubic-bezier(0.16, 1, 0.3, 1)";
const PET_REPLY_CROSS_EXIT_EASING = "cubic-bezier(0.2, 0, 0, 1)";
const PET_CHARACTER_RISE_DURATION_MS = 700;
const PET_CHARACTER_RISE_STAGGER_MS = 24;
const PET_CHARACTER_RISE_EASING = "cubic-bezier(0.2, 0.8, 0.2, 1)";
const PET_CONTINUATION_DOTS_DELAY_MS = -600;
const PET_MESSAGE_MAX_FONT_SIZE_PX = 26;
const PET_MESSAGE_MIN_FONT_SIZE_PX = 12;
const PET_MESSAGE_LINE_HEIGHT = 1.15;

type PetContinuationDotsStyle = CSSProperties & {
  "--pet-continuation-bounce-delay": string;
  "--pet-continuation-enter-duration": string;
  "--pet-continuation-enter-easing": string;
  "--pet-continuation-translate-y": string;
};

type PetContinuationDotUnitStyle = CSSProperties & {
  "--pet-continuation-enter-delay": string;
};

const petMessageStackStyle: CSSProperties = {
  position: "relative",
  display: "grid",
  justifyItems: "center",
  alignItems: "center",
  minWidth: 0,
  minHeight: 0,
  overflow: "visible",
  flexShrink: 0,
  width: "max-content",
  maxWidth: "270px",
  whiteSpace: "normal",
  wordBreak: "normal",
};

const petMessageLayerStyle: CSSProperties = {
  gridArea: "1 / 1",
  minWidth: 0,
  maxWidth: "100%",
  margin: 0,
  overflow: "visible",
  overflowWrap: "normal",
  textAlign: "center",
  lineHeight: PET_MESSAGE_LINE_HEIGHT,
  whiteSpace: "normal",
  wordBreak: "normal",
  transformOrigin: "center top",
};

function renderedMessageLineCount(layer: HTMLElement, fontSize: number) {
  const lineHeight = fontSize * PET_MESSAGE_LINE_HEIGHT;
  return Math.max(1, Math.ceil(layer.offsetHeight / lineHeight - 0.05));
}

function renderedMessageOverflowsHorizontally(layer: HTMLElement) {
  return layer.clientWidth > 0 && layer.scrollWidth - layer.clientWidth > 0.5;
}

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

function petMessageRevealDurationMs(text: string) {
  const unitCount = Array.from(text).filter((character) => !/^\s$/u.test(character)).length;
  return unitCount > 0
    ? PET_CHARACTER_RISE_DURATION_MS +
      (unitCount - 1) * PET_CHARACTER_RISE_STAGGER_MS +
      PET_CONTINUATION_DOTS_DELAY_MS
    : 0;
}

function PetMessageContinuationDots({
  revealDelayMs,
  translateY,
  animateEntrance = true,
}: {
  revealDelayMs: number;
  translateY: number;
  animateEntrance?: boolean;
}) {
  const style: PetContinuationDotsStyle = {
    "--pet-continuation-bounce-delay": `${
      animateEntrance ? revealDelayMs : 0
    }ms`,
    "--pet-continuation-enter-duration": `${PET_CHARACTER_RISE_DURATION_MS}ms`,
    "--pet-continuation-enter-easing": PET_CHARACTER_RISE_EASING,
    "--pet-continuation-translate-y": `${translateY}px`,
  };

  return (
    <span
      className={styles.continuationDots}
      data-pet-message-continuation="true"
      data-pet-message-continuation-delay={revealDelayMs}
      style={style}
      aria-hidden="true"
    >
      {Array.from({ length: 3 }, (_, index) => {
        const enterDelayMs = animateEntrance
          ? revealDelayMs + index * PET_CHARACTER_RISE_STAGGER_MS
          : 0;
        const unitStyle: PetContinuationDotUnitStyle = {
          "--pet-continuation-enter-delay": `${enterDelayMs}ms`,
        };

        return (
          <span
            key={index}
            className={`${styles.continuationDotUnit} ${
              animateEntrance ? "" : styles.continuationDotUnitEntered
            }`}
            data-pet-message-continuation-enter-delay={enterDelayMs}
            style={unitStyle}
          >
            <span
              className={styles.continuationDot}
              data-pet-message-continuation-dot={index + 1}
            />
          </span>
        );
      })}
    </span>
  );
}

function renderPetMessageUnits(
  text: string,
  translateY: number,
  showContinuationDots: boolean,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  let unitIndex = 0;
  const lines = text.split("\n");
  let finalWordKey: string | null = null;

  if (showContinuationDots) {
    lines.forEach((line, lineIndex) => {
      line.split(/(\s+)/).forEach((part, partIndex) => {
        if (part && !/^\s+$/.test(part)) {
          finalWordKey = `${lineIndex}-${partIndex}`;
        }
      });
    });
  }

  lines.forEach((line, lineIndex) => {
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
          {finalWordKey === `${lineIndex}-${partIndex}` ? (
            <PetMessageContinuationDots
              revealDelayMs={petMessageRevealDurationMs(text)}
              translateY={translateY}
            />
          ) : null}
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
  showContinuationDots,
}: {
  text: string;
  translateY: number;
  speechEndTrimMs: number;
  playSpeechAudio: boolean;
  showContinuationDots: boolean;
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
    const hapticTimeoutIds = units.map((_, index) =>
      window.setTimeout(
        () => hapticImpact("light"),
        index * PET_CHARACTER_RISE_STAGGER_MS,
      ),
    );

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
      hapticTimeoutIds.forEach((timeoutId) => window.clearTimeout(timeoutId));
      animations.forEach((animation) => animation.cancel());
    };
  }, [playSpeechAudio, speechEndTrimMs, text, translateY]);

  return (
    <span ref={textRef} aria-hidden="true">
      {renderPetMessageUnits(text, translateY, showContinuationDots)}
    </span>
  );
}

export function PetCharacterMessage({
  message,
  textTranslateY,
  speechEndTrimMs,
  onCurrentMessageLayout,
  maxCurrentMessageLines,
  minCurrentMessageFontSize = PET_MESSAGE_MIN_FONT_SIZE_PX,
}: {
  message: PetReplyMessage;
  textTranslateY: number;
  speechEndTrimMs: number;
  onCurrentMessageLayout?: (layout: PetMessageLayout) => void;
  maxCurrentMessageLines?: number;
  minCurrentMessageFontSize?: number;
}) {
  const [currentMessage, setCurrentMessage] = useState(message);
  const [previousMessage, setPreviousMessage] = useState<PetReplyMessage | null>(null);
  const [previousMessageSize, setPreviousMessageSize] = useState<PetMessageLayout | null>(null);
  const [currentFontSize, setCurrentFontSize] = useState(PET_MESSAGE_MAX_FONT_SIZE_PX);
  const [previousFontSize, setPreviousFontSize] = useState<number | null>(null);
  const currentMessageRef = useRef(message);
  const currentLayerRef = useRef<HTMLParagraphElement>(null);
  const previousLayerRef = useRef<HTMLParagraphElement>(null);

  useLayoutEffect(() => {
    if (message.id === currentMessageRef.current.id) {
      return;
    }

    const outgoingMessage = currentMessageRef.current;
    const outgoingLayer = currentLayerRef.current;
    const outgoingSize = outgoingLayer
      ? { width: outgoingLayer.offsetWidth, height: outgoingLayer.offsetHeight }
      : null;
    const outgoingFontSize = currentFontSize;
    currentMessageRef.current = message;

    setPreviousMessage(outgoingMessage);
    setPreviousMessageSize(outgoingSize);
    setPreviousFontSize(outgoingFontSize);
    setCurrentMessage(message);
    setCurrentFontSize(PET_MESSAGE_MAX_FONT_SIZE_PX);

    const timeoutId = window.setTimeout(() => {
      setPreviousMessage((storedMessage) => {
        if (storedMessage?.id !== outgoingMessage.id) {
          return storedMessage;
        }
        setPreviousMessageSize(null);
        setPreviousFontSize(null);
        return null;
      });
    }, PET_REPLY_EXIT_MS);

    return () => window.clearTimeout(timeoutId);
  }, [currentFontSize, message]);

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
        duration: PET_REPLY_ENTER_MS,
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

  useLayoutEffect(() => {
    const currentLayer = currentLayerRef.current;
    if (!currentLayer || !maxCurrentMessageLines) {
      return;
    }

    const minimumFontSize = Math.max(
      PET_MESSAGE_MIN_FONT_SIZE_PX,
      Math.min(PET_MESSAGE_MAX_FONT_SIZE_PX, minCurrentMessageFontSize),
    );
    let fittedFontSize = PET_MESSAGE_MAX_FONT_SIZE_PX;
    currentLayer.style.fontSize = `${fittedFontSize}px`;

    while (
      fittedFontSize > minimumFontSize &&
      (renderedMessageLineCount(currentLayer, fittedFontSize) > maxCurrentMessageLines ||
        renderedMessageOverflowsHorizontally(currentLayer))
    ) {
      fittedFontSize = Math.max(minimumFontSize, fittedFontSize - 1);
      currentLayer.style.fontSize = `${fittedFontSize}px`;
    }

    setCurrentFontSize((storedFontSize) =>
      storedFontSize === fittedFontSize ? storedFontSize : fittedFontSize,
    );
  }, [currentMessage.id, maxCurrentMessageLines, minCurrentMessageFontSize]);

  useLayoutEffect(() => {
    const currentLayer = currentLayerRef.current;
    if (!currentLayer || !onCurrentMessageLayout) {
      return;
    }

    const reportLayout = (layout: PetMessageLayout) => onCurrentMessageLayout(layout);
    reportLayout({ width: currentLayer.offsetWidth, height: currentLayer.offsetHeight });

    if (typeof ResizeObserver !== "function") {
      return;
    }

    const observer = new ResizeObserver(([entry]) => {
      if (entry) {
        reportLayout({ width: entry.contentRect.width, height: entry.contentRect.height });
      }
    });
    observer.observe(currentLayer);
    return () => observer.disconnect();
  }, [currentMessage.id, onCurrentMessageLayout]);

  return (
    <div
      className="main-speech-bubble__message text-center font-bold leading-[normal]"
      style={petMessageStackStyle}
      aria-live="polite"
    >
      {previousMessage ? (
        <p
          key={`previous-${previousMessage.id}`}
          ref={previousLayerRef}
          style={{
            ...petMessageLayerStyle,
            position: "absolute",
            top: "50%",
            left: "50%",
            zIndex: 2,
            width: previousMessageSize ? `${previousMessageSize.width}px` : "max-content",
            height: previousMessageSize ? `${previousMessageSize.height}px` : "auto",
            maxWidth: "none",
            fontSize: `${previousFontSize ?? PET_MESSAGE_MAX_FONT_SIZE_PX}px`,
            translate: "-50% -50%",
            opacity: 1,
            transform: "scale(1)",
            transformOrigin: "center center",
          }}
          aria-hidden="true"
        >
          {previousMessage.text}
          {previousMessage.hasNextPortion ? (
            <PetMessageContinuationDots
              revealDelayMs={0}
              translateY={0}
              animateEntrance={false}
            />
          ) : null}
        </p>
      ) : null}
      <p
        key={`current-${currentMessage.id}`}
        ref={currentLayerRef}
        style={{
          ...petMessageLayerStyle,
          position: "relative",
          zIndex: 1,
          fontSize: `${currentFontSize}px`,
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
          showContinuationDots={currentMessage.hasNextPortion}
        />
      </p>
    </div>
  );
}
