"use client";

import { ArrowLeft, Bug, Loader2, Send } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  consolidateLocalUserMemory,
  extractLocalLiteFacts,
  extractLocalUserMemory,
  generateLocalProactiveMessage,
  sendLocalChatMessage,
} from "@/lib/api";
import {
  appendLocalChatMessages,
  createLocalId,
  latestChatMessages,
  readLocalChatHistory,
  readLocalPetSettings,
} from "@/lib/localPetStorage";
import {
  applyMemoryConsolidationOperations,
  applyMemoryOperations,
  markMemoryContextUsed,
  readLocalPetMemory,
  recordProactiveDelivery,
  shouldRunDailyConsolidation,
  writeLocalPetMemory,
} from "@/lib/localPetMemoryStorage";
import {
  buildDailyProactiveMemoryContext,
  buildMemoryContextForMessage,
} from "@/lib/localPetMemoryRecall";
import {
  recordLiteOverlayPatchDebug,
  recordMemoryConsolidationDebug,
  recordMemoryContextDebug,
  recordMemoryOperationsDebug,
  recordReplyPromptDebug,
} from "@/lib/debugPanelStorage";
import { logBrowserPromptDebug } from "@/lib/promptDebug";
import { hapticNotification, useTelegramBackButton } from "@/lib/telegram";
import type { LocalChatMessage } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";

import { DebugPanel } from "./DebugPanel";

type ChatViewProps = {
  petId: string;
};

