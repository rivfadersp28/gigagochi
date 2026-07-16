"use client";

/* eslint-disable @next/next/no-img-element */
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
  queueOutfitGeneration,
  resumeCompletedPetGeneration,
  sendLocalChatMessage,
  simplifyOutfitRequest,
} from "@/lib/api";
import { presentError, type PresentedError } from "@/lib/errorPresentation";
import {
  createLocalId,
  readLocalChatHistory,
  readLocalPetState,
  readLocalPetSettings,
} from "@/lib/localPetStorage";
import {
  clearPendingOutfitGeneration,
  readPendingOutfitGeneration,
  setPendingOutfitJobId,
  writePendingOutfitGeneration,
  type PendingOutfitGeneration,
} from "@/lib/pendingOutfitGeneration";
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
import { playPetTapSound, primePetTapSound } from "@/lib/petTapAudio";
import { claimPetTapThanksForSession, petTapThanksReply } from "@/lib/petTapThanks";
import {
  recordMemoryContextDebug,
  recordReplyPromptDebug,
} from "@/lib/debugPanelStorage";
import {
  LocalPetTurnSupersededError,
  runLocalPetChatTurn,
} from "@/lib/localPetChatTurn";
import { foodReactionPrompt, type FoodId } from "@/lib/localPetFood";
import {
  playPetFeedSound,
  primePetFeedSound,
} from "@/lib/petFeedAudio";
import { SmoothBackgroundVideo } from "@/components/SmoothBackgroundVideo";
import { applyPetVoice, type PetVoiceMode } from "@/lib/petVoice";
import {
  appendRecentAmbientReply,
  hasShownAmbientReplyInSession,
  isLocalPetAmbientStorageKey,
  markAmbientReplyShownInSession,
  readRecentAmbientReplies,
} from "@/lib/localPetAmbientMemory";
import { withLocalPetMutationLock } from "@/lib/localPetMutationLock";
import { claimPetIntroduction } from "@/lib/localPetIntroduction";
import {
  FIRST_SESSION_COPY,
  firstSessionDashboardMessage,
  firstSessionReactionReply,
  isLocalFirstSessionActive,
  isLocalFirstSessionEnabled,
  readLocalPetFirstSession,
  restartLocalPetFirstSession,
  setLocalFirstSessionEnabled,
  updateLocalPetFirstSession,
  type LocalPetFirstSession,
} from "@/lib/localPetFirstSession";
import {
  splitPetReplyPortions,
  splitPetReplySentences,
} from "@/lib/petReplySentences";
import { logBrowserPromptDebug } from "@/lib/promptDebug";
import {
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
  clampPetExperience,
  PET_EXPERIENCE_MAX,
  PET_OUTFIT_EXPERIENCE_COST,
} from "@/lib/localPetExperience";
import { assetSetForPet } from "@/lib/petAssetSet";
import {
  refreshedTestPetAssetSet,
  TEST_PET_ASSET_SET,
  TEST_PET_DESCRIPTION,
} from "@/lib/testPetFixture";
import type { LocalPetState } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";
import { useTelegramCapabilities } from "@/lib/useTelegramCapabilities";

