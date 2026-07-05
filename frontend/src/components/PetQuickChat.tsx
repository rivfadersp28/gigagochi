"use client";

/* eslint-disable @next/next/no-img-element */
import { Loader2 } from "lucide-react";
import { FormEvent, useState } from "react";

import { ApiError, extractLocalLiteFacts, sendLocalChatMessage } from "@/lib/api";
import { appendLocalChatMessages, createLocalId, latestChatMessages } from "@/lib/localPetStorage";
import { primePetSpeechAudio } from "@/lib/petSpeechAudio";
import { logBrowserPromptDebug } from "@/lib/promptDebug";
import { hapticNotification } from "@/lib/telegram";
import type { LocalChatResponse, LocalPetState, PromptLayers, ReplyMode } from "@/lib/types";

type PetQuickChatProps = {
  pet: LocalPetState;
  promptLayers: PromptLayers;
  includePromptDebug: boolean;
  replyMode: ReplyMode;
  onChatResponse: (response: LocalChatResponse) => void;
  onLiteOverlayPatch?: (patch?: Record<string, unknown>) => void;
};

function errorMessage(caught: unknown, fallback: string): string {
  return caught instanceof ApiError ? caught.message : fallback;
}

export function PetQuickChat({
  pet,
  promptLayers,
  includePromptDebug,
  replyMode,
  onChatResponse,
  onLiteOverlayPatch,
}: PetQuickChatProps) {
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSending, setIsSending] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const message = input.trim().slice(0, 1000);
    if (!message || isSending) {
      return;
    }

    void primePetSpeechAudio();
    setInput("");
    setError(null);
    setIsSending(true);

    try {
      const userMessage = {
        id: createLocalId("message"),
        role: "user" as const,
        text: message,
        createdAt: new Date().toISOString(),
      };
      const history = latestChatMessages(12);
      appendLocalChatMessages([userMessage]);
      const response = await sendLocalChatMessage(message, pet, history, {
        promptLayers,
        includeDebug: includePromptDebug,
        replyMode,
      });
      logBrowserPromptDebug("quick chat reply", response);
      appendLocalChatMessages([
        {
          id: createLocalId("message"),
          role: "pet",
          text: response.reply,
          createdAt: new Date().toISOString(),
        },
      ]);
      onChatResponse(response);
      if (replyMode === "lite") {
        void extractLocalLiteFacts(message, response.reply, pet, history, {
          includeDebug: includePromptDebug,
        })
          .then((extraction) => {
            logBrowserPromptDebug("quick chat lite fact extraction", extraction);
            if (extraction.liteOverlayPatch) {
              onLiteOverlayPatch?.(extraction.liteOverlayPatch);
            }
          })
          .catch(() => undefined);
      }
    } catch (caught) {
      setError(errorMessage(caught, "Не удалось отправить сообщение."));
      hapticNotification("error");
    } finally {
      setIsSending(false);
    }
  }

  const isDisabled = isSending;

  return (
    <form
      onSubmit={handleSubmit}
      className="relative h-[108px] w-full overflow-hidden rounded-[40px] border-[0.5px] border-[rgba(0,0,0,0.07)] bg-[#f5f5f5] sm:h-[116px]"
    >
      <input
        value={input}
        onChange={(event) => setInput(event.target.value)}
        maxLength={1000}
        className="absolute left-[27.5px] right-[72px] top-[23px] h-[31px] bg-transparent p-0 text-[17px] font-normal leading-none text-black outline-none placeholder:text-black/30 disabled:cursor-not-allowed"
        placeholder="Как у тебя дела?"
        disabled={isDisabled}
        aria-label="Написать питомцу"
      />

      {error ? (
        <p
          className="absolute bottom-[23px] left-[28px] max-w-[280px] truncate text-[12px] leading-none text-[var(--danger)]"
          aria-live="polite"
        >
          {error}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={isDisabled || !input.trim()}
        className="absolute bottom-[16.5px] right-[16.5px] grid size-[36px] place-items-center rounded-full focus:outline-none focus:ring-2 focus:ring-black/10 disabled:cursor-not-allowed"
        aria-label="Отправить сообщение"
        title="Отправить"
      >
        {isSending ? (
          <span className="grid size-[36px] place-items-center rounded-full bg-black text-white">
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          </span>
        ) : (
          <img
            src="/figma/send-button.svg"
            alt=""
            aria-hidden="true"
            className="size-[36px] max-w-none"
            draggable={false}
          />
        )}
      </button>
    </form>
  );
}
