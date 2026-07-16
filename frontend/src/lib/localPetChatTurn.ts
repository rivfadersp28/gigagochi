import {
  consolidateLocalUserMemory,
  extractLocalLiteFacts,
  extractLocalUserMemory,
  sendLocalChatMessage,
} from "./api";
import {
  appendLocalChatMessages,
  createLocalId,
  readLocalChatHistory,
  readLocalPetState,
  removeLocalChatMessages,
} from "./localPetStorage";
import { extractDeterministicMemoryOperations } from "./localPetDeterministicMemory";
import { readComplimentHistory, rememberCompliment } from "./localPetCompliments";
import {
  applyMemoryConsolidationOperations,
  applyMemoryOperations,
  markMemoryContextUsed,
  readLocalPetMemory,
  shouldRunDailyConsolidation,
  writeLocalPetMemory,
} from "./localPetMemoryStorage";
import { buildMemoryContextForMessage } from "./localPetMemoryRecall";
import {
  type LocalPetMutationGuard,
  withLocalPetMutationLock,
} from "./localPetMutationLock";
import {
  recordLiteOverlayPatchDebug,
  recordMemoryConsolidationDebug,
  recordMemoryContextDebug,
  recordMemoryOperationsDebug,
  recordReplyPromptDebug,
} from "./debugPanelStorage";
import { logBrowserPromptDebug } from "./promptDebug";
import type {
  LocalChatMessage,
  LocalChatResponse,
  LocalPetState,
} from "./types";

export type LocalPetChatTurnResult = {
  response: LocalChatResponse;
  userMessage: LocalChatMessage;
  assistantMessage: LocalChatMessage;
};

type RunLocalPetChatTurnOptions = {
  pet: LocalPetState;
  message: string;
  includePromptDebug: boolean;
  replyMaxChars?: number;
  replyTransform?: (reply: string) => string;
  dialogueHookMessage?: LocalChatMessage | null;
  history?: LocalChatMessage[];
  logLabel: string;
  onHistoryChange?: (messages: LocalChatMessage[]) => void;
  onLiteOverlayPatch?: (patch: Record<string, unknown>, expectedPetId: string) => void;
};

export class LocalPetTurnSupersededError extends Error {
  constructor() {
    super("Local pet changed while the chat turn was in progress.");
    this.name = "LocalPetTurnSupersededError";
  }
}

const pendingPostReplyMemory = new Map<string, Promise<void>>();
const pendingPostReplyMemoryDepth = new Map<string, number>();
const MAX_PENDING_POST_REPLY_MEMORY_PER_PET = 4;

export async function waitForLocalPetPostReplyMemory(petId: string) {
  await pendingPostReplyMemory.get(petId);
}

function enqueuePostReplyMemory(
  petId: string,
  task: () => Promise<void>,
  onError: (error: unknown) => void,
): boolean {
  const depth = pendingPostReplyMemoryDepth.get(petId) ?? 0;
  if (depth >= MAX_PENDING_POST_REPLY_MEMORY_PER_PET) {
    return false;
  }
  pendingPostReplyMemoryDepth.set(petId, depth + 1);
  const previous = pendingPostReplyMemory.get(petId) ?? Promise.resolve();
  const pending = previous
    .then(task)
    .catch(onError)
    .finally(() => {
      const nextDepth = Math.max(0, (pendingPostReplyMemoryDepth.get(petId) ?? 1) - 1);
      if (nextDepth === 0) {
        pendingPostReplyMemoryDepth.delete(petId);
      } else {
        pendingPostReplyMemoryDepth.set(petId, nextDepth);
      }
      if (pendingPostReplyMemory.get(petId) === pending) {
        pendingPostReplyMemory.delete(petId);
      }
    });
  pendingPostReplyMemory.set(petId, pending);
  return true;
}

function appendUniqueMessages(
  history: LocalChatMessage[],
  messages: LocalChatMessage[],
): LocalChatMessage[] {
  const existingIds = new Set(history.map((item) => item.id));
  return [
    ...history,
    ...messages.filter((item) => !existingIds.has(item.id)),
  ];
}

function isCurrentPet(petId: string) {
  return readLocalPetState()?.petId === petId;
}

