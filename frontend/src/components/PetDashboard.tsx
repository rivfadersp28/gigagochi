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
import {
  splitPetReplyPortions,
  splitPetReplySentences,
} from "@/lib/petReplySentences";
import { logBrowserPromptDebug } from "@/lib/promptDebug";
import {
  canUseDerivedPetAssets,
  hapticNotification,
  useTelegramBackButton,
} from "@/lib/telegram";
import { TEST_PET_ASSET_SET, TEST_PET_DESCRIPTION } from "@/lib/testPetFixture";
import type { GenerateTravelResponse } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";

import { DebugPanel } from "./DebugPanel";
import { ConfirmActionDialog } from "./ConfirmActionDialog";
import { DraggableFoodToken, type FoodAsset } from "./pet-dashboard/DraggableFoodToken";
import type { PetReplyMessage } from "./pet-dashboard/PetCharacterMessage";
import { PetSpeechBubble } from "./pet-dashboard/PetSpeechBubble";
import {
  PET_THINKING_FRAME_SOURCES,
  PET_THINKING_MIN_VISIBLE_MS,
  PetThinkingIndicator,
} from "./pet-dashboard/PetThinkingIndicator";
import { StatProgressRing } from "./pet-dashboard/StatProgressRing";
import { TravelStoryOverlay } from "./pet-dashboard/TravelStoryOverlay";
import { useConversationKeyboardOffset } from "./pet-dashboard/useConversationKeyboardOffset";
import { usePetBackgroundAssets } from "./pet-dashboard/usePetBackgroundAssets";
import { usePetPushSnapshotSync } from "./pet-dashboard/usePetPushSnapshotSync";
import {
  generatedSceneVideoUrl,
  generatedVisualPosterUrl,
  hasGeneratedHappyAssets,
  hasGeneratedSadAssets,
  resolvedPetVisualMode,
  stageLabels,
  stateLabels,
  type PetVisualMode,
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
  maxPortions?: number;
  autoAdvanceDelayMs?: number;
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
const CHAT_REPLY_MAX_CHARS = 300;
const IDLE_REPLY_MAX_CHARS = 120;
const IDLE_REPLY_MAX_PORTIONS = 3;
const REPLY_AUTO_ADVANCE_MS = 3_000;
const UNNAMED_STATUS_NAME = "Без имени";
const ACTION_ICON_CACHE_VERSION = "20260710-figma-142-1509-1";
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

function createMinimumThinkingDelay() {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, PET_THINKING_MIN_VISIBLE_MS);
  });
}

function preloadImage(src: string) {
  return new Promise<void>((resolve, reject) => {
    const image = new Image();

    image.onload = () => {
      if (typeof image.decode !== "function") {
        resolve();
        return;
      }

      image.decode().then(resolve, reject);
    };
    image.onerror = () => reject(new Error(`Failed to load image: ${src}`));
    image.src = src;
  });
}

