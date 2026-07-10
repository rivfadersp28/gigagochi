"use client";

/* eslint-disable @next/next/no-img-element */
import { Bug, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { flushSync } from "react-dom";
import type {
  CSSProperties,
  FormEvent,
  MouseEvent,
} from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  generateLocalAmbientMessage,
  generatePetTravel,
  generateLocalProactiveMessage,
} from "@/lib/api";
import {
  createLocalId,
  readLocalChatHistory,
  readLocalPetSettings,
} from "@/lib/localPetStorage";
import {
  buildAmbientMemoryContext,
  buildDailyProactiveMemoryContext,
} from "@/lib/localPetMemoryRecall";
import {
  readLocalPetMemory,
  recordProactiveDelivery,
  writeLocalPetMemory,
} from "@/lib/localPetMemoryStorage";
import { primePetSpeechAudio } from "@/lib/petSpeechAudio";
import {
  recordMemoryContextDebug,
  recordReplyPromptDebug,
} from "@/lib/debugPanelStorage";
import { runLocalPetChatTurn } from "@/lib/localPetChatTurn";
import { playPetFeedSound, primePetFeedSound } from "@/lib/petFeedAudio";
import { applyPetVoice, type PetVoiceMode } from "@/lib/petVoice";
import {
  appendRecentAmbientReply,
  readRecentAmbientReplies,
} from "@/lib/localPetAmbientMemory";
import { splitPetReplySentences } from "@/lib/petReplySentences";
import { logBrowserPromptDebug } from "@/lib/promptDebug";
import { hapticNotification, useTelegramBackButton } from "@/lib/telegram";
import { TEST_PET_ASSET_SET, TEST_PET_DESCRIPTION } from "@/lib/testPetFixture";
import type { GenerateTravelResponse } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";

import { DebugPanel } from "./DebugPanel";
import { ConfirmActionDialog } from "./ConfirmActionDialog";
import { DraggableFoodToken, type FoodAsset } from "./pet-dashboard/DraggableFoodToken";
import { PetCharacterMessage, type PetReplyMessage } from "./pet-dashboard/PetCharacterMessage";
import { TravelStoryOverlay } from "./pet-dashboard/TravelStoryOverlay";
import { useConversationKeyboardOffset } from "./pet-dashboard/useConversationKeyboardOffset";
import { usePetPushSnapshotSync } from "./pet-dashboard/usePetPushSnapshotSync";
import {
  generatedSceneVideoUrl,
  generatedSpriteUrl,
  stageLabels,
  stateLabels,
} from "./pet-dashboard/petSprite";

type PetDashboardProps = {
  petId: string;
};

type ConversationSceneStyle = CSSProperties & {
  "--conversation-input-offset-y": string;
};

type ShowPetReplyOptions = {
  showInConversation?: boolean;
  dialogueHook?: boolean;
  voiceMode?: PetVoiceMode;
};

type ConfirmationAction = "resetPet" | "resetStats" | "openTestPet";

const confirmationCopy: Record<
  ConfirmationAction,
  { title: string; description: string; confirmLabel: string }
> = {
  resetPet: {
    title: "Создать нового питомца?",
    description: "Текущий питомец, история и локальный прогресс будут удалены.",
    confirmLabel: "Удалить и создать",
  },
  resetStats: {
    title: "Сбросить параметры?",
    description: "Голод, настроение и здоровье станут равны нулю.",
    confirmLabel: "Сбросить",
  },
  openTestPet: {
    title: "Открыть тестового питомца?",
    description: "Текущий питомец и история будут заменены тестовыми данными.",
    confirmLabel: "Заменить",
  },
};

