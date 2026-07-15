import { readFileSync } from "node:fs";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LocalInteractiveTravel } from "@/lib/localInteractiveTravel";
import { readLocalPetState, writeLocalPetState } from "@/lib/localPetStorage";
import { applyInteractiveTravelImpactsToPet } from "@/lib/localPetTravelImpacts";
import type { InteractiveTravelPart, InteractiveTravelState, LocalPetState } from "@/lib/types";

import { InteractiveTravelScreen } from "./InteractiveTravelScreen";

const travelScreenStyles = readFileSync(
  "src/components/InteractiveTravelScreen.module.css",
  "utf8",
);

function cssRule(className: string) {
  return travelScreenStyles.match(new RegExp(`\\.${className}\\s*\\{([^}]*)\\}`))?.[1] ?? "";
}

const mocks = vi.hoisted(() => ({
  animateInteractiveTravelPart: vi.fn(),
  applyInteractiveTravelImpacts: vi.fn(),
  cancelInteractiveTravel: vi.fn(),
  captureInteractiveTravelFinale: vi.fn(),
  clearLocalInteractiveTravel: vi.fn(),
  continueInteractiveTravel: vi.fn(),
  enqueueInteractiveTravelCancel: vi.fn(),
  enqueueInteractiveTravelCapture: vi.fn(),
  flushPendingInteractiveTravelOperations: vi.fn(),
  getInteractiveTravelDemo: vi.fn(),
  getInteractiveTravelSuggestions: vi.fn(),
  illustrateInteractiveTravelPart: vi.fn(),
  isLocalInteractiveTravelStorageKey: vi.fn(
    (key: string | null, petId: string) =>
      key === null || key === `gigagochi.interactive-travel.v3:${petId}`,
  ),
  mutationLockError: class LocalInteractiveTravelMutationLockError extends Error {},
  push: vi.fn(),
  readLocalInteractiveTravel: vi.fn(),
  readPendingInteractiveTravelOperations: vi.fn(),
  replace: vi.fn(),
  startInteractiveTravel: vi.fn(),
  resetInteractiveTravelGeneration: vi.fn(),
  sweep: vi.fn((callback: () => void) => callback()),
  canUseDebugMenu: false,
  useTelegramBackButton: vi.fn(),
  withLocalInteractiveTravelMutationLock: vi.fn(
    async (
      _petId: string,
      mutation: (guard: { assertOwned: () => void }) => unknown,
      _options?: { acquireTimeoutMs?: number },
    ) => {
      void _options;
      return mutation({ assertOwned: vi.fn() });
    },
  ),
  writeLocalInteractiveTravel: vi.fn(),
  writeLocalInteractiveTravelDurably: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
}));

vi.mock("glimm/react", () => ({
  useGlimm: () => ({ sweep: mocks.sweep }),
}));

vi.mock("@/lib/api", () => ({
  animateInteractiveTravelPart: (...args: unknown[]) =>
    mocks.animateInteractiveTravelPart(...args),
  cancelInteractiveTravel: (...args: unknown[]) =>
    mocks.cancelInteractiveTravel(...args),
  captureInteractiveTravelFinale: (...args: unknown[]) =>
    mocks.captureInteractiveTravelFinale(...args),
  continueInteractiveTravel: (...args: unknown[]) => mocks.continueInteractiveTravel(...args),
  getInteractiveTravelSuggestions: (...args: unknown[]) =>
    mocks.getInteractiveTravelSuggestions(...args),
  illustrateInteractiveTravelPart: (...args: unknown[]) =>
    mocks.illustrateInteractiveTravelPart(...args),
  startInteractiveTravel: (...args: unknown[]) => mocks.startInteractiveTravel(...args),
}));

vi.mock("@/lib/localInteractiveTravel", () => ({
  clearLocalInteractiveTravel: (...args: unknown[]) =>
    mocks.clearLocalInteractiveTravel(...args),
  isLocalInteractiveTravelStorageKey: (...args: [string | null, string]) =>
    mocks.isLocalInteractiveTravelStorageKey(...args),
  LocalInteractiveTravelMutationLockError: mocks.mutationLockError,
  readLocalInteractiveTravel: (...args: unknown[]) => mocks.readLocalInteractiveTravel(...args),
  withLocalInteractiveTravelMutationLock: (
    petId: string,
    mutation: (guard: { assertOwned: () => void }) => unknown,
    options?: { acquireTimeoutMs?: number },
  ) => mocks.withLocalInteractiveTravelMutationLock(petId, mutation, options),
  writeLocalInteractiveTravel: (...args: unknown[]) =>
    mocks.writeLocalInteractiveTravel(...args),
  writeLocalInteractiveTravelDurably: (...args: unknown[]) =>
    mocks.writeLocalInteractiveTravelDurably(...args),
}));

vi.mock("@/lib/interactiveTravelDebugApi", () => ({
  getInteractiveTravelDemo: (...args: unknown[]) =>
    mocks.getInteractiveTravelDemo(...args),
  resetInteractiveTravelGeneration: (...args: unknown[]) =>
    mocks.resetInteractiveTravelGeneration(...args),
}));

