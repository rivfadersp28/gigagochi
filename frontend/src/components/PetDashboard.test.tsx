import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import {
  resetLocalPetState,
  writeLocalPetState,
} from "@/lib/localPetStorage";
import {
  readLocalPetFirstSession,
  restartLocalPetFirstSession,
  updateLocalPetFirstSession,
} from "@/lib/localPetFirstSession";
import type { LocalPetState } from "@/lib/types";

import { PetDashboard } from "./PetDashboard";

const mocks = vi.hoisted(() => ({
  generateLocalAmbientMessage: vi.fn(),
  hapticNotification: vi.fn(),
  introduction: "Привет" as string | null,
  interactiveTravel: false,
  localPet: undefined as unknown,
  queueOutfitGeneration: vi.fn(),
  resumeCompletedPetGeneration: vi.fn(),
  router: {
    push: vi.fn(),
    replace: vi.fn(),
  },
  runLocalPetChatTurn: vi.fn(),
  simplifyOutfitRequest: vi.fn(),
  storyHistory: [] as Array<{ id: string; title: string; text: string }>,
  useTelegramBackButton: vi.fn(),
  withLocalPetMutationLock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => mocks.router,
}));

vi.mock("@/lib/useLocalPetState", () => ({
  useLocalPetState: () => mocks.localPet,
}));

vi.mock("@/lib/localPetChatTurn", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/localPetChatTurn")>(),
  runLocalPetChatTurn: mocks.runLocalPetChatTurn,
}));

vi.mock("@/lib/localPetMutationLock", () => ({
  withLocalPetMutationLock: (...args: [
    string,
    string,
    (guard: { assertOwned: () => void }) => unknown,
    unknown?,
  ]) => mocks.withLocalPetMutationLock(...args),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  generateLocalAmbientMessage: (...args: unknown[]) =>
    mocks.generateLocalAmbientMessage(...args),
  queueOutfitGeneration: (...args: unknown[]) => mocks.queueOutfitGeneration(...args),
  resumeCompletedPetGeneration: (...args: unknown[]) =>
    mocks.resumeCompletedPetGeneration(...args),
  simplifyOutfitRequest: (...args: unknown[]) => mocks.simplifyOutfitRequest(...args),
}));

vi.mock("@/lib/localPetIntroduction", () => ({
  claimPetIntroduction: () => mocks.introduction,
}));

vi.mock("@/lib/petSpeechAudio", () => ({
  primePetSpeechAudio: vi.fn(),
  stopPetSpeechAudio: vi.fn(),
}));

vi.mock("@/lib/petTapAudio", () => ({
  playPetTapSound: vi.fn(),
  primePetTapSound: vi.fn(),
}));

vi.mock("@/lib/petFeedAudio", () => ({
  playPetFeedSound: vi.fn(),
  primePetFeedSound: vi.fn(),
}));

vi.mock("@/lib/telegram", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/telegram")>(),
  canUseDebugMenu: () => false,
  canUseInteractiveTravel: () => false,
  hapticImpact: vi.fn(),
  hapticNotification: mocks.hapticNotification,
  setTelegramBackgroundColor: vi.fn(),
  useTelegramBackButton: (...args: unknown[]) => mocks.useTelegramBackButton(...args),
}));

vi.mock("@/lib/useTelegramCapabilities", () => ({
  useTelegramCapabilities: () => ({
    debugMenu: false,
    interactiveTravel: mocks.interactiveTravel,
  }),
}));

vi.mock("./pet-dashboard/useConversationKeyboardOffset", () => ({
  useConversationKeyboardOffset: () => 0,
}));

vi.mock("./pet-dashboard/usePetBackgroundAssets", () => ({
  usePetBackgroundAssets: vi.fn(),
}));

vi.mock("./pet-dashboard/usePetPushSnapshotSync", () => ({
  usePetPushSnapshotSync: vi.fn(),
}));

vi.mock("./pet-dashboard/usePetTapBulge", () => ({
  usePetTapBulge: () => ({
    canvasRef: { current: null },
    isBulgeVisible: false,
    triggerPetTapBulge: vi.fn(),
  }),
}));

vi.mock("./pet-dashboard/storyHistory", () => ({
  storyHistoryFromPet: () => mocks.storyHistory,
}));

