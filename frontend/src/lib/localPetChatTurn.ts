import { sendLocalChatMessage } from "./api";
import {
  appendLocalChatMessages,
  createLocalId,
  readLocalChatHistory,
} from "./localPetStorage";
import { extractDeterministicMemoryOperations } from "./localPetDeterministicMemory";
import {
  applyMemoryOperations,
  markMemoryContextUsed,
  readLocalPetMemory,
  writeLocalPetMemory,
} from "./localPetMemoryStorage";
import {
  buildMemoryContextForMessage,
  shouldUseDialogueHookContext,
} from "./localPetMemoryRecall";
import {
  recordMemoryContextDebug,
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
};

export async function runLocalPetChatTurn({
  pet,
  message,
  includePromptDebug,
  replyMaxChars,
  dialogueHookMessage,
  history,
  logLabel,
  onHistoryChange,
}: RunLocalPetChatTurnOptions): Promise<LocalPetChatTurnResult> {
  const now = new Date().toISOString();
  const userMessage: LocalChatMessage = {
    id: createLocalId("message"),
    role: "user",
    text: message,
    createdAt: now,
  };
  const fullHistoryBeforeMessage = history ?? readLocalChatHistory().messages;
  const historyBeforeMessage = fullHistoryBeforeMessage.slice(-12);
  const memoryBeforeMessage = readLocalPetMemory(pet.petId);
  const visibleContext = dialogueHookMessage && shouldUseDialogueHookContext(message)
    ? { lastPetLine: dialogueHookMessage.text }
    : undefined;
  const memoryContext = buildMemoryContextForMessage(
    fullHistoryBeforeMessage,
    message,
    new Date(now),
    memoryBeforeMessage,
  );

  if (includePromptDebug) {
    console.log(`[memory-debug] ${logLabel} recall context`, memoryContext);
  }
  recordMemoryContextDebug(memoryContext);
  const optimisticHistory = appendLocalChatMessages([userMessage]);
  const optimisticMessages = dialogueHookMessage
    ? [...optimisticHistory.messages.slice(0, -1), dialogueHookMessage, userMessage]
    : optimisticHistory.messages;
  onHistoryChange?.(optimisticMessages);

  const response = await sendLocalChatMessage(message, pet, historyBeforeMessage, {
    includeDebug: includePromptDebug,
    memoryContext,
    replyMaxChars,
    visibleContext,
  });
  logBrowserPromptDebug(`${logLabel} reply`, response);
  recordReplyPromptDebug(response);

  const assistantMessage: LocalChatMessage = {
    id: createLocalId("message"),
    role: "pet",
    text: response.reply,
    createdAt: new Date().toISOString(),
  };
  const nextHistory = appendLocalChatMessages([assistantMessage]);
  const nextMessages = dialogueHookMessage
    ? [...nextHistory.messages.slice(0, -2), dialogueHookMessage, userMessage, assistantMessage]
    : nextHistory.messages;
  onHistoryChange?.(nextMessages);

  const usedMemoryIds = memoryContext.relevantMemories.map((item) => item.id);
  const memoryOperations = extractDeterministicMemoryOperations(message);
  if (usedMemoryIds.length || memoryOperations.length) {
    let nextMemory = markMemoryContextUsed(memoryBeforeMessage, usedMemoryIds);
    if (memoryOperations.length) {
      nextMemory = applyMemoryOperations(nextMemory, memoryOperations, [userMessage.id]);
    }
    writeLocalPetMemory(nextMemory);
  }

  return {
    response,
    userMessage,
    assistantMessage,
  };
}