vi.mock("@/lib/pendingInteractiveTravelOperations", () => ({
  enqueueInteractiveTravelCancel: (...args: unknown[]) =>
    mocks.enqueueInteractiveTravelCancel(...args),
  enqueueInteractiveTravelCapture: (...args: unknown[]) =>
    mocks.enqueueInteractiveTravelCapture(...args),
  flushPendingInteractiveTravelOperations: (...args: unknown[]) =>
    mocks.flushPendingInteractiveTravelOperations(...args),
  readPendingInteractiveTravelOperations: (...args: unknown[]) =>
    mocks.readPendingInteractiveTravelOperations(...args),
}));

vi.mock("@/lib/telegram", () => ({
  canUseDebugMenu: () => mocks.canUseDebugMenu,
  hapticNotification: vi.fn(),
  setTelegramBackgroundColor: vi.fn(),
  useTelegramBackButton: (...args: unknown[]) => mocks.useTelegramBackButton(...args),
}));

vi.mock("@/lib/useTelegramCapabilities", () => ({
  useTelegramCapabilities: () => ({
    debugMenu: mocks.canUseDebugMenu,
    interactiveTravel: true,
  }),
}));

vi.mock("./pet-dashboard/PetSpeechBubble", () => ({
  PetSpeechBubble: ({
    message,
    minFontSize,
    fixedSize,
  }: {
    message: { text: string };
    minFontSize?: number;
    fixedSize?: unknown;
  }) => (
    <div
      data-testid="travel-speech"
      data-min-font-size={minFontSize}
      data-fixed-size={String(Boolean(fixedSize))}
    >
      {message.text}
    </div>
  ),
}));

const now = "2026-07-14T12:00:00.000Z";
const imageStates = {
  idle: "/pet.png",
  happy: "/pet.png",
  hungry: "/pet.png",
  sad: "/pet.png",
};
const pet: LocalPetState = {
  version: 2,
  petId: "pet-1",
  name: "Листик",
  description: "Лесной дух",
  createdAt: now,
  updatedAt: now,
  lastInteractionAt: now,
  lastStatsTickAt: now,
  lastStatTickAt: { hunger: now, happiness: now, energy: now },
  stage: "teen",
  mood: "idle",
  stats: { hunger: 50, happiness: 50, energy: 50 },
  assetSet: {
    assetSetId: "travel-test",
    generatedAt: now,
    images: { baby: imageStates, teen: imageStates, adult: imageStates },
  },
};

vi.mock("@/lib/useLocalPetState", () => ({
  useLocalPetState: () => ({
    pet,
    status: "ready",
    applyInteractiveTravelImpacts: (...args: unknown[]) =>
      mocks.applyInteractiveTravelImpacts(...args),
  }),
}));

function pendingPart(partNumber: number, storyText = "Сначала дорога петляет. Потом виден мост."):
  InteractiveTravelPart {
  return {
    partNumber,
    title: `Событие ${partNumber}`,
    storyText,
    transition:
      partNumber === 1
        ? undefined
        : { elapsedHours: 4, summary: "Прошло несколько часов." },
    challenge: `Что делать на шаге ${partNumber}?`,
    actionSuggestions: ["Идти вперёд", "Осмотреть мост", "Позвать сову"],
    backgroundImageUrl: `/story-${partNumber}.png`,
  };
}

function travel(parts: InteractiveTravelPart[], completed = false): InteractiveTravelState {
  return {
    travelId: "interactive-travel-1",
    generatedAt: now,
    destination: "облачный город",
    overallTitle: "Часы облачного города",
    introReaction: { text: "Облачный город? Уже бегу!", tone: "enthusiastic" },
    arcPlan: { goal: "Запустить часы" },
    parts,
    completed,
    outcomeValence: completed ? "positive" : undefined,
  };
}

function restoredSession(
  travelState: InteractiveTravelState,
  phase: LocalInteractiveTravel["presentation"]["phase"],
  partNumber = 1,
): LocalInteractiveTravel {
  return {
    travel: travelState,
    appliedResultParts: [],
    presentation: { phase, partNumber, portionIndex: 0 },
  };
}