async function persistPostReplyMemory({
  pet,
  message,
  reply,
  history,
  memoryContext,
  sourceMessageIds,
  includePromptDebug,
  onLiteOverlayPatch,
}: {
  pet: LocalPetState;
  message: string;
  reply: string;
  history: LocalChatMessage[];
  memoryContext: ReturnType<typeof buildMemoryContextForMessage>;
  sourceMessageIds: string[];
  includePromptDebug: boolean;
  onLiteOverlayPatch?: (patch: Record<string, unknown>, expectedPetId: string) => void;
}) {
  return withLocalPetMutationLock(pet.petId, "memory", async ({ assertOwned }) => {
    const currentPet = readLocalPetState();
    if (!currentPet || currentPet.petId !== pet.petId) {
      return;
    }
    const memoryBeforeExtraction = readLocalPetMemory(pet.petId);
    const [memoryResult, liteFactsResult] = await Promise.allSettled([
      extractLocalUserMemory(message, reply, currentPet, history, memoryBeforeExtraction, {
        includeDebug: includePromptDebug,
        memoryContext,
      }),
      extractLocalLiteFacts(message, reply, currentPet, history, {
        includeDebug: includePromptDebug,
      }),
    ]);
    assertOwned();
    if (!isCurrentPet(pet.petId)) {
      return;
    }

    if (memoryResult.status === "fulfilled") {
      if (memoryResult.value.operations.length) {
        const latestMemory = readLocalPetMemory(pet.petId);
        const updatedMemory = applyMemoryOperations(
          latestMemory,
          memoryResult.value.operations,
          sourceMessageIds,
        );
        assertOwned();
        if (!writeLocalPetMemory(updatedMemory, pet.petId)) {
          return;
        }
        if (includePromptDebug) {
          recordMemoryOperationsDebug(memoryResult.value.operations);
        }
      }

      const memoryAfterExtraction = readLocalPetMemory(pet.petId);
      if (shouldRunDailyConsolidation(memoryAfterExtraction)) {
        try {
          const consolidation = await consolidateLocalUserMemory(memoryAfterExtraction, {
            includeDebug: includePromptDebug,
          });
          assertOwned();
          if (!isCurrentPet(pet.petId)) {
            return;
          }
          if (consolidation.operations.length) {
            const latestMemory = readLocalPetMemory(pet.petId);
            const consolidated = applyMemoryConsolidationOperations(
              latestMemory,
              consolidation.operations,
            );
            if (!writeLocalPetMemory(consolidated, pet.petId)) {
              return;
            }
            if (includePromptDebug) {
              recordMemoryConsolidationDebug(consolidation.operations);
            }
          }
        } catch (error) {
          if (includePromptDebug) {
            console.warn("[memory-debug] memory consolidation failed", error);
          }
        }
      }
    }

    if (
      isCurrentPet(pet.petId)
      && liteFactsResult.status === "fulfilled"
      && liteFactsResult.value.liteOverlayPatch
    ) {
      assertOwned();
      onLiteOverlayPatch?.(liteFactsResult.value.liteOverlayPatch, pet.petId);
      if (includePromptDebug) {
        recordLiteOverlayPatchDebug(liteFactsResult.value.liteOverlayPatch);
      }
    }
  }, { acquireTimeoutMs: 10 * 60_000 });
}

