import { afterEach, beforeEach, expect, it, vi } from "vitest";

import {
  LocalPetTurnSupersededError,
  runLocalPetChatTurn,
  waitForLocalPetPostReplyMemory,
} from "./localPetChatTurn";
import {
  appendLocalChatMessages,
  clearLocalChatHistory,
  readLocalChatHistory,
  resetLocalPetState,
  writeLocalPetState,
} from "./localPetStorage";
import {
  applyMemoryOperations,
  readLocalPetMemory,
  localPetMemoryStorageKey,
  resetLocalPetMemory,
  writeLocalPetMemory,
} from "./localPetMemoryStorage";
import { readComplimentHistory } from "./localPetCompliments";
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

function replaceNavigatorLocks(value: unknown) {
  const descriptor = Object.getOwnPropertyDescriptor(navigator, "locks");
  Object.defineProperty(navigator, "locks", { configurable: true, value });
  return () => {
    if (descriptor) {
      Object.defineProperty(navigator, "locks", descriptor);
    } else {
      Reflect.deleteProperty(navigator, "locks");
    }
  };
}

function installSerialWebLocksMock() {
  const queues = new Map<string, Promise<void>>();
  const request = vi.fn(async (
    name: string,
    options: LockOptions,
    callback: () => Promise<unknown> | unknown,
  ) => {
    const previous = queues.get(name) ?? Promise.resolve();
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const tail = previous.catch(() => undefined).then(() => held);
    queues.set(name, tail);
    await previous.catch(() => undefined);
    if (options.signal?.aborted) {
      release();
      throw options.signal.reason;
    }
    try {
      return await callback();
    } finally {
      release();
      if (queues.get(name) === tail) {
        queues.delete(name);
      }
    }
  });
  return replaceNavigatorLocks({ request });
}

let restoreDefaultLocks: (() => void) | undefined;

beforeEach(() => {
  window.localStorage.clear();
  vi.clearAllMocks();
  restoreDefaultLocks = installSerialWebLocksMock();
  writeLocalPetState(pet);
  apiMocks.sendLocalChatMessage.mockResolvedValue({
    reply: "ответ",
    happinessDelta: 0,
  });
  apiMocks.extractLocalLiteFacts.mockResolvedValue({});
  apiMocks.extractLocalUserMemory.mockResolvedValue({ operations: [] });
  apiMocks.consolidateLocalUserMemory.mockResolvedValue({ operations: [] });
});

afterEach(() => {
  restoreDefaultLocks?.();
  restoreDefaultLocks = undefined;
});

it("serializes concurrent tab turns and rebuilds the second request from durable history", async () => {
  let resolveFirstReply:
    | ((value: { reply: string; happinessDelta: 0 }) => void)
    | undefined;
  apiMocks.sendLocalChatMessage
    .mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveFirstReply = resolve;
      }),
    )
    .mockResolvedValueOnce({ reply: "ответ второй вкладке", happinessDelta: 0 });

  const firstTurn = runLocalPetChatTurn({
    pet,
    message: "вопрос первой вкладки",
    includePromptDebug: false,
    logLabel: "tab-a",
  });
  await vi.waitFor(() => expect(apiMocks.sendLocalChatMessage).toHaveBeenCalledOnce());

  const secondTurn = runLocalPetChatTurn({
    pet,
    message: "вопрос второй вкладки",
    includePromptDebug: false,
    logLabel: "tab-b",
  });
  await new Promise((resolve) => window.setTimeout(resolve, 100));
  expect(apiMocks.sendLocalChatMessage).toHaveBeenCalledOnce();

  resolveFirstReply?.({ reply: "ответ первой вкладке", happinessDelta: 0 });
  await firstTurn;
  await vi.waitFor(() => expect(apiMocks.sendLocalChatMessage).toHaveBeenCalledTimes(2));
  await secondTurn;

  expect(readLocalChatHistory().messages.map(({ role, text }) => ({ role, text }))).toEqual([
    { role: "user", text: "вопрос первой вкладки" },
    { role: "pet", text: "ответ первой вкладке" },
    { role: "user", text: "вопрос второй вкладки" },
    { role: "pet", text: "ответ второй вкладке" },
  ]);
  expect(
    (apiMocks.sendLocalChatMessage.mock.calls[1]?.[2] as Array<{ text: string }>).map(
      (item) => item.text,
    ),
  ).toEqual(["вопрос первой вкладки", "ответ первой вкладке"]);
  await waitForLocalPetPostReplyMemory(pet.petId);
});