const INITIAL_PET_REPLY_FALLBACK = "…";
const DASHBOARD_REPLY_MAX_CHARS = 300;
const UNNAMED_STATUS_NAME = "Без имени";
const ACTION_ICON_CACHE_VERSION = "20260709-figma-117-1029-3";
const VIDEO_FILTER_CACHE_VERSION = "20260709-video-filter-normal-2";
const MAIN_SCENE_BACKGROUND_CACHE_VERSION = "20260709-main-screen-bg-2";
const SCENE_VIDEO_START_OFFSET_SECONDS = 0.1;
const mainSceneBackgroundSrc = `/figma/main-screen-bg.png?v=${MAIN_SCENE_BACKGROUND_CACHE_VERSION}`;
const videoFilterSrc = `/figma/video-filter-normal.png?v=${VIDEO_FILTER_CACHE_VERSION}`;
const actionIconSrc = {
  chat: `/figma/action-chat-icon-new.svg?v=${ACTION_ICON_CACHE_VERSION}`,
  feed: `/figma/action-feed-icon-new.svg?v=${ACTION_ICON_CACHE_VERSION}`,
  travel: `/figma/action-travel-icon-new.svg?v=${ACTION_ICON_CACHE_VERSION}`,
} as const;
const statusIconSrc = {
  hunger: `/figma/status-hunger-new.svg?v=${ACTION_ICON_CACHE_VERSION}`,
  mood: `/figma/status-mood-new.svg?v=${ACTION_ICON_CACHE_VERSION}`,
  energy: `/figma/status-energy-new.svg?v=${ACTION_ICON_CACHE_VERSION}`,
} as const;
const speechBubbleSrc = `/figma/speech-bubble-new.svg?v=${ACTION_ICON_CACHE_VERSION}`;
const conversationSendIconSrc = `/figma/conversation-send-icon.svg?v=${ACTION_ICON_CACHE_VERSION}`;

function seekSceneVideoToStart(video: HTMLVideoElement) {
  const startTime = Math.min(
    SCENE_VIDEO_START_OFFSET_SECONDS,
    Math.max(0, video.duration - 0.05),
  );
  if (Math.abs(video.currentTime - startTime) > 0.01) {
    video.currentTime = startTime;
  }
}

function restartSceneVideo(video: HTMLVideoElement) {
  seekSceneVideoToStart(video);
  void video.play().catch(() => undefined);
}

const feedFoodAssets = [
  {
    id: "berry-bowl",
    label: "ягоды",
    src: "/figma/feed-food-berry-bowl.svg",
    rotation: -8,
  },
  {
    id: "moon-bun",
    label: "булочка",
    src: "/figma/feed-food-moon-bun.svg",
    rotation: 7,
  },
  {
    id: "fish-snack",
    label: "рыбка",
    src: "/figma/feed-food-fish-snack.svg",
    rotation: -4,
  },
  {
    id: "leaf-crunch",
    label: "листик",
    src: "/figma/feed-food-leaf-crunch.svg",
    rotation: 6,
  },
] satisfies FoodAsset[];

function isPointNearRect(clientX: number, clientY: number, rect: DOMRect, padding: number) {
  return (
    clientX >= rect.left - padding &&
    clientX <= rect.right + padding &&
    clientY >= rect.top - padding &&
    clientY <= rect.bottom + padding
  );
}

