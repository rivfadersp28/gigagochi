"use client";

import { Settings } from "lucide-react";

import type { LocalPetState } from "@/lib/types";

import {
  generatedSpriteUrl,
  spriteStageOptions,
  spriteStateOptions,
  type PetStage,
  type PetState,
} from "./petSprite";

export type IdleAnimationSettings = {
  speed: number;
  maxScale: number;
  originX: number;
  originY: number;
  rotationSpeed: number;
  maxRotation: number;
  textTranslateY: number;
  speechEndTrimMs: number;
};

const DEFAULT_TEXT_TRANSLATE_Y_PX = 12;
const DEFAULT_SPEECH_END_TRIM_MS = 625;

export const defaultIdleAnimationSettings: IdleAnimationSettings = {
  speed: 1.5,
  maxScale: 1.12,
  originX: 62,
  originY: 100,
  rotationSpeed: 1.2,
  maxRotation: 5,
  textTranslateY: DEFAULT_TEXT_TRANSLATE_Y_PX,
  speechEndTrimMs: DEFAULT_SPEECH_END_TRIM_MS,
};

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

export function IdleAnimationControls({
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