it("preserves compliments committed by two serialized tab turns", async () => {
  let resolveFirstReply:
    | ((value: { reply: string; happinessDelta: 30; complimentKey: string }) => void)
    | undefined;
  apiMocks.sendLocalChatMessage
    .mockImplementationOnce(
      () => new Promise((resolve) => {
        resolveFirstReply = resolve;
      }),
    )
    .mockResolvedValueOnce({
      reply: "второй комплимент",
      happinessDelta: 30,
      complimentKey: "second-key",
    });

  const firstTurn = runLocalPetChatTurn({
    pet,
    message: "первый комплимент",
    includePromptDebug: false,
    logLabel: "compliment-tab-a",
  });
  await vi.waitFor(() => expect(apiMocks.sendLocalChatMessage).toHaveBeenCalledOnce());
  const secondTurn = runLocalPetChatTurn({
    pet,
    message: "второй комплимент",
    includePromptDebug: false,
    logLabel: "compliment-tab-b",
  });

  resolveFirstReply?.({
    reply: "первый комплимент",
    happinessDelta: 30,
    complimentKey: "first-key",
  });
  await Promise.all([firstTurn, secondTurn]);

  expect(readComplimentHistory(pet.petId)).toEqual(["first-key", "second-key"]);
  await waitForLocalPetPostReplyMemory(pet.petId);
});

it("keeps a single-view chat working when Web Locks are unavailable", async () => {
  const restoreLocks = replaceNavigatorLocks(undefined);
  const turn = runLocalPetChatTurn({
    pet,
    message: "сообщение без lock",
    includePromptDebug: false,
    logLabel: "missing-web-lock",
  });

  try {
    await expect(turn).resolves.toBeDefined();
    expect(apiMocks.sendLocalChatMessage).toHaveBeenCalledOnce();
    expect(readLocalChatHistory().messages.map((message) => message.role)).toEqual([
      "user",
      "pet",
    ]);
  } finally {
    restoreLocks();
  }
});

it("does not block the next reply on post-reply memory but serializes memory tasks", async () => {
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

  await vi.waitFor(() => {
    expect(apiMocks.sendLocalChatMessage).toHaveBeenCalledTimes(2);
  });
  await secondTurn;
  expect(apiMocks.extractLocalUserMemory).toHaveBeenCalledTimes(1);

  finishFirstExtraction?.({ operations: [] });
  await waitForLocalPetPostReplyMemory(pet.petId);
  expect(apiMocks.extractLocalUserMemory).toHaveBeenCalledTimes(2);
});

it("bounds deferred post-reply memory work without blocking visible replies", async () => {
  let finishFirstExtraction: ((value: { operations: never[] }) => void) | undefined;
  apiMocks.extractLocalUserMemory
    .mockImplementationOnce(
      () => new Promise((resolve) => {
        finishFirstExtraction = resolve;
      }),
    )
    .mockResolvedValue({ operations: [] });

  for (let index = 0; index < 7; index += 1) {
    await runLocalPetChatTurn({
      pet,
      message: `message-${index}`,
      includePromptDebug: false,
      logLabel: `bounded-${index}`,
    });
  }

  expect(apiMocks.sendLocalChatMessage).toHaveBeenCalledTimes(7);
  expect(apiMocks.extractLocalUserMemory).toHaveBeenCalledTimes(1);

  finishFirstExtraction?.({ operations: [] });
  await waitForLocalPetPostReplyMemory(pet.petId);
  expect(apiMocks.extractLocalUserMemory).toHaveBeenCalledTimes(4);
});

it("rolls back only its optimistic message when the request fails", async () => {
  let rejectRequest: ((reason: Error) => void) | undefined;
  apiMocks.sendLocalChatMessage.mockImplementationOnce(
    () => new Promise((_resolve, reject) => {
      rejectRequest = reject;
    }),
  );
  const historyChanges: string[][] = [];
  const turn = runLocalPetChatTurn({
    pet,
    message: "неотправленное",
    includePromptDebug: false,
    logLabel: "failed",
    onHistoryChange: (messages) => historyChanges.push(messages.map((item) => item.id)),
  });
  await vi.waitFor(() => expect(readLocalChatHistory().messages).toHaveLength(1));

  appendLocalChatMessages([
    {
      id: "concurrent-user",
      role: "user",
      text: "параллельный вопрос",
      createdAt: "2026-07-13T12:00:01.000Z",
    },
    {
      id: "concurrent-pet",
      role: "pet",
      text: "параллельный ответ",
      createdAt: "2026-07-13T12:00:02.000Z",
    },
  ]);
  rejectRequest?.(new Error("network failed"));

  await expect(turn).rejects.toThrow("network failed");
  expect(readLocalChatHistory().messages.map((item) => item.id)).toEqual([
    "concurrent-user",
    "concurrent-pet",
  ]);
  expect(historyChanges.at(-1)).toEqual(["concurrent-user", "concurrent-pet"]);
});

