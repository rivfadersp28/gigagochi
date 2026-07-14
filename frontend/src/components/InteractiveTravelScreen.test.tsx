import { readFileSync } from "node:fs";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LocalInteractiveTravel } from "@/lib/localInteractiveTravel";
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
  applyStatsPatch: vi.fn(),
  clearLocalInteractiveTravel: vi.fn(),
  continueInteractiveTravel: vi.fn(),
  getInteractiveTravelSuggestions: vi.fn(),
  illustrateInteractiveTravelPart: vi.fn(),
  push: vi.fn(),
  readLocalInteractiveTravel: vi.fn(),
  replace: vi.fn(),
  startInteractiveTravel: vi.fn(),
  resetInteractiveTravelGeneration: vi.fn(),
  canUseDebugMenu: false,
  writeLocalInteractiveTravel: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
}));

vi.mock("@/lib/api", () => ({
  animateInteractiveTravelPart: (...args: unknown[]) =>
    mocks.animateInteractiveTravelPart(...args),
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
  readLocalInteractiveTravel: (...args: unknown[]) => mocks.readLocalInteractiveTravel(...args),
  writeLocalInteractiveTravel: (...args: unknown[]) =>
    mocks.writeLocalInteractiveTravel(...args),
}));

vi.mock("@/lib/interactiveTravelDebugApi", () => ({
  resetInteractiveTravelGeneration: (...args: unknown[]) =>
    mocks.resetInteractiveTravelGeneration(...args),
}));

vi.mock("@/lib/telegram", () => ({
  canUseDebugMenu: () => mocks.canUseDebugMenu,
  hapticNotification: vi.fn(),
  setTelegramBackgroundColor: vi.fn(),
  useTelegramBackButton: vi.fn(),
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
    applyStatsPatch: (...args: unknown[]) => mocks.applyStatsPatch(...args),
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
    travelId: "travel-1",
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
  mocks.canUseDebugMenu = false;
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
});

afterEach(cleanup);

describe("InteractiveTravelScreen", () => {
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
  });

  it("resets server generations and local travel from the debug menu", async () => {
    mocks.canUseDebugMenu = true;
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "story"),
    );

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Перезапустить путешествие" }));

    await waitFor(() =>
      expect(mocks.resetInteractiveTravelGeneration).toHaveBeenCalledWith("travel-1"),
    );
    expect(mocks.clearLocalInteractiveTravel).toHaveBeenCalledWith("pet-1");
  });

  it("returns from a custom destination to the generated options and restores focus", async () => {
    mocks.readLocalInteractiveTravel.mockReturnValue(null);

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Свой вариант" }));
    expect(screen.getByLabelText("Свой вариант путешествия")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Вернуться к вариантам" }));

    const customTrigger = await screen.findByRole("button", { name: "Свой вариант" });
    expect(screen.queryByLabelText("Свой вариант путешествия")).not.toBeInTheDocument();
    expect(customTrigger).toHaveFocus();
    expect(mocks.push).not.toHaveBeenCalled();
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
      "/teen-idle-foreground.png",
    );
    expect(screen.getByRole("button", { name: "Создать новую историю" })).toBeInTheDocument();
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
    expect(screen.getByTestId("travel-speech")).toHaveAttribute("data-min-font-size", "20");
    expect(screen.getByTestId("travel-speech")).toHaveAttribute("data-fixed-size", "false");
    fireEvent.click(nextButton);
    expect(await screen.findByText("Потом виден мост")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Далее" }));

    expect(await screen.findByText("Что делать на шаге 1?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Идти вперёд" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Свой вариант" })).toBeInTheDocument();
  });

  it("keeps loading submit buttons named for assistive technology", async () => {
    const response = deferred<{ travel: InteractiveTravelState }>();
    mocks.readLocalInteractiveTravel.mockReturnValue(null);
    mocks.startInteractiveTravel.mockReturnValue(response.promise);

    render(<InteractiveTravelScreen petId="pet-1" />);

    fireEvent.click(await screen.findByRole("button", { name: "Свой вариант" }));
    fireEvent.change(screen.getByLabelText("Свой вариант путешествия"), {
      target: { value: "В город великанов" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Путешествие" }));

    expect(screen.getByRole("button", { name: "Путешествие" })).toBeDisabled();
  });

  it("keeps the custom action submit button named while continuing", async () => {
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

    expect(screen.getByRole("button", { name: "Подсказать" })).toBeDisabled();
  });

  it("keeps long choices and answers visually readable inside the fixed layout", () => {
    const actionRule = cssRule("actionButton");
    const actionsScrollRule = cssRule("actionsScroll");
    const answerRule = cssRule("answerCaption");
    const glassRule = cssRule("storyGlassButton");

    expect(actionRule).toContain("white-space: nowrap");
    expect(actionRule).not.toContain("max-width");
    expect(actionRule).not.toContain("text-overflow");
    expect(actionsScrollRule).toContain("overflow-x: auto");
    expect(actionsScrollRule).toContain("touch-action: pan-x");
    expect(actionsScrollRule).not.toContain("scroll-snap-type");
    expect(answerRule).toContain("max-height: 84px");
    expect(answerRule).toContain("overflow-y: auto");
    expect(answerRule).toContain("white-space: normal");
    expect(glassRule).toContain("background: rgb(255 255 255 / 20%)");
    expect(glassRule).toContain("--tw-backdrop-blur: blur(10px)");
    expect(glassRule).toContain("backdrop-filter: var(--tw-backdrop-blur)");
  });

  it("applies a resolved impact once and preloads the next illustration", async () => {
    const firstTravel = travel([pendingPart(1)]);
    mocks.readLocalInteractiveTravel.mockReturnValue(restoredSession(firstTravel, "choice"));
    mocks.continueInteractiveTravel.mockResolvedValue({
      travel: resolvedFirstWithPendingSecond(),
    });

    render(<InteractiveTravelScreen petId="pet-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Идти вперёд" }));

    expect(await screen.findByText("Хорошая мысль!")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Твой ответ" })).toHaveAttribute("tabindex", "0");
    expect(mocks.applyStatsPatch).toHaveBeenCalledTimes(1);
    expect(mocks.applyStatsPatch).toHaveBeenCalledWith({
      stats: { hunger: 54, happiness: 50, energy: 50 },
    });
    expect(mocks.illustrateInteractiveTravelPart).toHaveBeenCalledWith(
      expect.objectContaining({ travelId: "travel-1" }),
      expect.objectContaining({ partNumber: 2 }),
      pet,
    );
    await waitFor(() => {
      expect(mocks.writeLocalInteractiveTravel).toHaveBeenCalledWith(
        "pet-1",
        expect.objectContaining({ appliedResultParts: [1] }),
      );
    });
  });

  it("drops a stale continue response after Создать новую историю", async () => {
    const response = deferred<{ travel: InteractiveTravelState }>();
    mocks.readLocalInteractiveTravel.mockReturnValue(
      restoredSession(travel([pendingPart(1)]), "choice"),
    );
    mocks.continueInteractiveTravel.mockReturnValue(response.promise);

    render(<InteractiveTravelScreen petId="pet-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Идти вперёд" }));
    fireEvent.click(screen.getByRole("button", { name: "Создать новую историю" }));
    await act(async () => {
      response.resolve({ travel: resolvedFirstWithPendingSecond() });
      await response.promise;
    });

    expect(mocks.clearLocalInteractiveTravel).toHaveBeenCalledWith("pet-1");
    expect(mocks.applyStatsPatch).not.toHaveBeenCalled();
    expect(await screen.findByText("Куда отправим Листик?")).toBeInTheDocument();
  });
});
