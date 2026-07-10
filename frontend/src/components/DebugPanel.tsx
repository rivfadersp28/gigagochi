"use client";

import * as Dialog from "@radix-ui/react-dialog";
import {
  Bug,
  Circle,
  Database,
  FlaskConical,
  Frown,
  MessageSquareText,
  RotateCcw,
  Skull,
  Smile,
  Trash2,
  X,
  HeartPulse,
} from "lucide-react";
import { useEffect, useState } from "react";

import {
  clearDebugPanelEvents,
  DEBUG_PANEL_EVENTS_CHANGED,
  readDebugPanelEvents,
  type DebugPanelEvent,
} from "@/lib/debugPanelStorage";
import { readLocalPetMemory } from "@/lib/localPetMemoryStorage";
import type { LocalPetMemoryStateV1 } from "@/lib/localPetMemoryTypes";
import type { LocalPetState } from "@/lib/types";
import { cn } from "@/lib/utils";

import type { PetVisualMode } from "./pet-dashboard/petSprite";

type DebugPanelProps = {
  pet: LocalPetState;
  isOpen: boolean;
  onClose: () => void;
  onResetPet?: () => void;
  onResetPetStats?: () => void;
  onKillPet?: () => void;
  onRevivePet?: () => void;
  isPetDead?: boolean;
  onOpenTestPet?: () => void;
  canShowSadAsset?: boolean;
  canShowHappyAsset?: boolean;
  visualModeOverride?: PetVisualMode | null;
  onVisualModeOverrideChange?: (mode: PetVisualMode | null) => void;
};

type DebugTab = "feed" | "prompts" | "character";

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function jsonText(value: unknown) {
  return JSON.stringify(value ?? null, null, 2);
}

function formatDebugTime(value: string) {
  try {
    return new Intl.DateTimeFormat("ru", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date(value));
  } catch {
    return value;
  }
}

function getLiteOverlay(pet: LocalPetState): Record<string, unknown> | null {
  const bible = pet.assetSet?.characterBible;
  if (!isRecord(bible)) {
    return null;
  }
  const extensions = bible.extensions;
  if (!isRecord(extensions)) {
    return null;
  }
  return isRecord(extensions.lite_overlay) ? extensions.lite_overlay : null;
}

function getLiteSphere(
  overlay: Record<string, unknown> | null,
  sphere: string,
): Record<string, unknown> | null {
  if (!overlay || !isRecord(overlay.spheres)) {
    return null;
  }
  const value = overlay.spheres[sphere];
  return isRecord(value) ? value : null;
}

function characterSnapshot(pet: LocalPetState, memory: LocalPetMemoryStateV1) {
  const overlay = getLiteOverlay(pet);
  return {
    pet: {
      petId: pet.petId,
      name: pet.name,
      description: pet.description,
      stage: pet.stage,
      mood: pet.mood,
      stats: pet.stats,
      createdAt: pet.createdAt,
      updatedAt: pet.updatedAt,
      lastInteractionAt: pet.lastInteractionAt,
    },
    liteOverlay: {
      character: getLiteSphere(overlay, "character"),
      appearance: getLiteSphere(overlay, "appearance"),
      world: getLiteSphere(overlay, "world"),
      relationship: getLiteSphere(overlay, "relationship"),
      all: overlay,
    },
    userMemory: {
      userProfile: memory.userProfile,
      summary: memory.summary,
      memories: memory.memories,
      learnings: memory.learnings,
      proactiveLog: memory.proactiveLog,
    },
    characterBible: pet.assetSet?.characterBible ?? null,
  };
}

function EmptyState({ children }: { children: string }) {
  return (
    <div className="grid min-h-[180px] place-items-center px-6 text-center text-[13px] leading-[18px] text-black/42">
      {children}
    </div>
  );
}

function EventList({ events }: { events: DebugPanelEvent[] }) {
  if (!events.length) {
    return <EmptyState>Пока нет событий.</EmptyState>;
  }
  return (
    <div className="divide-y divide-black/10">
      {events.map((event) => (
        <article key={event.id} className="py-3">
          <div className="mb-2 flex items-start justify-between gap-3">
            <div className="min-w-0 text-[13px] font-medium leading-[17px] text-black/78">
              {event.title}
            </div>
            <time className="shrink-0 text-[11px] leading-[17px] text-black/35">
              {formatDebugTime(event.createdAt)}
            </time>
          </div>
          <pre className="max-h-[340px] overflow-auto whitespace-pre-wrap break-words rounded-[8px] bg-black/[0.035] p-3 font-mono text-[11px] leading-[15px] text-black/72">
            {event.text}
          </pre>
        </article>
      ))}
    </div>
  );
}

