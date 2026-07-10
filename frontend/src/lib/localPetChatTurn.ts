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
  dialogueHookMessage?: LocalChatMessage | null;
  history?: LocalChatMessage[];
  logLabel: string;
  onHistoryChange?: (messages: LocalChatMessage[]) => void;
  onLiteOverlayPatch?: (patch: Record<string, unknown>) => void;
};

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
  onLiteOverlayPatch?: (patch: Record<string, unknown>) => void;
}) {
  const memoryBeforeExtraction = readLocalPetMemory(pet.petId);
  const [memoryResult, liteFactsResult] = await Promise.allSettled([
    extractLocalUserMemory(message, reply, pet, history, memoryBeforeExtraction, {
      includeDebug: includePromptDebug,
      memoryContext,
    }),
    extractLocalLiteFacts(message, reply, pet, history, {
      includeDebug: includePromptDebug,
    }),
  ]);

  if (memoryResult.status === "fulfilled") {
    if (memoryResult.value.operations.length) {
      const latestMemory = readLocalPetMemory(pet.petId);
      const updatedMemory = applyMemoryOperations(
        latestMemory,
        memoryResult.value.operations,
        sourceMessageIds,
      );
      writeLocalPetMemory(updatedMemory);
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
        if (consolidation.operations.length) {
          writeLocalPetMemory(
            applyMemoryConsolidationOperations(
              readLocalPetMemory(pet.petId),
              consolidation.operations,
            ),
          );
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

  if (liteFactsResult.status === "fulfilled" && liteFactsResult.value.liteOverlayPatch) {
    onLiteOverlayPatch?.(liteFactsResult.value.liteOverlayPatch);
    if (includePromptDebug) {
      recordLiteOverlayPatchDebug(liteFactsResult.value.liteOverlayPatch);
    }
  }
}

export async function runLocalPetChatTurn({
  pet,
  message,
  includePromptDebug,
  replyMaxChars,
  dialogueHookMessage,
  history,
  logLabel,
  onHistoryChange,
  onLiteOverlayPatch,
}: RunLocalPetChatTurnOptions): Promise<LocalPetChatTurnResult> {
  const now = new Date().toISOString();
  const userMessage: LocalChatMessage = {
    id: createLocalId("message"),
    role: "user",
    text: message,
    createdAt: now,
  };
  const fullHistoryBeforeMessage = history ?? readLocalChatHistory().messages;
  const dialogueHookIsNew = Boolean(
    dialogueHookMessage
      && !fullHistoryBeforeMessage.some((item) => item.id === dialogueHookMessage.id),
  );
  const historyWithDialogueHook = dialogueHookMessage
    ? appendUniqueMessages(fullHistoryBeforeMessage, [dialogueHookMessage])
    : fullHistoryBeforeMessage;
  const historyBeforeMessage = historyWithDialogueHook.slice(-12);
  const memoryBeforeMessage = readLocalPetMemory(pet.petId);
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
  const optimisticHistory = appendLocalChatMessages([
    ...(dialogueHookMessage && dialogueHookIsNew ? [dialogueHookMessage] : []),
    userMessage,
  ]);
  onHistoryChange?.(optimisticHistory.messages);

  const response = await sendLocalChatMessage(message, pet, historyBeforeMessage, {
    includeDebug: includePromptDebug,
    complimentHistory: readComplimentHistory(pet.petId),
    memoryContext,
    replyMaxChars,
  });
  if (response.happinessDelta === 30 || response.happinessDelta === 100) {
    rememberCompliment(pet.petId, response.complimentKey ?? message);
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
  const nextHistory = appendLocalChatMessages([assistantMessage]);
  onHistoryChange?.(nextHistory.messages);

  const usedMemoryIds = memoryContext.relevantMemories.map((item) => item.id);
  const memoryOperations = extractDeterministicMemoryOperations(message);
  if (usedMemoryIds.length || memoryOperations.length) {
    let nextMemory = markMemoryContextUsed(memoryBeforeMessage, usedMemoryIds);
    if (memoryOperations.length) {
      nextMemory = applyMemoryOperations(nextMemory, memoryOperations, [userMessage.id]);
    }
    writeLocalPetMemory(nextMemory);
  }

  try {
    await persistPostReplyMemory({
      pet,
      message,
      reply: response.reply,
      history: historyBeforeMessage,
      memoryContext,
      sourceMessageIds: [userMessage.id, assistantMessage.id],
      includePromptDebug,
      onLiteOverlayPatch,
    });
  } catch (error: unknown) {
    if (includePromptDebug) {
      console.warn(`[memory-debug] ${logLabel} post-reply memory failed`, error);
    }
  }

  return {
    response,
    userMessage,
    assistantMessage,
  };
}