const pet: LocalPetState = {
  version: 2,
  petId: "pet-dashboard-race",
  description: "дракон",
  createdAt: "2026-07-15T10:00:00.000Z",
  updatedAt: "2026-07-15T10:00:00.000Z",
  lastInteractionAt: "2026-07-15T10:00:00.000Z",
  lastStatsTickAt: "2026-07-15T10:00:00.000Z",
  lastStatTickAt: {
    hunger: "2026-07-15T10:00:00.000Z",
    happiness: "2026-07-15T10:00:00.000Z",
    energy: "2026-07-15T10:00:00.000Z",
  },
  stage: "baby",
  mood: "idle",
  stats: { hunger: 50, happiness: 50, energy: 50 },
};

class ImmediatelyLoadedImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;

  decode() {
    return Promise.resolve();
  }

  set src(_value: string) {
    queueMicrotask(() => this.onload?.());
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("Image", ImmediatelyLoadedImage);
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: true,
    media: "(prefers-reduced-motion: reduce)",
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    disconnect() {}
  });
  vi.clearAllMocks();
  mocks.withLocalPetMutationLock.mockImplementation(
    async (
      _petId: string,
      _scope: string,
      mutation: (guard: { assertOwned: () => void }) => unknown,
    ) => mutation({ assertOwned: vi.fn() }),
  );
  mocks.introduction = "Привет";
  mocks.interactiveTravel = false;
  mocks.storyHistory = [];
  mocks.queueOutfitGeneration.mockResolvedValue("outfit-job-1");
  mocks.resumeCompletedPetGeneration.mockReturnValue(new Promise(() => undefined));
  mocks.simplifyOutfitRequest.mockResolvedValue({
    displayItem: "плащ",
    generationDescription: "зелёный походный плащ",
  });
  window.localStorage.clear();
  window.sessionStorage.clear();
  writeLocalPetState(pet);
  mocks.localPet = {
    pet,
    status: "ready",
    error: null,
    create: vi.fn(),
    feed: vi.fn(),
    play: vi.fn(),
    registerPetTap: vi.fn(),
    reset: vi.fn(),
    resetStats: vi.fn(),
    kill: vi.fn(),
    revive: vi.fn(),
    updateName: vi.fn(),
    applyGeneratedAssets: vi.fn(),
    applyMoodHint: vi.fn(),
    applyLiteOverlayPatch: vi.fn(),
    applyStoryLibraryPatch: vi.fn(),
    applyRecentStoryEventsPatch: vi.fn(),
    applyStatsPatch: vi.fn(),
    spendExperience: vi.fn(() => true),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("keeps the bottom actions enabled when onboarding is not opted in", async () => {
  const petWithPendingIntroduction: LocalPetState = {
    ...pet,
    introductionPending: true,
  };
  writeLocalPetState(petWithPendingIntroduction);
  (mocks.localPet as { pet: LocalPetState }).pet = petWithPendingIntroduction;
  mocks.interactiveTravel = true;

  render(<PetDashboard petId={petWithPendingIntroduction.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  expect(screen.getByRole("button", { name: "Покормить" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "В путешествие" })).toBeEnabled();
  expect(screen.getByLabelText("Баланс опыта: 450")).toBeInTheDocument();
  expect(screen.queryByRole("progressbar", { name: "Опыт 450 из 3000" }))
    .not.toBeInTheDocument();
});

it("guides the local first session through chat and both cards", async () => {
  const firstSessionPet: LocalPetState = {
    ...pet,
    name: "Листик",
    introductionPending: true,
  };
  window.localStorage.setItem("tamagochi:v2:first-session-enabled", "1");
  writeLocalPetState(firstSessionPet);
  const localPetMock = mocks.localPet as {
    pet: LocalPetState;
    feed: ReturnType<typeof vi.fn>;
  };
  localPetMock.pet = firstSessionPet;
  localPetMock.feed.mockReturnValue(firstSessionPet);
  mocks.interactiveTravel = true;
  mocks.runLocalPetChatTurn.mockResolvedValue({
    response: { reply: "Очень приятно!", happinessDelta: 0 },
  });

  render(<PetDashboard petId={firstSessionPet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  const onboardingChatButton = screen.getByRole("button", { name: "Поболтать" });
  expect(onboardingChatButton).toBeEnabled();
  expect(onboardingChatButton.parentElement).toHaveClass("w-full", "justify-center");
  expect(screen.queryByRole("button", { name: "Покормить" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/Баланс опыта/u)).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Поболтать" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Сообщение персонажу" }), {
    target: { value: "Меня зовут Сергей" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1_100);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3_100);
  });
  const firstReplyTransform = mocks.runLocalPetChatTurn.mock.calls[0]?.[0]?.replyTransform;
  expect(firstReplyTransform?.("Очень приятно! А как твои дела?")).toBe("Очень приятно!");
  expect(screen.getByLabelText("А чем ты любишь заниматься?")).toBeInTheDocument();

  fireEvent.change(screen.getByRole("textbox", { name: "Сообщение персонажу" }), {
    target: { value: "Люблю ходить в походы" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4_000);
  });
  const secondReplyTransform = mocks.runLocalPetChatTurn.mock.calls[1]?.[0]?.replyTransform;
  expect(secondReplyTransform?.("Здорово! Часто ходишь?")).toBe("Здорово!");

  const backHandler = mocks.useTelegramBackButton.mock.calls.at(-1)?.[0] as
    | (() => void)
    | undefined;
  act(() => backHandler?.());
  expect(screen.getByRole("button", { name: "Покормить" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Поболтать" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Покормить" }));

  const berries = screen.getByRole("button", { name: "Дать персонажу ягоды" });
  const remedy = screen.getByRole("button", { name: "Дать персонажу листик" });
  expect(berries).toBeEnabled();
  expect(remedy).toBeDisabled();
  fireEvent.click(berries);
  expect(screen.getByLabelText("Хм, вкусно!")).toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3_100);
  });
  expect(screen.getByLabelText("Но что-то я себя неважно чувствую")).toBeInTheDocument();
  expect(berries).toBeEnabled();
  expect(remedy).toBeEnabled();

  fireEvent.click(remedy);
  const helpBat = screen.getByRole("button", { name: "Помочь летучей мыши" });
  expect(helpBat).toBeEnabled();
  expect(helpBat).toHaveClass("main-action-button--onboarding");
  expect(helpBat.parentElement).toHaveClass("w-full", "justify-center");
  expect(screen.queryByRole("button", { name: "Покормить" })).not.toBeInTheDocument();
  fireEvent.click(helpBat);
  expect(mocks.router.push).toHaveBeenCalledWith(`/pet/${firstSessionPet.petId}/travel`);
  expect(localPetMock.feed).toHaveBeenNthCalledWith(1, "berry-bowl");
  expect(localPetMock.feed).toHaveBeenNthCalledWith(2, "leaf-crunch");
});

