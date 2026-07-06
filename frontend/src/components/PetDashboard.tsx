"use client";

/* eslint-disable @next/next/no-img-element */
import { Bug, Loader2, Settings, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useReward, type AnimationConfig } from "partycles";
import { flushSync } from "react-dom";
import type { CSSProperties, FormEvent, MouseEvent, ReactNode, RefObject } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  consolidateLocalUserMemory,
  extractLocalLiteFacts,
  extractLocalUserMemory,
  generatePetTravel,
  generateLocalProactiveMessage,
  sendLocalChatMessage,
} from "@/lib/api";
import {
  appendLocalChatMessages,
  consumePendingMainPetReply,
  createLocalId,
  latestChatMessages,
  readLocalChatHistory,
  readLocalPetSettings,
  writeLocalPetSettings,
} from "@/lib/localPetStorage";
import {
  buildDailyProactiveMemoryContext,
  buildMemoryContextForMessage,
} from "@/lib/localPetMemoryRecall";
import {
  applyMemoryConsolidationOperations,
  applyMemoryOperations,
  markMemoryContextUsed,
  readLocalPetMemory,
  recordProactiveDelivery,
  shouldRunDailyConsolidation,
  writeLocalPetMemory,
} from "@/lib/localPetMemoryStorage";
import { playPetSpeechAudioSequence, primePetSpeechAudio } from "@/lib/petSpeechAudio";
import {
  recordLiteOverlayPatchDebug,
  recordMemoryConsolidationDebug,
  recordMemoryContextDebug,
  recordMemoryOperationsDebug,
  recordReplyPromptDebug,
} from "@/lib/debugPanelStorage";
import { CHAT_RETURN_PET_REPLY } from "@/lib/petReplyPrompts";
import { logBrowserPromptDebug } from "@/lib/promptDebug";
import { hapticNotification, useTelegramBackButton } from "@/lib/telegram";
import type {
  GenerateTravelResponse,
  LocalPetState,
  PetLifeStage,
  PetMood,
} from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";

import { DebugPanel } from "./DebugPanel";

type PetDashboardProps = {
  petId: string;
};

type PetStage = PetLifeStage;
type PetState = PetMood;

type IdleAnimationSettings = {
  speed: number;
  maxScale: number;
  originX: number;
  originY: number;
  rotationSpeed: number;
  maxRotation: number;
  textTranslateY: number;
  speechEndTrimMs: number;
};

type IdleStretchStyle = CSSProperties & {
  "--pet-idle-duration": string;
  "--pet-idle-max-scale": number;
};

type IdleRotationStyle = CSSProperties & {
  "--pet-idle-rotation-duration": string;
  "--pet-idle-max-rotation": string;
};

type PetTapTargetStyle = CSSProperties & {
  "--pet-tap-scale-duration": string;
  "--pet-tap-scale-easing": string;
};

type ConversationSceneStyle = CSSProperties & {
  "--conversation-visible-height": string;
};

type SpeechBubbleSize = {
  width: number;
  height: number;
};

type PetReplyMessage = {
  id: number;
  text: string;
  playSpeechAudio: boolean;
};

type ShowPetReplyOptions = {
  showInConversation?: boolean;
};

type SelectedSprite = {
  stage: PetStage;
  state: PetState;
};

const IDLE_ANIMATION_BASE_DURATION_SECONDS = 2.4;
const IDLE_ROTATION_BASE_DURATION_SECONDS = 3.2;
const PET_REPLY_EXIT_MS = 280;
const PET_REPLY_CROSS_EASING = "cubic-bezier(0.16, 1, 0.3, 1)";
const PET_REPLY_CROSS_EXIT_EASING = "cubic-bezier(0.55, 0, 1, 0.45)";
const PET_CHARACTER_RISE_DURATION_MS = 700;
const PET_CHARACTER_RISE_STAGGER_MS = 24;
const PET_CHARACTER_RISE_EASING = "cubic-bezier(0.2, 0.8, 0.2, 1)";
const PET_TAP_SCALE_DURATION_MS = 500;
const PET_TAP_SCALE_EASING = "ease-in-out";
const PET_TAP_TRANSFORM_ORIGIN = "50% 50%";
const DEFAULT_TEXT_TRANSLATE_Y_PX = 12;
const DEFAULT_SPEECH_END_TRIM_MS = 625;
const FIGMA_SCREEN_HEIGHT = 874;
const FIGMA_KEYBOARD_VISIBLE_HEIGHT = 542;
const KEYBOARD_EAGER_SYNC_MS = 750;
const INITIAL_PET_DAY_PROMPT = "Расскажи о своем дне";
const INITIAL_PET_REPLY_MAX_CHARS = 40;
const INITIAL_PET_REPLY_FALLBACK = "День был ярким.";
const DASHBOARD_CHAT_REPLY_MAX_CHARS = 120;
const HARDCODED_STATUS_NAME = "Челепиздрик";
const HARDCODED_STATUS_LEVEL = "Уровень: 2";
const SPEECH_BUBBLE_MIN_WIDTH = 190;
const SPEECH_BUBBLE_MIN_HEIGHT = 86;
const SPEECH_BUBBLE_RADIUS = 43;
const SPEECH_BUBBLE_TAIL_HEIGHT = 21;
const SPEECH_BUBBLE_TAIL_HALF_WIDTH = 22;
const ACTION_ICON_CACHE_VERSION = "20260706-action-colors-4";
const TOP_STATUS_ASSET_CACHE_VERSION = "20260706-top-status-colors-2";
const MAIN_PET_BACKDROP_CACHE_VERSION = "20260706-main-pet-bg-1";
const mainStomachSrc = `/figma/main-stomach.svg?v=${TOP_STATUS_ASSET_CACHE_VERSION}`;
const mainPetBackdropSrc = `/figma/main-pet-bg.png?v=${MAIN_PET_BACKDROP_CACHE_VERSION}`;
const actionIconSrc = {
  chat: `/figma/action-chat-icon.svg?v=${ACTION_ICON_CACHE_VERSION}`,
  feed: `/figma/action-feed-icon.svg?v=${ACTION_ICON_CACHE_VERSION}`,
  travel: `/figma/action-travel-icon.svg?v=${ACTION_ICON_CACHE_VERSION}`,
} as const;

