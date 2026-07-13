"use client";

/* eslint-disable @next/next/no-img-element */
import { useGlimm } from "glimm/react";
import { Bug, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { flushSync } from "react-dom";
import type {
  CSSProperties,
  FormEvent,
  MouseEvent,
  PointerEvent as ReactPointerEvent,
} from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  generateLocalAmbientMessage,
  generateLocalProactiveMessage,
  sendLocalChatMessage,
} from "@/lib/api";
import { presentError, type PresentedError } from "@/lib/errorPresentation";
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
import { primePetSpeechAudio, stopPetSpeechAudio } from "@/lib/petSpeechAudio";
import {
  recordMemoryContextDebug,
  recordReplyPromptDebug,
} from "@/lib/debugPanelStorage";
import { runLocalPetChatTurn } from "@/lib/localPetChatTurn";
import { foodReactionPrompt, type FoodId } from "@/lib/localPetFood";
import { playPetFeedSound, primePetFeedSound } from "@/lib/petFeedAudio";
import { applyPetVoice, type PetVoiceMode } from "@/lib/petVoice";
import {
  appendRecentAmbientReply,
  readRecentAmbientReplies,
} from "@/lib/localPetAmbientMemory";
import { claimPetIntroduction } from "@/lib/localPetIntroduction";
import {
  splitPetReplyPortions,
  splitPetReplySentences,
} from "@/lib/petReplySentences";
import { logBrowserPromptDebug } from "@/lib/promptDebug";
import {
  canUseDebugMenu,
  hapticImpact,
  hapticNotification,
  setTelegramBackgroundColor,
  useTelegramBackButton,
} from "@/lib/telegram";
import {
  APP_BACKGROUND_COLOR,
  DASHBOARD_BACKGROUND_COLOR,
} from "@/lib/theme";
import {
  refreshedTestPetAssetSet,
  TEST_PET_ASSET_SET,
  TEST_PET_DESCRIPTION,
} from "@/lib/testPetFixture";
import type { LocalPetState } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";

import { DebugPanel } from "./DebugPanel";
import { ConfirmActionDialog } from "./ConfirmActionDialog";
import { ErrorNotice } from "./ErrorNotice";
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
import { storyHistoryFromPet } from "./pet-dashboard/storyHistory";
import { useConversationKeyboardOffset } from "./pet-dashboard/useConversationKeyboardOffset";
import { usePetBackgroundAssets } from "./pet-dashboard/usePetBackgroundAssets";
import { usePetPushSnapshotSync } from "./pet-dashboard/usePetPushSnapshotSync";
import {
  generatedSceneVideoUrl,
  generatedTapReactionImageUrl,
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
  showInFeed?: boolean;
  dialogueHook?: boolean;
  voiceMode?: PetVoiceMode;
  maxPortions?: number;
  autoAdvanceDelayMs?: number;
};

type ConfirmationAction = "resetPet" | "resetStats" | "openTestPet" | "killPet";

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
  killPet: {
    title: "Убить персонажа?",
    description: "Откроется экран смерти. Персонажа можно воскресить через debug-меню.",
    confirmLabel: "Убить",
  },
};