it("finishes onboarding only after the first outfit request is queued", async () => {
  mocks.simplifyOutfitRequest.mockResolvedValueOnce({
    displayItem: "футболка Metallica",
    generationDescription: "футболка Metallica",
  });
  const moods = {
    idle: "/outfit-idle.png",
    happy: "/outfit-happy.png",
    hungry: "/outfit-hungry.png",
    sad: "/outfit-sad.png",
  };
  const onboardingPet: LocalPetState = {
    ...pet,
    experience: 100,
    introductionPending: true,
    assetSet: {
      assetSetId: "onboarding-assets",
      generatedAt: "2026-07-15T10:00:00.000Z",
      images: {
        baby: { ...moods },
        teen: { ...moods },
        adult: { ...moods },
      },
    },
  };
  writeLocalPetState(onboardingPet);
  const onboardingLocalPetMock = mocks.localPet as {
    pet: LocalPetState;
    spendExperience: ReturnType<typeof vi.fn>;
  };
  onboardingLocalPetMock.pet = onboardingPet;
  const started = restartLocalPetFirstSession(onboardingPet.petId)!;
  updateLocalPetFirstSession(started, "awaiting-completion-message");

  render(<PetDashboard petId={onboardingPet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  const outfitButton = screen.getByRole("button", { name: "Нарядить" });
  expect(outfitButton.parentElement).toHaveClass("w-full", "justify-center");
  expect(screen.queryByRole("button", { name: "Поболтать" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Покормить" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "В путешествие" })).not.toBeInTheDocument();
  expect(readLocalPetFirstSession(onboardingPet)?.stage)
    .toBe("awaiting-completion-message");

  fireEvent.click(outfitButton);
  expect(screen.getByLabelText("Во что мне нарядиться?")).toBeInTheDocument();
  expect(screen.getByLabelText("Баланс опыта: 100")).toBeInTheDocument();
  expect(screen.queryByText("Уровень: Малыш")).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/Голод 50 из 100/u)).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Описание наряда" }), {
    target: { value: "Зелёный походный плащ" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Создать наряд за 20 монет" }));
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(mocks.queueOutfitGeneration).toHaveBeenCalledOnce();
  expect(onboardingLocalPetMock.spendExperience).toHaveBeenCalledWith(20, onboardingPet.petId);
  expect(screen.getByLabelText("Футболка Metallica?")).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "Описание наряда" })).not.toBeInTheDocument();
  expect(readLocalPetFirstSession(onboardingPet)?.stage).toBe("completed");

  await act(async () => {
    await vi.advanceTimersByTimeAsync(3_100);
  });
  expect(screen.getByLabelText("Интересно")).toBeInTheDocument();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(3_100);
  });
  expect(screen.getByLabelText("Я получу заказ примерно через 10 минут"))
    .toBeInTheDocument();
});

