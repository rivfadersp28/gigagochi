"use client";

import { ArrowLeft, Bug, Loader2, Send } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  generateLocalProactiveMessage,
} from "@/lib/api";
import { presentError, type PresentedError } from "@/lib/errorPresentation";
import {
  CHAT_HISTORY_STORAGE_KEY,
  createLocalId,
  readLocalChatHistory,
  readLocalPetState,
  readLocalPetSettings,
} from "@/lib/localPetStorage";
import { withLocalPetMutationLock } from "@/lib/localPetMutationLock";
import {
  readLocalPetMemory,
  recordProactiveDelivery,
  writeLocalPetMemory,
} from "@/lib/localPetMemoryStorage";
import { buildDailyProactiveMemoryContext } from "@/lib/localPetMemoryRecall";
import {
  LocalPetTurnSupersededError,
  runLocalPetChatTurn,
} from "@/lib/localPetChatTurn";
import {
  recordMemoryContextDebug,
  recordReplyPromptDebug,
} from "@/lib/debugPanelStorage";
import { logBrowserPromptDebug } from "@/lib/promptDebug";
import {
  hapticNotification,
  useTelegramBackButton,
} from "@/lib/telegram";
import type { LocalChatMessage } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";
import { useTelegramCapabilities } from "@/lib/useTelegramCapabilities";

import { DebugPanel } from "./DebugPanel";
import { ErrorNotice } from "./ErrorNotice";

type ChatViewProps = {
  petId: string;
};

