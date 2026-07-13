import { beforeEach, expect, it, vi } from "vitest";

import { runLocalPetChatTurn } from "./localPetChatTurn";
import type { LocalPetState } from "./types";

const apiMocks = vi.hoisted(() => ({
  consolidateLocalUserMemory: vi.fn(),
  extractLocalLiteFacts: vi.fn(),
  extractLocalUserMemory: vi.fn(),
  sendLocalChatMessage: vi.fn(),
}));

vi.mock("./api", () => apiMocks);

const pet: LocalPetState = {
  version: 2,
  petId: "pet-memory-order",
  description: "дракон",
  createdAt: "2026-07-13T12:00:00.000Z",
  updatedAt: "2026-07-13T12:00:00.000Z",
  lastInteractionAt: "2026-07-13T12:00:00.000Z",
  lastStatsTickAt: "2026-07-13T12:00:00.000Z",
  lastStatTickAt: {
    hunger: "2026-07-13T12:00:00.000Z",
    happiness: "2026-07-13T12:00:00.000Z",
    energy: "2026-07-13T12:00:00.000Z",
  },
  stage: "baby",
  mood: "idle",
  stats: { hunger: 100, happiness: 100, energy: 100 },
};

beforeEach(() => {
  window.localStorage.clear();
  vi.clearAllMocks();
  apiMocks.sendLocalChatMessage.mockResolvedValue({
    reply: "ответ",
    happinessDelta: 0,
  });
  apiMocks.extractLocalLiteFacts.mockResolvedValue({});
  apiMocks.consolidateLocalUserMemory.mockResolvedValue({ operations: [] });
});

it("waits for the previous post-reply memory write before starting the next turn", async () => {
  let finishFirstExtraction: ((value: { operations: never[] }) => void) | undefined;
  apiMocks.extractLocalUserMemory
    .mockImplementationOnce(
      () => new Promise((resolve) => {
        finishFirstExtraction = resolve;
      }),
    )
    .mockResolvedValue({ operations: [] });

  await runLocalPetChatTurn({
    pet,
    message: "первое",
    includePromptDebug: false,
    logLabel: "first",
  });
  const secondTurn = runLocalPetChatTurn({
    pet,
    message: "второе",
    includePromptDebug: false,
    logLabel: "second",
  });

  expect(apiMocks.sendLocalChatMessage).toHaveBeenCalledTimes(1);

  finishFirstExtraction?.({ operations: [] });
  await vi.waitFor(() => {
    expect(apiMocks.sendLocalChatMessage).toHaveBeenCalledTimes(2);
  });
  await secondTurn;
});