const mainActionButtonGlassStyle = {
  height: "58.203px",
  padding: "14px 17px 16px 13px",
  borderRadius: "24px",
  backgroundColor: "rgba(41, 41, 41, 0.4)",
  color: "#ffffff",
  boxShadow: "inset 0 2px 4px rgba(255, 255, 255, 0.09)",
  backdropFilter: "blur(20px)",
  WebkitBackdropFilter: "blur(20px)",
  lineHeight: "normal",
} satisfies CSSProperties;

const mainActionButtonStyle = {
  chat: { ...mainActionButtonGlassStyle, width: 192 },
  feed: { ...mainActionButtonGlassStyle, width: 198 },
  travel: { ...mainActionButtonGlassStyle, width: 241 },
} satisfies Record<"chat" | "feed" | "travel", CSSProperties>;

const mainScreenStyle = {
  backgroundColor: "#000000",
} satisfies CSSProperties;

const speechBubbleGlassStyle = {
  backgroundColor: "rgba(41, 41, 41, 0.4)",
  boxShadow: "inset 0 2px 4px rgba(255, 255, 255, 0.09)",
  backdropFilter: "blur(20px)",
  WebkitBackdropFilter: "blur(20px)",
} satisfies CSSProperties;

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

function buildSpeechBubbleClipPath({ width, height }: SpeechBubbleSize) {
  const w = Math.max(SPEECH_BUBBLE_MIN_WIDTH, width);
  const h = Math.max(SPEECH_BUBBLE_MIN_HEIGHT, height);
  const r = Math.min(SPEECH_BUBBLE_RADIUS, w / 2, h / 2);
  const cx = w / 2;
  const tailBottom = h + SPEECH_BUBBLE_TAIL_HEIGHT;
  const tailShoulder = SPEECH_BUBBLE_TAIL_HALF_WIDTH;
  const tailNeck = 11;
  const tailPoint = 4;

  return [
    `M ${r} 0`,
    `H ${w - r}`,
    `C ${w - r / 2} 0 ${w} ${r / 2} ${w} ${r}`,
    `V ${h - r}`,
    `C ${w} ${h - r / 2} ${w - r / 2} ${h} ${w - r} ${h}`,
    `H ${cx + tailShoulder}`,
    `C ${cx + tailNeck} ${h} ${cx + 8} ${h + 7} ${cx + 5} ${h + 14}`,
    `C ${cx + tailPoint} ${h + 17} ${cx + 2} ${tailBottom} ${cx} ${tailBottom}`,
    `C ${cx - 2} ${tailBottom} ${cx - tailPoint} ${h + 17} ${cx - 5} ${h + 14}`,
    `C ${cx - 8} ${h + 7} ${cx - tailNeck} ${h} ${cx - tailShoulder} ${h}`,
    `H ${r}`,
    `C ${r / 2} ${h} 0 ${h - r / 2} 0 ${h - r}`,
    `V ${r}`,
    `C 0 ${r / 2} ${r / 2} 0 ${r} 0`,
    "Z",
  ].join(" ");
}

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

const defaultIdleAnimationSettings: IdleAnimationSettings = {
  speed: 1.5,
  maxScale: 1.12,
  originX: 62,
  originY: 100,
  rotationSpeed: 1.2,
  maxRotation: 5,
  textTranslateY: DEFAULT_TEXT_TRANSLATE_Y_PX,
  speechEndTrimMs: DEFAULT_SPEECH_END_TRIM_MS,
};

const stageLabels: Record<PetStage, string> = {
  baby: "Baby",
  teen: "Teen",
  adult: "Adult",
};

const stateLabels: Record<PetState, string> = {
  idle: "Idle",
  happy: "Happy",
  sad: "Sad",
  hungry: "Hungry",
};

const spriteStageOptions = [
  { value: "baby", label: "Малой" },
  { value: "teen", label: "Подросток" },
  { value: "adult", label: "Взрослый" },
] satisfies Array<{ value: PetStage; label: string }>;

const spriteStateOptions = [
  { value: "idle", label: "Норм" },
  { value: "hungry", label: "Голодный" },
  { value: "sad", label: "Грустный" },
  { value: "happy", label: "Счастливый" },
] satisfies Array<{ value: PetState; label: string }>;

function shouldReduceMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function estimatedKeyboardVisibleHeight(fullHeight: number) {
  const ratio = FIGMA_KEYBOARD_VISIBLE_HEIGHT / FIGMA_SCREEN_HEIGHT;
  return Math.max(320, Math.min(fullHeight, Math.round(fullHeight * ratio)));
}

function shouldEagerlyUseKeyboardLayout() {
  return (
    window.innerWidth <= 480 ||
    navigator.maxTouchPoints > 0 ||
    window.matchMedia("(pointer: coarse)").matches
  );
}

function generatedSpriteUrl(pet: LocalPetState, stage: PetStage, state: PetState) {
  return pet.assetSet?.images[stage]?.[state] || null;
}

function finishAnimation(element: HTMLElement, finalStyles: Partial<CSSStyleDeclaration>) {
  Object.assign(element.style, finalStyles);
}

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

