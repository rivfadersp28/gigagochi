import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import {
  CHAT_HISTORY_STORAGE_KEY,
  writeLocalChatHistory,
  writeLocalPetState,
} from "@/lib/localPetStorage";
import type { LocalPetState } from "@/lib/types";

import { ChatView } from "./ChatView";

const mocks = vi.hoisted(() => ({
  applyMoodHint: vi.fn(),
  generateLocalProactiveMessage: vi.fn(),
  recordProactiveDelivery: vi.fn(),
  replace: vi.fn(),
  runLocalPetChatTurn: vi.fn(),
  withLocalPetMutationLock: vi.fn(),
  writeLocalPetMemory: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: mocks.replace }),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  generateLocalProactiveMessage: (...args: unknown[]) =>
    mocks.generateLocalProactiveMessage(...args),
}));

vi.mock("@/lib/localPetMemoryRecall", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/localPetMemoryRecall")>(),
  buildDailyProactiveMemoryContext: () => ({
    relevantMemories: [],
    proactiveCandidate: { memoryIds: ["memory-1"] },
  }),
}));

vi.mock("@/lib/localPetMemoryStorage", () => ({
  readLocalPetMemory: () => ({
    version: 2,
    petId: "pet-chat",
    createdAt: "2026-07-15T12:00:00.000Z",
    updatedAt: "2026-07-15T12:00:00.000Z",
    learnings: [],
    memories: [],
    proactiveLog: [],
  }),
  recordProactiveDelivery: (...args: unknown[]) => mocks.recordProactiveDelivery(...args),
  writeLocalPetMemory: (...args: unknown[]) => mocks.writeLocalPetMemory(...args),
}));

vi.mock("@/lib/localPetChatTurn", () => ({
  LocalPetTurnSupersededError: class LocalPetTurnSupersededError extends Error {},
  runLocalPetChatTurn: (...args: unknown[]) => mocks.runLocalPetChatTurn(...args),
}));

vi.mock("@/lib/localPetMutationLock", () => ({
  withLocalPetMutationLock: (
    petId: string,
    scope: string,
    mutation: (guard: { assertOwned: () => void }) => unknown,
    options?: unknown,
  ) => mocks.withLocalPetMutationLock(petId, scope, mutation, options),
}));

vi.mock("@/lib/telegram", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/telegram")>(),
  canUseDebugMenu: () => false,
  useTelegramBackButton: vi.fn(),
}));

vi.mock("@/lib/useTelegramCapabilities", () => ({
  useTelegramCapabilities: () => ({ debugMenu: false, interactiveTravel: false }),
}));

vi.mock("@/lib/useLocalPetState", () => ({
  useLocalPetState: () => ({
    pet,
    status: "ready",
    error: null,
    applyMoodHint: mocks.applyMoodHint,
    applyLiteOverlayPatch: vi.fn(),
    updateName: vi.fn(),
  }),
}));

const pet: LocalPetState = {
  version: 2,
  petId: "pet-chat",
  description: "дракон",
  createdAt: "2026-07-15T12:00:00.000Z",
  updatedAt: "2026-07-15T12:00:00.000Z",
  lastInteractionAt: "2026-07-15T12:00:00.000Z",
  lastStatsTickAt: "2026-07-15T12:00:00.000Z",
  lastStatTickAt: {
    hunger: "2026-07-15T12:00:00.000Z",
    happiness: "2026-07-15T12:00:00.000Z",
    energy: "2026-07-15T12:00:00.000Z",
  },
  stage: "baby",
  mood: "idle",
  stats: { hunger: 80, happiness: 80, energy: 80 },
};

Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
  configurable: true,
  value: vi.fn(),
});

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  writeLocalPetState(pet);
  mocks.recordProactiveDelivery.mockImplementation((memory) => memory);
  mocks.withLocalPetMutationLock.mockImplementation(
    async (
      _petId: string,
      _scope: string,
      mutation: (guard: { assertOwned: () => void }) => unknown,
    ) => mutation({ assertOwned: vi.fn() }),
  );
  mocks.generateLocalProactiveMessage.mockReturnValue(new Promise(() => undefined));
  mocks.runLocalPetChatTurn.mockReturnValue(new Promise(() => undefined));
});

afterEach(cleanup);

it("does not record or apply a proactive reply after unmount", async () => {
  let resolveProactive:
    | ((value: { reply: string; happinessDelta: 0 }) => void)
    | undefined;
  mocks.generateLocalProactiveMessage.mockImplementationOnce(
    () => new Promise((resolve) => {
      resolveProactive = resolve;
    }),
  );

  const { unmount } = render(<ChatView petId={pet.petId} />);
  await waitFor(() => expect(mocks.generateLocalProactiveMessage).toHaveBeenCalledOnce());
  unmount();

  await act(async () => {
    resolveProactive?.({ reply: "Поздний proactive", happinessDelta: 0 });
    await Promise.resolve();
  });

  expect(mocks.recordProactiveDelivery).not.toHaveBeenCalled();
  expect(mocks.writeLocalPetMemory).not.toHaveBeenCalled();
  expect(mocks.applyMoodHint).not.toHaveBeenCalled();
});

it("locks two same-tick form submissions before React commits isSending", () => {
  render(<ChatView petId={pet.petId} />);
  fireEvent.change(screen.getByLabelText("Сообщение питомцу"), {
    target: { value: "Привет" },
  });
  const form = screen.getByLabelText("Сообщение питомцу").closest("form");
  if (!form) {
    throw new Error("chat form not found");
  }

  act(() => {
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });

  expect(mocks.runLocalPetChatTurn).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("region", { name: "Диалог" })).toHaveAttribute(
    "aria-busy",
    "true",
  );
});

it("restores the draft when sending fails", async () => {
  mocks.runLocalPetChatTurn.mockRejectedValueOnce(new Error("network failed"));
  render(<ChatView petId={pet.petId} />);
  const input = screen.getByLabelText("Сообщение питомцу");
  fireEvent.change(input, { target: { value: "Не потеряй этот длинный текст" } });
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));

  await screen.findByRole("alert");
  expect(input).toHaveValue("Не потеряй этот длинный текст");
});

it("reconciles chat history written by another tab", async () => {
  render(<ChatView petId={pet.petId} />);

  await act(async () => {
    writeLocalChatHistory({
      version: 1,
      messages: [{
        id: "tab-b-message",
        role: "pet",
        text: "Сообщение из второй вкладки",
        createdAt: "2026-07-15T12:00:01.000Z",
      }],
    });
    window.dispatchEvent(new StorageEvent("storage", {
      key: CHAT_HISTORY_STORAGE_KEY,
    }));
  });

  expect(screen.getByText("Сообщение из второй вкладки")).toBeInTheDocument();
});