it("keeps the outfit form open and shows a visible error when queuing fails", async () => {
  vi.stubEnv("NODE_ENV", "development");
  mocks.queueOutfitGeneration.mockRejectedValueOnce(new Error("queue unavailable"));
  const outfitPet: LocalPetState = {
    ...pet,
    experience: 100,
    assetSet: undefined,
  };
  writeLocalPetState(outfitPet);
  (mocks.localPet as { pet: LocalPetState }).pet = outfitPet;

  render(<PetDashboard petId={outfitPet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  fireEvent.click(screen.getByRole("button", { name: "Нарядить" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Описание наряда" }), {
    target: { value: "В красную футболку" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Создать наряд за 20 монет" }));

  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(
    screen.getByText("Не получилось нарядить персонажа. Попробуйте ещё раз.")
      .closest('[role="alert"]'),
  ).toHaveClass("conversation-input-error");
  expect(mocks.queueOutfitGeneration).toHaveBeenCalledOnce();
  expect(screen.getByRole("textbox", { name: "Описание наряда" })).toBeInTheDocument();
});

it("does not show a late chat error after the pet is reset", async () => {
  let rejectChat: ((reason: Error) => void) | undefined;
  mocks.runLocalPetChatTurn.mockImplementationOnce(
    () => new Promise((_resolve, reject) => {
      rejectChat = reject;
    }),
  );

  const { container } = render(<PetDashboard petId={pet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  fireEvent.click(screen.getByRole("button", { name: "Поболтать" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Сообщение персонажу" }), {
    target: { value: "Ответь позже" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  expect(mocks.runLocalPetChatTurn).toHaveBeenCalledTimes(1);

  resetLocalPetState();
  await act(async () => {
    rejectChat?.(new Error("late request failed"));
    await vi.advanceTimersByTimeAsync(1_000);
  });

  expect(container.querySelector("#dashboard-chat-error")).not.toBeInTheDocument();
  expect(mocks.hapticNotification).not.toHaveBeenCalledWith("error");
});

it("restores the quick-chat draft when sending fails", async () => {
  mocks.runLocalPetChatTurn.mockRejectedValueOnce(new Error("network failed"));
  render(<PetDashboard petId={pet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  fireEvent.click(screen.getByRole("button", { name: "Поболтать" }));
  const input = screen.getByRole("textbox", { name: "Сообщение персонажу" });
  fireEvent.change(input, { target: { value: "Сохрани мой черновик" } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1_000);
  });
  expect(screen.getByRole("alert")).toBeInTheDocument();
  expect(input).toHaveValue("Сохрани мой черновик");
});

it("does not start an ambient reply while a chat turn is still running", async () => {
  mocks.runLocalPetChatTurn.mockReturnValue(new Promise(() => undefined));
  render(<PetDashboard petId={pet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  fireEvent.click(screen.getByRole("button", { name: "Поболтать" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Сообщение персонажу" }), {
    target: { value: "Подожди с ответом" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
  mocks.generateLocalAmbientMessage.mockClear();

  const backHandler = mocks.useTelegramBackButton.mock.calls.at(-1)?.[0] as
    | (() => void)
    | undefined;
  act(() => backHandler?.());

  expect(mocks.generateLocalAmbientMessage).not.toHaveBeenCalled();
});

it("does not apply a late ambient reply after the dashboard unmounts", async () => {
  let resolveAmbient:
    | ((value: { reply: string; happinessDelta: 0 }) => void)
    | undefined;
  mocks.introduction = null;
  mocks.generateLocalAmbientMessage.mockImplementationOnce(
    () => new Promise((resolve) => {
      resolveAmbient = resolve;
    }),
  );

  const { unmount } = render(<PetDashboard petId={pet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(mocks.generateLocalAmbientMessage).toHaveBeenCalledOnce();

  unmount();
  await act(async () => {
    resolveAmbient?.({ reply: "Поздний ответ", happinessDelta: 0 });
    await vi.advanceTimersByTimeAsync(2_000);
  });

  expect((mocks.localPet as { applyMoodHint: ReturnType<typeof vi.fn> }).applyMoodHint)
    .not.toHaveBeenCalled();
});

it("does not generate duplicate ambient replies when two tabs become idle together", async () => {
  mocks.introduction = null;
  mocks.generateLocalAmbientMessage.mockResolvedValue({
    reply: "Одна общая реплика",
    happinessDelta: 0,
  });
  let lockQueue: Promise<unknown> = Promise.resolve();
  mocks.withLocalPetMutationLock.mockImplementation(
    (
      _petId: string,
      _scope: string,
      mutation: (guard: { assertOwned: () => void }) => unknown,
    ) => {
      const result = lockQueue.then(() => mutation({ assertOwned: vi.fn() }));
      lockQueue = result.then(
        () => undefined,
        () => undefined,
      );
      return result;
    },
  );

  render(<PetDashboard petId={pet.petId} />);
  render(<PetDashboard petId={pet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_000);
    await lockQueue;
  });

  expect(mocks.generateLocalAmbientMessage).toHaveBeenCalledOnce();
});

it("generates only one ambient reply during a dashboard session", async () => {
  mocks.introduction = null;
  mocks.generateLocalAmbientMessage.mockResolvedValue({
    reply: "Слышал, почему лёд плавает?",
    happinessDelta: 0,
  });

  render(<PetDashboard petId={pet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_000);
  });
  expect(mocks.generateLocalAmbientMessage).toHaveBeenCalledOnce();

  fireEvent.click(screen.getByRole("button", { name: "Поболтать" }));
  const backHandler = mocks.useTelegramBackButton.mock.calls.at(-1)?.[0] as
    | (() => void)
    | undefined;
  act(() => backHandler?.());

  expect(mocks.generateLocalAmbientMessage).toHaveBeenCalledOnce();
});

it("restores the session ambient reply when the dashboard opens again", async () => {
  mocks.introduction = null;
  window.localStorage.setItem(
    `tamagochi:v1:ambient-replies:${pet.petId}`,
    JSON.stringify(["Лёд плавает из-за меньшей плотности"]),
  );
  window.sessionStorage.setItem(`tamagochi:v1:ambient-shown:${pet.petId}`, "1");

  render(<PetDashboard petId={pet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  expect(mocks.generateLocalAmbientMessage).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Лёд плавает из-за меньшей плотности")).toBeInTheDocument();
});

it("offers a keyboard action for petting the pet", async () => {
  (mocks.localPet as { registerPetTap: ReturnType<typeof vi.fn> }).registerPetTap
    .mockReturnValue({ rewarded: false, pet });
  render(<PetDashboard petId={pet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  fireEvent.click(screen.getByRole("button", { name: "Погладить Без имени" }));

  expect((mocks.localPet as { registerPetTap: ReturnType<typeof vi.fn> }).registerPetTap)
    .toHaveBeenCalledOnce();
});

it("removes visually hidden dashboard actions from keyboard navigation", async () => {
  render(<PetDashboard petId={pet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  fireEvent.click(screen.getByRole("button", { name: "Поболтать" }));

  const actions = document.querySelector(".main-actions-scroll");
  expect(actions).toHaveAttribute("aria-hidden", "true");
  expect(actions).toHaveAttribute("inert");
  expect(screen.queryByRole("button", { name: "Погладить Без имени" })).not.toBeInTheDocument();
});

it("opens story history from a reachable dashboard action", async () => {
  mocks.storyHistory = [{ id: "story-1", title: "Маяк", text: "Листик нашёл маяк." }];
  render(<PetDashboard petId={pet.petId} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  fireEvent.click(screen.getByRole("button", { name: "Истории" }));

  expect(screen.getByRole("heading", { name: "Истории" })).toBeInTheDocument();
  expect(document.querySelector("#travel-story-overlay")).toBeInTheDocument();
});
