"use client";

/* eslint-disable @next/next/no-img-element */
import { Loader2, Settings } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { CSSProperties, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

import { playPetSpeechAudioSequence } from "@/lib/petSpeechAudio";
import { hapticImpact } from "@/lib/telegram";
import type { LocalChatResponse, LocalPetStateV1, PetLifeStage, PetMood } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";

import { PetQuickChat } from "./PetQuickChat";

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

type PetReplyMessage = {
  id: number;
  text: string;
  playSpeechAudio: boolean;
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
const DEFAULT_TEXT_TRANSLATE_Y_PX = 12;
const DEFAULT_SPEECH_END_TRIM_MS = 625;

const petMessageStackStyle: CSSProperties = {
  display: "grid",
  justifyItems: "center",
  alignItems: "start",
  height: 21,
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
  maxScale: 1.15,
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

function generatedSpriteUrl(pet: LocalPetStateV1, stage: PetStage, state: PetState) {
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
      className="w-full max-w-[405px] text-center text-[17px] font-normal leading-[21px] text-black"
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
  pet: LocalPetStateV1;
  selectedStage: PetStage;
  selectedState: PetState;
  settings: IdleAnimationSettings;
  onChange: (nextSettings: IdleAnimationSettings) => void;
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
  onChange,
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

export function PetDashboard({ petId }: PetDashboardProps) {
  const router = useRouter();
  const localPet = useLocalPetState();
  const [isFeeding, setIsFeeding] = useState(false);
  const [isIdleControlsOpen, setIsIdleControlsOpen] = useState(false);
  const [selectedSprite, setSelectedSprite] = useState<SelectedSprite | null>(null);
  const [idleAnimationSettings, setIdleAnimationSettings] = useState<IdleAnimationSettings>(
    defaultIdleAnimationSettings,
  );
  const [petReplyMessage, setPetReplyMessage] = useState<PetReplyMessage | null>(null);
  const pet = localPet.pet;

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
    hapticImpact("light");
    window.setTimeout(() => setIsFeeding(false), 180);
  }

  function handlePlay() {
    localPet.play();
    hapticImpact("light");
  }

  function handleChatResponse(response: LocalChatResponse) {
    setPetReplyMessage((current) => ({
      id: (current?.id ?? 0) + 1,
      text: response.reply,
      playSpeechAudio: true,
    }));

    localPet.applyMoodHint(response.moodHint, response.loreMemoriesToSave);
    hapticImpact("light");
  }

  function handleReset() {
    const confirmed = window.confirm("Создать нового друга? Локальный прогресс будет удален.");
    if (!confirmed) {
      return;
    }
    hapticImpact("medium");
    localPet.reset();
    router.push("/");
  }

  if (localPet.status === "loading") {
    return (
      <main className="tma-screen grid place-items-center bg-white px-6">
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
  const previewPet: LocalPetStateV1 = selectedSprite
    ? { ...pet, stage: displayedStage, mood: displayedState }
    : pet;
  const visiblePetImage = generatedSpriteUrl(pet, displayedStage, displayedState) ?? "/figma/pet.png";
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

  return (
    <main className="tma-screen overflow-hidden bg-white text-black">
      {localPet.error ? (
        <div className="fixed left-5 right-5 top-[max(20px,calc(var(--tma-safe-top)+12px))] z-20 rounded-[8px] border border-[var(--danger-line)] bg-white px-4 py-3 text-sm text-[var(--danger)] sm:left-8 sm:right-auto sm:max-w-sm">
          {localPet.error}
        </div>
      ) : null}

      <section className="tma-screen relative w-screen overflow-hidden bg-white" aria-label="AI Tamagotchi">
        <IdleAnimationControls
          isOpen={isIdleControlsOpen}
          pet={pet}
          selectedStage={displayedStage}
          selectedState={displayedState}
          settings={animationSettings}
          onChange={setIdleAnimationSettings}
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

        <div className="sr-only" aria-live="polite">
          Stage {stageLabels[pet.stage]}. State {stateLabels[pet.mood]}. Hunger{" "}
          {pet.stats.hunger}/100. Happiness {pet.stats.happiness}/100. Energy{" "}
          {pet.stats.energy}/100. Cleanliness {pet.stats.cleanliness}/100.
        </div>

        <div className="absolute inset-x-0 top-[max(86px,calc(var(--tma-safe-top)+56px))] flex justify-center sm:top-[19.7vh]">
          <div className="relative h-[min(43vh,451px)] w-[min(78vw,501px)]">
            <div className="absolute bottom-[8%] left-1/2 h-[12%] w-[46%] -translate-x-1/2" aria-hidden="true">
              <div className="absolute inset-[-51.85%_-12.23%]">
                <img
                  src="/figma/pet-shadow.svg"
                  alt=""
                  className="block h-full w-full max-w-none"
                  draggable={false}
                />
              </div>
            </div>

            <div className="pet-idle-rotation absolute inset-0" style={idleRotationStyle}>
              <img
                src={visiblePetImage}
                alt={`AI Tamagotchi ${stageLabels[displayedStage]} ${stateLabels[displayedState]}`}
                className="pet-idle-y-animation h-full w-full max-w-none object-contain"
                style={idleStretchStyle}
                width={501}
                height={451}
                draggable={false}
              />
            </div>
          </div>
        </div>

        <div className="absolute bottom-[max(26px,calc(var(--tma-safe-bottom)+16px))] left-1/2 z-10 flex w-[min(405px,calc(100vw-40px))] -translate-x-1/2 flex-col items-center gap-[30px] sm:gap-[38px]">
          <div className="flex w-full flex-col items-center gap-[clamp(62px,11vh,108px)]">
            {petReplyMessage ? (
              <PetCharacterMessage
                message={petReplyMessage}
                textTranslateY={animationSettings.textTranslateY}
                speechEndTrimMs={animationSettings.speechEndTrimMs}
              />
            ) : (
              <div className="w-full max-w-[405px]" style={petMessageStackStyle} aria-hidden="true" />
            )}
            <PetQuickChat pet={previewPet} onChatResponse={handleChatResponse} />
          </div>

          <div className="flex items-center gap-[20px] sm:gap-[28px]">
            <button
              type="button"
              onClick={handleFeed}
              disabled={isFeeding}
              className="inline-flex h-[55px] items-center justify-center gap-[10px] rounded-[40px] bg-white p-[17px] text-[17px] font-normal leading-none text-black drop-shadow-[0_4px_14px_rgba(0,0,0,0.1)] transition-transform hover:-translate-y-0.5 focus:outline-none focus:ring-2 focus:ring-black/10 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isFeeding ? (
                <Loader2 className="size-[17px] animate-spin" aria-hidden="true" />
              ) : (
                <img
                  src="/figma/feed-icon.svg"
                  alt=""
                  aria-hidden="true"
                  className="h-[21.411px] w-[12.684px] max-w-none"
                  draggable={false}
                />
              )}
              <span className="whitespace-nowrap">Покормить</span>
            </button>

            <Link
              onClick={handlePlay}
              href={`/pet/${pet.petId}/chat`}
              className="inline-flex h-[55px] items-center justify-center gap-[10px] rounded-[40px] bg-white p-[17px] text-[17px] font-normal leading-none text-black drop-shadow-[0_4px_14px_rgba(0,0,0,0.1)] transition-transform hover:-translate-y-0.5 focus:outline-none focus:ring-2 focus:ring-black/10"
            >
              <img
                src="/figma/play-icon.svg"
                alt=""
                aria-hidden="true"
                className="h-[15.041px] w-[23.964px] max-w-none"
                draggable={false}
              />
              <span className="whitespace-nowrap">Поиграть</span>
            </Link>
          </div>
        </div>

        <button
          type="button"
          onClick={handleReset}
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
        </button>
      </section>
    </main>
  );
}