const INITIAL_PET_REPLY_FALLBACK = "…";
const PET_DEATH_MESSAGE = "Хозяин, я умер. Жаль, что я тебе не понравился";
const CHAT_REPLY_MAX_CHARS = 300;
const IDLE_REPLY_MAX_CHARS = 120;
const IDLE_REPLY_MAX_PORTIONS = 3;
const FOOD_REPLY_MAX_CHARS = 60;
const REPLY_AUTO_ADVANCE_MS = 3_000;
const UNNAMED_STATUS_NAME = "Без имени";
const ACTION_ICON_CACHE_VERSION = "20260710-figma-142-1509-1";
const VIDEO_FILTER_CACHE_VERSION = "20260713-video-filter-lossless-webp-1";
const MAIN_SCENE_BACKGROUND_CACHE_VERSION = "20260709-main-screen-bg-2";
const TAP_REACTION_DURATION_MS = 180;
const PET_SCENE_ASPECT_RATIO = 720 / 1280;
const PET_TAP_REGION = {
  left: 120 / 720,
  top: 320 / 1280,
  right: 600 / 720,
  bottom: 1040 / 1280,
} as const;
const mainSceneBackgroundSrc = `/figma/main-screen-bg.png?v=${MAIN_SCENE_BACKGROUND_CACHE_VERSION}`;
const emptySceneBackgroundSrc = "/figma/main-scene-underlay.webp";
const videoFilterSrc = `/figma/video-filter-normal.webp?v=${VIDEO_FILTER_CACHE_VERSION}`;
const actionIconSrc = {
  chat: `/figma/action-chat-icon-new.svg?v=${ACTION_ICON_CACHE_VERSION}`,
  feed: `/figma/action-feed-icon-new.svg?v=${ACTION_ICON_CACHE_VERSION}`,
  travel: `/figma/action-travel-icon-new.svg?v=${ACTION_ICON_CACHE_VERSION}`,
} as const;
const speechBubbleSrc = `/figma/speech-bubble-new.svg?v=${ACTION_ICON_CACHE_VERSION}`;
const conversationSendIconSrc = `/figma/conversation-send-icon.svg?v=${ACTION_ICON_CACHE_VERSION}`;

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
  const { sweep } = useGlimm();
  const localPet = useLocalPetState();
  const applyGeneratedAssets = localPet.applyGeneratedAssets;
  const [isFeeding, setIsFeeding] = useState(false);
  const [isDebugPanelOpen, setIsDebugPanelOpen] = useState(false);
  const [visualModeOverride, setVisualModeOverride] = useState<PetVisualMode | null>(null);
  const [visualProvider, setVisualProvider] = useState<"openai" | "kandinsky">("openai");
  const [confirmationAction, setConfirmationAction] = useState<ConfirmationAction | null>(null);
  const [isChatMode, setIsChatMode] = useState(false);
  const [isFeedMode, setIsFeedMode] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [chatError, setChatError] = useState<PresentedError | null>(null);
  const [isSendingChat, setIsSendingChat] = useState(false);
  const [isGeneratingIdleReply, setIsGeneratingIdleReply] = useState(false);
  const [isIdleSpeechBubbleDismissed, setIsIdleSpeechBubbleDismissed] = useState(false);
  const [isStoryHistoryOpen, setIsStoryHistoryOpen] = useState(false);
  const [conversationReplyMessageId, setConversationReplyMessageId] = useState<number | null>(null);
  const [feedReplyMessageId, setFeedReplyMessageId] = useState<number | null>(null);
  const [feedError, setFeedError] = useState<PresentedError | null>(null);
  const [feedSuccessId, setFeedSuccessId] = useState(0);
  const [isTapReactionVisible, setIsTapReactionVisible] = useState(false);
  const [loadedTapReactionSrc, setLoadedTapReactionSrc] = useState<string | null>(null);
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
  const sceneVideoRef = useRef<HTMLVideoElement>(null);
  const feedDropTargetRef = useRef<HTMLDivElement>(null);
  const tapReactionTimeoutRef = useRef<number | null>(null);
  const tapReactionWasPlayingRef = useRef(false);
  const foodReactionRequestIdRef = useRef(0);
  const foodReactionAbortRef = useRef<AbortController | null>(null);
  const isSendingChatRef = useRef(false);
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
  const finishTapReaction = useCallback(() => {
    if (tapReactionTimeoutRef.current !== null) {
      window.clearTimeout(tapReactionTimeoutRef.current);
      tapReactionTimeoutRef.current = null;
    }
    setIsTapReactionVisible(false);
    if (tapReactionWasPlayingRef.current) {
      void sceneVideoRef.current?.play().catch(() => undefined);
    }
    tapReactionWasPlayingRef.current = false;
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
  useEffect(() => {
    const refreshedAssetSet = refreshedTestPetAssetSet(pet?.assetSet);
    if (!refreshedAssetSet) {
      return;
    }
    applyGeneratedAssets(refreshedAssetSet);
  }, [applyGeneratedAssets, pet?.assetSet]);
  const kandinskyAssets = pet?.assetSet?.kandinskyAssets;
  const effectiveVisualProvider = visualProvider === "kandinsky" && kandinskyAssets
    ? "kandinsky"
    : "openai";
  const visualPet = pet
    && pet.assetSet
    && effectiveVisualProvider === "kandinsky"
    && kandinskyAssets
    ? {
        ...pet,
        assetSet: {
          ...pet.assetSet,
          ...kandinskyAssets,
          videoUrl: kandinskyAssets.videoUrl,
          sadVideoUrl: undefined,
          happyVideoUrl: undefined,
          tapReactionImageUrl: undefined,
          blinkImageUrl: undefined,
        },
      }
    : pet;
  const isPetDead = Boolean(pet?.diedAt);
  const storyHistory = pet ? storyHistoryFromPet(pet) : [];
  const canShowDebugMenu = canUseDebugMenu();
  const derivedAssetsEnabled = true;
  const hasSadAssets = derivedAssetsEnabled && visualPet
    ? hasGeneratedSadAssets(visualPet)
    : false;
  const hasHappyAssets = derivedAssetsEnabled && visualPet
    ? hasGeneratedHappyAssets(visualPet)
    : false;
  const visualMode = visualPet
    ? resolvedPetVisualMode(visualPet, visualModeOverride, derivedAssetsEnabled)
    : "normal";
  const requestedSceneBackgroundSrc = visualPet
    ? isPetDead
      ? emptySceneBackgroundSrc
      : generatedVisualPosterUrl(visualPet, visualMode)
        ?? mainSceneBackgroundSrc
    : null;
  const requestedSceneVideoSrc = visualPet && !isPetDead
    ? generatedSceneVideoUrl(visualPet, visualMode)
    : null;
  const sceneBackgroundSrc = loadedSceneMedia?.petId === petId
    ? loadedSceneMedia.backgroundSrc
    : requestedSceneBackgroundSrc;
  const sceneVideoSrc = loadedSceneMedia?.petId === petId
    ? loadedSceneMedia.videoSrc
    : requestedSceneVideoSrc;
  const requestedTapReactionSrc = pet && !isPetDead && effectiveVisualProvider === "openai"
    ? generatedTapReactionImageUrl(pet)
    : null;
  const tapReactionSrc = loadedTapReactionSrc === requestedTapReactionSrc
    ? loadedTapReactionSrc
    : null;
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

  useEffect(() => finishTapReaction, [finishTapReaction]);

  useEffect(() => {
    if (!requestedTapReactionSrc) {
      return;
    }

    let cancelled = false;
    void preloadImage(requestedTapReactionSrc)
      .then(() => {
        if (!cancelled) {
          setLoadedTapReactionSrc(requestedTapReactionSrc);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLoadedTapReactionSrc(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [requestedTapReactionSrc]);

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
    const staticImageSources = isPetDead
      ? [speechBubbleSrc]
      : dashboardStaticImageSources;
    const imageReady = [sceneBackgroundSrc, ...staticImageSources].map(preloadImage);
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
    isPetDead,
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
      if (cancelled) {
        return;
      }

      const nextSceneMedia = {
        petId,
        backgroundSrc: requestedSceneBackgroundSrc,
        videoSrc: requestedSceneVideoSrc,
      };
      const isVisibleReplacement = document.visibilityState === "visible"
        && loadedSceneMedia?.petId === petId;

      if (!isVisibleReplacement) {
        setLoadedSceneMedia(nextSceneMedia);
        return;
      }

      sweep(() => {
        if (!cancelled) {
          setLoadedSceneMedia(nextSceneMedia);
        }
      });
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
    sweep,
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
    if (options.showInFeed) {
      setFeedReplyMessageId(nextMessage.id);
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
    if (!pet || pet.diedAt) {
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
    foodReactionRequestIdRef.current += 1;
    foodReactionAbortRef.current?.abort();
    foodReactionAbortRef.current = null;
    setIsFeeding(false);
    setIsFeedMode(false);
  }, []);

  const handleMainScreenTap = useCallback((event: MouseEvent<HTMLElement>) => {
    if (
      isChatMode
      || isFeedMode
      || isDebugPanelOpen
      || isStoryHistoryOpen
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
    isStoryHistoryOpen,
  ]);

  const handlePetReactionPointerDown = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    if (
      isChatMode
      || isFeedMode
      || isDebugPanelOpen
      || isStoryHistoryOpen
      || confirmationAction
      || isPetDead
      || !tapReactionSrc
      || event.pointerType === "mouse" && event.button !== 0
    ) {
      return;
    }

    const target = event.target;
    if (target instanceof Element && target.closest("button, input, textarea, a")) {
      return;
    }

    const sceneRect = sceneRef.current?.getBoundingClientRect();
    if (!sceneRect) {
      return;
    }

    const mediaWidth = sceneRect.height * PET_SCENE_ASPECT_RATIO;
    const mediaLeft = sceneRect.left + (sceneRect.width - mediaWidth) / 2;
    const normalizedX = (event.clientX - mediaLeft) / mediaWidth;
    const normalizedY = (event.clientY - sceneRect.top) / sceneRect.height;
    if (
      normalizedX < PET_TAP_REGION.left
      || normalizedX > PET_TAP_REGION.right
      || normalizedY < PET_TAP_REGION.top
      || normalizedY > PET_TAP_REGION.bottom
    ) {
      return;
    }

    if (tapReactionTimeoutRef.current === null) {
      const video = sceneVideoRef.current;
      tapReactionWasPlayingRef.current = Boolean(video && !video.paused && !video.ended);
      video?.pause();
    } else {
      window.clearTimeout(tapReactionTimeoutRef.current);
    }

    setIsTapReactionVisible(true);
    hapticImpact("light");
    tapReactionTimeoutRef.current = window.setTimeout(
      finishTapReaction,
      TAP_REACTION_DURATION_MS,
    );
  }, [
    confirmationAction,
    finishTapReaction,
    isChatMode,
    isDebugPanelOpen,
    isFeedMode,
    isPetDead,
    isStoryHistoryOpen,
    tapReactionSrc,
  ]);

  const handleTelegramBack = useCallback(() => {
    if (isStoryHistoryOpen) {
      setIsStoryHistoryOpen(false);
      return;
    }

    if (isFeedMode) {
      closeFeedMode();
      return;
    }

    closeChatMode();
  }, [closeChatMode, closeFeedMode, isFeedMode, isStoryHistoryOpen]);

  useTelegramBackButton(handleTelegramBack, isChatMode || isFeedMode || isStoryHistoryOpen);

  useEffect(() => {
    setTelegramBackgroundColor(DASHBOARD_BACKGROUND_COLOR);
    return () => setTelegramBackgroundColor(APP_BACKGROUND_COLOR);
  }, []);

  useEffect(() => {
    primePetFeedSound();
  }, []);

  useEffect(() => () => {
    foodReactionAbortRef.current?.abort();
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

    cancelReplyAutoAdvance();
    cancelIdleReplyGeneration();
    setIsDebugPanelOpen(false);
    setFeedError(null);
    setFeedReplyMessageId(null);
    setIsFeedMode(true);
  }

  async function generateFoodReaction(
    nextPet: LocalPetState,
    foodId: FoodId,
    requestId: number,
    signal: AbortSignal,
  ) {
    const minimumThinkingTime = createMinimumThinkingDelay();
    try {
      const response = await sendLocalChatMessage(
        foodReactionPrompt(foodId),
        nextPet,
        readLocalChatHistory().messages.slice(-12),
        {
          includeDebug: includePromptDebug,
          replyMaxChars: FOOD_REPLY_MAX_CHARS,
          signal,
        },
      );
      if (includePromptDebug) {
        logBrowserPromptDebug(`dashboard food reaction: ${foodId} reply`, response);
        recordReplyPromptDebug(response);
      }
      await minimumThinkingTime;
      if (foodReactionRequestIdRef.current !== requestId) {
        return;
      }
      showPetReplyMessage(response.reply, true, {
        showInFeed: true,
        voiceMode: "generated",
        maxPortions: 1,
      });
    } catch (caught) {
      if (foodReactionRequestIdRef.current !== requestId || signal.aborted) {
        return;
      }
      await minimumThinkingTime;
      if (foodReactionRequestIdRef.current !== requestId) {
        return;
      }
      setFeedError(
        presentError(caught, "Питомец поел, но не смог ответить. Попробуйте ещё раз."),
      );
      hapticNotification("error");
    } finally {
      if (foodReactionRequestIdRef.current === requestId) {
        foodReactionAbortRef.current = null;
        setIsFeeding(false);
      }
    }
  }

  function serveFood(foodId: FoodId) {
    if (!pet) {
      return false;
    }

    cancelReplyAutoAdvance();
    setFeedError(null);
    setFeedReplyMessageId(null);
    const nextPet = localPet.feed(foodId);
    if (!nextPet) {
      return false;
    }

    const requestId = foodReactionRequestIdRef.current + 1;
    const controller = new AbortController();
    foodReactionRequestIdRef.current = requestId;
    foodReactionAbortRef.current?.abort();
    foodReactionAbortRef.current = controller;
    setIsFeeding(true);
    setFeedSuccessId((currentId) => currentId + 1);
    stopPetSpeechAudio();
    void playPetFeedSound();
    void primePetSpeechAudio();
    hapticNotification("success");
    void generateFoodReaction(nextPet, foodId, requestId, controller.signal);
    return true;
  }

  function handleFoodDrop(clientX: number, clientY: number, foodId: FoodId) {
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

    return serveFood(foodId);
  }

  function handleTravel() {
    if (!pet) {
      return;
    }

    cancelReplyAutoAdvance();
    setIsIdleSpeechBubbleDismissed(true);
    setIsFeedMode(false);
    setIsDebugPanelOpen(false);
    setIsStoryHistoryOpen(true);
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
      flushSync(() => {
        localPet.applyMoodHint(
          response.moodHint,
          response.storyLibraryPatch ?? response.debug?.storyLibraryPatch,
          response.happinessDelta,
        );
      });
      showPetReplyMessage(response.reply, true, {
        showInConversation: true,
        autoAdvanceDelayMs: REPLY_AUTO_ADVANCE_MS,
      });
      if (response.petPatch?.name) {
        localPet.updateName(response.petPatch.name);
      }
    } catch (caught) {
      await minimumThinkingTime;
      setChatError(
        presentError(caught, "Не получилось отправить сообщение. Попробуйте ещё раз."),
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

  function handleKillPet() {
    setConfirmationAction("killPet");
  }

  function handleRevivePet() {
    const revivedPet = localPet.revive();
    if (!revivedPet) {
      return;
    }
    setIsDebugPanelOpen(false);
    hapticNotification("success");
  }

  function handleStartOver() {
    localPet.reset();
    router.replace("/");
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
    } else if (confirmationAction === "killPet") {
      localPet.kill();
      setIsDebugPanelOpen(false);
      hapticNotification("warning");
    }
    setConfirmationAction(null);
  }

  useEffect(() => {
    if (
      localPet.status === "loading" ||
      !pet ||
      pet.diedAt ||
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

    const introduction = claimPetIntroduction(pet);
    if (introduction) {
      window.queueMicrotask(() => {
        showPetReplyMessage(introduction, true, {
          dialogueHook: true,
          voiceMode: "generated",
          maxPortions: IDLE_REPLY_MAX_PORTIONS,
          autoAdvanceDelayMs: REPLY_AUTO_ADVANCE_MS,
        });
      });
      return;
    }

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
        <div role="status" className="grid place-items-center gap-2 text-sm text-[var(--ink-muted)]">
          <Loader2 className="size-6 animate-spin" aria-hidden="true" />
          Загружаем питомца
        </div>
      </main>
    );
  }

  if (!pet || pet.petId !== petId) {
    return null;
  }

  if (assetLoadErrorForPetId === petId) {
    return (
      <main className="tma-screen grid place-items-center bg-[var(--paper)] px-6 text-center text-sm text-[var(--ink-muted)]">
        <div className="grid max-w-xs gap-4">
          <p>Не получилось загрузить питомца</p>
          <button
            type="button"
            className="rounded-lg bg-[var(--ink)] px-4 py-3 text-[var(--paper)]"
            onClick={() => window.location.reload()}
          >
            Открыть снова
          </button>
          <button
            type="button"
            className="rounded-lg border border-[var(--ink)] px-4 py-3 text-[var(--ink)]"
            onClick={handleStartOver}
          >
            Создать нового персонажа
          </button>
        </div>
      </main>
    );
  }

  if (assetsReadyForPetId !== petId) {
    return (
      <main className="tma-screen grid place-items-center bg-[var(--paper)] px-6">
        <div role="status" className="grid place-items-center gap-2 text-sm text-[var(--ink-muted)]">
          <Loader2 className="size-6 animate-spin" aria-hidden="true" />
          Загружаем питомца
        </div>
      </main>
    );
  }

  if (!sceneBackgroundSrc) {
    return null;
  }

  const debugPanel = canShowDebugMenu ? (
    <DebugPanel
      pet={pet}
      isOpen={isDebugPanelOpen}
      isPetDead={isPetDead}
      onClose={() => setIsDebugPanelOpen(false)}
      onResetPet={handleResetPet}
      onResetPetStats={handleResetPetStats}
      onKillPet={handleKillPet}
      onRevivePet={handleRevivePet}
      onOpenTestPet={handleOpenTestPet}
      canShowSadAsset={hasSadAssets}
      canShowHappyAsset={hasHappyAssets}
      visualModeOverride={visualModeOverride}
      onVisualModeOverrideChange={setVisualModeOverride}
      visualProvider={effectiveVisualProvider}
      canShowKandinskyAssets={Boolean(kandinskyAssets)}
      onVisualProviderChange={setVisualProvider}
    />
  ) : null;
  const debugTrigger = canShowDebugMenu ? (
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
  ) : null;

  if (isPetDead) {
    return (
      <main className="main-shell tma-screen overflow-clip">
        <section
          className="main-mobile-scene pet-death-scene tma-screen relative mx-auto w-full max-w-[402px] overflow-hidden"
          aria-label="Питомец умер"
        >
          <div className="pet-death-speech-bubble-anchor">
            <PetSpeechBubble
              isVisible
              message={{
                id: 0,
                text: PET_DEATH_MESSAGE,
                playSpeechAudio: false,
                hasNextPortion: false,
              }}
              scaleOrigin="bottom"
              shapeSrc={speechBubbleSrc}
            />
          </div>
          <button
            type="button"
            className="main-action-button pet-death-restart"
            onClick={handleStartOver}
          >
            начать сначала
          </button>
          {debugPanel}
          {debugTrigger}
        </section>
      </main>
    );
  }

  const displayedReply = petReplyMessage ?? {
    id: 0,
    text: INITIAL_PET_REPLY_FALLBACK,
    playSpeechAudio: false,
    hasNextPortion: false,
  };
  const shouldShowConversationReply =
    isChatMode && conversationReplyMessageId === displayedReply.id;
  const shouldShowFeedReply =
    isFeedMode && feedReplyMessageId === displayedReply.id;
  const isIdleThinkingVisible =
    !isChatMode && !isFeedMode && isGeneratingIdleReply;
  const shouldShowSpeechBubble =
    !isIdleThinkingVisible
    && (isFeedMode
      ? shouldShowFeedReply
      : isChatMode
        ? shouldShowConversationReply
        : !isIdleSpeechBubbleDismissed);
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
      className={`main-shell tma-screen overflow-clip ${
        isStoryHistoryOpen ? "main-shell--story-open" : ""
      }`}
      onClick={handleMainScreenTap}
    >
      <img
        src={videoFilterSrc}
        alt=""
        className="main-shell-filter-image"
        draggable={false}
        aria-hidden="true"
      />
      {localPet.error ? (
        <div className="fixed left-5 right-5 top-[max(20px,calc(var(--tma-safe-top)+12px))] z-20 rounded-[8px] border border-[var(--danger-line)] bg-white px-4 py-3 text-sm text-[var(--danger)] sm:left-8 sm:right-auto sm:max-w-sm">
          {localPet.error}
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
        onPointerDown={handlePetReactionPointerDown}
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
              ref={sceneVideoRef}
              src={sceneVideoSrc}
              poster={sceneBackgroundSrc}
              className="main-scene-background"
              autoPlay
              loop
              muted
              playsInline
              preload="auto"
            />
          ) : null}
          {isTapReactionVisible ? (
            <div className="pet-tap-reaction" aria-hidden="true">
              <img
                src={tapReactionSrc ?? sceneBackgroundSrc}
                alt=""
                className="main-scene-background"
                draggable={false}
              />
            </div>
          ) : null}
          <img
            src={videoFilterSrc}
            alt=""
            className="main-scene-filter-image"
            draggable={false}
          />
        </div>

        <div ref={feedDropTargetRef} className="feed-drop-target" aria-hidden="true" />

        {isIdleThinkingVisible || (isChatMode && isSendingChat) || (isFeedMode && isFeeding) ? (
          <PetThinkingIndicator />
        ) : null}

        {debugPanel}

        <div className="sr-only" aria-live="polite">
          Stage {stageLabels[pet.stage]}. State {stateLabels[pet.mood]}. Visual mode{" "}
          {visualMode}. Hunger{" "}
          {roundedHungerPercent}/100. Happiness {roundedMoodPercent}/100. Health{" "}
          {roundedHealthPercent}/100.
        </div>

        <div className="pet-status-name">
          {displayedPetName}
        </div>

        <div
          className="top-status-strip"
          aria-label={`Голод ${roundedHungerPercent} из 100, настроение ${roundedMoodPercent} из 100, здоровье ${roundedHealthPercent} из 100`}
        >
          <StatProgressRing value={hungerPercent} kind="hunger" />
          <StatProgressRing value={moodPercent} kind="mood" />
          <StatProgressRing value={healthPercent} kind="energy" />
        </div>

        <div
          className={`main-speech-bubble-anchor ${
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
            aria-describedby={chatError ? "dashboard-chat-error" : undefined}
            aria-invalid={Boolean(chatError)}
            autoComplete="off"
            autoCapitalize="sentences"
            enterKeyHint="send"
            inputMode="text"
          />

          {chatError ? (
            <ErrorNotice
              error={chatError}
              id="dashboard-chat-error"
              className="conversation-input-error"
            />
          ) : null}

          <button
            type="button"
            onClick={handleSendButtonClick}
            disabled={isSendingChat}
            tabIndex={isChatMode ? 0 : -1}
            className="conversation-send-button"
          >
            <img
              src={conversationSendIconSrc}
              alt=""
              className="conversation-send-button__icon"
              aria-hidden="true"
              draggable={false}
            />
            <span className="sr-only">Отправить</span>
          </button>
        </form>

        {debugTrigger}

        {isFeedMode ? (
          <div className="feed-mode-layer" aria-label="Кормление" aria-busy={isFeeding}>
            {feedSuccessId > 0 ? (
              <span key={feedSuccessId} className="feed-drop-pulse" aria-hidden="true" />
            ) : null}
            {feedError ? (
              <ErrorNotice error={feedError} className="feed-mode-error" />
            ) : null}
            <div className="feed-food-shelf" aria-label="Еда">
              {feedFoodAssets.map((food) => (
                <DraggableFoodToken
                  key={food.id}
                  food={food}
                  disabled={false}
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
            className="main-action-button main-action-button--travel"
          >
            <img
              src={actionIconSrc.travel}
              alt=""
              aria-hidden="true"
              className="main-action-button__icon"
              draggable={false}
            />
            <span>В путешествие</span>
          </button>
        </div>
      </div>
      {isStoryHistoryOpen ? (
        <TravelStoryOverlay
          stories={storyHistory}
          onClose={() => setIsStoryHistoryOpen(false)}
        />
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