import { DebugPanel } from "./DebugPanel";
import { ConfirmActionDialog } from "./ConfirmActionDialog";
import { ErrorNotice } from "./ErrorNotice";
import { XpAmount } from "./XpAmount";
import { DraggableFoodToken, type FoodAsset } from "./pet-dashboard/DraggableFoodToken";
import type { PetReplyMessage } from "./pet-dashboard/PetCharacterMessage";
import { PetSpeechBubble } from "./pet-dashboard/PetSpeechBubble";
import { PetTapParticleBurst } from "./pet-dashboard/PetTapParticleBurst";
import {
  PET_THINKING_FRAME_SOURCES,
  PET_THINKING_MIN_VISIBLE_MS,
  PetThinkingIndicator,
} from "./pet-dashboard/PetThinkingIndicator";
import { StatProgressRing } from "./pet-dashboard/StatProgressRing";
import { TravelStoryOverlay } from "./pet-dashboard/TravelStoryOverlay";
import { shouldReduceMotion } from "./pet-dashboard/motion";
import { storyHistoryFromPet } from "./pet-dashboard/storyHistory";
import { usePetTapBulge } from "./pet-dashboard/usePetTapBulge";
import { useConversationKeyboardOffset } from "./pet-dashboard/useConversationKeyboardOffset";
import { usePetBackgroundAssets } from "./pet-dashboard/usePetBackgroundAssets";
import { usePetPushSnapshotSync } from "./pet-dashboard/usePetPushSnapshotSync";
import {
  generatedSceneVideoUrl,
  generatedVisualPosterUrl,
  hasGeneratedHappyAssets,
  hasGeneratedSadAssets,
  resolvedPetVisualMode,
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

type PetTapParticleBurstState = {
  id: number;
  x: number;
  y: number;
  isExiting: boolean;
};

const MAX_ACTIVE_PET_TAP_PARTICLE_BURSTS = 2;
const PET_TAP_PARTICLE_INTERVAL_MS = 80;
const PET_TAP_THANKS_VISIBLE_MS = 5_000;

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
const OUTFIT_PROMPT = "Во что мне нарядиться?";
const UNNAMED_STATUS_NAME = "Без имени";
const ACTION_ICON_CACHE_VERSION = "20260714-figma-218-1907-1";
const VIDEO_FILTER_CACHE_VERSION = "20260713-video-filter-lossless-webp-1";
const MAIN_SCENE_BACKGROUND_CACHE_VERSION = "20260709-main-screen-bg-2";
const TRAVEL_ENTRY_BACKGROUND_VIDEO =
  "/figma/travel-entry-bg.mp4?ping_pong_v=20260714-2";
const PET_SCENE_ASPECT_RATIO = 720 / 1280;
const PET_TAP_REGION = {
  left: 120 / 720,
  top: 320 / 1280,
  right: 600 / 720,
  bottom: 1040 / 1280,
} as const;
const accessibleStageLabels: Record<LocalPetState["stage"], string> = {
  baby: "малыш",
  teen: "подросток",
  adult: "взрослый",
};

function outfitQueuedReply(displayItem: string) {
  const normalizedItem = displayItem.trim();
  const [firstLetter = "", ...rest] = Array.from(normalizedItem);
  const titledItem = firstLetter
    ? `${firstLetter.toLocaleUpperCase("ru-RU")}${rest.join("")}`
    : "Наряд";
  return `${titledItem}? Интересно. Я получу заказ примерно через 10 минут.`;
}
const accessibleMoodLabels: Record<LocalPetState["mood"], string> = {
  idle: "спокойное",
  happy: "радостное",
  sad: "грустное",
  hungry: "голодное",
};
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
    const timeoutId = window.setTimeout(() => {
      image.onload = null;
      image.onerror = null;
      reject(new Error(`Timed out loading image: ${src}`));
    }, 10_000);

    const settle = (callback: () => void) => {
      window.clearTimeout(timeoutId);
      image.onload = null;
      image.onerror = null;
      callback();
    };

    image.onload = () => {
      if (typeof image.decode !== "function") {
        settle(resolve);
        return;
      }

      void image.decode().then(
        () => settle(resolve),
        // Telegram WebView may reject decode() for an already loaded image.
        // onload is sufficient here; decode() is only a rendering optimization.
        () => settle(resolve),
      );
    };
    image.onerror = () => settle(() => reject(new Error(`Failed to load image: ${src}`)));
    image.src = src;
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
  const localPet = useLocalPetState();
  const {
    canvasRef: petTapBulgeCanvasRef,
    isBulgeVisible: isPetTapBulgeVisible,
    triggerPetTapBulge,
  } = usePetTapBulge();
  const applyGeneratedAssets = localPet.applyGeneratedAssets;
  const [isFeeding, setIsFeeding] = useState(false);
  const [isDebugPanelOpen, setIsDebugPanelOpen] = useState(false);
  const [visualModeOverride, setVisualModeOverride] = useState<PetVisualMode | null>(null);
  const [visualProvider, setVisualProvider] = useState<"openai" | "kandinsky">("openai");
  const [confirmationAction, setConfirmationAction] = useState<ConfirmationAction | null>(null);
  const [isChatMode, setIsChatMode] = useState(false);
  const [isFeedMode, setIsFeedMode] = useState(false);
  const [isOutfitMode, setIsOutfitMode] = useState(false);
  const [outfitInput, setOutfitInput] = useState("");
  const [outfitError, setOutfitError] = useState<PresentedError | null>(null);
  const [isGeneratingOutfit, setIsGeneratingOutfit] = useState(false);
  const [pendingOutfit, setPendingOutfit] = useState<PendingOutfitGeneration | null>(
    () => readPendingOutfitGeneration(),
  );
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
  const [petTapParticleBursts, setPetTapParticleBursts] = useState<PetTapParticleBurstState[]>([]);
  const lastPetTapParticleBurstAtRef = useRef(Number.NEGATIVE_INFINITY);
  const [includePromptDebug] = useState(() => readLocalPetSettings().includePromptDebug);
  const [petReplyMessage, setPetReplyMessage] = useState<PetReplyMessage | null>(null);
  const [assetsReadyForPetId, setAssetsReadyForPetId] = useState<string | null>(null);
  const [assetLoadErrorForPetId, setAssetLoadErrorForPetId] = useState<string | null>(null);
  const proactiveAttemptedRef = useRef(false);
  const outfitAbortRef = useRef<AbortController | null>(null);
  const ambientRequestIdRef = useRef(0);
  const ambientSessionPetIdRef = useRef<string | null>(null);
  const ambientSessionPendingRef = useRef(false);
  const idleThinkingRequestIdRef = useRef(0);
  const ambientReplyHistoryRef = useRef<string[]>([]);
  const petReplyMessageRef = useRef<PetReplyMessage | null>(null);
  const petReplySentenceQueueRef = useRef<string[]>([]);
  const replyAutoAdvanceRef = useRef<{ messageId: number; delayMs: number } | null>(null);
  const replyAutoAdvanceTimeoutRef = useRef<number | null>(null);
  const petTapThanksProtectionRef = useRef<{
    messageId: number;
    expiresAt: number;
    timeoutId: number;
  } | null>(null);
  const activeDialogueHookRef = useRef<string | null>(null);
  const chatInputRef = useRef<HTMLInputElement>(null);
  const sceneRef = useRef<HTMLElement>(null);
  const sceneVideoRef = useRef<HTMLVideoElement>(null);
  const petTapParticleBurstIdRef = useRef(0);
  const feedDropTargetRef = useRef<HTMLDivElement>(null);
  const foodReactionRequestIdRef = useRef(0);
  const foodReactionAbortRef = useRef<AbortController | null>(null);
  const isSendingChatRef = useRef(false);
  const mountedRef = useRef(false);
  const cancelReplyAutoAdvance = useCallback(() => {
    if (replyAutoAdvanceTimeoutRef.current !== null) {
      window.clearTimeout(replyAutoAdvanceTimeoutRef.current);
      replyAutoAdvanceTimeoutRef.current = null;
    }
    replyAutoAdvanceRef.current = null;
    petReplySentenceQueueRef.current = [];
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      outfitAbortRef.current?.abort();
    };
  }, []);
  const cancelPetTapThanksProtection = useCallback(() => {
    const protection = petTapThanksProtectionRef.current;
    if (protection) {
      window.clearTimeout(protection.timeoutId);
      petTapThanksProtectionRef.current = null;
    }
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
  useEffect(() => {
    if (!pendingOutfit || pendingOutfit.petId !== pet?.petId) return;
    const controller = new AbortController();
    outfitAbortRef.current?.abort();
    outfitAbortRef.current = controller;

    void (async () => {
      try {
        const jobId = pendingOutfit.jobId ?? await queueOutfitGeneration(
          pendingOutfit.generationDescription,
          pendingOutfit.references,
          { requestKey: pendingOutfit.requestKey, signal: controller.signal },
        );
        if (!pendingOutfit.jobId) {
          setPendingOutfitJobId(pendingOutfit.requestKey, jobId);
          setPendingOutfit((current) => current?.requestKey === pendingOutfit.requestKey
            ? { ...current, jobId }
            : current);
        }
        const generated = await resumeCompletedPetGeneration(jobId, {
          signal: controller.signal,
        });
        const currentPet = readLocalPetState();
        if (currentPet?.petId !== pendingOutfit.petId) return;
        const nextAssetSet = {
          ...generated,
          characterTemplate: pendingOutfit.baseAssetSet.characterTemplate,
          characterBible: pendingOutfit.baseAssetSet.characterBible ?? generated.characterBible,
        };
        if (applyGeneratedAssets(
          nextAssetSet,
          pendingOutfit.petId,
          pendingOutfit.baseAssetSet,
          true,
        )) {
          clearPendingOutfitGeneration(pendingOutfit.requestKey);
          setPendingOutfit(null);
        }
      } catch (caught) {
        if (!controller.signal.aborted) {
          setOutfitError(presentError(
            caught,
            "Не получилось подготовить наряд. Попробуйте ещё раз.",
          ));
        }
      } finally {
        if (outfitAbortRef.current === controller) outfitAbortRef.current = null;
      }
    })();

    return () => controller.abort();
  }, [applyGeneratedAssets, pendingOutfit, pet?.petId]);
  const [firstSession, setFirstSession] = useState<LocalPetFirstSession | null | undefined>(
    undefined,
  );
  const firstSessionActive = isLocalFirstSessionActive(firstSession);
  const currentPetId = pet?.petId;
  const currentPetIntroductionPending = pet?.introductionPending;
  useEffect(() => {
    let cancelled = false;
    window.queueMicrotask(() => {
      if (cancelled) {
        return;
      }
      if (!currentPetId || currentPetId !== petId) {
        setFirstSession(null);
        return;
      }
      setFirstSession(readLocalPetFirstSession({
        petId: currentPetId,
        introductionPending: currentPetIntroductionPending,
      }));
    });
    return () => {
      cancelled = true;
    };
  }, [currentPetId, currentPetIntroductionPending, petId]);
  useEffect(() => {
    const refreshedAssetSet = refreshedTestPetAssetSet(pet?.assetSet);
    if (!refreshedAssetSet) {
      return;
    }
    applyGeneratedAssets(refreshedAssetSet, pet?.petId, pet?.assetSet);
  }, [applyGeneratedAssets, pet?.assetSet, pet?.petId]);
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
          blinkImageUrl: undefined,
        },
      }
    : pet;
  const isPetDead = Boolean(pet?.diedAt);
  const storyHistory = pet ? storyHistoryFromPet(pet) : [];
  const capabilities = useTelegramCapabilities();
  const canShowDebugMenu = capabilities.debugMenu;
  const canShowInteractiveTravel = capabilities.interactiveTravel;
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
  const sceneBackgroundSrc = requestedSceneBackgroundSrc;
  const sceneVideoSrc = requestedSceneVideoSrc;
  const conversationInputOffsetY = useConversationKeyboardOffset(
    isChatMode || isOutfitMode,
    sceneRef,
  );
  usePetBackgroundAssets({
    assetSet: pet?.assetSet,
    petId: pet?.petId,
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
    resetPet: localPet.reset,
  });
  useEffect(() => {
    ambientReplyHistoryRef.current = readRecentAmbientReplies(petId);
    const reconcileAmbientReplies = (event: StorageEvent) => {
      if (isLocalPetAmbientStorageKey(event.key, petId)) {
        ambientReplyHistoryRef.current = readRecentAmbientReplies(petId);
      }
    };
    window.addEventListener("storage", reconcileAmbientReplies);
    return () => window.removeEventListener("storage", reconcileAmbientReplies);
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

    const staticImageSources = isPetDead
      ? [speechBubbleSrc]
      : dashboardStaticImageSources;
    void Promise.allSettled(staticImageSources.map(preloadImage));

    void preloadImage(sceneBackgroundSrc)
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
  ]);

  const showPetReplyMessage = useCallback((
    text: string,
    playSpeechAudio: boolean,
    options: ShowPetReplyOptions = {},
  ) => {
    cancelPetTapThanksProtection();
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
    return nextMessage.id;
  }, [cancelPetTapThanksProtection, pet]);

  const protectPetTapThanksReply = useCallback((messageId: number) => {
    cancelPetTapThanksProtection();
    const timeoutId = window.setTimeout(() => {
      const protection = petTapThanksProtectionRef.current;
      if (!protection || protection.messageId !== messageId) {
        return;
      }
      petTapThanksProtectionRef.current = null;
      if (petReplyMessageRef.current?.id === messageId) {
        setIsIdleSpeechBubbleDismissed(true);
      }
    }, PET_TAP_THANKS_VISIBLE_MS);
    petTapThanksProtectionRef.current = {
      messageId,
      expiresAt: Date.now() + PET_TAP_THANKS_VISIBLE_MS,
      timeoutId,
    };
  }, [cancelPetTapThanksProtection]);

  useEffect(() => cancelPetTapThanksProtection, [cancelPetTapThanksProtection]);

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
    setFeedReplyMessageId((currentFeedMessageId) =>
      currentFeedMessageId === currentMessage.id
        ? nextMessage.id
        : currentFeedMessageId,
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
    if (
      !pet
      || pet.diedAt
      || firstSessionActive
      || ambientSessionPetIdRef.current === pet.petId
      || ambientSessionPendingRef.current
    ) {
      return;
    }

    if (hasShownAmbientReplyInSession(pet.petId)) {
      const previousReply = readRecentAmbientReplies(pet.petId).at(-1);
      if (previousReply) {
        showPetReplyMessage(previousReply, true, {
          dialogueHook: true,
          voiceMode: "generated",
          maxPortions: IDLE_REPLY_MAX_PORTIONS,
          autoAdvanceDelayMs: REPLY_AUTO_ADVANCE_MS,
        });
      }
      ambientSessionPetIdRef.current = pet.petId;
      return;
    }

    ambientSessionPendingRef.current = true;
    const requestId = ambientRequestIdRef.current + 1;
    ambientRequestIdRef.current = requestId;
    const {
      minimumThinkingTime,
      requestId: idleThinkingRequestId,
    } = beginIdleThinking();
    const requestedPetId = pet.petId;
    const ambientBaseline = readRecentAmbientReplies(requestedPetId);

    void withLocalPetMutationLock(
      requestedPetId,
      "conversation",
      async ({ assertOwned }) => {
        const currentPet = readLocalPetState();
        if (!currentPet || currentPet.petId !== requestedPetId) {
          return;
        }
        const latestAmbientReplies = readRecentAmbientReplies(requestedPetId);
        if (
          latestAmbientReplies.length !== ambientBaseline.length
          || latestAmbientReplies.some(
            (reply, index) => reply !== ambientBaseline[index],
          )
        ) {
          ambientReplyHistoryRef.current = latestAmbientReplies;
          return;
        }
        const history = readLocalChatHistory().messages;
        const memoryContext = buildAmbientMemoryContext(
          readLocalPetMemory(requestedPetId),
        );
        const response = await generateLocalAmbientMessage(currentPet, {
          includeDebug: includePromptDebug,
          memoryContext,
          history,
          recentAmbientReplies: readRecentAmbientReplies(requestedPetId),
          replyMaxChars: IDLE_REPLY_MAX_CHARS,
        });
        await minimumThinkingTime;
        assertOwned();
        if (
          ambientRequestIdRef.current !== requestId ||
          idleThinkingRequestIdRef.current !== idleThinkingRequestId ||
          readLocalPetState()?.petId !== requestedPetId
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
        ambientSessionPetIdRef.current = requestedPetId;
        markAmbientReplyShownInSession(requestedPetId);
        ambientReplyHistoryRef.current = appendRecentAmbientReply(requestedPetId, response.reply);
        localPet.applyMoodHint(
          response.moodHint,
          response.storyLibraryPatch ?? response.debug?.storyLibraryPatch,
          0,
          requestedPetId,
        );
      },
    )
      .catch(async () => {
        ambientSessionPendingRef.current = false;
        await minimumThinkingTime;
        if (
          ambientRequestIdRef.current !== requestId ||
          idleThinkingRequestIdRef.current !== idleThinkingRequestId ||
          readLocalPetState()?.petId !== requestedPetId
        ) {
          return;
        }
        showPetReplyMessage("…", false, { voiceMode: "system" });
      })
      .finally(() => {
        ambientSessionPendingRef.current = false;
        if (
          ambientRequestIdRef.current === requestId &&
          idleThinkingRequestIdRef.current === idleThinkingRequestId
        ) {
          setIsGeneratingIdleReply(false);
        }
      });
  }, [
    beginIdleThinking,
    firstSessionActive,
    includePromptDebug,
    localPet,
    pet,
    showPetReplyMessage,
  ]);

  const closeChatMode = useCallback(() => {
    cancelReplyAutoAdvance();
    setIsChatMode(false);
    setChatError(null);
    setConversationReplyMessageId(null);
    if (!isSendingChatRef.current) {
      requestAmbientReply();
    }
    chatInputRef.current?.blur();
  }, [cancelReplyAutoAdvance, requestAmbientReply]);

  const closeFeedMode = useCallback(() => {
    foodReactionRequestIdRef.current += 1;
    foodReactionAbortRef.current?.abort();
    foodReactionAbortRef.current = null;
    setIsFeeding(false);
    setIsFeedMode(false);
  }, []);

  const triggerPetTapParticles = useCallback((x: number, y: number) => {
    if (shouldReduceMotion()) {
      return;
    }

    const now = performance.now();
    if (now - lastPetTapParticleBurstAtRef.current < PET_TAP_PARTICLE_INTERVAL_MS) {
      return;
    }
    lastPetTapParticleBurstAtRef.current = now;

    const id = petTapParticleBurstIdRef.current + 1;
    petTapParticleBurstIdRef.current = id;
    setPetTapParticleBursts((current) => {
      const activeBursts = current.filter((burst) => !burst.isExiting);
      if (activeBursts.length < MAX_ACTIVE_PET_TAP_PARTICLE_BURSTS) {
        return [...current, { id, x, y, isExiting: false }];
      }

      const oldestActiveId = activeBursts[0]?.id;
      const retainedBursts = current
        .filter((burst) => !burst.isExiting)
        .map((burst) => (
          burst.id === oldestActiveId ? { ...burst, isExiting: true } : burst
        ));
      return [...retainedBursts, { id, x, y, isExiting: false }];
    });
  }, []);

  const removePetTapParticleBurst = useCallback((burstId: number) => {
    setPetTapParticleBursts((current) => current.filter((burst) => burst.id !== burstId));
  }, []);

  const activatePetReaction = useCallback(({
    clientX,
    clientY,
    mediaWidth,
    mediaHeight,
    normalizedX,
    normalizedY,
  }: {
    clientX: number;
    clientY: number;
    mediaWidth: number;
    mediaHeight: number;
    normalizedX: number;
    normalizedY: number;
  }) => {
    triggerPetTapBulge({
      video: sceneVideoRef.current,
      mediaWidth,
      mediaHeight,
      normalizedX,
      normalizedY,
    });
    triggerPetTapParticles(clientX, clientY);
    void playPetTapSound();
    hapticImpact("light");

    const tapResult = localPet.registerPetTap();
    if (
      tapResult?.rewarded
      && claimPetTapThanksForSession(tapResult.pet.petId)
    ) {
      const messageId = showPetReplyMessage(petTapThanksReply(), true, {
        dialogueHook: true,
        voiceMode: "local",
        maxPortions: 1,
      });
      protectPetTapThanksReply(messageId);
    }
  }, [
    localPet,
    protectPetTapThanksReply,
    showPetReplyMessage,
    triggerPetTapBulge,
    triggerPetTapParticles,
  ]);

  const handleMainScreenTap = useCallback((event: MouseEvent<HTMLElement>) => {
    if (
      isChatMode
      || isFeedMode
      || isOutfitMode
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

    const protectedReply = petTapThanksProtectionRef.current;
    if (
      protectedReply
      && protectedReply.messageId === petReplyMessageRef.current?.id
      && Date.now() < protectedReply.expiresAt
    ) {
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
    isOutfitMode,
    isStoryHistoryOpen,
  ]);

  const handlePetReactionPointerDown = useCallback((event: ReactPointerEvent<HTMLElement>) => {
    if (
      isChatMode
      || isFeedMode
      || isOutfitMode
      || isDebugPanelOpen
      || isStoryHistoryOpen
      || confirmationAction
      || isPetDead
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

    activatePetReaction({
      clientX: event.clientX,
      clientY: event.clientY,
      mediaWidth,
      mediaHeight: sceneRect.height,
      normalizedX,
      normalizedY,
    });
  }, [
    activatePetReaction,
    confirmationAction,
    isChatMode,
    isDebugPanelOpen,
    isFeedMode,
    isOutfitMode,
    isPetDead,
    isStoryHistoryOpen,
  ]);

  const handleKeyboardPetReaction = useCallback(() => {
    if (
      isChatMode
      || isFeedMode
      || isOutfitMode
      || isDebugPanelOpen
      || isStoryHistoryOpen
      || confirmationAction
      || isPetDead
    ) {
      return;
    }
    const sceneRect = sceneRef.current?.getBoundingClientRect();
    if (!sceneRect) {
      return;
    }
    const normalizedX = (PET_TAP_REGION.left + PET_TAP_REGION.right) / 2;
    const normalizedY = (PET_TAP_REGION.top + PET_TAP_REGION.bottom) / 2;
    const mediaWidth = sceneRect.height * PET_SCENE_ASPECT_RATIO;
    const mediaLeft = sceneRect.left + (sceneRect.width - mediaWidth) / 2;
    activatePetReaction({
      clientX: mediaLeft + normalizedX * mediaWidth,
      clientY: sceneRect.top + normalizedY * sceneRect.height,
      mediaWidth,
      mediaHeight: sceneRect.height,
      normalizedX,
      normalizedY,
    });
  }, [
    activatePetReaction,
    confirmationAction,
    isChatMode,
    isDebugPanelOpen,
    isFeedMode,
    isOutfitMode,
    isPetDead,
    isStoryHistoryOpen,
  ]);

  const closeOutfitMode = useCallback(() => {
    cancelReplyAutoAdvance();
    setIsOutfitMode(false);
    setOutfitError(null);
    setConversationReplyMessageId(null);
    chatInputRef.current?.blur();
  }, [cancelReplyAutoAdvance]);

  const handleTelegramBack = useCallback(() => {
    if (isStoryHistoryOpen) {
      setIsStoryHistoryOpen(false);
      return;
    }

    if (isFeedMode) {
      closeFeedMode();
      return;
    }

    if (isOutfitMode) {
      closeOutfitMode();
      return;
    }

    closeChatMode();
  }, [
    closeChatMode,
    closeOutfitMode,
    closeFeedMode,
    isFeedMode,
    isOutfitMode,
    isStoryHistoryOpen,
  ]);

  useTelegramBackButton(
    handleTelegramBack,
    isChatMode || isFeedMode || isOutfitMode || isStoryHistoryOpen,
  );

  useEffect(() => {
    setTelegramBackgroundColor(DASHBOARD_BACKGROUND_COLOR);
    return () => setTelegramBackgroundColor(APP_BACKGROUND_COLOR);
  }, []);

  useEffect(() => {
    primePetTapSound();
    primePetFeedSound();
  }, []);

  useEffect(() => {
    if (!canShowInteractiveTravel) {
      return;
    }
    const preload = document.createElement("link");
    preload.rel = "preload";
    preload.as = "video";
    preload.href = TRAVEL_ENTRY_BACKGROUND_VIDEO;
    preload.type = "video/mp4";
    document.head.appendChild(preload);
    return () => preload.remove();
  }, [canShowInteractiveTravel]);

  useEffect(() => () => {
    ambientRequestIdRef.current += 1;
    idleThinkingRequestIdRef.current += 1;
    foodReactionRequestIdRef.current += 1;
    foodReactionAbortRef.current?.abort();
    foodReactionAbortRef.current = null;
    stopPetSpeechAudio();
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
    if (
      !pet
      || (firstSessionActive
        && firstSession?.stage !== "awaiting-first-food"
        && firstSession?.stage !== "awaiting-remedy")
    ) {
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
      if (
        foodReactionRequestIdRef.current !== requestId
        || readLocalPetState()?.petId !== nextPet.petId
      ) {
        return;
      }
      showPetReplyMessage(response.reply, true, {
        showInFeed: true,
        voiceMode: "generated",
        maxPortions: 1,
      });
    } catch (caught) {
      if (
        foodReactionRequestIdRef.current !== requestId
        || signal.aborted
        || readLocalPetState()?.petId !== nextPet.petId
      ) {
        return;
      }
      await minimumThinkingTime;
      if (
        foodReactionRequestIdRef.current !== requestId
        || readLocalPetState()?.petId !== nextPet.petId
      ) {
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
    if (
      firstSession?.stage === "awaiting-first-food"
      && foodId !== "berry-bowl"
    ) {
      hapticNotification("warning");
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

    if (firstSession?.stage === "awaiting-first-food") {
      const nextFirstSession = updateLocalPetFirstSession(
        firstSession,
        "awaiting-remedy",
      );
      setFirstSession(nextFirstSession);
      showPetReplyMessage(FIRST_SESSION_COPY.afterFirstFood, true, {
        showInFeed: true,
        voiceMode: "generated",
        autoAdvanceDelayMs: REPLY_AUTO_ADVANCE_MS,
      });
      foodReactionAbortRef.current = null;
      setIsFeeding(false);
      return true;
    }

    if (
      firstSession?.stage === "awaiting-remedy"
      && foodId === "leaf-crunch"
    ) {
      const nextFirstSession = updateLocalPetFirstSession(
        firstSession,
        "awaiting-travel",
      );
      setFirstSession(nextFirstSession);
      showPetReplyMessage(FIRST_SESSION_COPY.afterRemedy, true, {
        voiceMode: "generated",
        autoAdvanceDelayMs: REPLY_AUTO_ADVANCE_MS,
      });
      foodReactionAbortRef.current = null;
      setIsFeeding(false);
      setIsFeedMode(false);
      return true;
    }

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
    if (
      !pet
      || (firstSessionActive
        && firstSession?.stage !== "awaiting-travel"
        && firstSession?.stage !== "confirming-travel")
    ) {
      return;
    }

    cancelReplyAutoAdvance();
    setIsIdleSpeechBubbleDismissed(true);
    setIsFeedMode(false);
    setIsDebugPanelOpen(false);
    router.push(`/pet/${petId}/travel`);
  }

  function handleOpenOutfitMode() {
    cancelReplyAutoAdvance();
    cancelIdleReplyGeneration();
    setIsDebugPanelOpen(false);
    setOutfitError(null);
    setConversationReplyMessageId(null);

    flushSync(() => {
      setIsOutfitMode(true);
    });
    showPetReplyMessage(OUTFIT_PROMPT, true, {
      showInConversation: true,
      voiceMode: "system",
    });
    focusChatInput();
  }

  async function submitOutfitRequest() {
    const request = outfitInput.trim();
    if (!request || !pet || isGeneratingOutfit) {
      return;
    }
    if (clampPetExperience(pet.experience) < PET_OUTFIT_EXPERIENCE_COST) {
      setOutfitError({ message: "Не хватает опыта для нового наряда." });
      hapticNotification("error");
      return;
    }
    const requestedPetId = pet.petId;
    const currentAssetSet = assetSetForPet(pet);
    const controller = new AbortController();
    outfitAbortRef.current?.abort();
    outfitAbortRef.current = controller;
    setOutfitError(null);
    setIsGeneratingOutfit(true);
    chatInputRef.current?.blur();

    try {
      const simplified = await simplifyOutfitRequest(request, pet.description, {
        signal: controller.signal,
      });
      if (!currentAssetSet) {
        throw new Error("OUTFIT_REFERENCE_ASSETS_MISSING");
      }
      const currentImages = currentAssetSet.images[pet.stage];
      const references = {
        idleImageUrl: currentImages.idle,
        sadImageUrl: currentImages.sad,
        happyImageUrl: currentImages.happy,
      };
      const pending: PendingOutfitGeneration = {
        version: 1,
        petId: requestedPetId,
        requestKey: createLocalId("outfit"),
        displayItem: simplified.displayItem,
        generationDescription: simplified.generationDescription,
        references,
        baseAssetSet: currentAssetSet,
        createdAt: Date.now(),
      };
      if (!writePendingOutfitGeneration(pending)) {
        throw new Error("OUTFIT_STORAGE_FAILED");
      }
      const jobId = await queueOutfitGeneration(
        pending.generationDescription,
        references,
        { requestKey: pending.requestKey, signal: controller.signal },
      );
      if (!localPet.spendExperience(PET_OUTFIT_EXPERIENCE_COST, requestedPetId)) {
        throw new Error("OUTFIT_EXPERIENCE_SPEND_FAILED");
      }
      setPendingOutfitJobId(pending.requestKey, jobId);
      setPendingOutfit({ ...pending, jobId });
      if (firstSession?.stage === "awaiting-completion-message") {
        setFirstSession(updateLocalPetFirstSession(firstSession, "completed"));
      }
      setOutfitInput("");
      setIsOutfitMode(false);
      setConversationReplyMessageId(null);
      chatInputRef.current?.blur();
      showPetReplyMessage(outfitQueuedReply(pending.displayItem), true, {
        voiceMode: "system",
        autoAdvanceDelayMs: REPLY_AUTO_ADVANCE_MS,
      });
      hapticNotification("success");
    } catch (caught) {
      if (controller.signal.aborted) {
        return;
      }
      setOutfitError(
        presentError(caught, "Не получилось нарядить персонажа. Попробуйте ещё раз."),
      );
      hapticNotification("error");
    } finally {
      if (outfitAbortRef.current === controller) {
        outfitAbortRef.current = null;
      }
      if (mountedRef.current) {
      setIsGeneratingOutfit(false);
      }
    }
  }

  function handleOutfitSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitOutfitRequest();
  }

  function handleOutfitButtonClick(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    void submitOutfitRequest();
  }

  function focusChatInput() {
    chatInputRef.current?.focus({ preventScroll: true });
  }

  function dismissChatKeyboard() {
    chatInputRef.current?.blur();
  }

  function handleOpenChatMode() {
    if (
      firstSessionActive
      && firstSession?.stage !== "awaiting-chat"
      && firstSession?.stage !== "awaiting-chat-followup"
    ) {
      return;
    }
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
    const requestedPetId = pet.petId;

    try {
      const { response } = await runLocalPetChatTurn({
        pet,
        message,
        includePromptDebug,
        replyMaxChars: CHAT_REPLY_MAX_CHARS,
        replyTransform: firstSession?.stage === "awaiting-chat"
          ? (reply) => firstSessionReactionReply(
              reply,
              FIRST_SESSION_COPY.afterNameFallback,
            )
          : firstSession?.stage === "awaiting-chat-followup"
            ? (reply) => firstSessionReactionReply(
                reply,
                FIRST_SESSION_COPY.afterChatFallback,
              )
            : undefined,
        dialogueHookMessage,
        logLabel: "dashboard quick chat",
        onLiteOverlayPatch: localPet.applyLiteOverlayPatch,
      });
      await minimumThinkingTime;
      if (readLocalPetState()?.petId !== requestedPetId) {
        return;
      }
      flushSync(() => {
        localPet.applyMoodHint(
          response.moodHint,
          response.storyLibraryPatch ?? response.debug?.storyLibraryPatch,
          response.happinessDelta,
          requestedPetId,
        );
      });
      const isFirstSessionInitialChat = firstSession?.stage === "awaiting-chat";
      const isFirstSessionFollowupChat = firstSession?.stage === "awaiting-chat-followup";
      if (isFirstSessionInitialChat) {
        setFirstSession(updateLocalPetFirstSession(
          firstSession,
          "awaiting-chat-followup",
        ));
      } else if (isFirstSessionFollowupChat) {
        setFirstSession(updateLocalPetFirstSession(
          firstSession,
          "awaiting-first-food",
        ));
      }
      const visibleReply = isFirstSessionInitialChat
        ? `${response.reply} ${FIRST_SESSION_COPY.afterName}`
        : isFirstSessionFollowupChat
          ? `${response.reply} ${FIRST_SESSION_COPY.afterChat}`
          : response.reply;
      showPetReplyMessage(visibleReply, true, {
        showInConversation: true,
        autoAdvanceDelayMs: REPLY_AUTO_ADVANCE_MS,
      });
      if (response.petPatch?.name) {
        localPet.updateName(response.petPatch.name, requestedPetId);
      }
    } catch (caught) {
      await minimumThinkingTime;
      const currentPet = readLocalPetState();
      if (
        caught instanceof LocalPetTurnSupersededError
        || currentPet?.petId !== requestedPetId
      ) {
        return;
      }
      if (mountedRef.current) {
        setChatInput((currentDraft) => currentDraft || message);
      }
      setChatError(
        presentError(caught, "Не получилось отправить сообщение. Попробуйте ещё раз."),
      );
      hapticNotification("error");
    } finally {
      isSendingChatRef.current = false;
      if (mountedRef.current) {
        setIsSendingChat(false);
      }
      if (mountedRef.current && !options.dismissKeyboard) {
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

  function restartFirstSessionFlow() {
    if (!pet) {
      return;
    }
    cancelReplyAutoAdvance();
    cancelIdleReplyGeneration();
    setIsChatMode(false);
    setIsFeedMode(false);
    const restarted = restartLocalPetFirstSession(pet.petId);
    setFirstSession(restarted);
    if (restarted) {
      showPetReplyMessage(firstSessionDashboardMessage(pet, restarted) ?? "", true, {
        dialogueHook: true,
        voiceMode: "generated",
        autoAdvanceDelayMs: REPLY_AUTO_ADVANCE_MS,
      });
    }
    setIsDebugPanelOpen(false);
  }

  function toggleFirstSessionFlow() {
    const enabled = !isLocalFirstSessionEnabled();
    setLocalFirstSessionEnabled(enabled);
    if (enabled) {
      restartFirstSessionFlow();
      return;
    }
    setFirstSession(null);
    setIsDebugPanelOpen(false);
    requestAmbientReply();
  }

  function handleStartOver() {
    if (localPet.reset()) {
      router.replace("/");
    }
  }

  function confirmDashboardAction() {
    if (confirmationAction === "resetPet") {
      setIsDebugPanelOpen(false);
      if (localPet.reset()) {
        router.push("/");
      }
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
      firstSession === undefined ||
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

    if (firstSessionActive && firstSession) {
      // Claim the legacy introduction marker so it cannot replay after onboarding completes.
      claimPetIntroduction(pet);
      const message = firstSessionDashboardMessage(pet, firstSession);
      if (message) {
        window.queueMicrotask(() => {
          showPetReplyMessage(message, true, {
            dialogueHook: true,
            voiceMode: "generated",
            autoAdvanceDelayMs: REPLY_AUTO_ADVANCE_MS,
          });
        });
      }
      return;
    }

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

    window.queueMicrotask(() => {
      void withLocalPetMutationLock(
        pet.petId,
        "conversation",
        async ({ assertOwned }) => {
          const currentPet = readLocalPetState();
          if (!currentPet || currentPet.petId !== pet.petId || !mountedRef.current) {
            return false;
          }
          const memory = readLocalPetMemory(pet.petId);
          const history = readLocalChatHistory().messages;
          const proactiveMemoryContext = buildDailyProactiveMemoryContext(
            currentPet,
            memory,
            history,
          );
          if (!proactiveMemoryContext) {
            return false;
          }
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
          try {
            const response = await generateLocalProactiveMessage(
              currentPet,
              proactiveMemoryContext,
              {
                includeDebug: includePromptDebug,
              },
            );
            await minimumThinkingTime;
            assertOwned();
            if (
              idleThinkingRequestIdRef.current !== idleThinkingRequestId ||
              petReplyMessageRef.current ||
              readLocalPetState()?.petId !== pet.petId
            ) {
              return true;
            }
            if (includePromptDebug) {
              logBrowserPromptDebug("dashboard proactive chat", response);
              recordReplyPromptDebug(response);
            }
            await withLocalPetMutationLock(
              pet.petId,
              "memory",
              ({ assertOwned: assertMemoryOwned }) => {
                const latestMemory = readLocalPetMemory(pet.petId);
                const delivered = recordProactiveDelivery(
                  latestMemory,
                  proactiveMemoryContext.proactiveCandidate?.memoryIds ?? [],
                  response.reply,
                );
                assertMemoryOwned();
                if (!writeLocalPetMemory(delivered, pet.petId)) {
                  throw new LocalPetTurnSupersededError();
                }
              },
              { acquireTimeoutMs: 10 * 60_000 },
            );
            assertOwned();
            if (!mountedRef.current || readLocalPetState()?.petId !== pet.petId) {
              return true;
            }
            showPetReplyMessage(response.reply, true, { dialogueHook: true });
            localPet.applyMoodHint(
              response.moodHint,
              response.storyLibraryPatch ?? response.debug?.storyLibraryPatch,
              0,
              pet.petId,
            );
            return true;
          } catch {
            await minimumThinkingTime;
            return true;
          } finally {
            if (idleThinkingRequestIdRef.current === idleThinkingRequestId) {
              setIsGeneratingIdleReply(false);
            }
          }
        },
      )
        .then((handled) => {
          if (!handled && mountedRef.current) {
            requestAmbientReply();
          }
        })
        .catch(() => undefined);
    });
  }, [
    assetsReadyForPetId,
    beginIdleThinking,
    firstSession,
    firstSessionActive,
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
      onOpenTravelDemo={() => {
        setIsDebugPanelOpen(false);
        router.push(`/pet/${pet.petId}/travel?demo=1`);
      }}
      firstSessionEnabled={isLocalFirstSessionEnabled()}
      onToggleFirstSession={toggleFirstSessionFlow}
      onRestartFirstSession={restartFirstSessionFlow}
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
      className="main-debug-button absolute left-[327px] top-[685px] z-40"
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
    (isChatMode || isOutfitMode) && conversationReplyMessageId === displayedReply.id;
  const shouldShowFeedReply =
    isFeedMode && feedReplyMessageId === displayedReply.id;
  const isIdleThinkingVisible =
    !isChatMode && !isFeedMode && !isOutfitMode && isGeneratingIdleReply;
  const shouldShowSpeechBubble =
    !isIdleThinkingVisible
    && (isFeedMode
      ? shouldShowFeedReply
      : isChatMode || isOutfitMode
        ? shouldShowConversationReply
        : !isIdleSpeechBubbleDismissed);
  const displayedPetName = pet.name?.trim() || UNNAMED_STATUS_NAME;
  const hungerPercent = Math.max(0, Math.min(100, pet.stats.hunger));
  const moodPercent = Math.max(0, Math.min(100, pet.stats.happiness));
  const healthPercent = Math.max(0, Math.min(100, pet.stats.energy));
  const experience = clampPetExperience(pet.experience);
  const roundedHungerPercent = Math.round(hungerPercent);
  const roundedMoodPercent = Math.round(moodPercent);
  const roundedHealthPercent = Math.round(healthPercent);
  const showAllMainActions = !firstSessionActive;
  const showFirstSessionChatAction = showAllMainActions
    || firstSession?.stage === "awaiting-chat"
    || firstSession?.stage === "awaiting-chat-followup";
  const showFirstSessionFeedAction = showAllMainActions
    || firstSession?.stage === "awaiting-first-food"
    || firstSession?.stage === "awaiting-remedy";
  const showFirstSessionTravelAction = showAllMainActions
    || firstSession?.stage === "awaiting-travel"
    || firstSession?.stage === "confirming-travel";
  const showFirstSessionOutfitAction = showAllMainActions
    || firstSession?.stage === "awaiting-completion-message";
  const visibleFirstSessionActionCount = firstSessionActive
    ? Number(showFirstSessionChatAction)
      + Number(showFirstSessionFeedAction)
      + Number(canShowInteractiveTravel && showFirstSessionTravelAction)
      + Number(showFirstSessionOutfitAction)
    : 0;
  const centerSingleFirstSessionAction = visibleFirstSessionActionCount === 1;
  const showExperience = !firstSession
    || firstSession.stage === "awaiting-completion-message"
    || firstSession.stage === "completed";
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
          isChatMode || isOutfitMode ? "main-mobile-scene--chat" : ""
        } ${
          isOutfitMode ? "main-mobile-scene--outfit" : ""
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
            <SmoothBackgroundVideo
              src={sceneVideoSrc}
              className="main-scene-video"
              onActiveVideoChange={(video) => {
                sceneVideoRef.current = video;
              }}
            />
          ) : null}
          <canvas
            ref={petTapBulgeCanvasRef}
            className={`main-scene-background pet-tap-bulge ${
              isPetTapBulgeVisible ? "pet-tap-bulge--visible" : ""
            }`}
            aria-hidden="true"
          />
          <img
            src={videoFilterSrc}
            alt=""
            className="main-scene-filter-image"
            draggable={false}
          />
        </div>

        {!isChatMode && !isFeedMode && !isOutfitMode ? (
          <button
            type="button"
            className="pet-reaction-button"
            onClick={handleKeyboardPetReaction}
            aria-label={`Погладить ${displayedPetName}`}
          />
        ) : null}

        {petTapParticleBursts.map((burst) => (
          <PetTapParticleBurst
            key={burst.id}
            id={burst.id}
            x={burst.x}
            y={burst.y}
            isExiting={burst.isExiting}
            onComplete={removePetTapParticleBurst}
          />
        ))}

        <div ref={feedDropTargetRef} className="feed-drop-target" aria-hidden="true" />

        {isIdleThinkingVisible || (isChatMode && isSendingChat) || (isFeedMode && isFeeding) ? (
          <PetThinkingIndicator />
        ) : null}

        {debugPanel}

        <div className="sr-only">
          Стадия: {accessibleStageLabels[pet.stage]}. Состояние: {accessibleMoodLabels[pet.mood]}.
          Голод: {roundedHungerPercent} из 100. Настроение: {roundedMoodPercent} из 100.
          Здоровье: {roundedHealthPercent} из 100.
          {showExperience ? ` Опыт: ${experience} из ${PET_EXPERIENCE_MAX}.` : ""}
        </div>

        {showExperience ? (
          <>
            {!isOutfitMode ? (
              <div className="pet-status-level">
                Уровень: Малыш
              </div>
            ) : null}

            <XpAmount
              text={`${experience}`}
              ariaLabel={`Баланс опыта: ${experience}`}
              className="pet-experience-balance"
            />
          </>
        ) : null}

        {!isOutfitMode ? (
          <div
            className="top-status-strip"
            aria-label={`Голод ${roundedHungerPercent} из 100, настроение ${roundedMoodPercent} из 100, здоровье ${roundedHealthPercent} из 100`}
          >
            <StatProgressRing value={hungerPercent} kind="hunger" />
            <StatProgressRing value={moodPercent} kind="mood" />
            <StatProgressRing value={healthPercent} kind="energy" />
          </div>
        ) : null}

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
            fixedWidth={isOutfitMode ? 272 : undefined}
            fixedHeight={isOutfitMode ? 146 : undefined}
            horizontalPadding={isOutfitMode ? 20 : undefined}
          />
        </div>

        <form
          onSubmit={isOutfitMode ? handleOutfitSubmit : handleChatSubmit}
          className={`conversation-input-panel ${
            isOutfitMode ? "conversation-input-panel--outfit" : ""
          }`}
          aria-hidden={!isChatMode && !isOutfitMode}
          aria-busy={isSendingChat || isGeneratingOutfit}
        >
          <input
            ref={chatInputRef}
            value={isOutfitMode ? outfitInput : chatInput}
            onChange={(event) => {
              if (isOutfitMode) {
                setOutfitInput(event.currentTarget.value);
                if (outfitError) {
                  setOutfitError(null);
                }
              } else {
                setChatInput(event.currentTarget.value);
                if (chatError) {
                  setChatError(null);
                }
              }
            }}
            maxLength={1000}
            disabled={
              (!isChatMode && !isOutfitMode)
              || isSendingChat
              || isGeneratingOutfit
            }
            tabIndex={isChatMode || isOutfitMode ? 0 : -1}
            className="conversation-input"
            placeholder={isOutfitMode ? "В футболку Metallica" : "Расскажи о себе"}
            aria-label={isOutfitMode ? "Описание наряда" : "Сообщение персонажу"}
            aria-describedby={
              isOutfitMode && outfitError
                ? "dashboard-outfit-error"
                : chatError
                  ? "dashboard-chat-error"
                  : undefined
            }
            aria-invalid={Boolean(isOutfitMode ? outfitError : chatError)}
            autoComplete="off"
            autoCapitalize="sentences"
            enterKeyHint="send"
            inputMode="text"
          />

          {isOutfitMode && outfitError ? (
            <ErrorNotice
              error={outfitError}
              id="dashboard-outfit-error"
              className="conversation-input-error"
            />
          ) : chatError ? (
            <ErrorNotice
              error={chatError}
              id="dashboard-chat-error"
              className="conversation-input-error"
            />
          ) : null}

          <button
            type="button"
            onClick={isOutfitMode ? handleOutfitButtonClick : handleSendButtonClick}
            disabled={
              isOutfitMode
                ? !outfitInput.trim()
                  || isGeneratingOutfit
                  || experience < PET_OUTFIT_EXPERIENCE_COST
                : isSendingChat
            }
            tabIndex={isChatMode || isOutfitMode ? 0 : -1}
            className={`conversation-send-button ${
              isOutfitMode ? "conversation-send-button--outfit" : ""
            }`}
            aria-label={
              isOutfitMode
                ? `Создать наряд за ${PET_OUTFIT_EXPERIENCE_COST} монет`
                : "Отправить"
            }
          >
            {isOutfitMode ? (
              isGeneratingOutfit ? (
                <Loader2 className="size-[20px] animate-spin" aria-hidden="true" />
              ) : (
                <XpAmount
                  text={`${PET_OUTFIT_EXPERIENCE_COST}`}
                  ariaLabel={`Стоимость: ${PET_OUTFIT_EXPERIENCE_COST} монет`}
                  className="conversation-outfit-cost"
                />
              )
            ) : (
              <img
                src={conversationSendIconSrc}
                alt=""
                className="conversation-send-button__icon"
                aria-hidden="true"
                draggable={false}
              />
            )}
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
                  disabled={
                    firstSession?.stage === "awaiting-first-food"
                      ? food.id !== "berry-bowl"
                      : false
                  }
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
          isChatMode || isFeedMode || isOutfitMode ? "conversation-fade-target--hidden" : ""
        } ${centerSingleFirstSessionAction ? "main-actions-scroll--single" : ""}`}
        aria-hidden={isChatMode || isFeedMode || isOutfitMode}
        inert={isChatMode || isFeedMode || isOutfitMode ? true : undefined}
      >
        <div className={`main-actions-row flex gap-[19px] ${
          centerSingleFirstSessionAction
            ? "w-full justify-center"
            : "w-max pr-[29px]"
        }`}>
          {showFirstSessionChatAction ? (
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
          ) : null}
          {showFirstSessionFeedAction ? (
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
          ) : null}

          {canShowInteractiveTravel && showFirstSessionTravelAction ? (
            <button
              type="button"
              onClick={handleTravel}
              className={`main-action-button main-action-button--travel ${
                firstSessionActive ? "main-action-button--onboarding" : ""
              }`}
            >
              <img
                src={actionIconSrc.travel}
                alt=""
                aria-hidden="true"
                className="main-action-button__icon"
                draggable={false}
              />
              <span>
                {firstSession?.stage === "awaiting-travel"
                  ? "Помочь летучей мыши"
                  : "В путешествие"}
              </span>
            </button>
          ) : null}
          {showFirstSessionOutfitAction ? (
            <button
              type="button"
              onClick={handleOpenOutfitMode}
              disabled={
                isGeneratingOutfit
                || pet?.assetSet?.backgroundGenerationStatus === "running"
              }
              className="main-action-button main-action-button--outfit"
            >
              <span>Нарядить</span>
            </button>
          ) : null}
          {showAllMainActions && storyHistory.length ? (
            <button
              type="button"
              onClick={() => setIsStoryHistoryOpen(true)}
              className="main-action-button"
              aria-controls="travel-story-overlay"
              aria-expanded={isStoryHistoryOpen}
            >
              <span>Истории</span>
            </button>
          ) : null}
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