function resolvedFirstWithPendingSecond() {
  const first: InteractiveTravelPart = {
    ...pendingPart(1),
    answer: "Идти вперёд",
    result: {
      text: "Листик перешёл мост и добрался до башни.",
      adviceAssessment: "helpful",
      reaction: "Хорошая мысль!",
      reactionTone: "enthusiastic",
      consequence: "Мост пройден.",
      outcomeValence: "positive",
      statImpacts: [{ stat: "hunger", amount: 4, reason: "Листик нашёл яблоко." }],
    },
  };
  const second = pendingPart(2);
  delete second.backgroundImageUrl;
  return travel([first, second]);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, "", "/");
  window.localStorage.clear();
  writeLocalPetState(pet);
  mocks.readLocalInteractiveTravel.mockReturnValue(null);
  mocks.withLocalInteractiveTravelMutationLock.mockImplementation(
    async (
      _petId: string,
      mutation: (guard: { assertOwned: () => void }) => unknown,
      _options?: { acquireTimeoutMs?: number },
    ) => {
      void _options;
      return mutation({ assertOwned: vi.fn() });
    },
  );
  mocks.canUseDebugMenu = false;
  mocks.cancelInteractiveTravel.mockResolvedValue(undefined);
  mocks.captureInteractiveTravelFinale.mockResolvedValue(undefined);
  mocks.enqueueInteractiveTravelCancel.mockReturnValue(true);
  mocks.enqueueInteractiveTravelCapture.mockReturnValue(true);
  mocks.flushPendingInteractiveTravelOperations.mockResolvedValue(undefined);
  mocks.getInteractiveTravelDemo.mockResolvedValue({
    demoId: "demo-1",
    travel: {
      ...travel([
      {
        ...pendingPart(1, "Готовая демо-история."),
        backgroundVideoUrl: "/story-1.mp4",
        answer: "Идти вперёд",
        result: {
          text: "Готовый демо-результат.",
          adviceAssessment: "helpful",
          reaction: "Выбираю готовый путь.",
          reactionTone: "determined",
          consequence: "Путь открыт.",
          outcomeValence: "positive",
          statImpacts: [],
        },
      },
      {
        ...pendingPart(2, "Вторая готовая часть."),
        backgroundVideoUrl: "/story-2.mp4",
        answer: "Идти вперёд",
        result: {
          text: "Второй готовый результат.",
          adviceAssessment: "helpful",
          reaction: "Иду дальше.",
          reactionTone: "determined",
          consequence: "Дорога пройдена.",
          outcomeValence: "positive",
          statImpacts: [],
        },
      },
      {
        ...pendingPart(3, "Третья готовая часть."),
        backgroundVideoUrl: "/story-3.mp4",
        answer: "Идти вперёд",
        result: {
          text: "Третий готовый результат.",
          adviceAssessment: "helpful",
          reaction: "Финиширую.",
          reactionTone: "determined",
          consequence: "История завершена.",
          outcomeValence: "positive",
          statImpacts: [],
        },
      },
      ], true),
      introReaction: { text: "Демо начинается!", tone: "determined" },
    },
  });
  mocks.readPendingInteractiveTravelOperations.mockReturnValue([]);
  mocks.resetInteractiveTravelGeneration.mockResolvedValue(undefined);
  mocks.getInteractiveTravelSuggestions.mockResolvedValue({
    destinations: ["В горы", "В океан", "На Луну"],
  });
  mocks.illustrateInteractiveTravelPart.mockResolvedValue({
    partNumber: 2,
    imageUrl: "/story-2.png",
  });
  mocks.animateInteractiveTravelPart.mockImplementation(
    async (_travelState: InteractiveTravelState, part: InteractiveTravelPart) => ({
      partNumber: part.partNumber,
      videoUrl: `/story-${part.partNumber}.mp4`,
    }),
  );
  mocks.writeLocalInteractiveTravelDurably.mockImplementation(
    (_petId: string, value: LocalInteractiveTravel) => {
      mocks.readLocalInteractiveTravel.mockReturnValue(value);
      return value;
    },
  );
  mocks.applyInteractiveTravelImpacts.mockImplementation((
    input: {
      travelId: string;
      parts: InteractiveTravelPart[];
      legacyAppliedResultParts?: number[];
    },
    expectedPetId?: string,
  ) => {
    const currentPet = readLocalPetState();
    return currentPet && currentPet.petId === expectedPetId
      ? applyInteractiveTravelImpactsToPet(
          currentPet,
          input.travelId,
          input.parts,
          input.legacyAppliedResultParts,
        )
      : null;
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("InteractiveTravelScreen", () => {
  it("auto-starts the demo opened from the main debug panel", async () => {
    mocks.canUseDebugMenu = true;
    window.history.replaceState({}, "", "/pet/pet-1/travel?demo=1");

    render(<InteractiveTravelScreen petId="pet-1" />);

    await waitFor(() => expect(mocks.getInteractiveTravelDemo).toHaveBeenCalledOnce());
    expect(await screen.findByText("Демо начинается!")).toBeInTheDocument();
  });

  it("renders a generated vertical video without exposing its still poster", async () => {
    const animatedPart = {
      ...pendingPart(1),
      backgroundVideoUrl: "/story-1.mp4",
    };
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([animatedPart]), "story"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    await waitFor(() =>
      expect(document.querySelector('video[src="/story-1.mp4"]')).not.toBeNull(),
    );
    const video = document.querySelector<HTMLVideoElement>('video[src="/story-1.mp4"]');
    expect(video).not.toBeNull();
    expect(video).not.toHaveAttribute("poster");
    expect(video).toHaveAttribute("autoplay");
    expect(video).toHaveAttribute("loop");
  });

  it("keeps the entry video visible instead of rendering a generated still", async () => {
    mocks.animateInteractiveTravelPart.mockReturnValue(new Promise(() => undefined));
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "story"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    await waitFor(() =>
      expect(document.querySelector('video[src^="/figma/travel-entry-bg.mp4"]')).not.toBeNull(),
    );
    expect(document.querySelector('img[src="/story-1.png"]')).toBeNull();
    expect(cssRule("viewport")).toContain("position: fixed");
    expect(cssRule("viewport")).toContain("inset: 0");
    expect(cssRule("scene")).toContain("width: 100%");
    expect(cssRule("background")).toContain("object-fit: cover");
    expect(cssRule("bubbleAnchor")).toContain("display: flex");
    expect(cssRule("bubbleAnchor")).toContain("justify-content: center");
  });

  it("keeps the previous scene while preparing the next part", async () => {
    const firstPart = {
      ...pendingPart(1),
      backgroundVideoUrl: "/story-1.mp4",
    };
    const secondPart = pendingPart(2);
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([firstPart, secondPart]), "departureWait", 2),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    expect(await screen.findByText(/Листик продолжает путешествие/u)).toBeInTheDocument();
    await waitFor(() =>
      expect(document.querySelector('video[src="/story-1.mp4"]')).not.toBeNull(),
    );
  });

  it("registers the Telegram BackButton in production-capable travel UI", async () => {
    mocks.readLocalInteractiveTravel.mockReturnValue(null);

    render(<InteractiveTravelScreen petId="pet-1" />);

    await waitFor(() => expect(mocks.useTelegramBackButton).toHaveBeenCalled());
    expect(mocks.useTelegramBackButton.mock.calls).not.toContainEqual([
      expect.any(Function),
      false,
    ]);
    expect(mocks.useTelegramBackButton.mock.calls.every((call) => call.length === 1)).toBe(true);
  });

  it("lets the user continue when the departure video is still pending", async () => {
    mocks.animateInteractiveTravelPart.mockReturnValue(new Promise(() => undefined));
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "departureWait"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Продолжить без видео" }));

    expect(await screen.findByText("Сначала дорога петляет")).toBeInTheDocument();
  });

  it("leaves departure wait automatically after its bounded deadline", async () => {
    vi.useFakeTimers();
    mocks.animateInteractiveTravelPart.mockReturnValue(new Promise(() => undefined));
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "departureWait"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("button", { name: "Продолжить без видео" })).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(90_000);
    });

    expect(screen.getByText("Сначала дорога петляет")).toBeInTheDocument();
  });

  it("resets server generations and local travel from the debug menu", async () => {
    mocks.canUseDebugMenu = true;
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "story"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Перезапустить путешествие" }));

    await waitFor(() =>
      expect(mocks.resetInteractiveTravelGeneration).toHaveBeenCalledWith(
        "interactive-travel-1",
      ),
    );
    expect(mocks.clearLocalInteractiveTravel).toHaveBeenCalledWith("pet-1");
  });

  it("runs the demo in memory without generation or local travel writes", async () => {
    vi.useFakeTimers();
    mocks.canUseDebugMenu = true;

    render(<InteractiveTravelScreen petId="pet-1" />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    fireEvent.click(screen.getByRole("button", { name: "Запустить демо-историю" }));
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText("Демо начинается!")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_200);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(screen.getByText("Листик отправился в путь, дорога займет какое-то время..."))
      .toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_999);
    });
    expect(screen.queryByText("Готовая демо-история")).not.toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(screen.getByText("Готовая демо-история")).toBeInTheDocument();

    expect(mocks.continueInteractiveTravel).not.toHaveBeenCalled();
    expect(mocks.illustrateInteractiveTravelPart).not.toHaveBeenCalled();
    expect(mocks.animateInteractiveTravelPart).not.toHaveBeenCalled();
    expect(mocks.writeLocalInteractiveTravelDurably).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("gigagochi.interactive-travel.v3:pet-1")).toBeNull();
  });

  it("queues a durable cancellation before clearing the local session", async () => {
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "story"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Создать новую историю" }),
    );

    await waitFor(() =>
      expect(mocks.enqueueInteractiveTravelCancel).toHaveBeenCalledWith(
        "interactive-travel-1",
      ),
    );
    expect(mocks.clearLocalInteractiveTravel).toHaveBeenCalledWith("pet-1");
    expect(mocks.enqueueInteractiveTravelCancel.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.clearLocalInteractiveTravel.mock.invocationCallOrder[0],
    );
  });

  it("returns from a custom destination to the generated options and restores focus", async () => {
    mocks.readLocalInteractiveTravel.mockReturnValue(null);

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Свой вариант" }));
    expect(await screen.findByLabelText("Свой вариант путешествия")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Вернуться к вариантам" }));

    const customTrigger = await screen.findByRole("button", { name: "Свой вариант" });
    expect(screen.queryByLabelText("Свой вариант путешествия")).not.toBeInTheDocument();
    expect(customTrigger).toHaveFocus();
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("keeps the destination action visible and explains why it is disabled", async () => {
    mocks.readLocalInteractiveTravel.mockReturnValue(null);

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Свой вариант" }));
    const input = screen.getByLabelText("Свой вариант путешествия");
    const submit = screen.getByRole("button", { name: "Путешествие" });
    expect(input).toHaveAttribute(
      "aria-describedby",
      "travel-destination-requirement",
    );
    expect(screen.getByText("Напиши место, чтобы начать путешествие")).toBeInTheDocument();
    expect(submit).toBeDisabled();

    fireEvent.change(input, { target: { value: "В город великанов" } });
    expect(submit).toBeEnabled();
  });

  it("returns from a custom action to the current choices and restores focus", async () => {
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "choice"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Свой вариант" }));
    expect(screen.getByLabelText("Свой вариант действия")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Вернуться к вариантам" }));

    const customTrigger = await screen.findByRole("button", { name: "Свой вариант" });
    expect(screen.queryByLabelText("Свой вариант действия")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Идти вперёд" })).toBeInTheDocument();
    expect(customTrigger).toHaveFocus();
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it("keeps the advice action visible and explains why it is disabled", async () => {
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "choice"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Свой вариант" }));
    const input = screen.getByLabelText("Свой вариант действия");
    const submit = screen.getByRole("button", { name: "Подсказать" });
    expect(input).toHaveAttribute("aria-describedby", "travel-advice-requirement");
    expect(screen.getByText("Напиши совет, чтобы подсказать действие")).toBeInTheDocument();
    expect(submit).toBeDisabled();

    fireEvent.change(input, { target: { value: "Проверить мост" } });
    expect(submit).toBeEnabled();
  });

  it("opens a native custom destination input and starts with the generated reaction", async () => {
    mocks.readLocalInteractiveTravel.mockReturnValue(null);
    mocks.startInteractiveTravel.mockResolvedValue({ travel: travel([pendingPart(1)]) });

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Свой вариант" }));
    fireEvent.change(screen.getByLabelText("Свой вариант путешествия"), {
      target: { value: "В город великанов" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Путешествие" }));

    expect(await screen.findByText("Облачный город?")).toBeInTheDocument();
    expect(mocks.startInteractiveTravel).toHaveBeenCalledWith(
      "В город великанов",
      pet,
      {
        includeDebug: false,
        history: [],
        memoryContext: {
          relevantMemories: [],
          summary: undefined,
          userProfile: undefined,
        },
      },
    );
    expect(screen.getByRole("img", { name: "Листик" })).toHaveAttribute(
      "src",
      "/teen-idle-foreground.png?travel_foreground_v=20260714-1",
    );
    expect(screen.getByRole("button", { name: "Создать новую историю" })).toBeInTheDocument();
  });

  it("adopts a travel started by another tab before entering the start mutation", async () => {
    let stored: LocalInteractiveTravel | null = null;
    mocks.readLocalInteractiveTravel.mockImplementation(() => stored);
    mocks.startInteractiveTravel.mockResolvedValue({ travel: travel([pendingPart(1)]) });

    render(<InteractiveTravelScreen petId="pet-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Свой вариант" }));
    fireEvent.change(screen.getByLabelText("Свой вариант путешествия"), {
      target: { value: "В город великанов" },
    });
    stored = restoredSession(
      travel([{ ...pendingPart(1), backgroundVideoUrl: "/other-tab.mp4" }]),
      "story",
    );
    fireEvent.click(screen.getByRole("button", { name: "Путешествие" }));

    expect(await screen.findByText("Сначала дорога петляет")).toBeInTheDocument();
    expect(mocks.startInteractiveTravel).not.toHaveBeenCalled();
  });

  it("changes story portions only by Далее, then shows generated horizontal choices", async () => {
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "story"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    expect(await screen.findByText("Сначала дорога петляет")).toBeInTheDocument();
    expect(screen.queryByText("Потом виден мост")).not.toBeInTheDocument();
    const nextButton = screen.getByRole("button", { name: "Далее" });
    expect(nextButton.className).toMatch(/storyGlassButton/u);
    expect(screen.getByTestId("travel-speech").parentElement).not.toHaveAttribute("aria-live");
    expect(screen.getByTestId("travel-speech")).toHaveAttribute("data-min-font-size", "14");
    expect(screen.getByTestId("travel-speech")).toHaveAttribute("data-fixed-size", "false");
    fireEvent.click(nextButton);
    expect(await screen.findByText("Потом виден мост")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Далее" }));

    expect(await screen.findByText("Что делать на шаге 1?")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Варианты действий" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Идти вперёд" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Свой вариант" })).toBeInTheDocument();
  });

  it("shows every challenge portion before revealing choices with the direct question", async () => {
    const pending = pendingPart(1);
    pending.challenge =
      "На воротах древнего святилища появилась цветная корочка. "
      + "Страж считает её мхом, а алхимик — минералом. "
      + "Кто образует лишайник?";
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pending]), "choice"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    expect(
      await screen.findByText("На воротах древнего святилища появилась цветная корочка"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Варианты действий" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Далее" }));
    expect(
      await screen.findByText("Страж считает её мхом, а алхимик — минералом"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Варианты действий" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Далее" }));
    expect(await screen.findByText("Кто образует лишайник?")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Варианты действий" })).toBeInTheDocument();
  });

  it("hides the travel interface and shows the standard thinking animation while starting", async () => {
    const response = deferred<{ travel: InteractiveTravelState }>();
    mocks.readLocalInteractiveTravel.mockReturnValue(null);
    mocks.startInteractiveTravel.mockReturnValue(response.promise);

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Свой вариант" }));
    fireEvent.change(screen.getByLabelText("Свой вариант путешествия"), {
      target: { value: "В город великанов" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Путешествие" }));

    expect(screen.queryByRole("img", { name: "Листик" })).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Персонаж думает" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Путешествие" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Вернуться к персонажу" })).not.toBeInTheDocument();
  });

  it("does not start paid media when the new travel cannot be stored durably", async () => {
    mocks.readLocalInteractiveTravel.mockReturnValue(null);
    mocks.startInteractiveTravel.mockResolvedValue({
      travel: travel([pendingPart(1)]),
    });
    mocks.writeLocalInteractiveTravelDurably.mockReturnValue(null);

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Свой вариант" }));
    fireEvent.change(screen.getByLabelText("Свой вариант путешествия"), {
      target: { value: "В город великанов" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Путешествие" }));

    expect(
      await screen.findByText(/Не удалось сохранить прогресс/u),
    ).toBeInTheDocument();
    expect(mocks.illustrateInteractiveTravelPart).not.toHaveBeenCalled();
    expect(mocks.animateInteractiveTravelPart).not.toHaveBeenCalled();
  });

  it("ignores a start response from the previous pet after the route pet changes", async () => {
    const response = deferred<{ travel: InteractiveTravelState }>();
    mocks.readLocalInteractiveTravel.mockReturnValue(null);
    mocks.startInteractiveTravel.mockReturnValue(response.promise);

    const { rerender } = render(<InteractiveTravelScreen petId="pet-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Свой вариант" }));
    fireEvent.change(screen.getByLabelText("Свой вариант путешествия"), {
      target: { value: "В город великанов" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Путешествие" }));
    await waitFor(() => expect(mocks.startInteractiveTravel).toHaveBeenCalledOnce());

    rerender(<InteractiveTravelScreen petId="pet-2" />);
    mocks.writeLocalInteractiveTravelDurably.mockClear();
    await act(async () => {
      response.resolve({ travel: travel([pendingPart(1)]) });
      await response.promise;
    });

    expect(mocks.writeLocalInteractiveTravelDurably).not.toHaveBeenCalled();
  });

  it("hides the custom action interface behind the standard loader while continuing", async () => {
    const response = deferred<{ travel: InteractiveTravelState }>();
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "choice"),
    );
    mocks.continueInteractiveTravel.mockReturnValue(response.promise);

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Свой вариант" }));
    fireEvent.change(screen.getByLabelText("Свой вариант действия"), {
      target: { value: "Прыгнуть через мост" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подсказать" }));

    expect(screen.queryByRole("button", { name: "Подсказать" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Свой вариант действия")).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Персонаж думает" })).toBeInTheDocument();
  });

  it("keeps long choices and answers visually readable inside the fixed layout", () => {
    const actionRule = cssRule("actionButton");
    const actionsScrollRule = cssRule("actionsScroll");
    const glassRule = cssRule("storyGlassButton");

    expect(actionRule).toContain("white-space: nowrap");
    expect(actionRule).not.toContain("max-width");
    expect(actionRule).not.toContain("text-overflow");
    expect(actionsScrollRule).toContain("overflow-x: auto");
    expect(actionsScrollRule).toContain("touch-action: pan-x");
    expect(actionsScrollRule).not.toContain("scroll-snap-type");
    expect(glassRule).toContain("background: rgb(22 22 22 / 94%)");
    expect(glassRule).not.toContain("backdrop-filter");
  });

  it("applies a resolved impact once and preloads the next illustration", async () => {
    const firstTravel = travel([pendingPart(1)]);
    mocks.readLocalInteractiveTravel.mockReturnValue(restoredSession(firstTravel, "choice"));
    mocks.continueInteractiveTravel.mockResolvedValue({
      travel: resolvedFirstWithPendingSecond(),
    });

    render(<InteractiveTravelScreen petId="pet-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Идти вперёд" }));

    expect(
      await screen.findByText("Листик перешёл мост и добрался до башни"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Твой ответ")).not.toBeInTheDocument();
    expect(mocks.applyInteractiveTravelImpacts).toHaveBeenCalledTimes(1);
    expect(mocks.applyInteractiveTravelImpacts).toHaveBeenCalledWith(
      expect.objectContaining({
        travelId: "interactive-travel-1",
        legacyAppliedResultParts: [],
      }),
      "pet-1",
    );
    expect(mocks.illustrateInteractiveTravelPart).toHaveBeenCalledWith(
      expect.objectContaining({ travelId: "interactive-travel-1" }),
      expect.objectContaining({ partNumber: 2 }),
      pet,
    );
    await waitFor(() => {
      expect(mocks.writeLocalInteractiveTravelDurably).toHaveBeenCalledWith(
        "pet-1",
        expect.objectContaining({ appliedResultParts: [1] }),
      );
    });
  });

  it("adopts a part advanced by another tab instead of continuing it twice", async () => {
    const initial = restoredSession(
      travel([{ ...pendingPart(1), backgroundVideoUrl: "/part-1.mp4" }]),
      "choice",
    );
    let stored = initial;
    mocks.readLocalInteractiveTravel.mockImplementation(() => stored);

    render(<InteractiveTravelScreen petId="pet-1" />);
    const choice = await screen.findByRole("button", { name: "Идти вперёд" });
    stored = restoredSession(resolvedFirstWithPendingSecond(), "result");
    fireEvent.click(choice);

    expect(
      await screen.findByText("Листик перешёл мост и добрался до башни"),
    ).toBeInTheDocument();
    expect(mocks.continueInteractiveTravel).not.toHaveBeenCalled();
  });

  it("does not start next-part media when the stat receipt is not durable", async () => {
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "choice"),
    );
    mocks.continueInteractiveTravel.mockResolvedValue({
      travel: resolvedFirstWithPendingSecond(),
    });
    mocks.applyInteractiveTravelImpacts.mockReturnValue(null);

    render(<InteractiveTravelScreen petId="pet-1" />);
    const choice = await screen.findByRole("button", { name: "Идти вперёд" });
    mocks.illustrateInteractiveTravelPart.mockClear();
    mocks.animateInteractiveTravelPart.mockClear();
    fireEvent.click(choice);

    expect(await screen.findByText(/Не удалось сохранить прогресс/u)).toBeInTheDocument();
    expect(mocks.writeLocalInteractiveTravelDurably).toHaveBeenCalledWith(
      "pet-1",
      expect.objectContaining({
        travel: expect.objectContaining({
          parts: expect.arrayContaining([
            expect.objectContaining({ partNumber: 1, result: expect.any(Object) }),
          ]),
        }),
        appliedResultParts: [],
      }),
    );
    expect(mocks.illustrateInteractiveTravelPart).not.toHaveBeenCalled();
    expect(mocks.animateInteractiveTravelPart).not.toHaveBeenCalled();
  });

  it("bases a resolved impact on stats updated during the request", async () => {
    const response = deferred<{ travel: InteractiveTravelState }>();
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "choice"),
    );
    mocks.continueInteractiveTravel.mockReturnValue(response.promise);

    render(<InteractiveTravelScreen petId="pet-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Идти вперёд" }));

    writeLocalPetState({
      ...pet,
      stats: { hunger: 70, happiness: 65, energy: 44 },
    });
    await act(async () => {
      response.resolve({ travel: resolvedFirstWithPendingSecond() });
      await response.promise;
    });

    await waitFor(() => expect(mocks.applyInteractiveTravelImpacts).toHaveBeenCalledOnce());
    const application = mocks.applyInteractiveTravelImpacts.mock.results.at(-1)?.value;
    expect(application?.pet.stats).toEqual({ hunger: 74, happiness: 65, energy: 44 });
  });

  it("persists a late animation after advice advances the UI request epoch", async () => {
    const animation = deferred<{ partNumber: number; videoUrl: string }>();
    mocks.animateInteractiveTravelPart.mockReturnValue(animation.promise);
    mocks.continueInteractiveTravel.mockReturnValue(new Promise(() => undefined));
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "story"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    await waitFor(() => expect(mocks.animateInteractiveTravelPart).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "Далее" }));
    await screen.findByText("Потом виден мост");
    fireEvent.click(screen.getByRole("button", { name: "Далее" }));
    fireEvent.click(await screen.findByRole("button", { name: "Идти вперёд" }));

    await act(async () => {
      animation.resolve({ partNumber: 1, videoUrl: "/story-1.mp4" });
      await animation.promise;
    });

    await waitFor(() =>
      expect(mocks.writeLocalInteractiveTravelDurably).toHaveBeenCalledWith(
        "pet-1",
        expect.objectContaining({
          travel: expect.objectContaining({
            parts: [expect.objectContaining({ backgroundVideoUrl: "/story-1.mp4" })],
          }),
        }),
      ),
    );
  });

  it("queues a late media commit behind continue and patches its newer result", async () => {
    let lockTail: Promise<unknown> = Promise.resolve();
    mocks.withLocalInteractiveTravelMutationLock.mockImplementation(
      (
        _petId: string,
        mutation: (guard: { assertOwned: () => void }) => unknown,
      ) => {
        const result = lockTail.then(() => mutation({ assertOwned: vi.fn() }));
        lockTail = result.then(() => undefined, () => undefined);
        return result;
      },
    );
    const animation = deferred<{ partNumber: number; videoUrl: string }>();
    const continuation = deferred<{ travel: InteractiveTravelState }>();
    mocks.animateInteractiveTravelPart.mockReturnValue(animation.promise);
    mocks.continueInteractiveTravel.mockReturnValue(continuation.promise);
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "story"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);
    await waitFor(() => expect(mocks.animateInteractiveTravelPart).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "Далее" }));
    await screen.findByText("Потом виден мост");
    fireEvent.click(screen.getByRole("button", { name: "Далее" }));
    fireEvent.click(await screen.findByRole("button", { name: "Идти вперёд" }));
    await waitFor(() => expect(mocks.continueInteractiveTravel).toHaveBeenCalledOnce());

    await act(async () => {
      animation.resolve({ partNumber: 1, videoUrl: "/queued-part-1.mp4" });
      await animation.promise;
    });
    expect(
      mocks.writeLocalInteractiveTravelDurably.mock.calls.some(([, value]) =>
        (value as LocalInteractiveTravel).travel.parts.some(
          (part) => part.backgroundVideoUrl === "/queued-part-1.mp4",
        ),
      ),
    ).toBe(false);

    const completedPart = resolvedFirstWithPendingSecond().parts[0];
    await act(async () => {
      continuation.resolve({ travel: travel([completedPart], true) });
      await continuation.promise;
    });

    await waitFor(() =>
      expect(mocks.writeLocalInteractiveTravelDurably).toHaveBeenCalledWith(
        "pet-1",
        expect.objectContaining({
          travel: expect.objectContaining({
            completed: true,
            parts: [
              expect.objectContaining({
                result: expect.any(Object),
                backgroundVideoUrl: "/queued-part-1.mp4",
              }),
            ],
          }),
        }),
      ),
    );
    expect(mocks.withLocalInteractiveTravelMutationLock).toHaveBeenCalledWith(
      "pet-1",
      expect.any(Function),
      { acquireTimeoutMs: 310_000 },
    );
  });

  it("patches a late media URL into the latest cross-tab snapshot", async () => {
    const animation = deferred<{ partNumber: number; videoUrl: string }>();
    const initial = restoredSession(travel([pendingPart(1)]), "story");
    let stored = initial;
    mocks.readLocalInteractiveTravel.mockImplementation(() => stored);
    mocks.animateInteractiveTravelPart.mockReturnValue(animation.promise);

    render(<InteractiveTravelScreen petId="pet-1" />);
    await waitFor(() => expect(mocks.animateInteractiveTravelPart).toHaveBeenCalledOnce());
    stored = restoredSession(resolvedFirstWithPendingSecond(), "result");

    await act(async () => {
      animation.resolve({ partNumber: 1, videoUrl: "/late-part-1.mp4" });
      await animation.promise;
    });

    await waitFor(() =>
      expect(mocks.writeLocalInteractiveTravelDurably).toHaveBeenCalledWith(
        "pet-1",
        expect.objectContaining({
          travel: expect.objectContaining({
            parts: [
              expect.objectContaining({
                partNumber: 1,
                result: expect.any(Object),
                backgroundVideoUrl: "/late-part-1.mp4",
              }),
              expect.objectContaining({ partNumber: 2 }),
            ],
          }),
        }),
      ),
    );
  });

  it("reconciles an advance written by another tab through the storage event", async () => {
    const initial = restoredSession(
      travel([{ ...pendingPart(1), backgroundVideoUrl: "/part-1.mp4" }]),
      "choice",
    );
    let stored = initial;
    mocks.readLocalInteractiveTravel.mockImplementation(() => stored);

    render(<InteractiveTravelScreen petId="pet-1" />);
    expect(await screen.findByText("Что делать на шаге 1?")).toBeInTheDocument();
    stored = restoredSession(resolvedFirstWithPendingSecond(), "result");
    await act(async () => {
      window.dispatchEvent(new StorageEvent("storage", {
        key: "gigagochi.interactive-travel.v3:pet-1",
      }));
    });

    expect(
      await screen.findByText("Листик перешёл мост и добрался до башни"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(mocks.writeLocalInteractiveTravelDurably).toHaveBeenCalled(),
    );
    const writesAfterFirstEvent = mocks.writeLocalInteractiveTravelDurably.mock.calls.length;
    await act(async () => {
      window.dispatchEvent(new StorageEvent("storage", {
        key: "gigagochi.interactive-travel.v3:pet-1",
      }));
      await Promise.resolve();
    });
    expect(mocks.writeLocalInteractiveTravelDurably).toHaveBeenCalledTimes(
      writesAfterFirstEvent,
    );
  });

  it("reconciles a travel cleared by another tab through the storage event", async () => {
    let stored: LocalInteractiveTravel | null = restoredSession(
      travel([{ ...pendingPart(1), backgroundVideoUrl: "/part-1.mp4" }]),
      "choice",
    );
    mocks.readLocalInteractiveTravel.mockImplementation(() => stored);

    render(<InteractiveTravelScreen petId="pet-1" />);
    expect(await screen.findByText("Что делать на шаге 1?")).toBeInTheDocument();
    stored = null;
    await act(async () => {
      window.dispatchEvent(new StorageEvent("storage", {
        key: "gigagochi.interactive-travel.v3:pet-1",
      }));
    });

    expect(await screen.findByRole("button", { name: "Свой вариант" })).toBeInTheDocument();
  });

  it("rejects a late animation after the travel is reset", async () => {
    const animation = deferred<{ partNumber: number; videoUrl: string }>();
    mocks.animateInteractiveTravelPart.mockReturnValue(animation.promise);
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "story"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    await waitFor(() => expect(mocks.animateInteractiveTravelPart).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "Создать новую историю" }));
    mocks.writeLocalInteractiveTravelDurably.mockClear();
    await act(async () => {
      animation.resolve({ partNumber: 1, videoUrl: "/stale-story-1.mp4" });
      await animation.promise;
    });

    expect(mocks.writeLocalInteractiveTravelDurably).not.toHaveBeenCalled();
  });

  it("queues a completed finale once while late media is patched separately", async () => {
    const animation = deferred<{ partNumber: number; videoUrl: string }>();
    const completedPart = resolvedFirstWithPendingSecond().parts[0];
    mocks.animateInteractiveTravelPart.mockReturnValue(animation.promise);
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([completedPart], true), "completed"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    await waitFor(() => {
      expect(mocks.enqueueInteractiveTravelCapture).toHaveBeenCalledOnce();
      expect(mocks.animateInteractiveTravelPart).toHaveBeenCalledOnce();
    });
    await act(async () => {
      animation.resolve({ partNumber: 1, videoUrl: "/story-1.mp4" });
      await animation.promise;
    });

    expect(mocks.enqueueInteractiveTravelCapture).toHaveBeenCalledOnce();
    expect(mocks.writeLocalInteractiveTravelDurably).toHaveBeenLastCalledWith(
      "pet-1",
      expect.objectContaining({
        travel: expect.objectContaining({
          parts: [expect.objectContaining({ backgroundVideoUrl: "/story-1.mp4" })],
        }),
      }),
    );
  });

  it("starts a new travel from the completed production UI", async () => {
    const completedPart = resolvedFirstWithPendingSecond().parts[0];
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([completedPart], true), "completed"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);
    expect(
      await screen.findByText("Я устал — на сегодня хватит. Отправляюсь домой."),
    ).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Новое путешествие" }));

    await waitFor(() =>
      expect(mocks.clearLocalInteractiveTravel).toHaveBeenCalledWith("pet-1"),
    );
    expect(await screen.findByRole("button", { name: "Свой вариант" })).toBeInTheDocument();
  });

  it("hides travel controls while a continue request is pending", async () => {
    const response = deferred<{ travel: InteractiveTravelState }>();
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "choice"),
    );
    mocks.continueInteractiveTravel.mockReturnValue(response.promise);

    render(<InteractiveTravelScreen petId="pet-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Идти вперёд" }));

    expect(screen.queryByRole("button", { name: "Создать новую историю" })).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Персонаж думает" })).toBeInTheDocument();
    expect(mocks.applyInteractiveTravelImpacts).not.toHaveBeenCalled();
  });
});
