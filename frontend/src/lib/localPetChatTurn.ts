import {
  extractLocalLiteFacts,
  extractLocalUserMemory,
  sendLocalChatMessage,
} from "./api";
import {
  appendLocalChatMessages,
  createLocalId,
  latestChatMessages,
} from "./localPetStorage";
import {
  applyMemoryOperations,
  markMemoryContextUsed,
  readLocalPetMemory,
  shouldRunDailyConsolidation,
  writeLocalPetMemory,
} from "./localPetMemoryStorage";
import { buildMemoryContextForMessage } from "./localPetMemoryRecall";
import {
  recordLiteOverlayPatchDebug,
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
  onMemoryConsolidationNeeded?: () => void;
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
  onLiteOverlayPatch,
  onMemoryConsolidationNeeded,
}: RunLocalPetChatTurnOptions): Promise<LocalPetChatTurnResult> {
  const now = new Date().toISOString();
  const userMessage: LocalChatMessage = {
    id: createLocalId("message"),
    role: "user",
    text: message,
    createdAt: now,
  };
  const historyBeforeMessage = history ?? latestChatMessages(12);
  const historyForPrompt = dialogueHookMessage
    ? [...historyBeforeMessage, dialogueHookMessage].slice(-12)
    : historyBeforeMessage;
  const memoryBeforeMessage = readLocalPetMemory(pet.petId);
  const memoryContext = buildMemoryContextForMessage(
    memoryBeforeMessage,
    message,
    new Date(now),
  );

  if (includePromptDebug) {
    console.log(`[memory-debug] ${logLabel} recall context`, memoryContext);
  }
  recordMemoryContextDebug(memoryContext);
  const optimisticHistory = appendLocalChatMessages(
    dialogueHookMessage ? [dialogueHookMessage, userMessage] : [userMessage],
  );
  onHistoryChange?.(optimisticHistory.messages);

  const response = await sendLocalChatMessage(message, pet, historyForPrompt, {
    includeDebug: includePromptDebug,
    memoryContext,
    replyMaxChars,
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
  onHistoryChange?.(nextHistory.messages);
  recordLiteOverlayPatchDebug(response.debug?.liteOverlayPatch);

  const selectedMemoryIds = memoryContext.relevantMemories.map((item) => item.id);
  if (selectedMemoryIds.length) {
    writeLocalPetMemory(markMemoryContextUsed(readLocalPetMemory(pet.petId), selectedMemoryIds));
  }

  void extractLocalLiteFacts(message, response.reply, pet, historyForPrompt, {
    includeDebug: includePromptDebug,
  })
    .then((extraction) => {
      logBrowserPromptDebug(`${logLabel} lite fact extraction`, extraction);
      if (extraction.liteOverlayPatch) {
        recordLiteOverlayPatchDebug(extraction.liteOverlayPatch);
        onLiteOverlayPatch?.(extraction.liteOverlayPatch);
      }
    })
    .catch(() => undefined);

  void extractLocalUserMemory(
    message,
    response.reply,
    pet,
    historyForPrompt,
    memoryBeforeMessage,
    {
      includeDebug: includePromptDebug,
      memoryContext,
    },
  )
    .then((extraction) => {
      logBrowserPromptDebug(`${logLabel} memory extraction`, extraction);
      recordMemoryOperationsDebug(extraction.operations);
      const latestMemory = readLocalPetMemory(pet.petId);
      const nextMemory = applyMemoryOperations(latestMemory, extraction.operations, [
        userMessage.id,
        assistantMessage.id,
      ]);
      writeLocalPetMemory(nextMemory);
      if (shouldRunDailyConsolidation(nextMemory)) {
        onMemoryConsolidationNeeded?.();
      }
    })
    .catch(() => undefined);

  return {
    response,
    userMessage,
    assistantMessage,
  };
}