async function runLocalPetChatTurnLocked({
  pet,
  message,
  includePromptDebug,
  replyMaxChars,
  replyTransform,
  dialogueHookMessage,
  history,
  logLabel,
  onHistoryChange,
  onLiteOverlayPatch,
}: RunLocalPetChatTurnOptions, guard: LocalPetMutationGuard): Promise<LocalPetChatTurnResult> {
  const currentPet = readLocalPetState();
  if (!currentPet || currentPet.petId !== pet.petId) {
    throw new LocalPetTurnSupersededError();
  }
  const requestPet = currentPet;
  const now = new Date().toISOString();
  const userMessage: LocalChatMessage = {
    id: createLocalId("message"),
    role: "user",
    text: message,
    createdAt: now,
  };
  const storedHistoryBeforeMessage = readLocalChatHistory().messages;
  const fullHistoryBeforeMessage = history
    ? appendUniqueMessages(storedHistoryBeforeMessage, history)
    : storedHistoryBeforeMessage;
  const dialogueHookIsNew = Boolean(
    dialogueHookMessage
      && !fullHistoryBeforeMessage.some((item) => item.id === dialogueHookMessage.id),
  );
  const historyWithDialogueHook = dialogueHookMessage
    ? appendUniqueMessages(fullHistoryBeforeMessage, [dialogueHookMessage])
    : fullHistoryBeforeMessage;
  const historyBeforeMessage = historyWithDialogueHook.slice(-12);
  const memoryBeforeMessage = readLocalPetMemory(requestPet.petId);
  const memoryContext = buildMemoryContextForMessage(
    historyWithDialogueHook,
    message,
    new Date(now),
    memoryBeforeMessage,
  );

  if (includePromptDebug) {
    console.log(`[memory-debug] ${logLabel} recall context`, memoryContext);
    recordMemoryContextDebug(memoryContext);
  }
  guard.assertOwned();
  const optimisticHistory = appendLocalChatMessages([
    ...(dialogueHookMessage && dialogueHookIsNew ? [dialogueHookMessage] : []),
    userMessage,
  ]);
  onHistoryChange?.(optimisticHistory.messages);

  let response: LocalChatResponse;
  try {
    response = await sendLocalChatMessage(message, requestPet, historyBeforeMessage, {
      includeDebug: includePromptDebug,
      complimentHistory: readComplimentHistory(requestPet.petId),
      memoryContext,
      replyMaxChars,
    });
  } catch (error) {
    guard.assertOwned();
    const rolledBackHistory = removeLocalChatMessages([userMessage.id]);
    if (isCurrentPet(requestPet.petId)) {
      onHistoryChange?.(rolledBackHistory.messages);
    }
    throw error;
  }
  guard.assertOwned();
  if (!isCurrentPet(requestPet.petId)) {
    removeLocalChatMessages([userMessage.id]);
    throw new LocalPetTurnSupersededError();
  }
  if (replyTransform) {
    response = {
      ...response,
      reply: replyTransform(response.reply),
    };
  }
  if (response.happinessDelta === 30 || response.happinessDelta === 100) {
    rememberCompliment(requestPet.petId, response.complimentKey ?? message);
  }
  if (includePromptDebug) {
    logBrowserPromptDebug(`${logLabel} reply`, response);
    recordReplyPromptDebug(response);
  }

  const assistantMessage: LocalChatMessage = {
    id: createLocalId("message"),
    role: "pet",
    text: response.reply,
    createdAt: new Date().toISOString(),
  };
  guard.assertOwned();
  const nextHistory = appendLocalChatMessages([assistantMessage]);
  onHistoryChange?.(nextHistory.messages);

  const usedMemoryIds = memoryContext.relevantMemories.map((item) => item.id);
  const memoryOperations = extractDeterministicMemoryOperations(message);
  if (usedMemoryIds.length || memoryOperations.length) {
    await withLocalPetMutationLock(
      requestPet.petId,
      "memory",
      ({ assertOwned }) => {
        let nextMemory = markMemoryContextUsed(
          readLocalPetMemory(requestPet.petId),
          usedMemoryIds,
        );
        if (memoryOperations.length) {
          nextMemory = applyMemoryOperations(nextMemory, memoryOperations, [userMessage.id]);
        }
        assertOwned();
        if (!writeLocalPetMemory(nextMemory, requestPet.petId)) {
          throw new LocalPetTurnSupersededError();
        }
      },
      { acquireTimeoutMs: 10 * 60_000 },
    );
  }

  const memoryTaskEnqueued = enqueuePostReplyMemory(
    requestPet.petId,
    () => persistPostReplyMemory({
      pet: requestPet,
      message,
      reply: response.reply,
      history: historyBeforeMessage,
      memoryContext,
      sourceMessageIds: [userMessage.id, assistantMessage.id],
      includePromptDebug,
      onLiteOverlayPatch,
    }),
    (error: unknown) => {
      if (includePromptDebug) {
        console.warn(`[memory-debug] ${logLabel} post-reply memory failed`, error);
      }
    },
  );
  if (!memoryTaskEnqueued && includePromptDebug) {
    console.warn(`[memory-debug] ${logLabel} post-reply memory queue is full`);
  }

  return {
    response,
    userMessage,
    assistantMessage,
  };
}

export async function runLocalPetChatTurn(
  options: RunLocalPetChatTurnOptions,
): Promise<LocalPetChatTurnResult> {
  return withLocalPetMutationLock(
    options.pet.petId,
    "conversation",
    (guard) => runLocalPetChatTurnLocked(options, guard),
  );
}