export function PetDashboard({ petId }: PetDashboardProps) {
  const router = useRouter();
  const localPet = useLocalPetState();
  const [isFeeding, setIsFeeding] = useState(false);
  const [isDebugPanelOpen, setIsDebugPanelOpen] = useState(false);
  const [confirmationAction, setConfirmationAction] = useState<ConfirmationAction | null>(null);
  const [isChatMode, setIsChatMode] = useState(false);
  const [isFeedMode, setIsFeedMode] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [chatError, setChatError] = useState<string | null>(null);
  const [isSendingChat, setIsSendingChat] = useState(false);
  const [isTravelGenerating, setIsTravelGenerating] = useState(false);
  const [travelResult, setTravelResult] = useState<GenerateTravelResponse | null>(null);
  const [travelError, setTravelError] = useState<string | null>(null);
  const [conversationReplyMessageId, setConversationReplyMessageId] = useState<number | null>(null);
  const [feedSuccessId, setFeedSuccessId] = useState(0);
  const [includePromptDebug] = useState(() => readLocalPetSettings().includePromptDebug);
  const [petReplyMessage, setPetReplyMessage] = useState<PetReplyMessage | null>(null);
  const proactiveAttemptedRef = useRef(false);
  const ambientRequestIdRef = useRef(0);
  const ambientReplyHistoryRef = useRef<string[]>([]);
  const petReplyMessageRef = useRef<PetReplyMessage | null>(null);
  const petReplySentenceQueueRef = useRef<string[]>([]);
  const activeDialogueHookRef = useRef<string | null>(null);
  const chatInputRef = useRef<HTMLInputElement>(null);
  const sceneRef = useRef<HTMLElement>(null);
  const feedDropTargetRef = useRef<HTMLDivElement>(null);
  const isSendingChatRef = useRef(false);
  const isTravelGeneratingRef = useRef(false);
  const pet = localPet.pet;
  const conversationInputOffsetY = useConversationKeyboardOffset(isChatMode, sceneRef);
  usePetPushSnapshotSync({
    status: localPet.status,
    pet,
    petId,
    recentAmbientRepliesRef: ambientReplyHistoryRef,
    applyStatsPatch: localPet.applyStatsPatch,
    applyLiteOverlayPatch: localPet.applyLiteOverlayPatch,
    applyStoryLibraryPatch: localPet.applyStoryLibraryPatch,
    applyRecentStoryEventsPatch: localPet.applyRecentStoryEventsPatch,
  });
  useEffect(() => {
    ambientReplyHistoryRef.current = readRecentAmbientReplies(petId);
  }, [petId]);

  const showPetReplyMessage = useCallback((
    text: string,
    playSpeechAudio: boolean,
    options: ShowPetReplyOptions = {},
  ) => {
    const voicedText = applyPetVoice(text, pet, {
      mode: options.voiceMode ?? "generated",
    });
    const sentences = splitPetReplySentences(voicedText);
    const nextMessage = {
      id: (petReplyMessageRef.current?.id ?? 0) + 1,
      text: sentences[0] ?? voicedText,
      playSpeechAudio,
    };
    petReplySentenceQueueRef.current = sentences.slice(1);
    petReplyMessageRef.current = nextMessage;
    activeDialogueHookRef.current = options.dialogueHook ? voicedText : null;
    setPetReplyMessage(nextMessage);
    if (options.showInConversation) {
      setConversationReplyMessageId(nextMessage.id);
    }
  }, [pet]);

  const advancePetReplySentence = useCallback(() => {
    const currentMessage = petReplyMessageRef.current;
    const [nextSentence, ...remainingSentences] = petReplySentenceQueueRef.current;
    if (!currentMessage || !nextSentence) {
      return false;
    }

    const nextMessage = {
      ...currentMessage,
      id: currentMessage.id + 1,
      text: nextSentence,
    };
    petReplySentenceQueueRef.current = remainingSentences;
    petReplyMessageRef.current = nextMessage;
    setPetReplyMessage(nextMessage);
    setConversationReplyMessageId((currentConversationMessageId) =>
      currentConversationMessageId === currentMessage.id
        ? nextMessage.id
        : currentConversationMessageId,
    );
    return true;
  }, []);

  const requestAmbientReply = useCallback(() => {
    if (!pet) {
      return;
    }

    const requestId = ambientRequestIdRef.current + 1;
    ambientRequestIdRef.current = requestId;
    const history = readLocalChatHistory().messages;
    const memoryContext = buildAmbientMemoryContext(readLocalPetMemory(pet.petId));

    void generateLocalAmbientMessage(pet, {
      includeDebug: includePromptDebug,
      memoryContext,
      history,
      recentAmbientReplies: ambientReplyHistoryRef.current,
      replyMaxChars: DASHBOARD_REPLY_MAX_CHARS,
    })
      .then((response) => {
        if (ambientRequestIdRef.current !== requestId) {
          return;
        }
        if (includePromptDebug) {
          logBrowserPromptDebug("dashboard ambient", response);
          recordReplyPromptDebug(response);
        }
        showPetReplyMessage(response.reply, true, {
          dialogueHook: true,
          voiceMode: "generated",
        });
        ambientReplyHistoryRef.current = appendRecentAmbientReply(pet.petId, response.reply);
        localPet.applyMoodHint(
          response.moodHint,
          response.storyLibraryPatch ?? response.debug?.storyLibraryPatch,
        );
      })
      .catch(() => {
        if (ambientRequestIdRef.current !== requestId) {
          return;
        }
        showPetReplyMessage("…", false, { voiceMode: "system" });
      });
  }, [includePromptDebug, localPet, pet, showPetReplyMessage]);

  const closeChatMode = useCallback(() => {
    setIsChatMode(false);
    setChatError(null);
    setConversationReplyMessageId(null);
    requestAmbientReply();
    chatInputRef.current?.blur();
  }, [requestAmbientReply]);

  const closeFeedMode = useCallback(() => {
    setIsFeedMode(false);
  }, []);

  const handleTelegramBack = useCallback(() => {
    if (isFeedMode) {
      closeFeedMode();
      return;
    }

    closeChatMode();
  }, [closeChatMode, closeFeedMode, isFeedMode]);

  useTelegramBackButton(handleTelegramBack, isChatMode || isFeedMode);

  useEffect(() => {
    primePetFeedSound();
  }, []);

  useEffect(() => {
    petReplyMessageRef.current = petReplyMessage;
  }, [petReplyMessage]);

  useEffect(() => {
    if (localPet.status === "loading") {
      return;
    }
    if (!pet || pet.petId !== petId) {
      router.replace("/");
    }
  }, [localPet.status, pet, petId, router]);

  function handleFeed() {
    if (!pet) {
      return;
    }

    setIsDebugPanelOpen(false);
    setTravelError(null);
    setIsFeedMode(true);
  }

  function serveFood() {
    if (!pet || isFeeding) {
      return false;
    }

    setIsFeeding(true);
    const nextPet = localPet.feed();
    if (!nextPet) {
      setIsFeeding(false);
      return false;
    }

    setFeedSuccessId((currentId) => currentId + 1);
    void playPetFeedSound();
    hapticNotification("success");
    window.setTimeout(() => setIsFeeding(false), 420);
    return true;
  }

  function handleFoodDrop(clientX: number, clientY: number) {
    if (!isFeedMode) {
      return false;
    }

    const dropTarget = feedDropTargetRef.current;
    if (!dropTarget) {
      return false;
    }

    const targetRect = dropTarget.getBoundingClientRect();
    if (!isPointNearRect(clientX, clientY, targetRect, 18)) {
      return false;
    }

    return serveFood();
  }

  async function handleTravel() {
    if (!pet || isTravelGeneratingRef.current) {
      return;
    }

    void primePetSpeechAudio();
    isTravelGeneratingRef.current = true;
    setIsTravelGenerating(true);
    setTravelError(null);
    setIsFeedMode(false);
    setIsDebugPanelOpen(false);
    showPetReplyMessage("Собираю путешествие...", false, { voiceMode: "local" });
    const travelStatusTimeoutId = window.setTimeout(() => {
      if (isTravelGeneratingRef.current) {
        showPetReplyMessage("Рисую кадры путешествия...", false, { voiceMode: "local" });
      }
    }, 2200);

    try {
      const response = await generatePetTravel(pet, { includeDebug: includePromptDebug });
      logBrowserPromptDebug("dashboard travel", response);
      setTravelResult(response);
      localPet.play();

      const firstSceneText = response.story.scenes[0]?.text.trim();
      showPetReplyMessage(firstSceneText || "Я вернулся с теплой находкой!", true, {
        voiceMode: firstSceneText ? "generated" : "local",
      });
    } catch (caught) {
      const message =
        caught instanceof ApiError ? caught.message : "Не удалось создать путешествие.";
      setTravelError(message);
      showPetReplyMessage("Путешествие не собралось. Попробуем позже.", false, {
        voiceMode: "local",
      });
      hapticNotification("error");
    } finally {
      window.clearTimeout(travelStatusTimeoutId);
      isTravelGeneratingRef.current = false;
      setIsTravelGenerating(false);
    }
  }

  function focusChatInput() {
    chatInputRef.current?.focus({ preventScroll: true });
  }

  function dismissChatKeyboard() {
    chatInputRef.current?.blur();
  }

  function handleOpenChatMode() {
    setIsFeedMode(false);
    setIsDebugPanelOpen(false);
    setChatError(null);
    setConversationReplyMessageId(null);

    flushSync(() => {
      setIsChatMode(true);
    });
    focusChatInput();
  }

  async function submitChatMessage(options: { dismissKeyboard?: boolean } = {}) {
    const message = chatInput.trim().slice(0, 1000);
    if (!message || isSendingChatRef.current || !pet) {
      if (options.dismissKeyboard) {
        dismissChatKeyboard();
      } else {
        focusChatInput();
      }
      return;
    }

    void primePetSpeechAudio();
    if (options.dismissKeyboard) {
      dismissChatKeyboard();
    }
    setChatInput("");
    setChatError(null);
    setConversationReplyMessageId(null);
    isSendingChatRef.current = true;
    setIsSendingChat(true);

    const activeDialogueHook = activeDialogueHookRef.current;
    activeDialogueHookRef.current = null;
    const dialogueHookMessage = activeDialogueHook
      ? {
          id: createLocalId("message"),
          role: "pet" as const,
          text: activeDialogueHook,
          createdAt: new Date().toISOString(),
        }
      : null;

    try {
      const { response } = await runLocalPetChatTurn({
        pet,
        message,
        includePromptDebug,
        replyMaxChars: DASHBOARD_REPLY_MAX_CHARS,
        dialogueHookMessage,
        logLabel: "dashboard quick chat",
        onLiteOverlayPatch: localPet.applyLiteOverlayPatch,
      });
      showPetReplyMessage(response.reply, true, { showInConversation: true });
      localPet.applyMoodHint(
        response.moodHint,
        response.storyLibraryPatch ?? response.debug?.storyLibraryPatch,
      );
      if (response.petPatch?.name) {
        localPet.updateName(response.petPatch.name);
      }
    } catch (caught) {
      setChatError(
        caught instanceof ApiError ? caught.message : "Не удалось отправить сообщение.",
      );
      hapticNotification("error");
    } finally {
      isSendingChatRef.current = false;
      setIsSendingChat(false);
      if (!options.dismissKeyboard) {
        window.requestAnimationFrame(() => {
          focusChatInput();
        });
      }
    }
  }

  function handleChatSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitChatMessage();
  }

  function handleSendButtonClick(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    void submitChatMessage({ dismissKeyboard: true });
  }

  function handleSceneClickCapture(event: MouseEvent<HTMLElement>) {
    if (travelResult || isDebugPanelOpen || confirmationAction) {
      return;
    }
    if (!advancePetReplySentence()) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
  }

  function handleResetPet() {
    setConfirmationAction("resetPet");
  }

  function handleResetPetStats() {
    setConfirmationAction("resetStats");
  }

  function handleOpenTestPet() {
    setConfirmationAction("openTestPet");
  }

  function confirmDashboardAction() {
    if (confirmationAction === "resetPet") {
      setIsDebugPanelOpen(false);
      localPet.reset();
      router.push("/");
    } else if (confirmationAction === "resetStats") {
      localPet.resetStats();
      hapticNotification("warning");
    } else if (confirmationAction === "openTestPet") {
      const testPet = localPet.create(TEST_PET_DESCRIPTION, TEST_PET_ASSET_SET);
      setIsDebugPanelOpen(false);
      hapticNotification("success");
      router.replace(`/pet/${testPet.petId}`);
    }
    setConfirmationAction(null);
  }

  useEffect(() => {
    if (localPet.status === "loading" || !pet || pet.petId !== petId) {
      return;
    }
    if (proactiveAttemptedRef.current) {
      return;
    }
    if (petReplyMessageRef.current) {
      proactiveAttemptedRef.current = true;
      return;
    }
    proactiveAttemptedRef.current = true;

    const memory = readLocalPetMemory(pet.petId);
    const history = readLocalChatHistory().messages;
    const proactiveMemoryContext = buildDailyProactiveMemoryContext(pet, memory, history);
    if (proactiveMemoryContext) {
      if (includePromptDebug) {
        console.log("[memory-debug] dashboard proactive candidate", proactiveMemoryContext);
      }
      if (includePromptDebug) {
        recordMemoryContextDebug(
          proactiveMemoryContext,
          "Память подставлена в proactive prompt",
        );
      }
      void generateLocalProactiveMessage(pet, proactiveMemoryContext, {
        includeDebug: includePromptDebug,
      })
        .then((response) => {
          if (petReplyMessageRef.current) {
            return;
          }
          if (includePromptDebug) {
            logBrowserPromptDebug("dashboard proactive chat", response);
            recordReplyPromptDebug(response);
          }
          const latestMemory = readLocalPetMemory(pet.petId);
          writeLocalPetMemory(
            recordProactiveDelivery(
              latestMemory,
              proactiveMemoryContext.proactiveCandidate?.memoryIds ?? [],
              response.reply,
            ),
          );
          showPetReplyMessage(response.reply, true, { dialogueHook: true });
          localPet.applyMoodHint(
            response.moodHint,
            response.storyLibraryPatch ?? response.debug?.storyLibraryPatch,
          );
        })
        .catch(() => undefined);
      return;
    }

    requestAmbientReply();
  }, [includePromptDebug, localPet, pet, petId, requestAmbientReply, showPetReplyMessage]);

  if (localPet.status === "loading") {
    return (
      <main className="tma-screen grid place-items-center bg-[var(--paper)] px-6">
        <Loader2 className="size-6 animate-spin text-[var(--ink-muted)]" aria-label="Loading" />
      </main>
    );
  }

  if (!pet || pet.petId !== petId) {
    return null;
  }

  const sceneBackgroundSrc = generatedSpriteUrl(pet, pet.stage, pet.mood) ?? mainSceneBackgroundSrc;
  const sceneVideoSrc = generatedSceneVideoUrl(pet);
  const displayedReply = petReplyMessage ?? {
    id: 0,
    text: INITIAL_PET_REPLY_FALLBACK,
    playSpeechAudio: false,
  };
  const shouldShowConversationReply =
    isChatMode && conversationReplyMessageId === displayedReply.id;
  const displayedPetName = pet.name?.trim() || UNNAMED_STATUS_NAME;
  const hungerPercent = Math.max(0, Math.min(100, pet.stats.hunger));
  const moodPercent = Math.max(0, Math.min(100, pet.stats.happiness));
  const healthPercent = Math.max(0, Math.min(100, pet.stats.energy));
  const roundedHungerPercent = Math.round(hungerPercent);
  const roundedMoodPercent = Math.round(moodPercent);
  const roundedHealthPercent = Math.round(healthPercent);
  const conversationSceneStyle: ConversationSceneStyle = {
    "--conversation-input-offset-y": `${conversationInputOffsetY}px`,
  };

  return (
    <main
      className="main-shell tma-screen overflow-clip"
      onClickCapture={handleSceneClickCapture}
    >
      {localPet.error ? (
        <div className="fixed left-5 right-5 top-[max(20px,calc(var(--tma-safe-top)+12px))] z-20 rounded-[8px] border border-[var(--danger-line)] bg-white px-4 py-3 text-sm text-[var(--danger)] sm:left-8 sm:right-auto sm:max-w-sm">
          {localPet.error}
        </div>
      ) : null}
      {travelError ? (
        <div className="fixed left-5 right-5 top-[max(20px,calc(var(--tma-safe-top)+12px))] z-40 rounded-[8px] border border-[var(--danger-line)] bg-white px-4 py-3 text-sm text-[var(--danger)] sm:left-8 sm:right-auto sm:max-w-sm">
          {travelError}
        </div>
      ) : null}

      <section
        ref={sceneRef}
        className={`main-mobile-scene tma-screen relative mx-auto w-full max-w-[402px] overflow-hidden ${
          isChatMode ? "main-mobile-scene--chat" : ""
        } ${
          isFeedMode ? "main-mobile-scene--feed" : ""
        } ${
          shouldShowConversationReply ? "main-mobile-scene--reply" : ""
        }`}
        style={conversationSceneStyle}
        aria-label="AI Tamagotchi"
      >
        <div className="conversation-appbar" aria-hidden={!isChatMode}>
          <div className="conversation-appbar__blur" aria-hidden="true" />
        </div>

        <div className="main-scene-media" aria-hidden="true">
          <img
            src={sceneBackgroundSrc}
            alt=""
            className="main-scene-background"
            draggable={false}
          />
          {sceneVideoSrc ? (
            <video
              src={sceneVideoSrc}
              poster={sceneBackgroundSrc}
              className="main-scene-background"
              autoPlay
              muted
              playsInline
              preload="auto"
              onLoadedMetadata={(event) => restartSceneVideo(event.currentTarget)}
              onEnded={(event) => restartSceneVideo(event.currentTarget)}
            />
          ) : null}
          <img
            src={videoFilterSrc}
            alt=""
            className="main-scene-filter-image"
            draggable={false}
          />
        </div>

        <div ref={feedDropTargetRef} className="feed-drop-target" aria-hidden="true" />

        <DebugPanel
          pet={pet}
          isOpen={isDebugPanelOpen}
          onClose={() => setIsDebugPanelOpen(false)}
          onResetPet={handleResetPet}
          onResetPetStats={handleResetPetStats}
          onOpenTestPet={handleOpenTestPet}
        />

        <div className="sr-only" aria-live="polite">
          Stage {stageLabels[pet.stage]}. State {stateLabels[pet.mood]}. Hunger{" "}
          {roundedHungerPercent}/100. Happiness {roundedMoodPercent}/100. Health{" "}
          {roundedHealthPercent}/100.
        </div>

        <div className="pet-status-name conversation-fade-target">
          {displayedPetName}
        </div>

        <div
          className="top-status-strip conversation-fade-target"
          aria-label={`Голод ${roundedHungerPercent} из 100, настроение ${roundedMoodPercent} из 100, здоровье ${roundedHealthPercent} из 100`}
        >
          <img src={statusIconSrc.hunger} alt="" aria-hidden="true" draggable={false} />
          <img src={statusIconSrc.mood} alt="" aria-hidden="true" draggable={false} />
          <img src={statusIconSrc.energy} alt="" aria-hidden="true" draggable={false} />
        </div>

        <div
          className={`feed-fade-target absolute left-1/2 top-[226px] z-30 -translate-x-1/2 ${
            shouldShowConversationReply
              ? "conversation-reply-bubble"
              : "conversation-fade-target"
          }`}
        >
          <div className="main-speech-bubble">
            <img
              src={speechBubbleSrc}
              alt=""
              className="main-speech-bubble__shape"
              aria-hidden="true"
              draggable={false}
            />
            <PetCharacterMessage
              message={displayedReply}
              textTranslateY={0}
              speechEndTrimMs={0}
            />
          </div>
        </div>

        <form
          onSubmit={handleChatSubmit}
          className="conversation-input-panel"
          aria-hidden={!isChatMode}
          aria-busy={isSendingChat}
        >
          <input
            ref={chatInputRef}
            value={chatInput}
            onChange={(event) => {
              setChatInput(event.currentTarget.value);
              if (chatError) {
                setChatError(null);
              }
            }}
            maxLength={1000}
            disabled={!isChatMode || isSendingChat}
            tabIndex={isChatMode ? 0 : -1}
            className="conversation-input"
            placeholder="Расскажи о себе"
            aria-label="Сообщение персонажу"
            autoComplete="off"
            autoCapitalize="sentences"
            enterKeyHint="send"
            inputMode="text"
          />

          {chatError ? (
            <p className="conversation-input-error" aria-live="polite">
              {chatError}
            </p>
          ) : null}

          <button
            type="button"
            onClick={handleSendButtonClick}
            disabled={isSendingChat}
            tabIndex={isChatMode ? 0 : -1}
            className="conversation-send-button"
          >
            {isSendingChat ? (
              <Loader2 className="size-[20px] animate-spin" aria-hidden="true" />
            ) : (
              <img
                src={conversationSendIconSrc}
                alt=""
                className="conversation-send-button__icon"
                aria-hidden="true"
                draggable={false}
              />
            )}
            <span className="sr-only">Отправить</span>
          </button>
        </form>

        <button
          type="button"
          aria-controls="debug-panel"
          aria-expanded={isDebugPanelOpen}
          aria-label={isDebugPanelOpen ? "Скрыть debug-панель" : "Показать debug-панель"}
          onClick={() => setIsDebugPanelOpen(true)}
          className="main-debug-button feed-fade-target conversation-fade-target absolute left-[327px] top-[685px] z-40"
        >
          <Bug className="main-debug-button__fallback-icon" aria-hidden="true" />
          <span className="main-debug-button__symbol" aria-hidden="true">
            􀌛
          </span>
        </button>

        {isFeedMode ? (
          <div className="feed-mode-layer" aria-label="Кормление">
            {feedSuccessId > 0 ? (
              <span key={feedSuccessId} className="feed-drop-pulse" aria-hidden="true" />
            ) : null}
            <div className="feed-food-shelf" aria-label="Еда">
              {feedFoodAssets.map((food) => (
                <DraggableFoodToken
                  key={food.id}
                  food={food}
                  disabled={isFeeding}
                  onDrop={handleFoodDrop}
                  onActivate={serveFood}
                />
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <div
        className={`main-actions-scroll conversation-fade-target absolute top-[762px] z-30 ${
          isChatMode || isFeedMode ? "conversation-fade-target--hidden" : ""
        }`}
      >
        <div className="flex w-max gap-[19px] pr-[29px]">
          <button
            type="button"
            className="main-action-button main-action-button--chat"
            onClick={handleOpenChatMode}
          >
            <img
              src={actionIconSrc.chat}
              alt=""
              aria-hidden="true"
              className="main-action-button__icon"
              draggable={false}
            />
            <span>Поболтать</span>
          </button>
          <button
            type="button"
            onClick={handleFeed}
            disabled={isFeeding}
            className="main-action-button main-action-button--feed disabled:cursor-not-allowed disabled:opacity-70"
          >
            {isFeeding ? (
              <Loader2 className="size-[24px] animate-spin" aria-hidden="true" />
            ) : (
              <img
                src={actionIconSrc.feed}
                alt=""
                aria-hidden="true"
                className="main-action-button__icon"
                draggable={false}
              />
            )}
            <span>Покормить</span>
          </button>

          <button
            type="button"
            onClick={handleTravel}
            disabled={isTravelGenerating}
            className="main-action-button main-action-button--travel disabled:cursor-not-allowed disabled:opacity-70"
          >
            {isTravelGenerating ? (
              <Loader2 className="size-[24px] animate-spin" aria-hidden="true" />
            ) : (
              <img
                src={actionIconSrc.travel}
                alt=""
                aria-hidden="true"
                className="main-action-button__icon"
                draggable={false}
              />
            )}
            <span>В путешествие</span>
          </button>
        </div>
      </div>
      {travelResult ? (
        <TravelStoryOverlay result={travelResult} onClose={() => setTravelResult(null)} />
      ) : null}
      {confirmationAction ? (
        <ConfirmActionDialog
          open
          {...confirmationCopy[confirmationAction]}
          onOpenChange={(open) => !open && setConfirmationAction(null)}
          onConfirm={confirmDashboardAction}
        />
      ) : null}
    </main>
  );
}