export function ChatView({ petId }: ChatViewProps) {
  const router = useRouter();
  const localPet = useLocalPetState();
  const [messages, setMessages] = useState<LocalChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState<PresentedError | null>(null);
  const [isSending, setIsSending] = useState(false);
  const [isDebugPanelOpen, setIsDebugPanelOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const mountedRef = useRef(false);
  const isSendingRef = useRef(false);
  const proactiveAttemptedRef = useRef(false);
  const proactiveDialogueHookRef = useRef<LocalChatMessage | null>(null);
  const pet = localPet.pet;
  const canShowDebugMenu = useTelegramCapabilities().debugMenu;

  const goBack = useCallback(() => {
    router.push(`/pet/${petId}`);
  }, [petId, router]);

  useTelegramBackButton(goBack);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      setMessages(readLocalChatHistory().messages);
    }, 0);
    const reconcileHistory = (event: StorageEvent) => {
      if (event.key === null || event.key === CHAT_HISTORY_STORAGE_KEY) {
        setMessages(readLocalChatHistory().messages);
      }
    };
    window.addEventListener("storage", reconcileHistory);
    return () => {
      window.clearTimeout(timeoutId);
      window.removeEventListener("storage", reconcileHistory);
    };
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

  useEffect(() => {
    if (localPet.status === "loading" || !pet || pet.petId !== petId) {
      return;
    }
    if (proactiveAttemptedRef.current) {
      return;
    }
    proactiveAttemptedRef.current = true;

    const requestedPetId = pet.petId;
    void withLocalPetMutationLock(
      requestedPetId,
      "conversation",
      async ({ assertOwned }) => {
        const currentPet = readLocalPetState();
        if (!mountedRef.current || currentPet?.petId !== requestedPetId) {
          return;
        }
        const settings = readLocalPetSettings();
        const memory = readLocalPetMemory(requestedPetId);
        const history = readLocalChatHistory().messages;
        const memoryContext = buildDailyProactiveMemoryContext(
          currentPet,
          memory,
          history,
        );
        if (!memoryContext) {
          return;
        }
        if (settings.includePromptDebug) {
          console.log("[memory-debug] proactive candidate", memoryContext);
          recordMemoryContextDebug(memoryContext, "Память подставлена в proactive prompt");
        }
        const response = await generateLocalProactiveMessage(currentPet, memoryContext, {
          includeDebug: settings.includePromptDebug,
        });
        assertOwned();
        if (!mountedRef.current || readLocalPetState()?.petId !== requestedPetId) {
          return;
        }
        if (settings.includePromptDebug) {
          logBrowserPromptDebug("proactive chat", response);
          recordReplyPromptDebug(response);
        }
        const proactiveMessage: LocalChatMessage = {
          id: createLocalId("message"),
          role: "pet",
          text: response.reply,
          createdAt: new Date().toISOString(),
        };
        await withLocalPetMutationLock(
          requestedPetId,
          "memory",
          ({ assertOwned: assertMemoryOwned }) => {
            const latestMemory = readLocalPetMemory(requestedPetId);
            const delivered = recordProactiveDelivery(
              latestMemory,
              memoryContext.proactiveCandidate?.memoryIds ?? [],
              response.reply,
            );
            assertMemoryOwned();
            if (!writeLocalPetMemory(delivered, requestedPetId)) {
              throw new LocalPetTurnSupersededError();
            }
          },
          { acquireTimeoutMs: 10 * 60_000 },
        );
        assertOwned();
        if (!mountedRef.current || readLocalPetState()?.petId !== requestedPetId) {
          return;
        }
        proactiveDialogueHookRef.current = proactiveMessage;
        setMessages([...readLocalChatHistory().messages, proactiveMessage]);
        localPet.applyMoodHint(
          response.moodHint,
          response.storyLibraryPatch ?? response.debug?.storyLibraryPatch,
          0,
          requestedPetId,
        );
      },
    )
      .catch(() => undefined);
  }, [localPet, pet, petId]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSendingRef.current) {
      return;
    }
    const message = input.trim();
    if (!message) {
      return;
    }

    if (!pet || pet.petId !== petId) {
      return;
    }

    isSendingRef.current = true;
    setInput("");
    setError(null);
    setIsSending(true);
    const dialogueHookMessage = proactiveDialogueHookRef.current;
    proactiveDialogueHookRef.current = null;
    const requestedPetId = pet.petId;

    try {
      const settings = readLocalPetSettings();
      const { response } = await runLocalPetChatTurn({
        pet,
        message,
        includePromptDebug: settings.includePromptDebug,
        dialogueHookMessage,
        logLabel: "chat",
        onHistoryChange: setMessages,
        onLiteOverlayPatch: localPet.applyLiteOverlayPatch,
      });
      localPet.applyMoodHint(
        response.moodHint,
        response.storyLibraryPatch ?? response.debug?.storyLibraryPatch,
        response.happinessDelta,
        requestedPetId,
      );
      if (response.petPatch?.name) {
        localPet.updateName(response.petPatch.name, requestedPetId);
      }
    } catch (caught) {
      const currentPet = readLocalPetState();
      if (
        caught instanceof LocalPetTurnSupersededError
        || currentPet?.petId !== requestedPetId
      ) {
        return;
      }
      if (mountedRef.current) {
        setInput((currentDraft) => currentDraft || message);
      }
      setError(presentError(caught, "Не получилось отправить сообщение. Попробуйте ещё раз."));
      hapticNotification("error");
    } finally {
      isSendingRef.current = false;
      if (mountedRef.current) {
        setIsSending(false);
      }
    }
  }

  if (localPet.status === "loading") {
    return (
      <main className="tma-screen grid place-items-center bg-[var(--paper)] px-6">
        <div role="status" className="grid place-items-center gap-2 text-sm text-[var(--ink-muted)]">
          <Loader2 className="size-5 animate-spin" aria-hidden="true" />
          Загружаем чат
        </div>
      </main>
    );
  }

  return (
    <main className="tma-screen bg-[var(--paper)] px-5 pb-[calc(var(--tma-safe-bottom)+18px)] pt-[calc(var(--tma-safe-top)+24px)] text-[var(--ink)] sm:px-8 sm:py-10">
      <div className="mx-auto grid h-full w-full max-w-4xl grid-rows-[auto_1fr_auto] gap-5">
        <header className="flex items-center justify-between border-b border-[var(--line-soft)] pb-5">
          <div>
            <p className="text-sm font-medium text-[var(--ink-muted)]">AI Tamagotchi</p>
            <h1 className="mt-1 text-balance text-2xl font-semibold text-[var(--ink)]">Чат</h1>
          </div>
          <div className="flex items-center gap-2">
            {pet && canShowDebugMenu ? (
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
              Назад
            </Link>
          </div>
        </header>

        <section
          aria-label="Диалог"
          aria-live="polite"
          aria-busy={isSending}
          className="min-h-0 overflow-y-auto rounded-[8px] border border-[var(--line)] bg-[var(--surface-raised)] p-4"
        >
          {messages.length === 0 ? (
            <div className="grid h-full place-items-center text-sm text-[var(--ink-muted)]">
              Напишите питомцу
            </div>
          ) : (
            <div className="grid gap-3">
              {messages.map((message) => (
                <div
                  key={message.id}
                  className={
                    message.role === "user"
                      ? "ml-auto max-w-[72%] [overflow-wrap:anywhere] rounded-[8px] bg-[var(--ink)] px-4 py-3 text-sm leading-6 text-[var(--paper)]"
                      : "mr-auto max-w-[72%] [overflow-wrap:anywhere] rounded-[8px] border border-[var(--line-soft)] bg-[var(--surface)] px-4 py-3 text-sm leading-6 text-[var(--ink)]"
                  }
                >
                  {message.text}
                </div>
              ))}
              {isSending ? (
                <div className="mr-auto inline-flex items-center gap-2 rounded-[8px] border border-[var(--line-soft)] bg-[var(--surface)] px-4 py-3 text-sm text-[var(--ink-muted)]">
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                  Питомец думает
                </div>
              ) : null}
              <div ref={bottomRef} />
            </div>
          )}
        </section>

        <form onSubmit={handleSubmit} className="grid gap-2">
          {error ? (
            <ErrorNotice
              error={error}
              id="chat-error"
              className="rounded-[8px] border border-[var(--danger-line)] bg-[var(--danger-bg)] px-3 py-2 text-sm text-[var(--danger)]"
            />
          ) : null}
          <div className="flex gap-3">
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              maxLength={1000}
              className="h-11 min-w-0 flex-1 rounded-[8px] border border-[var(--line)] bg-[var(--control)] px-4 text-sm text-[var(--ink)] outline-none placeholder:text-[var(--ink-faint)] focus:border-[var(--leaf)] focus:ring-2 focus:ring-[var(--leaf-soft)]"
              placeholder="Сообщение"
              aria-label="Сообщение питомцу"
              aria-describedby={error ? "chat-error" : undefined}
              aria-invalid={Boolean(error)}
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
              Отправить
            </button>
          </div>
        </form>
      </div>
      {pet && canShowDebugMenu ? (
        <DebugPanel
          pet={pet}
          isOpen={isDebugPanelOpen}
          onClose={() => setIsDebugPanelOpen(false)}
        />
      ) : null}
    </main>
  );
}