function PromptList({ events }: { events: DebugPanelEvent[] }) {
  if (!events.length) {
    return <EmptyState>Пока нет запросов реплик.</EmptyState>;
  }
  return (
    <div className="divide-y divide-black/10">
      {events.map((event) => (
        <article key={event.id} className="py-3">
          <time className="mb-2 block text-[11px] leading-none text-black/35">
            {formatDebugTime(event.createdAt)}
          </time>
          <pre className="max-h-[460px] overflow-auto whitespace-pre-wrap break-words rounded-[8px] bg-black/[0.035] p-3 font-mono text-[11px] leading-[15px] text-black/72">
            {event.text}
          </pre>
        </article>
      ))}
    </div>
  );
}

function CharacterInfo({ pet, memory }: { pet: LocalPetState; memory: LocalPetMemoryStateV1 }) {
  const snapshot = characterSnapshot(pet, memory);
  const sections = [
    ["Мир", snapshot.liteOverlay.world],
    ["Характер", snapshot.liteOverlay.character],
    ["Вид", snapshot.liteOverlay.appearance],
    ["Отношения", snapshot.liteOverlay.relationship],
    ["Память пользователя", snapshot.userMemory],
    ["Полный character bible", snapshot.characterBible],
  ] satisfies [string, unknown][];

  return (
    <div className="divide-y divide-black/10">
      <section className="py-3">
        <div className="mb-2 text-[13px] font-medium leading-[17px] text-black/78">
          База персонажа
        </div>
        <pre className="overflow-auto whitespace-pre-wrap break-words rounded-[8px] bg-black/[0.035] p-3 font-mono text-[11px] leading-[15px] text-black/72">
          {jsonText(snapshot.pet)}
        </pre>
      </section>
      {sections.map(([title, value]) => (
        <section key={title} className="py-3">
          <div className="mb-2 text-[13px] font-medium leading-[17px] text-black/78">
            {title}
          </div>
          <pre className="max-h-[380px] overflow-auto whitespace-pre-wrap break-words rounded-[8px] bg-black/[0.035] p-3 font-mono text-[11px] leading-[15px] text-black/72">
            {jsonText(value)}
          </pre>
        </section>
      ))}
    </div>
  );
}

function BackgroundGenerationProgress({ pet }: { pet: LocalPetState }) {
  const assetSet = pet.assetSet;
  const status = assetSet?.backgroundGenerationStatus;
  const phase = assetSet?.backgroundGenerationPhase;
  const progress = status === "succeeded" || phase === "completed"
    ? 4
    : phase === "generating_happy_video"
      ? 3
      : phase === "generating_happy_image"
        ? 2
        : phase === "generating_sad_video"
          ? 1
          : 0;
  const label = status === "succeeded"
    ? "Готово"
    : status === "failed"
      ? "Готово с ошибками"
      : phase === "generating_happy_video"
        ? "Генерируется счастливое видео"
        : phase === "generating_happy_image"
          ? "Генерируется счастливая картинка"
          : phase === "generating_sad_video"
            ? "Генерируется грустное видео"
            : phase === "generating_sad_image"
              ? "Генерируется грустная картинка"
              : assetSet?.generationJobId
                ? "Ожидание фоновой генерации"
                : "Фоновая генерация не запускалась";

  return (
    <section className="mt-4 rounded-lg bg-black/[0.035] p-3" aria-label="Фоновая генерация">
      <div className="flex items-center justify-between gap-3 text-[12px] leading-[16px]">
        <span className="text-pretty font-medium text-black/62">{label}</span>
        <span className="shrink-0 tabular-nums text-black/42">{progress}/4</span>
      </div>
      <div
        className="mt-2 grid grid-cols-4 gap-1"
        role="progressbar"
        aria-label={`${label}: ${progress} из 4`}
        aria-valuemin={0}
        aria-valuemax={4}
        aria-valuenow={progress}
      >
        {[1, 2, 3, 4].map((step) => (
          <span
            key={step}
            className={cn(
              "h-1.5 rounded-full",
              step <= progress ? "bg-black/60" : "bg-black/10",
            )}
            aria-hidden="true"
          />
        ))}
      </div>
      {assetSet?.backgroundGenerationError ? (
        <p className="mt-2 text-pretty text-[11px] leading-[15px] text-red-700">
          {assetSet.backgroundGenerationError}
        </p>
      ) : null}
    </section>
  );
}