it("applies deterministic memory on top of memory written while the reply was pending", async () => {
  let resolveRequest: ((value: { reply: string; happinessDelta: 0 }) => void) | undefined;
  apiMocks.sendLocalChatMessage.mockImplementationOnce(
    () => new Promise((resolve) => {
      resolveRequest = resolve;
    }),
  );
  const turn = runLocalPetChatTurn({
    pet,
    message: "меня зовут Анна",
    includePromptDebug: false,
    logLabel: "concurrent-memory",
  });
  await vi.waitFor(() => expect(apiMocks.sendLocalChatMessage).toHaveBeenCalledOnce());

  writeLocalPetMemory(applyMemoryOperations(
    readLocalPetMemory(pet.petId),
    [{
      type: "remember_user_fact",
      kind: "preference",
      text: "Пользователь любит чай.",
      normalizedKey: "preference-tea",
      confidence: 1,
      importance: 1,
      tags: [],
    }],
  ));
  resolveRequest?.({ reply: "Буду знать", happinessDelta: 0 });
  await turn;

  expect(readLocalPetMemory(pet.petId).memories.map((item) => item.normalizedKey)).toEqual(
    expect.arrayContaining(["preference-tea", "user-name"]),
  );
});

it("does not append a late pet A reply to pet B history", async () => {
  let resolveRequest: ((value: { reply: string; happinessDelta: 0 }) => void) | undefined;
  apiMocks.sendLocalChatMessage.mockImplementationOnce(
    () => new Promise((resolve) => {
      resolveRequest = resolve;
    }),
  );
  const turn = runLocalPetChatTurn({
    pet,
    message: "ответь позже",
    includePromptDebug: false,
    logLabel: "superseded",
  });
  await vi.waitFor(() => expect(readLocalChatHistory().messages).toHaveLength(1));

  writeLocalPetState({ ...pet, petId: "pet-b", description: "грифон" });
  clearLocalChatHistory();
  resolveRequest?.({ reply: "поздний ответ A", happinessDelta: 0 });

  await expect(turn).rejects.toBeInstanceOf(LocalPetTurnSupersededError);
  expect(readLocalChatHistory().messages).toEqual([]);
});

it("does not append a late reply after the pet is reset", async () => {
  let resolveRequest: ((value: { reply: string; happinessDelta: 0 }) => void) | undefined;
  apiMocks.sendLocalChatMessage.mockImplementationOnce(
    () => new Promise((resolve) => {
      resolveRequest = resolve;
    }),
  );
  const turn = runLocalPetChatTurn({
    pet,
    message: "ответь после сброса",
    includePromptDebug: false,
    logLabel: "reset",
  });
  await vi.waitFor(() => expect(readLocalChatHistory().messages).toHaveLength(1));

  resetLocalPetState();
  resolveRequest?.({ reply: "поздний ответ", happinessDelta: 0 });

  await expect(turn).rejects.toBeInstanceOf(LocalPetTurnSupersededError);
  expect(readLocalChatHistory().messages).toEqual([]);
});

it("tags a deferred lite-overlay patch with the originating pet id", async () => {
  let resolveLiteFacts:
    | ((value: { liteOverlayPatch: Record<string, unknown> }) => void)
    | undefined;
  apiMocks.extractLocalLiteFacts.mockImplementationOnce(
    () => new Promise((resolve) => {
      resolveLiteFacts = resolve;
    }),
  );
  const onLiteOverlayPatch = vi.fn();

  await runLocalPetChatTurn({
    pet,
    message: "запомни факт",
    includePromptDebug: false,
    logLabel: "lite-overlay",
    onLiteOverlayPatch,
  });
  await vi.waitFor(() => expect(apiMocks.extractLocalLiteFacts).toHaveBeenCalledTimes(1));
  writeLocalPetState({ ...pet, petId: "pet-b", description: "грифон" });

  const patch = { facts: [{ key: "origin", value: "A" }] };
  resolveLiteFacts?.({ liteOverlayPatch: patch });

  await waitForLocalPetPostReplyMemory(pet.petId);
  expect(onLiteOverlayPatch).not.toHaveBeenCalled();
});

it("does not resurrect old-pet memory after reset during deferred extraction", async () => {
  let resolveExtraction:
    | ((value: { operations: Array<Record<string, unknown>> }) => void)
    | undefined;
  apiMocks.extractLocalUserMemory.mockImplementationOnce(
    () => new Promise((resolve) => {
      resolveExtraction = resolve;
    }),
  );

  await runLocalPetChatTurn({
    pet,
    message: "нейтральное сообщение",
    includePromptDebug: false,
    logLabel: "late-memory",
  });
  await vi.waitFor(() => expect(apiMocks.extractLocalUserMemory).toHaveBeenCalledTimes(1));

  resetLocalPetMemory(pet.petId);
  resetLocalPetState();
  resolveExtraction?.({
    operations: [
      {
        type: "remember_user_fact",
        kind: "user_fact",
        text: "данные удалённого питомца",
        normalizedKey: "deleted-pet-data",
        confidence: 1,
        importance: 1,
      },
    ],
  });

  await waitForLocalPetPostReplyMemory(pet.petId);
  expect(window.localStorage.getItem(localPetMemoryStorageKey(pet.petId))).toBeNull();
});