export function ChatView({ petId }: ChatViewProps) {
  const router = useRouter();
  const localPet = useLocalPetState();
  const [messages, setMessages] = useState<LocalChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);
  const [isDebugPanelOpen, setIsDebugPanelOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const proactiveAttemptedRef = useRef(false);
  const pet = localPet.pet;

  const goBack = useCallback(() => {
    router.push(`/pet/${petId}`);
  }, [petId, router]);

  useTelegramBackButton(goBack);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setMessages(readLocalChatHistory().messages);
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, []);

  useEffect(() => {
    if (localPet.status === "loading") {
      return;
    }
    if (!pet || pet.petId !== petId) {
      router.replace("/");
    }
  }, [localPet.status, pet, petId, router]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages, isSending]);

  const runMemoryConsolidationIfNeeded = useCallback(
    async (includePromptDebug: boolean) => {
      if (!pet) {
        return;
      }
      const memory = readLocalPetMemory(pet.petId);
      if (!shouldRunDailyConsolidation(memory)) {
        return;
      }
      const consolidation = await consolidateLocalUserMemory(memory, {
        includeDebug: includePromptDebug,
      });
      logBrowserPromptDebug("memory consolidation", consolidation);
      recordMemoryConsolidationDebug(consolidation.operations);
      const latestMemory = readLocalPetMemory(pet.petId);
      writeLocalPetMemory(
        applyMemoryConsolidationOperations(latestMemory, consolidation.operations),
      );
    },
    [pet],
  );

  useEffect(() => {
    if (localPet.status === "loading" || !pet || pet.petId !== petId) {
      return;
    }
    if (proactiveAttemptedRef.current) {
      return;
    }
    proactiveAttemptedRef.current = true;

    const settings = readLocalPetSettings();
    void runMemoryConsolidationIfNeeded(settings.includePromptDebug).catch(() => undefined);

    const memory = readLocalPetMemory(pet.petId);
    const history = readLocalChatHistory().messages;
    const memoryContext = buildDailyProactiveMemoryContext(pet, memory, history);
    if (!memoryContext) {
      return;
    }
    if (settings.includePromptDebug) {
      console.log("[memory-debug] proactive candidate", memoryContext);
    }
    recordMemoryContextDebug(memoryContext, "Память подставлена в proactive prompt");
    void generateLocalProactiveMessage(pet, memoryContext, {
      includeDebug: true,
    })
      .then((response) => {
        logBrowserPromptDebug("proactive chat", response);
        recordReplyPromptDebug(response);
        const proactiveMessage: LocalChatMessage = {
          id: createLocalId("message"),
          role: "pet",
          text: response.reply,
          createdAt: new Date().toISOString(),
        };
        setMessages(appendLocalChatMessages([proactiveMessage]).messages);
        const latestMemory = readLocalPetMemory(pet.petId);
        writeLocalPetMemory(
          recordProactiveDelivery(
            latestMemory,
            memoryContext.proactiveCandidate?.memoryIds ?? [],
            response.reply,
          ),
        );
        localPet.applyMoodHint(response.moodHint);
      })
      .catch(() => undefined);
  }, [localPet, pet, petId, runMemoryConsolidationIfNeeded]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = input.trim();
    if (!message) {
      return;
    }

    const now = new Date().toISOString();
    if (!pet) {
      return;
    }

    const localUserMessage: LocalChatMessage = {
      id: createLocalId("message"),
      role: "user",
      text: message,
      createdAt: now,
    };

    setInput("");
    setError(null);
    setIsSending(true);
    const historyBeforeMessage = latestChatMessages(12);
    const memoryBeforeMessage = readLocalPetMemory(pet.petId);
    const memoryContext = buildMemoryContextForMessage(
      memoryBeforeMessage,
      message,
      new Date(now),
    );
    setMessages(appendLocalChatMessages([localUserMessage]).messages);

    try {
      const settings = readLocalPetSettings();
      if (settings.includePromptDebug) {
        console.log("[memory-debug] recall context", memoryContext);
      }
      recordMemoryContextDebug(memoryContext);
      const response = await sendLocalChatMessage(message, pet, historyBeforeMessage, {
        includeDebug: true,
        memoryContext,
      });
      logBrowserPromptDebug("chat reply", response);
      recordReplyPromptDebug(response);
      const assistantMessage: LocalChatMessage = {
        id: createLocalId("message"),
        role: "pet",
        text: response.reply,
        createdAt: new Date().toISOString(),
      };
      setMessages(appendLocalChatMessages([assistantMessage]).messages);
      recordLiteOverlayPatchDebug(response.debug?.liteOverlayPatch);
      localPet.applyMoodHint(response.moodHint, response.debug?.liteOverlayPatch);
      const selectedMemoryIds = memoryContext.relevantMemories.map((item) => item.id);
      if (selectedMemoryIds.length) {
        writeLocalPetMemory(
          markMemoryContextUsed(readLocalPetMemory(pet.petId), selectedMemoryIds),
        );
      }
      void extractLocalLiteFacts(message, response.reply, pet, historyBeforeMessage, {
        includeDebug: settings.includePromptDebug,
      })
        .then((extraction) => {
          logBrowserPromptDebug("lite fact extraction", extraction);
          if (extraction.liteOverlayPatch) {
            recordLiteOverlayPatchDebug(extraction.liteOverlayPatch);
            localPet.applyLiteOverlayPatch(extraction.liteOverlayPatch);
          }
        })
        .catch(() => undefined);
      void extractLocalUserMemory(
        message,
        response.reply,
        pet,
        historyBeforeMessage,
        memoryBeforeMessage,
        {
          includeDebug: settings.includePromptDebug,
          memoryContext,
        },
      )
        .then((extraction) => {
          logBrowserPromptDebug("memory extraction", extraction);
          recordMemoryOperationsDebug(extraction.operations);
          const latestMemory = readLocalPetMemory(pet.petId);
          const nextMemory = applyMemoryOperations(latestMemory, extraction.operations, [
            localUserMessage.id,
            assistantMessage.id,
          ]);
          writeLocalPetMemory(nextMemory);
          if (shouldRunDailyConsolidation(nextMemory)) {
            void runMemoryConsolidationIfNeeded(settings.includePromptDebug).catch(
              () => undefined,
            );
          }
        })
        .catch(() => undefined);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(caught.message);
      } else {
        setError("Не удалось отправить сообщение.");
      }
      hapticNotification("error");
    } finally {
      setIsSending(false);
    }
  }

  if (localPet.status === "loading") {
    return (
      <main className="tma-screen grid place-items-center bg-[var(--paper)] px-6">
        <Loader2 className="size-5 animate-spin text-[var(--ink-muted)]" aria-label="Loading" />
      </main>
    );
  }

  return (
    <main className="tma-screen bg-[var(--paper)] px-5 pb-[calc(var(--tma-safe-bottom)+18px)] pt-[calc(var(--tma-safe-top)+24px)] text-[var(--ink)] sm:px-8 sm:py-10">
      <div className="mx-auto grid h-full w-full max-w-4xl grid-rows-[auto_1fr_auto] gap-5">
        <header className="flex items-center justify-between border-b border-[var(--line-soft)] pb-5">
          <div>
            <p className="text-sm font-medium text-[var(--ink-muted)]">AI Tamagotchi</p>
            <h1 className="mt-1 text-2xl font-semibold text-[var(--ink)]">Chat</h1>
          </div>
          <div className="flex items-center gap-2">
            {pet ? (
              <button
                type="button"
                aria-controls="debug-panel"
                aria-expanded={isDebugPanelOpen}
                aria-label="Открыть debug-панель"
                onClick={() => setIsDebugPanelOpen(true)}
                className="grid size-10 place-items-center rounded-[8px] border border-[var(--line)] text-[var(--ink)] transition-colors hover:bg-[var(--surface)] focus:outline-none focus:ring-2 focus:ring-[var(--leaf-soft)]"
              >
                <Bug className="size-4" aria-hidden="true" />
              </button>
            ) : null}
            <Link
              href={`/pet/${petId}`}
              className="inline-flex h-10 items-center gap-2 rounded-[8px] border border-[var(--line)] px-3 text-sm font-medium text-[var(--ink)] transition-colors hover:bg-[var(--surface)]"
            >
              <ArrowLeft className="size-4" aria-hidden="true" />
              Back
            </Link>
          </div>
        </header>

        <section className="min-h-0 overflow-y-auto rounded-[8px] border border-[var(--line)] bg-[var(--surface-raised)] p-4">
          {messages.length === 0 ? (
            <div className="grid h-full place-items-center text-sm text-[var(--ink-muted)]">
              No messages
            </div>
          ) : (
            <div className="grid gap-3">
              {messages.map((message) => (
                <div
                  key={message.id}
                  className={
                    message.role === "user"
                      ? "ml-auto max-w-[72%] rounded-[8px] bg-[var(--ink)] px-4 py-3 text-sm leading-6 text-[var(--paper)]"
                      : "mr-auto max-w-[72%] rounded-[8px] border border-[var(--line-soft)] bg-[var(--surface)] px-4 py-3 text-sm leading-6 text-[var(--ink)]"
                  }
                >
                  {message.text}
                </div>
              ))}
              {isSending ? (
                <div className="mr-auto inline-flex items-center gap-2 rounded-[8px] border border-[var(--line-soft)] bg-[var(--surface)] px-4 py-3 text-sm text-[var(--ink-muted)]">
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                  Thinking
                </div>
              ) : null}
              <div ref={bottomRef} />
            </div>
          )}
        </section>

        <form onSubmit={handleSubmit} className="grid gap-2">
          {error ? (
            <div className="rounded-[8px] border border-[var(--danger-line)] bg-[var(--danger-bg)] px-3 py-2 text-sm text-[var(--danger)]">
              {error}
            </div>
          ) : null}
          <div className="flex gap-3">
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              maxLength={1000}
              className="h-11 min-w-0 flex-1 rounded-[8px] border border-[var(--line)] bg-[var(--control)] px-4 text-sm text-[var(--ink)] outline-none placeholder:text-[var(--ink-faint)] focus:border-[var(--leaf)] focus:ring-2 focus:ring-[var(--leaf-soft)]"
              placeholder="Message"
              disabled={isSending}
            />
            <button
              type="submit"
              disabled={isSending || !input.trim()}
              className="inline-flex h-11 items-center gap-2 rounded-[8px] bg-[var(--leaf)] px-4 text-sm font-medium text-white transition-colors hover:bg-[var(--leaf-strong)] focus:outline-none focus:ring-2 focus:ring-[var(--leaf-soft)] disabled:cursor-not-allowed disabled:opacity-55"
            >
              {isSending ? (
                <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              ) : (
                <Send className="size-4" aria-hidden="true" />
              )}
              Send
            </button>
          </div>
        </form>
      </div>
      {pet ? (
        <DebugPanel
          pet={pet}
          isOpen={isDebugPanelOpen}
          onClose={() => setIsDebugPanelOpen(false)}
        />
      ) : null}
    </main>
  );
}
