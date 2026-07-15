import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import {
  resetLocalPetState,
  writeLocalPetState,
} from "@/lib/localPetStorage";
import type { LocalPetState } from "@/lib/types";

import { PetDashboard } from "./PetDashboard";

const mocks = vi.hoisted(() => ({
  generateLocalAmbientMessage: vi.fn(),
  hapticNotification: vi.fn(),
  introduction: "Привет" as string | null,
  localPet: undefined as unknown,
  router: {
    push: vi.fn(),
    replace: vi.fn(),
  },
  runLocalPetChatTurn: vi.fn(),
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
  useTelegramCapabilities: () => ({ debugMenu: false, interactiveTravel: false }),
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
  mocks.storyHistory = [];
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
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
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