function MainPetBackdrop({ src }: { src: string }) {
  return (
    <div className="main-pet-backdrop" aria-hidden="true">
      <img src={src} alt="" draggable={false} />
    </div>
  );
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

function PetCharacterMessage({
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

type IdleAnimationControlsProps = {
  isOpen: boolean;
  pet: LocalPetState;
  selectedStage: PetStage;
  selectedState: PetState;
  settings: IdleAnimationSettings;
  includePromptDebug: boolean;
  onChange: (nextSettings: IdleAnimationSettings) => void;
  onChangeIncludePromptDebug: (enabled: boolean) => void;
  onSelectStage: (stage: PetStage) => void;
  onSelectState: (state: PetState) => void;
  onResetSprite: () => void;
  onToggle: () => void;
};

function IdleAnimationControls({
  isOpen,
  pet,
  selectedStage,
  selectedState,
  settings,
  includePromptDebug,
  onChange,
  onChangeIncludePromptDebug,
  onSelectStage,
  onSelectState,
  onResetSprite,
  onToggle,
}: IdleAnimationControlsProps) {
  const sliders = [
    {
      key: "speed",
      label: "Скорость Y",
      min: 0.2,
      max: 3,
      step: 0.1,
      value: settings.speed,
      displayValue: `${settings.speed.toFixed(1)}x`,
    },
    {
      key: "maxScale",
      label: "Max scale Y",
      min: 1,
      max: 1.5,
      step: 0.01,
      value: settings.maxScale,
      displayValue: settings.maxScale.toFixed(2),
    },
    {
      key: "originX",
      label: "Origin X",
      min: 0,
      max: 100,
      step: 1,
      value: settings.originX,
      displayValue: `${settings.originX}%`,
    },
    {
      key: "originY",
      label: "Origin Y",
      min: 0,
      max: 100,
      step: 1,
      value: settings.originY,
      displayValue: `${settings.originY}%`,
    },
    {
      key: "rotationSpeed",
      label: "Скорость поворота",
      min: 0.2,
      max: 3,
      step: 0.1,
      value: settings.rotationSpeed,
      displayValue: `${settings.rotationSpeed.toFixed(1)}x`,
    },
    {
      key: "maxRotation",
      label: "Max angle",
      min: 0,
      max: 30,
      step: 1,
      value: settings.maxRotation,
      displayValue: `${settings.maxRotation}deg`,
    },
    {
      key: "textTranslateY",
      label: "Text translateY",
      min: 0,
      max: 80,
      step: 1,
      value: settings.textTranslateY,
      displayValue: `${settings.textTranslateY}px`,
    },
    {
      key: "speechEndTrimMs",
      label: "Обрезка звука",
      min: 0,
      max: 1500,
      step: 25,
      value: settings.speechEndTrimMs,
      displayValue: `${settings.speechEndTrimMs}ms`,
    },
  ] satisfies Array<{
    key: keyof IdleAnimationSettings;
    label: string;
    min: number;
    max: number;
    step: number;
    value: number;
    displayValue: string;
  }>;

  return (
    <>
      <button
        type="button"
        aria-controls="animation-controls"
        aria-expanded={isOpen}
        aria-label={isOpen ? "Скрыть настройки анимации" : "Показать настройки анимации"}
        onClick={onToggle}
        className="fixed left-5 top-5 z-40 grid size-[42px] place-items-center rounded-full border border-black/10 bg-[#f3f3f3] text-black/45 shadow-[0_8px_22px_rgba(0,0,0,0.08)] transition-colors hover:bg-[#e9e9e9] hover:text-black/60 focus:outline-none focus:ring-2 focus:ring-black/15"
      >
        <Settings className="size-5" aria-hidden="true" />
      </button>

      {isOpen ? (
        <div
          id="animation-controls"
          className="fixed left-5 top-[74px] z-30 max-h-[calc(var(--tma-viewport-height)-94px)] w-[282px] overflow-y-auto rounded-[8px] border border-black/10 bg-white/95 p-4 text-[13px] leading-none text-black shadow-[0_12px_30px_rgba(0,0,0,0.08)]"
          aria-label="Настройки питомца"
        >
          <div className="mb-4 flex items-center justify-between gap-3">
            <div className="text-[14px] font-medium leading-none">Preview</div>
            <button
              type="button"
              onClick={onResetSprite}
              className="h-[24px] text-[12px] leading-none text-black/45 transition-colors hover:text-black/70 focus:outline-none focus:ring-2 focus:ring-black/10"
            >
              Auto
            </button>
          </div>

          <div className="mb-5 grid gap-4 border-b border-black/10 pb-5">
            <div role="group" aria-label="Возраст персонажа" className="grid gap-2">
              <div className="text-[12px] leading-none text-black/45">Возраст</div>
              <div className="grid grid-cols-3 gap-1">
                {spriteStageOptions.map((option) => {
                  const isSelected = option.value === selectedStage;
                  const isAvailable = Boolean(generatedSpriteUrl(pet, option.value, selectedState));

                  return (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={isSelected}
                      disabled={!isAvailable}
                      onClick={() => onSelectStage(option.value)}
                      className="h-[32px] rounded-[8px] border border-black/10 px-2 text-[12px] leading-none transition-colors focus:outline-none focus:ring-2 focus:ring-black/10 disabled:cursor-not-allowed disabled:opacity-35"
                      style={{
                        backgroundColor: isSelected ? "rgba(0,0,0,0.86)" : "rgba(0,0,0,0.035)",
                        color: isSelected ? "white" : "rgba(0,0,0,0.62)",
                      }}
                    >
                      {option.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div role="group" aria-label="Состояние персонажа" className="grid gap-2">
              <div className="text-[12px] leading-none text-black/45">Состояние</div>
              <div className="grid grid-cols-2 gap-1">
                {spriteStateOptions.map((option) => {
                  const isSelected = option.value === selectedState;
                  const isAvailable = Boolean(generatedSpriteUrl(pet, selectedStage, option.value));

                  return (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={isSelected}
                      disabled={!isAvailable}
                      onClick={() => onSelectState(option.value)}
                      className="h-[32px] rounded-[8px] border border-black/10 px-2 text-[12px] leading-none transition-colors focus:outline-none focus:ring-2 focus:ring-black/10 disabled:cursor-not-allowed disabled:opacity-35"
                      style={{
                        backgroundColor: isSelected ? "rgba(0,0,0,0.86)" : "rgba(0,0,0,0.035)",
                        color: isSelected ? "white" : "rgba(0,0,0,0.62)",
                      }}
                    >
                      {option.label}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="mb-5 grid gap-3 border-b border-black/10 pb-5">
            <label className="flex min-h-[30px] items-center gap-2 rounded-[8px] bg-black/[0.035] px-2 text-[11px] leading-[13px] text-black/65">
              <input
                type="checkbox"
                checked={includePromptDebug}
                onChange={(event) => onChangeIncludePromptDebug(event.currentTarget.checked)}
                className="size-3 accent-black"
              />
              <span>Debug prompts</span>
            </label>
          </div>

          <div className="mb-4 text-[14px] font-medium leading-none">Animation</div>
          <div className="flex flex-col gap-4">
            {sliders.map((slider) => (
              <label key={slider.key} className="grid gap-2">
                <span className="flex items-center justify-between">
                  <span>{slider.label}</span>
                  <span className="text-black/45">{slider.displayValue}</span>
                </span>
                <input
                  type="range"
                  min={slider.min}
                  max={slider.max}
                  step={slider.step}
                  value={slider.value}
                  onChange={(event) =>
                    onChange({ ...settings, [slider.key]: Number(event.currentTarget.value) })
                  }
                  className="h-5 w-full accent-black"
                />
              </label>
            ))}
          </div>
        </div>
      ) : null}
    </>
  );
}

type TravelSceneCardStyle = CSSProperties & {
  "--travel-card-rotation": string;
  "--travel-card-offset-x": string;
};

type TravelStoryOverlayProps = {
  result: GenerateTravelResponse;
  onClose: () => void;
};

const TRAVEL_CARD_ROTATIONS = [5, -5, 4, -4, 3, -3, 5] as const;

function TravelStoryOverlay({ result, onClose }: TravelStoryOverlayProps) {
  const imagesBySceneIndex = new Map(
    result.images.map((image) => [image.sceneIndex, image.imageUrl]),
  );
  const visibleScenes = result.story.scenes
    .map((scene) => ({
      scene,
      imageUrl: imagesBySceneIndex.get(scene.index),
    }))
    .filter((item): item is { scene: typeof item.scene; imageUrl: string } =>
      Boolean(item.imageUrl),
    );

  return (
    <section className="travel-story-overlay" aria-label={result.story.title}>
      <div className="travel-story-phone">
        <button
          type="button"
          className="travel-story-close"
          aria-label="Закрыть путешествие"
          onClick={onClose}
        >
          <X className="size-[22px]" aria-hidden="true" />
        </button>

        <div className="travel-story-stack">
          {visibleScenes.map(({ scene, imageUrl }, index) => {
            const slotStyle: TravelSceneCardStyle = {
              "--travel-card-rotation": `${
                TRAVEL_CARD_ROTATIONS[index % TRAVEL_CARD_ROTATIONS.length]
              }deg`,
              "--travel-card-offset-x": index % 2 === 0 ? "17.08px" : "28.02px",
            };

            return (
              <article
                key={`${result.travelId}-${scene.index}`}
                className="travel-scene-card-slot"
                style={slotStyle}
                aria-label={`${scene.title}. ${scene.text}`}
              >
                <div className="travel-scene-card">
                  <img src={imageUrl} alt={scene.title} draggable={false} />
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}

export function PetDashboard({ petId }: PetDashboardProps) {
  const router = useRouter();
  const localPet = useLocalPetState();
  const [isFeeding, setIsFeeding] = useState(false);
  const [isIdleControlsOpen, setIsIdleControlsOpen] = useState(false);
  const [isDebugPanelOpen, setIsDebugPanelOpen] = useState(false);
  const [isChatMode, setIsChatMode] = useState(false);
  const [isKeyboardRaised, setIsKeyboardRaised] = useState(false);
  const [conversationVisibleHeight, setConversationVisibleHeight] = useState(874);
  const [chatInput, setChatInput] = useState("");
  const [chatError, setChatError] = useState<string | null>(null);
  const [isSendingChat, setIsSendingChat] = useState(false);
  const [isTravelGenerating, setIsTravelGenerating] = useState(false);
  const [travelResult, setTravelResult] = useState<GenerateTravelResponse | null>(null);
  const [travelError, setTravelError] = useState<string | null>(null);
  const [conversationReplyMessageId, setConversationReplyMessageId] = useState<number | null>(null);
  const [petTapPulseId, setPetTapPulseId] = useState(0);
  const [selectedSprite, setSelectedSprite] = useState<SelectedSprite | null>(null);
  const [idleAnimationSettings, setIdleAnimationSettings] = useState<IdleAnimationSettings>(
    defaultIdleAnimationSettings,
  );
  const [promptSettings, setPromptSettings] = useState(() => readLocalPetSettings());
  const [petReplyMessage, setPetReplyMessage] = useState<PetReplyMessage | null>(null);
  const [speechBubbleSize, setSpeechBubbleSize] = useState<SpeechBubbleSize>({
    width: SPEECH_BUBBLE_MIN_WIDTH,
    height: SPEECH_BUBBLE_MIN_HEIGHT,
  });
  const proactiveAttemptedRef = useRef(false);
  const petReplyMessageRef = useRef<PetReplyMessage | null>(null);
  const speechBubbleRef = useRef<HTMLDivElement>(null);
  const chatInputRef = useRef<HTMLInputElement>(null);
  const petTapTargetRef = useRef<HTMLButtonElement>(null);
  const isSendingChatRef = useRef(false);
  const isTravelGeneratingRef = useRef(false);
  const conversationFullHeightRef = useRef(0);
  const keyboardSyncUntilRef = useRef(0);
  const pet = localPet.pet;
  const includePromptDebug = promptSettings.includePromptDebug;
  const { replay: replayPetTapParticles } = useReward(
    petTapTargetRef as RefObject<HTMLElement>,
    "bubbles",
    petTapParticleConfig,
  );

  const showPetReplyMessage = useCallback((
    text: string,
    playSpeechAudio: boolean,
    options: ShowPetReplyOptions = {},
  ) => {
    const nextMessage = {
      id: (petReplyMessageRef.current?.id ?? 0) + 1,
      text,
      playSpeechAudio,
    };
    petReplyMessageRef.current = nextMessage;
    setPetReplyMessage(nextMessage);
    if (options.showInConversation) {
      setConversationReplyMessageId(nextMessage.id);
    }
  }, []);

  const closeChatMode = useCallback(() => {
    keyboardSyncUntilRef.current = 0;
    setIsChatMode(false);
    setIsKeyboardRaised(false);
    setChatError(null);
    setConversationReplyMessageId(null);
    showPetReplyMessage(CHAT_RETURN_PET_REPLY, true);
    chatInputRef.current?.blur();
  }, [showPetReplyMessage]);

  useTelegramBackButton(closeChatMode, isChatMode);

  const handlePetTap = useCallback(() => {
    if (shouldReduceMotion()) {
      return;
    }
    void replayPetTapParticles();
    setPetTapPulseId((currentId) => currentId + 1);
  }, [replayPetTapParticles]);

  useEffect(() => {
    const node = speechBubbleRef.current;
    if (!node) {
      return undefined;
    }

    const updateSpeechBubbleSize = () => {
      const rect = node.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        return;
      }

      const nextSize = {
        width: Math.round(rect.width * 2) / 2,
        height: Math.round(rect.height * 2) / 2,
      };

      setSpeechBubbleSize((currentSize) => {
        if (
          Math.abs(currentSize.width - nextSize.width) < 0.5 &&
          Math.abs(currentSize.height - nextSize.height) < 0.5
        ) {
          return currentSize;
        }

        return nextSize;
      });
    };

    updateSpeechBubbleSize();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", updateSpeechBubbleSize);
      return () => window.removeEventListener("resize", updateSpeechBubbleSize);
    }

    const resizeObserver = new ResizeObserver(updateSpeechBubbleSize);
    resizeObserver.observe(node);

    return () => resizeObserver.disconnect();
  }, [localPet.status]);

  useEffect(() => {
    petReplyMessageRef.current = petReplyMessage;
  }, [petReplyMessage]);

  useEffect(() => {
    if (!isChatMode) {
      return;
    }

    const baseHeight = conversationFullHeightRef.current || window.innerHeight || FIGMA_SCREEN_HEIGHT;
    const estimatedVisibleHeight = estimatedKeyboardVisibleHeight(baseHeight);

    function updateKeyboardState() {
      const visualViewport = window.visualViewport;
      const visibleHeight = visualViewport
        ? visualViewport.height + visualViewport.offsetTop
        : window.innerHeight;
      const roundedVisibleHeight = Math.round(visibleHeight);
      const keyboardIsVisible = baseHeight - visibleHeight > 120;
      const shouldHoldKeyboardLayout = performance.now() < keyboardSyncUntilRef.current;

      if (keyboardIsVisible) {
        setConversationVisibleHeight(roundedVisibleHeight);
        setIsKeyboardRaised(true);
        return;
      }

      if (shouldHoldKeyboardLayout) {
        setConversationVisibleHeight((currentHeight) =>
          currentHeight >= baseHeight - 1 ? estimatedVisibleHeight : currentHeight,
        );
        setIsKeyboardRaised(true);
        return;
      }

      setConversationVisibleHeight(roundedVisibleHeight);
      setIsKeyboardRaised(false);
    }

    updateKeyboardState();
    const syncTimeoutId = window.setTimeout(
      updateKeyboardState,
      KEYBOARD_EAGER_SYNC_MS + 40,
    );
    window.addEventListener("resize", updateKeyboardState);
    window.visualViewport?.addEventListener("resize", updateKeyboardState);
    window.visualViewport?.addEventListener("scroll", updateKeyboardState);

    return () => {
      window.clearTimeout(syncTimeoutId);
      window.removeEventListener("resize", updateKeyboardState);
      window.visualViewport?.removeEventListener("resize", updateKeyboardState);
      window.visualViewport?.removeEventListener("scroll", updateKeyboardState);
    };
  }, [isChatMode]);

  useEffect(() => {
    if (localPet.status === "loading") {
      return;
    }
    if (!pet || pet.petId !== petId) {
      router.replace("/");
    }
  }, [localPet.status, pet, petId, router]);

  function handleFeed() {
    if (!pet || isFeeding) {
      return;
    }

    setIsFeeding(true);
    localPet.feed();
    window.setTimeout(() => setIsFeeding(false), 180);
  }

  async function handleTravel() {
    if (!pet || isTravelGeneratingRef.current) {
      return;
    }

    void primePetSpeechAudio();
    isTravelGeneratingRef.current = true;
    setIsTravelGenerating(true);
    setTravelError(null);
    setIsIdleControlsOpen(false);
    setIsDebugPanelOpen(false);
    showPetReplyMessage("Собираю путешествие...", false);
    const travelStatusTimeoutId = window.setTimeout(() => {
      if (isTravelGeneratingRef.current) {
        showPetReplyMessage("Рисую кадры путешествия...", false);
      }
    }, 2200);

    try {
      const response = await generatePetTravel(pet, { includeDebug: includePromptDebug });
      logBrowserPromptDebug("dashboard travel", response);
      setTravelResult(response);
      localPet.play();

      const firstSceneText = response.story.scenes[0]?.text.trim();
      showPetReplyMessage(firstSceneText || "Я вернулся с теплой находкой!", true);
    } catch (caught) {
      const message =
        caught instanceof ApiError ? caught.message : "Не удалось создать путешествие.";
      setTravelError(message);
      showPetReplyMessage("Путешествие не собралось. Попробуем позже.", false);
      hapticNotification("error");
    } finally {
      window.clearTimeout(travelStatusTimeoutId);
      isTravelGeneratingRef.current = false;
      setIsTravelGenerating(false);
    }
  }

  function focusChatInput() {
    chatInputRef.current?.focus({ preventScroll: true });
  }

  function dismissChatKeyboard() {
    keyboardSyncUntilRef.current = 0;
    setConversationVisibleHeight(
      conversationFullHeightRef.current || window.innerHeight || FIGMA_SCREEN_HEIGHT,
    );
    setIsKeyboardRaised(false);
    chatInputRef.current?.blur();
  }

  function handleOpenChatMode() {
    setIsIdleControlsOpen(false);
    setIsDebugPanelOpen(false);
    setChatError(null);
    setConversationReplyMessageId(null);
    const fullHeight = window.innerHeight || FIGMA_SCREEN_HEIGHT;
    const shouldSyncKeyboard = shouldEagerlyUseKeyboardLayout();
    conversationFullHeightRef.current = fullHeight;
    keyboardSyncUntilRef.current = shouldSyncKeyboard
      ? performance.now() + KEYBOARD_EAGER_SYNC_MS
      : 0;

    flushSync(() => {
      setConversationVisibleHeight(
        shouldSyncKeyboard ? estimatedKeyboardVisibleHeight(fullHeight) : fullHeight,
      );
      setIsKeyboardRaised(shouldSyncKeyboard);
      setIsChatMode(true);
    });
    focusChatInput();
  }

  async function submitChatMessage(options: { dismissKeyboard?: boolean } = {}) {
    const message = chatInput.trim().slice(0, 1000);
    if (!message || isSendingChatRef.current || !pet) {
      if (options.dismissKeyboard) {
        dismissChatKeyboard();
      } else {
        focusChatInput();
      }
      return;
    }

    void primePetSpeechAudio();
    if (options.dismissKeyboard) {
      dismissChatKeyboard();
    } else {
      keyboardSyncUntilRef.current = performance.now() + KEYBOARD_EAGER_SYNC_MS;
      setIsKeyboardRaised(true);
    }
    setChatInput("");
    setChatError(null);
    setConversationReplyMessageId(null);
    isSendingChatRef.current = true;
    setIsSendingChat(true);

    const userMessage = {
      id: createLocalId("message"),
      role: "user" as const,
      text: message,
      createdAt: new Date().toISOString(),
    };
    const history = latestChatMessages(12);
    const memoryBeforeMessage = readLocalPetMemory(pet.petId);
    const memoryContext = buildMemoryContextForMessage(memoryBeforeMessage, message);

    if (includePromptDebug) {
      console.log("[memory-debug] dashboard quick chat recall context", memoryContext);
    }
    recordMemoryContextDebug(memoryContext);
    appendLocalChatMessages([userMessage]);

    try {
      const response = await sendLocalChatMessage(message, pet, history, {
        includeDebug: true,
        memoryContext,
        replyMaxChars: DASHBOARD_CHAT_REPLY_MAX_CHARS,
      });
      logBrowserPromptDebug("dashboard quick chat reply", response);
      recordReplyPromptDebug(response);

      const assistantMessage = {
        id: createLocalId("message"),
        role: "pet" as const,
        text: response.reply,
        createdAt: new Date().toISOString(),
      };
      appendLocalChatMessages([assistantMessage]);
      recordLiteOverlayPatchDebug(response.debug?.liteOverlayPatch);
      showPetReplyMessage(response.reply, true, { showInConversation: true });
      localPet.applyMoodHint(response.moodHint, response.debug?.liteOverlayPatch);

      const selectedMemoryIds = memoryContext.relevantMemories.map((item) => item.id);
      if (selectedMemoryIds.length) {
        writeLocalPetMemory(
          markMemoryContextUsed(readLocalPetMemory(pet.petId), selectedMemoryIds),
        );
      }

      void extractLocalLiteFacts(message, response.reply, pet, history, {
        includeDebug: includePromptDebug,
      })
        .then((extraction) => {
          logBrowserPromptDebug("dashboard quick chat lite fact extraction", extraction);
          if (extraction.liteOverlayPatch) {
            recordLiteOverlayPatchDebug(extraction.liteOverlayPatch);
            localPet.applyLiteOverlayPatch(extraction.liteOverlayPatch);
          }
        })
        .catch(() => undefined);

      void extractLocalUserMemory(message, response.reply, pet, history, memoryBeforeMessage, {
        includeDebug: includePromptDebug,
        memoryContext,
      })
        .then((extraction) => {
          logBrowserPromptDebug("dashboard quick chat memory extraction", extraction);
          recordMemoryOperationsDebug(extraction.operations);
          const latestMemory = readLocalPetMemory(pet.petId);
          writeLocalPetMemory(
            applyMemoryOperations(latestMemory, extraction.operations, [
              userMessage.id,
              assistantMessage.id,
            ]),
          );
        })
        .catch(() => undefined);
    } catch (caught) {
      setChatError(
        caught instanceof ApiError ? caught.message : "Не удалось отправить сообщение.",
      );
      hapticNotification("error");
    } finally {
      isSendingChatRef.current = false;
      setIsSendingChat(false);
      if (!options.dismissKeyboard) {
        window.requestAnimationFrame(() => {
          focusChatInput();
        });
      }
    }
  }

  function handleChatSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitChatMessage();
  }

  function handleSendButtonClick(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    void submitChatMessage({ dismissKeyboard: true });
  }

  function handleResetPet() {
    const confirmed = window.confirm("Сбросить персонажа и создать нового? Локальный прогресс будет удален.");
    if (!confirmed) {
      return;
    }
    setIsDebugPanelOpen(false);
    localPet.reset();
    router.push("/");
  }

  function handlePromptDebugChange(enabled: boolean) {
    const nextSettings = {
      includePromptDebug: enabled,
    };
    setPromptSettings(nextSettings);
    writeLocalPetSettings(nextSettings);
  }

  useEffect(() => {
    if (localPet.status === "loading" || !pet || pet.petId !== petId) {
      return;
    }

    const pendingReply = consumePendingMainPetReply(pet.petId);
    if (!pendingReply) {
      return;
    }

    showPetReplyMessage(pendingReply.text, true);
  }, [localPet.status, pet, petId, showPetReplyMessage]);

  useEffect(() => {
    if (localPet.status === "loading" || !pet || pet.petId !== petId) {
      return;
    }
    if (proactiveAttemptedRef.current) {
      return;
    }
    if (petReplyMessageRef.current) {
      proactiveAttemptedRef.current = true;
      return;
    }
    proactiveAttemptedRef.current = true;

    const memory = readLocalPetMemory(pet.petId);
    if (shouldRunDailyConsolidation(memory)) {
      void consolidateLocalUserMemory(memory, { includeDebug: includePromptDebug })
        .then((consolidation) => {
          logBrowserPromptDebug("dashboard memory consolidation", consolidation);
          recordMemoryConsolidationDebug(consolidation.operations);
          const latestMemory = readLocalPetMemory(pet.petId);
          writeLocalPetMemory(
            applyMemoryConsolidationOperations(latestMemory, consolidation.operations),
          );
        })
        .catch(() => undefined);
    }

    const history = readLocalChatHistory().messages;
    const proactiveMemoryContext = buildDailyProactiveMemoryContext(pet, memory, history);
    if (proactiveMemoryContext) {
      if (includePromptDebug) {
        console.log("[memory-debug] dashboard proactive candidate", proactiveMemoryContext);
      }
      recordMemoryContextDebug(proactiveMemoryContext, "Память подставлена в proactive prompt");
      void generateLocalProactiveMessage(pet, proactiveMemoryContext, { includeDebug: true })
        .then((response) => {
          if (petReplyMessageRef.current) {
            return;
          }
          logBrowserPromptDebug("dashboard proactive chat", response);
          recordReplyPromptDebug(response);
          appendLocalChatMessages([
            {
              id: createLocalId("message"),
              role: "pet",
              text: response.reply,
              createdAt: new Date().toISOString(),
            },
          ]);
          const latestMemory = readLocalPetMemory(pet.petId);
          writeLocalPetMemory(
            recordProactiveDelivery(
              latestMemory,
              proactiveMemoryContext.proactiveCandidate?.memoryIds ?? [],
              response.reply,
            ),
          );
          showPetReplyMessage(response.reply, true);
          localPet.applyMoodHint(response.moodHint);
        })
        .catch(() => undefined);
      return;
    }

    const initialMemoryContext = buildMemoryContextForMessage(memory, INITIAL_PET_DAY_PROMPT);
    if (includePromptDebug) {
      console.log("[memory-debug] dashboard initial bubble context", initialMemoryContext);
    }
    recordMemoryContextDebug(initialMemoryContext, "Память подставлена в стартовый бабл");
    void sendLocalChatMessage(INITIAL_PET_DAY_PROMPT, pet, history, {
      includeDebug: true,
      memoryContext: initialMemoryContext,
      replyMaxChars: INITIAL_PET_REPLY_MAX_CHARS,
    })
      .then((response) => {
        if (petReplyMessageRef.current) {
          return;
        }
        const rawReplyText = response.reply.trim();
        const replyText = rawReplyText || INITIAL_PET_REPLY_FALLBACK;
        logBrowserPromptDebug("dashboard initial bubble reply", response);
        recordReplyPromptDebug(response);
        recordLiteOverlayPatchDebug(response.debug?.liteOverlayPatch);
        showPetReplyMessage(replyText, Boolean(rawReplyText));
        localPet.applyMoodHint(response.moodHint, response.debug?.liteOverlayPatch);
      })
      .catch(() => {
        if (petReplyMessageRef.current) {
          return;
        }
        showPetReplyMessage(INITIAL_PET_REPLY_FALLBACK, false);
      });
  }, [includePromptDebug, localPet, pet, petId, showPetReplyMessage]);

  if (localPet.status === "loading") {
    return (
      <main className="tma-screen grid place-items-center bg-[var(--paper)] px-6">
        <Loader2 className="size-6 animate-spin text-[var(--ink-muted)]" aria-label="Loading" />
      </main>
    );
  }

  if (!pet || pet.petId !== petId) {
    return null;
  }

  const animationSettings = { ...defaultIdleAnimationSettings, ...idleAnimationSettings };
  const displayedStage = selectedSprite?.stage ?? pet.stage;
  const displayedState = selectedSprite?.state ?? pet.mood;
  const visiblePetImage =
    generatedSpriteUrl(pet, displayedStage, displayedState) ?? "/figma/main-pet.png";
  const visiblePetBackdropImage =
    visiblePetImage === "/figma/main-pet.png" ? mainPetBackdropSrc : visiblePetImage;
  const displayedReply = petReplyMessage ?? {
    id: 0,
    text: INITIAL_PET_REPLY_FALLBACK,
    playSpeechAudio: false,
  };
  const shouldShowConversationReply =
    isChatMode && conversationReplyMessageId === displayedReply.id;
  const transformOrigin = `${animationSettings.originX}% ${animationSettings.originY}%`;
  const idleStretchStyle: IdleStretchStyle = {
    "--pet-idle-duration": `${(
      IDLE_ANIMATION_BASE_DURATION_SECONDS / animationSettings.speed
    ).toFixed(2)}s`,
    "--pet-idle-max-scale": animationSettings.maxScale,
    transformOrigin,
  };
  const idleRotationStyle: IdleRotationStyle = {
    "--pet-idle-rotation-duration": `${(
      IDLE_ROTATION_BASE_DURATION_SECONDS / animationSettings.rotationSpeed
    ).toFixed(2)}s`,
    "--pet-idle-max-rotation": `${animationSettings.maxRotation}deg`,
    transformOrigin,
  };
  const petTapTargetStyle: PetTapTargetStyle = {
    "--pet-tap-scale-duration": `${PET_TAP_SCALE_DURATION_MS}ms`,
    "--pet-tap-scale-easing": PET_TAP_SCALE_EASING,
    transformOrigin: PET_TAP_TRANSFORM_ORIGIN,
  };
  const conversationSceneStyle: ConversationSceneStyle = {
    ...mainScreenStyle,
    "--conversation-visible-height": `${conversationVisibleHeight}px`,
  };
  const speechBubbleClipPath = buildSpeechBubbleClipPath(speechBubbleSize);
  const speechBubbleShapeStyle = {
    ...speechBubbleGlassStyle,
    clipPath: `path("${speechBubbleClipPath}")`,
    WebkitClipPath: `path("${speechBubbleClipPath}")`,
  } satisfies CSSProperties;

  return (
    <main className="tma-screen relative overflow-hidden bg-black text-white" style={mainScreenStyle}>
      {localPet.error ? (
        <div className="fixed left-5 right-5 top-[max(20px,calc(var(--tma-safe-top)+12px))] z-20 rounded-[8px] border border-[var(--danger-line)] bg-white px-4 py-3 text-sm text-[var(--danger)] sm:left-8 sm:right-auto sm:max-w-sm">
          {localPet.error}
        </div>
      ) : null}
      {travelError ? (
        <div className="fixed left-5 right-5 top-[max(20px,calc(var(--tma-safe-top)+12px))] z-40 rounded-[8px] border border-[var(--danger-line)] bg-white px-4 py-3 text-sm text-[var(--danger)] sm:left-8 sm:right-auto sm:max-w-sm">
          {travelError}
        </div>
      ) : null}

      <section
        className={`main-mobile-scene tma-screen relative mx-auto w-full max-w-[402px] overflow-hidden bg-black ${
          isChatMode ? "main-mobile-scene--chat" : ""
        } ${
          isKeyboardRaised ? "main-mobile-scene--keyboard" : ""
        } ${
          shouldShowConversationReply ? "main-mobile-scene--reply" : ""
        }`}
        style={conversationSceneStyle}
        aria-label="AI Tamagotchi"
      >
        <div className="conversation-appbar" aria-hidden={!isChatMode}>
          <div className="conversation-appbar__blur" aria-hidden="true" />
        </div>

        <MainPetBackdrop src={visiblePetBackdropImage} />

        <div className="hidden" aria-hidden="true">
          <IdleAnimationControls
            isOpen={isIdleControlsOpen}
            pet={pet}
            selectedStage={displayedStage}
            selectedState={displayedState}
            settings={animationSettings}
            includePromptDebug={includePromptDebug}
            onChange={setIdleAnimationSettings}
            onChangeIncludePromptDebug={handlePromptDebugChange}
            onSelectStage={(stage) =>
              setSelectedSprite((current) => ({
                stage,
                state: current?.state ?? pet.mood,
              }))
            }
            onSelectState={(state) =>
              setSelectedSprite((current) => ({
                stage: current?.stage ?? pet.stage,
                state,
              }))
            }
            onResetSprite={() => setSelectedSprite(null)}
            onToggle={() => setIsIdleControlsOpen((current) => !current)}
          />
        </div>

        <DebugPanel
          pet={pet}
          isOpen={isDebugPanelOpen}
          onClose={() => setIsDebugPanelOpen(false)}
          onResetPet={handleResetPet}
        />

        <div className="sr-only" aria-live="polite">
          Stage {stageLabels[pet.stage]}. State {stateLabels[pet.mood]}. Hunger{" "}
          {pet.stats.hunger}/100. Happiness {pet.stats.happiness}/100. Energy{" "}
          {pet.stats.energy}/100. Cleanliness {pet.stats.cleanliness}/100.
        </div>

        <div className="conversation-fade-target absolute left-[31px] top-[118px] z-20">
          <div className="text-[17px] font-[800] leading-[normal] text-white">
            {HARDCODED_STATUS_NAME}
          </div>
          <div className="mt-[2px] text-[32.073px] font-[800] leading-[normal] text-white">
            {HARDCODED_STATUS_LEVEL}
          </div>
        </div>

        <img
          src={mainStomachSrc}
          alt=""
          aria-hidden="true"
          className="conversation-fade-target absolute left-[315px] top-[118px] z-20 h-[57.933px] w-[58.601px] max-w-none"
          draggable={false}
        />

        <div
          className={`absolute left-1/2 top-[226px] z-30 -translate-x-1/2 ${
            shouldShowConversationReply
              ? "conversation-reply-bubble"
              : "conversation-fade-target"
          }`}
        >
          <div className="main-speech-bubble" ref={speechBubbleRef}>
            <span
              className="main-speech-bubble__shape"
              style={speechBubbleShapeStyle}
              aria-hidden="true"
            />
            <PetCharacterMessage
              message={displayedReply}
              textTranslateY={animationSettings.textTranslateY}
              speechEndTrimMs={animationSettings.speechEndTrimMs}
            />
          </div>
        </div>

        <div className="main-pet-stage absolute left-0 top-[277px] z-20 h-[372px] w-[402px]">
          <div className="pet-idle-rotation absolute left-0 top-0 h-[357px] w-[402px]" style={idleRotationStyle}>
            <button
              key={petTapPulseId}
              ref={petTapTargetRef}
              type="button"
              className={`pet-tap-target absolute left-0 top-[7.76%] h-[101.37%] w-full ${
                petTapPulseId > 0 ? "pet-tap-target--pulse" : ""
              }`}
              style={petTapTargetStyle}
              onClick={handlePetTap}
              aria-label="Погладить персонажа"
            >
              <img
                src={visiblePetImage}
                alt=""
                aria-hidden="true"
                className="pet-idle-y-animation h-full w-full max-w-none object-contain"
                style={idleStretchStyle}
                width={402}
                height={362}
                draggable={false}
              />
            </button>
          </div>
        </div>

        <form
          onSubmit={handleChatSubmit}
          className="conversation-input-panel"
          aria-hidden={!isChatMode}
          aria-busy={isSendingChat}
        >
          <input
            ref={chatInputRef}
            value={chatInput}
            onChange={(event) => {
              setChatInput(event.currentTarget.value);
              if (chatError) {
                setChatError(null);
              }
            }}
            maxLength={1000}
            disabled={!isChatMode || isSendingChat}
            tabIndex={isChatMode ? 0 : -1}
            className="conversation-input"
            placeholder="Как у тебя прошел день?"
            aria-label="Сообщение персонажу"
            autoComplete="off"
            autoCapitalize="sentences"
            enterKeyHint="send"
            inputMode="text"
          />

          {chatError ? (
            <p className="conversation-input-error" aria-live="polite">
              {chatError}
            </p>
          ) : null}

          <button
            type="button"
            onClick={handleSendButtonClick}
            disabled={isSendingChat}
            tabIndex={isChatMode ? 0 : -1}
            className="conversation-send-button"
          >
            {isSendingChat ? (
              <Loader2 className="size-[18px] animate-spin" aria-hidden="true" />
            ) : null}
            <span>Отправить</span>
          </button>
        </form>

        <button
          type="button"
          aria-controls="debug-panel"
          aria-expanded={isDebugPanelOpen}
          aria-label={isDebugPanelOpen ? "Скрыть debug-панель" : "Показать debug-панель"}
          onClick={() => {
            setIsIdleControlsOpen(false);
            setIsDebugPanelOpen(true);
          }}
          className="conversation-fade-target absolute left-[327px] top-[685px] z-40 grid size-[58.203px] place-items-center overflow-hidden rounded-[24px] bg-[rgba(41,41,41,0.4)] text-white/90 shadow-[inset_0_2px_4px_rgba(255,255,255,0.09)] transition-colors hover:bg-[rgba(55,55,55,0.5)] focus:outline-none focus:ring-2 focus:ring-white/20"
        >
          <Bug className="size-[22px]" aria-hidden="true" />
        </button>
      </section>

      <div
        className={`main-actions-scroll conversation-fade-target absolute top-[762px] z-30 ${
          isChatMode ? "conversation-fade-target--hidden" : ""
        }`}
      >
        <div className="flex w-max gap-[19px] pr-[29px]">
          <button
            type="button"
            className="main-action-button main-action-button--chat"
            style={mainActionButtonStyle.chat}
            onClick={handleOpenChatMode}
          >
            <img
              src={actionIconSrc.chat}
              alt=""
              aria-hidden="true"
              className="main-action-button__icon"
              draggable={false}
            />
            <span>Поболтать</span>
          </button>
          <button
            type="button"
            onClick={handleFeed}
            disabled={isFeeding}
            className="main-action-button main-action-button--feed disabled:cursor-not-allowed disabled:opacity-70"
            style={mainActionButtonStyle.feed}
          >
            {isFeeding ? (
              <Loader2 className="size-[24px] animate-spin" aria-hidden="true" />
            ) : (
              <img
                src={actionIconSrc.feed}
                alt=""
                aria-hidden="true"
                className="main-action-button__icon"
                draggable={false}
              />
            )}
            <span>Покормить</span>
          </button>

          <button
            type="button"
            onClick={handleTravel}
            disabled={isTravelGenerating}
            className="main-action-button main-action-button--travel disabled:cursor-not-allowed disabled:opacity-70"
            style={mainActionButtonStyle.travel}
          >
            {isTravelGenerating ? (
              <Loader2 className="size-[24px] animate-spin" aria-hidden="true" />
            ) : (
              <img
                src={actionIconSrc.travel}
                alt=""
                aria-hidden="true"
                className="main-action-button__icon"
                draggable={false}
              />
            )}
            <span>В путешествие</span>
          </button>
        </div>
      </div>
      {travelResult ? (
        <TravelStoryOverlay result={travelResult} onClose={() => setTravelResult(null)} />
      ) : null}
    </main>
  );
}