export function DebugPanel({
  pet,
  isOpen,
  onClose,
  onResetPet,
  onResetPetStats,
  onKillPet,
  onRevivePet,
  isPetDead = false,
  onOpenTestPet,
  canShowSadAsset = false,
  canShowHappyAsset = false,
  visualModeOverride = null,
  onVisualModeOverrideChange,
}: DebugPanelProps) {
  const [activeTab, setActiveTab] = useState<DebugTab>("feed");
  const [events, setEvents] = useState<DebugPanelEvent[]>(() =>
    [...readDebugPanelEvents()].reverse(),
  );

  useEffect(() => {
    function handleChange() {
      setEvents([...readDebugPanelEvents()].reverse());
    }
    window.addEventListener(DEBUG_PANEL_EVENTS_CHANGED, handleChange);
    window.addEventListener("storage", handleChange);
    return () => {
      window.removeEventListener(DEBUG_PANEL_EVENTS_CHANGED, handleChange);
      window.removeEventListener("storage", handleChange);
    };
  }, []);

  const memory = readLocalPetMemory(pet.petId);
  const promptEvents = events.filter((event) => event.kind === "prompt");

  if (!isOpen) {
    return null;
  }

  const tabs = [
    { id: "feed", label: "Лента", icon: Database },
    { id: "prompts", label: "Prompts", icon: MessageSquareText },
    { id: "character", label: "Персонаж", icon: Bug },
  ] satisfies { id: DebugTab; label: string; icon: typeof Bug }[];
  const visualModes = [
    { id: "sad", label: "Грустный", icon: Frown, disabled: !canShowSadAsset },
    { id: "normal", label: "Нормальный", icon: Circle, disabled: false },
    { id: "happy", label: "Счастливый", icon: Smile, disabled: !canShowHappyAsset },
  ] satisfies {
    id: PetVisualMode;
    label: string;
    icon: typeof Bug;
    disabled: boolean;
  }[];

  return (
    <Dialog.Root open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/[0.16]" />
        <Dialog.Content asChild>
          <aside
            id="debug-panel"
            className="fixed bottom-[max(14px,var(--tma-safe-bottom))] right-[max(12px,var(--tma-safe-right))] top-[max(72px,calc(var(--tma-safe-top)+64px))] z-50 flex w-[min(520px,calc(100vw-24px))] flex-col overflow-hidden rounded-lg border border-black/10 bg-white text-black shadow-xl outline-none"
          >
        <header className="border-b border-black/10 px-4 py-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <Dialog.Title asChild>
                <h2 className="text-balance break-words text-[18px] font-semibold leading-[22px] text-black">
                  Debug
                </h2>
              </Dialog.Title>
              <Dialog.Description asChild>
                <p className="mt-1 text-pretty text-[12px] leading-[16px] text-black/45">
                  Prompts, память и данные персонажа
                </p>
              </Dialog.Description>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {onResetPet ? (
                <button
                  type="button"
                  aria-label="Сбросить персонажа и создать нового"
                  onClick={onResetPet}
                  className="grid size-8 place-items-center rounded-full text-black/45 transition-colors hover:bg-black/[0.04] hover:text-black/70 focus:outline-none focus:ring-2 focus:ring-black/10"
                >
                  <RotateCcw className="size-4" aria-hidden="true" />
                </button>
              ) : null}
              <button
                type="button"
                aria-label="Очистить debug-ленту"
                onClick={clearDebugPanelEvents}
                className="grid size-8 place-items-center rounded-full text-black/45 transition-colors hover:bg-black/[0.04] hover:text-black/70 focus:outline-none focus:ring-2 focus:ring-black/10"
              >
                <Trash2 className="size-4" aria-hidden="true" />
              </button>
              <Dialog.Close asChild>
                <button
                  type="button"
                  aria-label="Закрыть"
                  className="grid size-8 place-items-center rounded-full text-black/45 transition-colors hover:bg-black/[0.04] hover:text-black/70 focus:outline-none focus:ring-2 focus:ring-black/10"
                >
                  <X className="size-4" aria-hidden="true" />
                </button>
              </Dialog.Close>
            </div>
          </div>

          {onResetPetStats || onKillPet || onRevivePet || onOpenTestPet || onVisualModeOverrideChange ? (
            <div className="mt-4 grid gap-2">
              {onOpenTestPet ? (
                <button
                  type="button"
                  onClick={onOpenTestPet}
                  className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-[8px] bg-black/[0.055] px-3 text-[12px] font-medium leading-none text-black/62 transition-colors hover:bg-black/[0.085] hover:text-black/78 focus:outline-none focus:ring-2 focus:ring-black/10"
                >
                  <FlaskConical className="size-3.5" aria-hidden="true" />
                  <span>Открыть тестового персонажа</span>
                </button>
              ) : null}
              {onKillPet ? (
                <button
                  type="button"
                  onClick={onKillPet}
                  disabled={isPetDead}
                  className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-[8px] bg-black/[0.055] px-3 text-[12px] font-medium leading-none text-black/62 transition-colors hover:bg-black/[0.085] hover:text-black/78 focus:outline-none focus:ring-2 focus:ring-black/10 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <Skull className="size-3.5" aria-hidden="true" />
                  <span>Убить персонажа</span>
                </button>
              ) : null}
              {onRevivePet ? (
                <button
                  type="button"
                  onClick={onRevivePet}
                  disabled={!isPetDead}
                  className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-[8px] bg-black/[0.055] px-3 text-[12px] font-medium leading-none text-black/62 transition-colors hover:bg-black/[0.085] hover:text-black/78 focus:outline-none focus:ring-2 focus:ring-black/10 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <HeartPulse className="size-3.5" aria-hidden="true" />
                  <span>Воскресить последнего персонажа</span>
                </button>
              ) : null}
              {onResetPetStats ? (
                <button
                  type="button"
                  onClick={onResetPetStats}
                  className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-[8px] bg-black/[0.055] px-3 text-[12px] font-medium leading-none text-black/62 transition-colors hover:bg-black/[0.085] hover:text-black/78 focus:outline-none focus:ring-2 focus:ring-black/10"
                >
                  <RotateCcw className="size-3.5" aria-hidden="true" />
                  <span>Сбросить параметры персонажа</span>
                </button>
              ) : null}
              {onVisualModeOverrideChange ? (
                <div>
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <span className="text-[12px] font-medium leading-[16px] text-black/62">
                      Вид персонажа
                    </span>
                    <span className="text-[11px] leading-[16px] text-black/42">
                      {visualModeOverride ? "Ручной режим" : "Авто"}
                    </span>
                  </div>
                  <div
                    className="grid grid-cols-3 gap-1 rounded-[8px] bg-black/[0.035] p-1"
                    role="group"
                    aria-label="Принудительный вид персонажа"
                  >
                    {visualModes.map((mode) => {
                      const Icon = mode.icon;
                      const isSelected = visualModeOverride === mode.id;
                      return (
                        <button
                          key={mode.id}
                          type="button"
                          aria-pressed={isSelected}
                          disabled={mode.disabled}
                          onClick={() => onVisualModeOverrideChange(
                            isSelected ? null : mode.id,
                          )}
                          className={cn(
                            "inline-flex h-9 items-center justify-center gap-1.5 rounded-[7px] px-2 text-[11px] font-medium leading-none transition-colors focus:outline-none focus:ring-2 focus:ring-black/10 disabled:cursor-not-allowed disabled:opacity-40",
                            isSelected
                              ? "bg-white text-black/82 shadow-sm"
                              : "text-black/45 hover:bg-white/60 hover:text-black/70",
                          )}
                        >
                          <Icon className="size-3.5" aria-hidden="true" />
                          <span>{mode.label}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}

          <BackgroundGenerationProgress pet={pet} />

          <div className="mt-4 grid grid-cols-3 gap-1 rounded-[8px] bg-black/[0.035] p-1">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const isSelected = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  aria-pressed={isSelected}
                  onClick={() => setActiveTab(tab.id)}
                  className="inline-flex h-9 items-center justify-center gap-1.5 rounded-[7px] px-2 text-[12px] font-medium leading-none transition-colors focus:outline-none focus:ring-2 focus:ring-black/10"
                  style={{
                    backgroundColor: isSelected ? "white" : "transparent",
                    color: isSelected ? "rgba(0,0,0,0.82)" : "rgba(0,0,0,0.45)",
                    boxShadow: isSelected ? "0 1px 3px rgba(0,0,0,0.07)" : "none",
                  }}
                >
                  <Icon className="size-3.5" aria-hidden="true" />
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-1">
          {activeTab === "feed" ? <EventList events={events} /> : null}
          {activeTab === "prompts" ? <PromptList events={promptEvents} /> : null}
          {activeTab === "character" ? <CharacterInfo pet={pet} memory={memory} /> : null}
        </div>
          </aside>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