function preloadVideo(src: string) {
  return new Promise<void>((resolve, reject) => {
    const video = document.createElement("video");
    const cleanup = () => {
      video.onloadeddata = null;
      video.onerror = null;
    };

    video.preload = "auto";
    video.muted = true;
    video.playsInline = true;
    video.onloadeddata = () => {
      cleanup();
      resolve();
    };
    video.onerror = () => {
      cleanup();
      reject(new Error(`Failed to load video: ${src}`));
    };
    video.src = src;
    video.load();
  });
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

const dashboardStaticImageSources = [
  videoFilterSrc,
  ...Object.values(actionIconSrc),
  speechBubbleSrc,
  conversationSendIconSrc,
  ...PET_THINKING_FRAME_SOURCES,
  ...feedFoodAssets.map((food) => food.src),
];

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
  const [visualModeOverride, setVisualModeOverride] = useState<PetVisualMode | null>(null);
  const [confirmationAction, setConfirmationAction] = useState<ConfirmationAction | null>(null);
  const [isChatMode, setIsChatMode] = useState(false);
  const [isFeedMode, setIsFeedMode] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [chatError, setChatError] = useState<string | null>(null);
  const [isSendingChat, setIsSendingChat] = useState(false);
  const [isGeneratingIdleReply, setIsGeneratingIdleReply] = useState(false);
  const [isIdleSpeechBubbleDismissed, setIsIdleSpeechBubbleDismissed] = useState(false);
  const [isTravelGenerating, setIsTravelGenerating] = useState(false);
  const [travelResult, setTravelResult] = useState<GenerateTravelResponse | null>(null);
  const [travelError, setTravelError] = useState<string | null>(null);
  const [conversationReplyMessageId, setConversationReplyMessageId] = useState<number | null>(null);
  const [feedSuccessId, setFeedSuccessId] = useState(0);
  const [includePromptDebug] = useState(() => readLocalPetSettings().includePromptDebug);
  const [petReplyMessage, setPetReplyMessage] = useState<PetReplyMessage | null>(null);
  const [assetsReadyForPetId, setAssetsReadyForPetId] = useState<string | null>(null);
  const [assetLoadErrorForPetId, setAssetLoadErrorForPetId] = useState<string | null>(null);
  const [loadedSceneMedia, setLoadedSceneMedia] = useState<{
    petId: string;
    backgroundSrc: string;
    videoSrc: string | null;
  } | null>(null);
  const proactiveAttemptedRef = useRef(false);
  const ambientRequestIdRef = useRef(0);
  const idleThinkingRequestIdRef = useRef(0);
  const ambientReplyHistoryRef = useRef<string[]>([]);
  const petReplyMessageRef = useRef<PetReplyMessage | null>(null);
  const petReplySentenceQueueRef = useRef<string[]>([]);
  const replyAutoAdvanceRef = useRef<{ messageId: number; delayMs: number } | null>(null);
  const replyAutoAdvanceTimeoutRef = useRef<number | null>(null);
  const activeDialogueHookRef = useRef<string | null>(null);
  const chatInputRef = useRef<HTMLInputElement>(null);
  const sceneRef = useRef<HTMLElement>(null);
  const feedDropTargetRef = useRef<HTMLDivElement>(null);
  const isSendingChatRef = useRef(false);
  const isTravelGeneratingRef = useRef(false);
  const cancelReplyAutoAdvance = useCallback(() => {
    if (replyAutoAdvanceTimeoutRef.current !== null) {
      window.clearTimeout(replyAutoAdvanceTimeoutRef.current);
      replyAutoAdvanceTimeoutRef.current = null;
    }
    replyAutoAdvanceRef.current = null;
    petReplySentenceQueueRef.current = [];
  }, []);
  const cancelIdleReplyGeneration = useCallback(() => {
    ambientRequestIdRef.current += 1;
    idleThinkingRequestIdRef.current += 1;
    setIsGeneratingIdleReply(false);
  }, []);
  const beginIdleThinking = useCallback(() => {
    const requestId = idleThinkingRequestIdRef.current + 1;
    idleThinkingRequestIdRef.current = requestId;
    setIsGeneratingIdleReply(true);
    return {
      minimumThinkingTime: createMinimumThinkingDelay(),
      requestId,
    };
  }, []);
  const pet = localPet.pet;
  const derivedAssetsEnabled = canUseDerivedPetAssets();
  const hasSadAssets = derivedAssetsEnabled && pet ? hasGeneratedSadAssets(pet) : false;
  const hasHappyAssets = derivedAssetsEnabled && pet ? hasGeneratedHappyAssets(pet) : false;
  const visualMode = pet
    ? resolvedPetVisualMode(pet, visualModeOverride, derivedAssetsEnabled)
    : "normal";
  const requestedSceneBackgroundSrc = pet
    ? generatedVisualPosterUrl(pet, visualMode)
      ?? mainSceneBackgroundSrc
    : null;
  const requestedSceneVideoSrc = pet ? generatedSceneVideoUrl(pet, visualMode) : null;
  const sceneBackgroundSrc = loadedSceneMedia?.petId === petId
    ? loadedSceneMedia.backgroundSrc
    : requestedSceneBackgroundSrc;
  const sceneVideoSrc = loadedSceneMedia?.petId === petId
    ? loadedSceneMedia.videoSrc
    : requestedSceneVideoSrc;
  const conversationInputOffsetY = useConversationKeyboardOffset(isChatMode, sceneRef);
  usePetBackgroundAssets({
    assetSet: pet?.assetSet,
    applyGeneratedAssets: localPet.applyGeneratedAssets,
    derivedAssetsEnabled,
  });
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

  useEffect(() => {
    if (
      localPet.status !== "ready"
      || !pet
      || pet.petId !== petId
      || !sceneBackgroundSrc
      || assetsReadyForPetId === petId
    ) {
      return;
    }

    let cancelled = false;

    const fontReady = document.fonts?.ready ?? Promise.resolve();
    const imageReady = [sceneBackgroundSrc, ...dashboardStaticImageSources].map(preloadImage);
    const videoReady = sceneVideoSrc ? [preloadVideo(sceneVideoSrc)] : [];

    void Promise.all([fontReady, ...imageReady, ...videoReady])
      .then(() => {
        if (!cancelled) {
          setAssetsReadyForPetId(petId);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setAssetLoadErrorForPetId(petId);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [
    assetsReadyForPetId,
    localPet.status,
    pet,
    petId,
    sceneBackgroundSrc,
    sceneVideoSrc,
  ]);

  useEffect(() => {
    if (
      assetsReadyForPetId !== petId
      || !requestedSceneBackgroundSrc
      || (
        loadedSceneMedia?.petId === petId
        && loadedSceneMedia.backgroundSrc === requestedSceneBackgroundSrc
        && loadedSceneMedia.videoSrc === requestedSceneVideoSrc
      )
    ) {
      return;
    }

    let cancelled = false;
    const imageReady = preloadImage(requestedSceneBackgroundSrc);
    const videoReady = requestedSceneVideoSrc
      ? preloadVideo(requestedSceneVideoSrc)
      : Promise.resolve();

    void Promise.all([imageReady, videoReady]).then(() => {
      if (!cancelled) {
        setLoadedSceneMedia({
          petId,
          backgroundSrc: requestedSceneBackgroundSrc,
          videoSrc: requestedSceneVideoSrc,
        });
      }
    }).catch(() => {
      // Keep the currently rendered media if a generated variant cannot be preloaded.
    });

    return () => {
      cancelled = true;
    };
  }, [
    assetsReadyForPetId,
    loadedSceneMedia,
    petId,
    requestedSceneBackgroundSrc,
    requestedSceneVideoSrc,
  ]);

  const showPetReplyMessage = useCallback((
    text: string,
    playSpeechAudio: boolean,
    options: ShowPetReplyOptions = {},
  ) => {
    if (replyAutoAdvanceTimeoutRef.current !== null) {
      window.clearTimeout(replyAutoAdvanceTimeoutRef.current);
      replyAutoAdvanceTimeoutRef.current = null;
    }
    const voicedText = applyPetVoice(text, pet, {
      mode: options.voiceMode ?? "generated",
    });
    const sentences = options.maxPortions
      ? splitPetReplyPortions(voicedText, {
          maxPortions: options.maxPortions,
        })
      : splitPetReplySentences(voicedText);
    const remainingSentences = sentences.slice(1);
    const nextMessage = {
      id: (petReplyMessageRef.current?.id ?? 0) + 1,
      text: sentences[0] ?? voicedText,
      playSpeechAudio,
      hasNextPortion: remainingSentences.length > 0,
    };
    petReplySentenceQueueRef.current = remainingSentences;
    replyAutoAdvanceRef.current = remainingSentences.length > 0 && options.autoAdvanceDelayMs
      ? { messageId: nextMessage.id, delayMs: options.autoAdvanceDelayMs }
      : null;
    petReplyMessageRef.current = nextMessage;
    activeDialogueHookRef.current = options.dialogueHook ? voicedText : null;
    setIsIdleSpeechBubbleDismissed(false);
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
      hasNextPortion: remainingSentences.length > 0,
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

  useEffect(() => {
    const autoAdvance = replyAutoAdvanceRef.current;
    if (
      !autoAdvance
      || autoAdvance.messageId !== petReplyMessage?.id
      || petReplySentenceQueueRef.current.length === 0
    ) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      replyAutoAdvanceTimeoutRef.current = null;
      if (replyAutoAdvanceRef.current?.messageId !== autoAdvance.messageId) {
        return;
      }

      const didAdvance = advancePetReplySentence();
      const nextMessage = petReplyMessageRef.current;
      if (didAdvance && nextMessage && petReplySentenceQueueRef.current.length > 0) {
        replyAutoAdvanceRef.current = {
          messageId: nextMessage.id,
          delayMs: autoAdvance.delayMs,
        };
      } else {
        replyAutoAdvanceRef.current = null;
      }
    }, autoAdvance.delayMs);
    replyAutoAdvanceTimeoutRef.current = timeoutId;

    return () => {
      window.clearTimeout(timeoutId);
      if (replyAutoAdvanceTimeoutRef.current === timeoutId) {
        replyAutoAdvanceTimeoutRef.current = null;
      }
    };
  }, [advancePetReplySentence, petReplyMessage?.id]);

  const requestAmbientReply = useCallback(() => {
    if (!pet) {
      return;
    }

    const requestId = ambientRequestIdRef.current + 1;
    ambientRequestIdRef.current = requestId;
    const {
      minimumThinkingTime,
      requestId: idleThinkingRequestId,
    } = beginIdleThinking();
    const history = readLocalChatHistory().messages;
    const memoryContext = buildAmbientMemoryContext(readLocalPetMemory(pet.petId));

    void generateLocalAmbientMessage(pet, {
      includeDebug: includePromptDebug,
      memoryContext,
      history,
      recentAmbientReplies: ambientReplyHistoryRef.current,
      replyMaxChars: IDLE_REPLY_MAX_CHARS,
    })
      .then(async (response) => {
        await minimumThinkingTime;
        if (
          ambientRequestIdRef.current !== requestId ||
          idleThinkingRequestIdRef.current !== idleThinkingRequestId
        ) {
          return;
        }
        if (includePromptDebug) {
          logBrowserPromptDebug("dashboard ambient", response);
          recordReplyPromptDebug(response);
        }
        showPetReplyMessage(response.reply, true, {
          dialogueHook: true,
          voiceMode: "generated",
          maxPortions: IDLE_REPLY_MAX_PORTIONS,
          autoAdvanceDelayMs: REPLY_AUTO_ADVANCE_MS,
        });
        ambientReplyHistoryRef.current = appendRecentAmbientReply(pet.petId, response.reply);
        localPet.applyMoodHint(
          response.moodHint,
          response.storyLibraryPatch ?? response.debug?.storyLibraryPatch,
        );
      })
      .catch(async () => {
        await minimumThinkingTime;
        if (
          ambientRequestIdRef.current !== requestId ||
          idleThinkingRequestIdRef.current !== idleThinkingRequestId
        ) {
          return;
        }
        showPetReplyMessage("…", false, { voiceMode: "system" });
      })
      .finally(() => {
        if (
          ambientRequestIdRef.current === requestId &&
          idleThinkingRequestIdRef.current === idleThinkingRequestId
        ) {
          setIsGeneratingIdleReply(false);
        }
      });
  }, [beginIdleThinking, includePromptDebug, localPet, pet, showPetReplyMessage]);

  const closeChatMode = useCallback(() => {
    cancelReplyAutoAdvance();
    setIsChatMode(false);
    setChatError(null);
    setConversationReplyMessageId(null);
    requestAmbientReply();
    chatInputRef.current?.blur();
  }, [cancelReplyAutoAdvance, requestAmbientReply]);

  const closeFeedMode = useCallback(() => {
    setIsFeedMode(false);
  }, []);

  const handleMainScreenTap = useCallback((event: MouseEvent<HTMLElement>) => {
    if (
      isChatMode
      || isFeedMode
      || isDebugPanelOpen
      || travelResult
      || confirmationAction
    ) {
      return;
    }

    const target = event.target;
    if (target instanceof Element && target.closest("button")) {
      return;
    }

    cancelReplyAutoAdvance();
    setIsIdleSpeechBubbleDismissed(true);
  }, [
    cancelReplyAutoAdvance,
    confirmationAction,
    isChatMode,
    isDebugPanelOpen,
    isFeedMode,
    travelResult,
  ]);

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
    cancelReplyAutoAdvance();
    cancelIdleReplyGeneration();
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

    cancelReplyAutoAdvance();
    cancelIdleReplyGeneration();
    void primePetSpeechAudio();
    if (options.dismissKeyboard) {
      dismissChatKeyboard();
    }
    setChatInput("");
    setChatError(null);
    setConversationReplyMessageId(null);
    isSendingChatRef.current = true;
    setIsSendingChat(true);
    const minimumThinkingTime = createMinimumThinkingDelay();

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
        replyMaxChars: CHAT_REPLY_MAX_CHARS,
        dialogueHookMessage,
        logLabel: "dashboard quick chat",
        onLiteOverlayPatch: localPet.applyLiteOverlayPatch,
      });
      await minimumThinkingTime;
      showPetReplyMessage(response.reply, true, {
        showInConversation: true,
        autoAdvanceDelayMs: REPLY_AUTO_ADVANCE_MS,
      });
      localPet.applyMoodHint(
        response.moodHint,
        response.storyLibraryPatch ?? response.debug?.storyLibraryPatch,
        response.happinessDelta,
      );
      if (response.petPatch?.name) {
        localPet.updateName(response.petPatch.name);
      }
    } catch (caught) {
      await minimumThinkingTime;
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
    void submitChatMessage({ dismissKeyboard: true });
  }

  function handleSendButtonClick(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    void submitChatMessage({ dismissKeyboard: true });
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
    if (
      localPet.status === "loading" ||
      !pet ||
      pet.petId !== petId ||
      assetsReadyForPetId !== petId
    ) {
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
      window.queueMicrotask(() => {
        const {
          minimumThinkingTime,
          requestId: idleThinkingRequestId,
        } = beginIdleThinking();
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
          .then(async (response) => {
            await minimumThinkingTime;
            if (
              idleThinkingRequestIdRef.current !== idleThinkingRequestId ||
              petReplyMessageRef.current
            ) {
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
          .catch(async () => {
            await minimumThinkingTime;
          })
          .finally(() => {
            if (idleThinkingRequestIdRef.current === idleThinkingRequestId) {
              setIsGeneratingIdleReply(false);
            }
          });
      });
      return;
    }

    window.queueMicrotask(requestAmbientReply);
  }, [
    assetsReadyForPetId,
    beginIdleThinking,
    includePromptDebug,
    localPet,
    pet,
    petId,
    requestAmbientReply,
    showPetReplyMessage,
  ]);

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

  if (assetLoadErrorForPetId === petId) {
    return (
      <main className="tma-screen grid place-items-center bg-[var(--paper)] px-6 text-center text-sm text-[var(--ink-muted)]">
        Не удалось загрузить интерфейс. Откройте приложение ещё раз.
      </main>
    );
  }

  if (assetsReadyForPetId !== petId) {
    return (
      <main className="tma-screen grid place-items-center bg-[var(--paper)] px-6">
        <Loader2 className="size-6 animate-spin text-[var(--ink-muted)]" aria-label="Loading" />
      </main>
    );
  }

  if (!sceneBackgroundSrc) {
    return null;
  }

  const displayedReply = petReplyMessage ?? {
    id: 0,
    text: INITIAL_PET_REPLY_FALLBACK,
    playSpeechAudio: false,
    hasNextPortion: false,
  };
  const shouldShowConversationReply =
    isChatMode && conversationReplyMessageId === displayedReply.id;
  const isIdleThinkingVisible =
    !isChatMode && !isFeedMode && isGeneratingIdleReply;
  const shouldShowSpeechBubble =
    !isFeedMode
    && !isIdleThinkingVisible
    && (isChatMode ? shouldShowConversationReply : !isIdleSpeechBubbleDismissed);
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
      onClick={handleMainScreenTap}
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

        {isIdleThinkingVisible || (isChatMode && isSendingChat) ? (
          <PetThinkingIndicator />
        ) : null}

        <DebugPanel
          pet={pet}
          isOpen={isDebugPanelOpen}
          onClose={() => setIsDebugPanelOpen(false)}
          onResetPet={handleResetPet}
          onResetPetStats={handleResetPetStats}
          onOpenTestPet={handleOpenTestPet}
          canShowSadAsset={hasSadAssets}
          canShowHappyAsset={hasHappyAssets}
          visualModeOverride={visualModeOverride}
          onVisualModeOverrideChange={setVisualModeOverride}
        />

        <div className="sr-only" aria-live="polite">
          Stage {stageLabels[pet.stage]}. State {stateLabels[pet.mood]}. Visual mode{" "}
          {visualMode}. Hunger{" "}
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
          <StatProgressRing value={hungerPercent} kind="hunger" />
          <StatProgressRing value={moodPercent} kind="mood" />
          <StatProgressRing value={healthPercent} kind="energy" />
        </div>

        <div
          className={`main-speech-bubble-anchor feed-fade-target ${
            shouldShowConversationReply
              ? "conversation-reply-bubble"
              : "conversation-fade-target"
          } ${isIdleThinkingVisible ? "conversation-fade-target--hidden" : ""}`}
        >
          <PetSpeechBubble
            isVisible={shouldShowSpeechBubble}
            message={displayedReply}
            scaleOrigin="bottom"
            shapeSrc={speechBubbleSrc}
          />
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
