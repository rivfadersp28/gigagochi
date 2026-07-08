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
  canUseTmaApi,
  consolidateLocalUserMemory,
  generateLocalAmbientMessage,
  generatePetTravel,
  generateLocalProactiveMessage,
  registerPetPushSnapshot,
} from "@/lib/api";
import {
  appendLocalChatMessages,
  createLocalId,
  readLocalChatHistory,
  readLocalPetSettings,
  writeLocalPetSettings,
} from "@/lib/localPetStorage";
import { buildDailyProactiveMemoryContext } from "@/lib/localPetMemoryRecall";
import {
  applyMemoryConsolidationOperations,
  readLocalPetMemory,
  recordProactiveDelivery,
  shouldRunDailyConsolidation,
  writeLocalPetMemory,
} from "@/lib/localPetMemoryStorage";
import { primePetSpeechAudio } from "@/lib/petSpeechAudio";
import { playPetTapSound, primePetTapSound } from "@/lib/petTapAudio";
import {
  recordMemoryConsolidationDebug,
  recordMemoryContextDebug,
  recordReplyPromptDebug,
} from "@/lib/debugPanelStorage";
import { runLocalPetChatTurn } from "@/lib/localPetChatTurn";
import { playPetFeedSound, primePetFeedSound } from "@/lib/petFeedAudio";
import { applyPetVoice, type PetVoiceMode } from "@/lib/petVoice";
import { logBrowserPromptDebug } from "@/lib/promptDebug";
import { hapticNotification, useTelegramBackButton } from "@/lib/telegram";
import type { GenerateTravelResponse } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";

import { DebugPanel } from "./DebugPanel";
import { DraggableFoodToken, type FoodAsset } from "./pet-dashboard/DraggableFoodToken";
import {
  IdleAnimationControls,
  defaultIdleAnimationSettings,
  type IdleAnimationSettings,
} from "./pet-dashboard/IdleAnimationControls";
import { MainPetBackdrop } from "./pet-dashboard/MainPetBackdrop";
import { PetCharacterMessage, type PetReplyMessage } from "./pet-dashboard/PetCharacterMessage";
import { PetTapParticleBurst } from "./pet-dashboard/PetTapParticleBurst";
import { StatProgressRing } from "./pet-dashboard/StatProgressRing";
import { TravelStoryOverlay } from "./pet-dashboard/TravelStoryOverlay";
import { shouldReduceMotion } from "./pet-dashboard/motion";
import {
  generatedSpriteUrl,
  stageLabels,
  stateLabels,
  type PetStage,
  type PetState,
} from "./pet-dashboard/petSprite";

type PetDashboardProps = {
  petId: string;
};

type IdleStretchStyle = CSSProperties & {
  "--pet-idle-duration": string;
  "--pet-idle-max-scale": number;
};

type IdleRotationStyle = CSSProperties & {
  "--pet-idle-rotation-duration": string;
  "--pet-idle-max-rotation": string;
};

type PetTapTargetStyle = CSSProperties & {
  "--pet-tap-scale-duration": string;
  "--pet-tap-scale-easing": string;
};

type ConversationSceneStyle = CSSProperties & {
  "--conversation-visible-height": string;
};

type SpeechBubbleSize = {
  width: number;
  height: number;
};

type ShowPetReplyOptions = {
  showInConversation?: boolean;
  dialogueHook?: boolean;
  voiceMode?: PetVoiceMode;
};

type SelectedSprite = {
  stage: PetStage;
  state: PetState;
};

const IDLE_ANIMATION_BASE_DURATION_SECONDS = 2.4;
const IDLE_ROTATION_BASE_DURATION_SECONDS = 3.2;
const PET_TAP_SCALE_DURATION_MS = 500;
const PET_TAP_SCALE_EASING = "ease-in-out";
const PET_TAP_TRANSFORM_ORIGIN = "50% 50%";
const FIGMA_SCREEN_HEIGHT = 874;
const FIGMA_KEYBOARD_VISIBLE_HEIGHT = 542;
const KEYBOARD_EAGER_SYNC_MS = 750;
const INITIAL_PET_REPLY_FALLBACK = "…";
const DASHBOARD_CHAT_REPLY_MAX_CHARS = 120;
const DEFAULT_STATUS_NAME = "Челепиздрик";
const PUSH_SNAPSHOT_SYNC_MIN_INTERVAL_MS = 10 * 60_000;
const AMBIENT_MEMORY_KINDS = new Set<string>([
  "preference",
  "relationship",
  "routine",
  "boundary",
]);
const SPEECH_BUBBLE_MIN_WIDTH = 190;
const SPEECH_BUBBLE_MIN_HEIGHT = 86;
const SPEECH_BUBBLE_RADIUS = 43;
const SPEECH_BUBBLE_TAIL_HEIGHT = 21;
const SPEECH_BUBBLE_TAIL_HALF_WIDTH = 22;
const ACTION_ICON_CACHE_VERSION = "20260706-action-colors-4";
const MAIN_PET_BACKDROP_CACHE_VERSION = "20260706-main-pet-bg-1";
const mainPetBackdropSrc = `/figma/main-pet-bg.png?v=${MAIN_PET_BACKDROP_CACHE_VERSION}`;
const actionIconSrc = {
  chat: `/figma/action-chat-icon.svg?v=${ACTION_ICON_CACHE_VERSION}`,
  feed: `/figma/action-feed-icon.svg?v=${ACTION_ICON_CACHE_VERSION}`,
  travel: `/figma/action-travel-icon.svg?v=${ACTION_ICON_CACHE_VERSION}`,
} as const;

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

const mainActionButtonGlassStyle = {
  height: "58.203px",
  padding: "14px 17px 16px 13px",
  borderRadius: "24px",
  backgroundColor: "rgba(41, 41, 41, 0.4)",
  color: "#ffffff",
  boxShadow: "inset 0 2px 4px rgba(255, 255, 255, 0.09)",
  backdropFilter: "blur(20px)",
  WebkitBackdropFilter: "blur(20px)",
  lineHeight: "normal",
} satisfies CSSProperties;

const mainActionButtonStyle = {
  chat: { ...mainActionButtonGlassStyle, width: 192 },
  feed: { ...mainActionButtonGlassStyle, width: 198 },
  travel: { ...mainActionButtonGlassStyle, width: 241 },
} satisfies Record<"chat" | "feed" | "travel", CSSProperties>;

const mainScreenStyle = {
  backgroundColor: "#000000",
} satisfies CSSProperties;

const speechBubbleGlassStyle = {
  backgroundColor: "rgba(41, 41, 41, 0.4)",
  boxShadow: "inset 0 2px 4px rgba(255, 255, 255, 0.09)",
  backdropFilter: "blur(20px)",
  WebkitBackdropFilter: "blur(20px)",
} satisfies CSSProperties;

function buildPushSnapshotMemoryContext(petId: string) {
  const memory = readLocalPetMemory(petId);
  const now = Date.now();
  const relevantMemories = memory.memories
    .filter((item) => !item.expiresAt || Date.parse(item.expiresAt) >= now)
    .sort((left, right) => {
      const importanceDelta = right.importance - left.importance;
      if (importanceDelta !== 0) {
        return importanceDelta;
      }
      return Date.parse(right.updatedAt) - Date.parse(left.updatedAt);
    })
    .slice(0, 5)
    .map((item) => ({
      id: item.id,
      kind: item.kind,
      text: item.text,
      dueAt: item.dueAt,
    }));

  return {
    summary: memory.summary,
    userProfile: memory.userProfile,
    relevantMemories,
  };
}

function buildSpeechBubbleClipPath({ width, height }: SpeechBubbleSize) {
  const w = Math.max(SPEECH_BUBBLE_MIN_WIDTH, width);
  const h = Math.max(SPEECH_BUBBLE_MIN_HEIGHT, height);
  const r = Math.min(SPEECH_BUBBLE_RADIUS, w / 2, h / 2);
  const cx = w / 2;
  const tailBottom = h + SPEECH_BUBBLE_TAIL_HEIGHT;
  const tailShoulder = SPEECH_BUBBLE_TAIL_HALF_WIDTH;
  const tailNeck = 11;
  const tailPoint = 4;

  return [
    `M ${r} 0`,
    `H ${w - r}`,
    `C ${w - r / 2} 0 ${w} ${r / 2} ${w} ${r}`,
    `V ${h - r}`,
    `C ${w} ${h - r / 2} ${w - r / 2} ${h} ${w - r} ${h}`,
    `H ${cx + tailShoulder}`,
    `C ${cx + tailNeck} ${h} ${cx + 8} ${h + 7} ${cx + 5} ${h + 14}`,
    `C ${cx + tailPoint} ${h + 17} ${cx + 2} ${tailBottom} ${cx} ${tailBottom}`,
    `C ${cx - 2} ${tailBottom} ${cx - tailPoint} ${h + 17} ${cx - 5} ${h + 14}`,
    `C ${cx - 8} ${h + 7} ${cx - tailNeck} ${h} ${cx - tailShoulder} ${h}`,
    `H ${r}`,
    `C ${r / 2} ${h} 0 ${h - r / 2} 0 ${h - r}`,
    `V ${r}`,
    `C 0 ${r / 2} ${r / 2} 0 ${r} 0`,
    "Z",
  ].join(" ");
}

function isPointNearRect(clientX: number, clientY: number, rect: DOMRect, padding: number) {
  return (
    clientX >= rect.left - padding &&
    clientX <= rect.right + padding &&
    clientY >= rect.top - padding &&
    clientY <= rect.bottom + padding
  );
}

function estimatedKeyboardVisibleHeight(fullHeight: number) {
  const ratio = FIGMA_KEYBOARD_VISIBLE_HEIGHT / FIGMA_SCREEN_HEIGHT;
  return Math.max(320, Math.min(fullHeight, Math.round(fullHeight * ratio)));
}

function shouldEagerlyUseKeyboardLayout() {
  return (
    window.innerWidth <= 480 ||
    navigator.maxTouchPoints > 0 ||
    window.matchMedia("(pointer: coarse)").matches
  );
}

export function PetDashboard({ petId }: PetDashboardProps) {
  const router = useRouter();
  const localPet = useLocalPetState();
  const [isFeeding, setIsFeeding] = useState(false);
  const [isIdleControlsOpen, setIsIdleControlsOpen] = useState(false);
  const [isDebugPanelOpen, setIsDebugPanelOpen] = useState(false);
  const [isChatMode, setIsChatMode] = useState(false);
  const [isFeedMode, setIsFeedMode] = useState(false);
  const [isKeyboardRaised, setIsKeyboardRaised] = useState(false);
  const [conversationVisibleHeight, setConversationVisibleHeight] = useState(874);
  const [chatInput, setChatInput] = useState("");
  const [chatError, setChatError] = useState<string | null>(null);
  const [isSendingChat, setIsSendingChat] = useState(false);
  const [isTravelGenerating, setIsTravelGenerating] = useState(false);
  const [travelResult, setTravelResult] = useState<GenerateTravelResponse | null>(null);
  const [travelError, setTravelError] = useState<string | null>(null);
  const [conversationReplyMessageId, setConversationReplyMessageId] = useState<number | null>(null);
  const [feedSuccessId, setFeedSuccessId] = useState(0);
  const [petTapParticleBursts, setPetTapParticleBursts] = useState<number[]>([]);
  const [petTapPulseId, setPetTapPulseId] = useState(0);
  const [selectedSprite, setSelectedSprite] = useState<SelectedSprite | null>(null);
  const [idleAnimationSettings, setIdleAnimationSettings] = useState<IdleAnimationSettings>(
    defaultIdleAnimationSettings,
  );
  const [promptSettings, setPromptSettings] = useState(() => readLocalPetSettings());
  const [petReplyMessage, setPetReplyMessage] = useState<PetReplyMessage | null>(null);
  const [speechBubbleSize, setSpeechBubbleSize] = useState<SpeechBubbleSize>({
    width: SPEECH_BUBBLE_MIN_WIDTH,
    height: SPEECH_BUBBLE_MIN_HEIGHT,
  });
  const proactiveAttemptedRef = useRef(false);
  const ambientRequestIdRef = useRef(0);
  const ambientReplyHistoryRef = useRef<string[]>([]);
  const pushSnapshotSyncRef = useRef<{
    petId: string;
    syncedAt: number;
    updatedAt: string;
  } | null>(null);
  const petReplyMessageRef = useRef<PetReplyMessage | null>(null);
  const activeDialogueHookRef = useRef<string | null>(null);
  const speechBubbleRef = useRef<HTMLDivElement>(null);
  const chatInputRef = useRef<HTMLInputElement>(null);
  const petTapTargetRef = useRef<HTMLButtonElement>(null);
  const feedDropTargetRef = useRef<HTMLDivElement>(null);
  const petTapParticleBurstIdRef = useRef(0);
  const isSendingChatRef = useRef(false);
  const isTravelGeneratingRef = useRef(false);
  const conversationFullHeightRef = useRef(0);
  const keyboardSyncUntilRef = useRef(0);
  const pet = localPet.pet;
  const includePromptDebug = promptSettings.includePromptDebug;

  useEffect(() => {
    ambientReplyHistoryRef.current = [];
  }, [petId]);

  const showPetReplyMessage = useCallback((
    text: string,
    playSpeechAudio: boolean,
    options: ShowPetReplyOptions = {},
  ) => {
    const voicedText = applyPetVoice(text, pet, {
      mode: options.voiceMode ?? "generated",
    });
    const nextMessage = {
      id: (petReplyMessageRef.current?.id ?? 0) + 1,
      text: voicedText,
      playSpeechAudio,
    };
    petReplyMessageRef.current = nextMessage;
    activeDialogueHookRef.current = options.dialogueHook ? voicedText : null;
    setPetReplyMessage(nextMessage);
    if (options.showInConversation) {
      setConversationReplyMessageId(nextMessage.id);
    }
  }, [pet]);

  const requestAmbientReply = useCallback(() => {
    if (!pet) {
      return;
    }

    const requestId = ambientRequestIdRef.current + 1;
    ambientRequestIdRef.current = requestId;
    const memory = readLocalPetMemory(pet.petId);
    const ambientMemories = memory.memories
      .filter((item) => AMBIENT_MEMORY_KINDS.has(item.kind) && !item.dueAt)
      .slice(0, 3);
    const memoryContext = {
      userProfile: memory.userProfile,
      relevantMemories: ambientMemories.map((item) => ({
        id: item.id,
        kind: item.kind,
        text: item.text,
        dueAt: item.dueAt,
      })),
    };
    const history = readLocalChatHistory().messages;

    void generateLocalAmbientMessage(pet, {
      includeDebug: includePromptDebug,
      memoryContext,
      history,
      recentAmbientReplies: ambientReplyHistoryRef.current,
      replyMaxChars: 160,
    })
      .then((response) => {
        if (ambientRequestIdRef.current !== requestId) {
          return;
        }
        logBrowserPromptDebug("dashboard ambient", response);
        recordReplyPromptDebug(response);
        showPetReplyMessage(response.reply, true, {
          dialogueHook: true,
          voiceMode: "generated",
        });
        ambientReplyHistoryRef.current = [
          ...ambientReplyHistoryRef.current,
          response.reply,
        ].slice(-5);
        localPet.applyMoodHint(
          response.moodHint,
          response.debug?.liteOverlayPatch,
          response.debug?.storyLibraryPatch,
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
    keyboardSyncUntilRef.current = 0;
    setIsChatMode(false);
    setIsKeyboardRaised(false);
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

  const triggerPetTapVisualFeedback = useCallback(() => {
    if (shouldReduceMotion()) {
      return;
    }

    const nextBurstId = petTapParticleBurstIdRef.current + 1;
    petTapParticleBurstIdRef.current = nextBurstId;
    setPetTapParticleBursts((currentBursts) => [...currentBursts, nextBurstId]);
    setPetTapPulseId((currentId) => currentId + 1);
  }, []);

  const handlePetTap = useCallback(() => {
    void playPetTapSound();
    triggerPetTapVisualFeedback();
  }, [triggerPetTapVisualFeedback]);

  const removePetTapParticleBurst = useCallback((burstId: number) => {
    setPetTapParticleBursts((currentBursts) =>
      currentBursts.filter((currentBurstId) => currentBurstId !== burstId),
    );
  }, []);

  useEffect(() => {
    primePetTapSound();
    primePetFeedSound();
  }, []);

  useEffect(() => {
    const node = speechBubbleRef.current;
    if (!node) {
      return undefined;
    }

    const updateSpeechBubbleSize = () => {
      const rect = node.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        return;
      }

      const nextSize = {
        width: Math.round(rect.width * 2) / 2,
        height: Math.round(rect.height * 2) / 2,
      };

      setSpeechBubbleSize((currentSize) => {
        if (
          Math.abs(currentSize.width - nextSize.width) < 0.5 &&
          Math.abs(currentSize.height - nextSize.height) < 0.5
        ) {
          return currentSize;
        }

        return nextSize;
      });
    };

    updateSpeechBubbleSize();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", updateSpeechBubbleSize);
      return () => window.removeEventListener("resize", updateSpeechBubbleSize);
    }

    const resizeObserver = new ResizeObserver(updateSpeechBubbleSize);
    resizeObserver.observe(node);

    return () => resizeObserver.disconnect();
  }, [localPet.status]);

  useEffect(() => {
    petReplyMessageRef.current = petReplyMessage;
  }, [petReplyMessage]);

  useEffect(() => {
    if (!isChatMode) {
      return;
    }

    const baseHeight = conversationFullHeightRef.current || window.innerHeight || FIGMA_SCREEN_HEIGHT;
    const estimatedVisibleHeight = estimatedKeyboardVisibleHeight(baseHeight);

    function updateKeyboardState() {
      const visualViewport = window.visualViewport;
      const visibleHeight = visualViewport
        ? visualViewport.height + visualViewport.offsetTop
        : window.innerHeight;
      const roundedVisibleHeight = Math.round(visibleHeight);
      const keyboardIsVisible = baseHeight - visibleHeight > 120;
      const shouldHoldKeyboardLayout = performance.now() < keyboardSyncUntilRef.current;

      if (keyboardIsVisible) {
        setConversationVisibleHeight(roundedVisibleHeight);
        setIsKeyboardRaised(true);
        return;
      }

      if (shouldHoldKeyboardLayout) {
        setConversationVisibleHeight((currentHeight) =>
          currentHeight >= baseHeight - 1 ? estimatedVisibleHeight : currentHeight,
        );
        setIsKeyboardRaised(true);
        return;
      }

      setConversationVisibleHeight(roundedVisibleHeight);
      setIsKeyboardRaised(false);
    }

    updateKeyboardState();
    const syncTimeoutId = window.setTimeout(
      updateKeyboardState,
      KEYBOARD_EAGER_SYNC_MS + 40,
    );
    window.addEventListener("resize", updateKeyboardState);
    window.visualViewport?.addEventListener("resize", updateKeyboardState);
    window.visualViewport?.addEventListener("scroll", updateKeyboardState);

    return () => {
      window.clearTimeout(syncTimeoutId);
      window.removeEventListener("resize", updateKeyboardState);
      window.visualViewport?.removeEventListener("resize", updateKeyboardState);
      window.visualViewport?.removeEventListener("scroll", updateKeyboardState);
    };
  }, [isChatMode]);

  useEffect(() => {
    if (localPet.status === "loading") {
      return;
    }
    if (!pet || pet.petId !== petId) {
      router.replace("/");
    }
  }, [localPet.status, pet, petId, router]);

  useEffect(() => {
    if (localPet.status !== "ready" || !pet || pet.petId !== petId || !canUseTmaApi()) {
      return;
    }

    const now = Date.now();
    const lastSync = pushSnapshotSyncRef.current;
    if (
      lastSync?.petId === pet.petId &&
      lastSync.updatedAt === pet.updatedAt &&
      now - lastSync.syncedAt < PUSH_SNAPSHOT_SYNC_MIN_INTERVAL_MS
    ) {
      return;
    }

    pushSnapshotSyncRef.current = {
      petId: pet.petId,
      syncedAt: now,
      updatedAt: pet.updatedAt,
    };

    void registerPetPushSnapshot(pet, buildPushSnapshotMemoryContext(pet.petId)).catch(
      () => undefined,
    );
  }, [localPet.status, pet, petId]);

  function handleFeed() {
    if (!pet) {
      return;
    }

    setIsIdleControlsOpen(false);
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
    triggerPetTapVisualFeedback();
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
    setIsIdleControlsOpen(false);
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
    keyboardSyncUntilRef.current = 0;
    setConversationVisibleHeight(
      conversationFullHeightRef.current || window.innerHeight || FIGMA_SCREEN_HEIGHT,
    );
    setIsKeyboardRaised(false);
    chatInputRef.current?.blur();
  }

  function handleOpenChatMode() {
    setIsFeedMode(false);
    setIsIdleControlsOpen(false);
    setIsDebugPanelOpen(false);
    setChatError(null);
    setConversationReplyMessageId(null);
    const fullHeight = window.innerHeight || FIGMA_SCREEN_HEIGHT;
    const shouldSyncKeyboard = shouldEagerlyUseKeyboardLayout();
    conversationFullHeightRef.current = fullHeight;
    keyboardSyncUntilRef.current = shouldSyncKeyboard
      ? performance.now() + KEYBOARD_EAGER_SYNC_MS
      : 0;

    flushSync(() => {
      setConversationVisibleHeight(
        shouldSyncKeyboard ? estimatedKeyboardVisibleHeight(fullHeight) : fullHeight,
      );
      setIsKeyboardRaised(shouldSyncKeyboard);
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
    } else {
      keyboardSyncUntilRef.current = performance.now() + KEYBOARD_EAGER_SYNC_MS;
      setIsKeyboardRaised(true);
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
        replyMaxChars: DASHBOARD_CHAT_REPLY_MAX_CHARS,
        dialogueHookMessage,
        logLabel: "dashboard quick chat",
        onLiteOverlayPatch: localPet.applyLiteOverlayPatch,
        onMemoryConsolidationNeeded: () => {
          const memory = readLocalPetMemory(pet.petId);
          void consolidateLocalUserMemory(memory, { includeDebug: includePromptDebug })
            .then((consolidation) => {
              logBrowserPromptDebug("dashboard memory consolidation", consolidation);
              recordMemoryConsolidationDebug(consolidation.operations);
              const latestMemory = readLocalPetMemory(pet.petId);
              writeLocalPetMemory(
                applyMemoryConsolidationOperations(latestMemory, consolidation.operations),
              );
            })
            .catch(() => undefined);
        },
      });
      showPetReplyMessage(response.reply, true, { showInConversation: true });
      localPet.applyMoodHint(
        response.moodHint,
        response.debug?.liteOverlayPatch,
        response.debug?.storyLibraryPatch,
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

  function handleResetPet() {
    const confirmed = window.confirm("Сбросить персонажа и создать нового? Локальный прогресс будет удален.");
    if (!confirmed) {
      return;
    }
    setIsDebugPanelOpen(false);
    localPet.reset();
    router.push("/");
  }

  function handleResetPetStats() {
    const confirmed = window.confirm("Сбросить параметры персонажа в 0?");
    if (!confirmed) {
      return;
    }

    localPet.resetStats();
    hapticNotification("warning");
  }

  function handlePromptDebugChange(enabled: boolean) {
    const nextSettings = {
      includePromptDebug: enabled,
    };
    setPromptSettings(nextSettings);
    writeLocalPetSettings(nextSettings);
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
    if (shouldRunDailyConsolidation(memory)) {
      void consolidateLocalUserMemory(memory, { includeDebug: includePromptDebug })
        .then((consolidation) => {
          logBrowserPromptDebug("dashboard memory consolidation", consolidation);
          recordMemoryConsolidationDebug(consolidation.operations);
          const latestMemory = readLocalPetMemory(pet.petId);
          writeLocalPetMemory(
            applyMemoryConsolidationOperations(latestMemory, consolidation.operations),
          );
        })
        .catch(() => undefined);
    }

    const history = readLocalChatHistory().messages;
    const proactiveMemoryContext = buildDailyProactiveMemoryContext(pet, memory, history);
    if (proactiveMemoryContext) {
      if (includePromptDebug) {
        console.log("[memory-debug] dashboard proactive candidate", proactiveMemoryContext);
      }
      recordMemoryContextDebug(proactiveMemoryContext, "Память подставлена в proactive prompt");
      void generateLocalProactiveMessage(pet, proactiveMemoryContext, {
        includeDebug: includePromptDebug,
      })
        .then((response) => {
          if (petReplyMessageRef.current) {
            return;
          }
          logBrowserPromptDebug("dashboard proactive chat", response);
          recordReplyPromptDebug(response);
          appendLocalChatMessages([
            {
              id: createLocalId("message"),
              role: "pet",
              text: response.reply,
              createdAt: new Date().toISOString(),
            },
          ]);
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
            response.debug?.liteOverlayPatch,
            response.debug?.storyLibraryPatch,
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

  const animationSettings = { ...defaultIdleAnimationSettings, ...idleAnimationSettings };
  const displayedStage = selectedSprite?.stage ?? pet.stage;
  const displayedState = selectedSprite?.state ?? pet.mood;
  const visiblePetImage =
    generatedSpriteUrl(pet, displayedStage, displayedState) ?? "/figma/main-pet.png";
  const visiblePetBackdropImage =
    visiblePetImage === "/figma/main-pet.png" ? mainPetBackdropSrc : visiblePetImage;
  const displayedReply = petReplyMessage ?? {
    id: 0,
    text: INITIAL_PET_REPLY_FALLBACK,
    playSpeechAudio: false,
  };
  const shouldShowConversationReply =
    isChatMode && conversationReplyMessageId === displayedReply.id;
  const displayedPetName = pet.name?.trim() || DEFAULT_STATUS_NAME;
  const hungerPercent = Math.max(0, Math.min(100, pet.stats.hunger));
  const moodPercent = Math.max(0, Math.min(100, pet.stats.happiness));
  const energyPercent = Math.max(0, Math.min(100, pet.stats.energy));
  const roundedHungerPercent = Math.round(hungerPercent);
  const roundedMoodPercent = Math.round(moodPercent);
  const roundedEnergyPercent = Math.round(energyPercent);
  const transformOrigin = `${animationSettings.originX}% ${animationSettings.originY}%`;
  const idleStretchStyle: IdleStretchStyle = {
    "--pet-idle-duration": `${(
      IDLE_ANIMATION_BASE_DURATION_SECONDS / animationSettings.speed
    ).toFixed(2)}s`,
    "--pet-idle-max-scale": animationSettings.maxScale,
    transformOrigin,
  };
  const idleRotationStyle: IdleRotationStyle = {
    "--pet-idle-rotation-duration": `${(
      IDLE_ROTATION_BASE_DURATION_SECONDS / animationSettings.rotationSpeed
    ).toFixed(2)}s`,
    "--pet-idle-max-rotation": `${animationSettings.maxRotation}deg`,
    transformOrigin,
  };
  const petTapTargetStyle: PetTapTargetStyle = {
    "--pet-tap-scale-duration": `${PET_TAP_SCALE_DURATION_MS}ms`,
    "--pet-tap-scale-easing": PET_TAP_SCALE_EASING,
    transformOrigin: PET_TAP_TRANSFORM_ORIGIN,
  };
  const conversationSceneStyle: ConversationSceneStyle = {
    ...mainScreenStyle,
    "--conversation-visible-height": `${conversationVisibleHeight}px`,
  };
  const speechBubbleClipPath = buildSpeechBubbleClipPath(speechBubbleSize);
  const speechBubbleShapeStyle = {
    ...speechBubbleGlassStyle,
    clipPath: `path("${speechBubbleClipPath}")`,
    WebkitClipPath: `path("${speechBubbleClipPath}")`,
  } satisfies CSSProperties;

  return (
    <main className="tma-screen relative overflow-hidden bg-black text-white" style={mainScreenStyle}>
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
        className={`main-mobile-scene tma-screen relative mx-auto w-full max-w-[402px] overflow-hidden bg-black ${
          isChatMode ? "main-mobile-scene--chat" : ""
        } ${
          isFeedMode ? "main-mobile-scene--feed" : ""
        } ${
          isKeyboardRaised ? "main-mobile-scene--keyboard" : ""
        } ${
          shouldShowConversationReply ? "main-mobile-scene--reply" : ""
        }`}
        style={conversationSceneStyle}
        aria-label="AI Tamagotchi"
      >
        <div className="conversation-appbar" aria-hidden={!isChatMode}>
          <div className="conversation-appbar__blur" aria-hidden="true" />
        </div>

        <MainPetBackdrop src={visiblePetBackdropImage} />

        <div className="hidden" aria-hidden="true">
          <IdleAnimationControls
            isOpen={isIdleControlsOpen}
            pet={pet}
            selectedStage={displayedStage}
            selectedState={displayedState}
            settings={animationSettings}
            includePromptDebug={includePromptDebug}
            onChange={setIdleAnimationSettings}
            onChangeIncludePromptDebug={handlePromptDebugChange}
            onSelectStage={(stage) =>
              setSelectedSprite((current) => ({
                stage,
                state: current?.state ?? pet.mood,
              }))
            }
            onSelectState={(state) =>
              setSelectedSprite((current) => ({
                stage: current?.stage ?? pet.stage,
                state,
              }))
            }
            onResetSprite={() => setSelectedSprite(null)}
            onToggle={() => setIsIdleControlsOpen((current) => !current)}
          />
        </div>

        <DebugPanel
          pet={pet}
          isOpen={isDebugPanelOpen}
          onClose={() => setIsDebugPanelOpen(false)}
          onResetPet={handleResetPet}
          onResetPetStats={handleResetPetStats}
        />

        <div className="sr-only" aria-live="polite">
          Stage {stageLabels[pet.stage]}. State {stateLabels[pet.mood]}. Hunger{" "}
          {roundedHungerPercent}/100. Happiness {roundedMoodPercent}/100. Energy{" "}
          {roundedEnergyPercent}/100. Cleanliness {Math.round(pet.stats.cleanliness)}/100.
        </div>

        <div className="pet-status-name conversation-fade-target">
          {displayedPetName}
        </div>

        <div
          className="top-status-strip conversation-fade-target"
          aria-label={`Голод ${roundedHungerPercent} из 100, настроение ${roundedMoodPercent} из 100, энергия ${roundedEnergyPercent} из 100`}
        >
          <StatProgressRing value={hungerPercent} kind="hunger" />
          <StatProgressRing value={moodPercent} kind="mood" />
          <StatProgressRing value={energyPercent} kind="energy" />
        </div>

        <div
          className={`feed-fade-target absolute left-1/2 top-[226px] z-30 -translate-x-1/2 ${
            shouldShowConversationReply
              ? "conversation-reply-bubble"
              : "conversation-fade-target"
          }`}
        >
          <div className="main-speech-bubble" ref={speechBubbleRef}>
            <span
              className="main-speech-bubble__shape"
              style={speechBubbleShapeStyle}
              aria-hidden="true"
            />
            <PetCharacterMessage
              message={displayedReply}
              textTranslateY={animationSettings.textTranslateY}
              speechEndTrimMs={animationSettings.speechEndTrimMs}
            />
          </div>
        </div>

        <div
          ref={feedDropTargetRef}
          className="main-pet-stage absolute left-0 top-[277px] z-20 h-[372px] w-[402px]"
        >
          <div className="pet-idle-rotation absolute left-0 top-0 h-[357px] w-[402px]" style={idleRotationStyle}>
            {petTapParticleBursts.map((burstId) => (
              <PetTapParticleBurst
                key={burstId}
                id={burstId}
                targetRef={petTapTargetRef}
                onComplete={removePetTapParticleBurst}
              />
            ))}
            <button
              key={petTapPulseId}
              ref={petTapTargetRef}
              type="button"
              className={`pet-tap-target absolute left-0 top-[7.76%] h-[101.37%] w-full ${
                petTapPulseId > 0 ? "pet-tap-target--pulse" : ""
              }`}
              style={petTapTargetStyle}
              onClick={handlePetTap}
              data-button-press-sound="off"
              aria-label="Погладить персонажа"
            >
              <img
                src={visiblePetImage}
                alt=""
                aria-hidden="true"
                className="pet-idle-y-animation h-full w-full max-w-none object-contain"
                style={idleStretchStyle}
                width={402}
                height={362}
                draggable={false}
              />
            </button>
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
            placeholder="Как у тебя прошел день?"
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
              <Loader2 className="size-[18px] animate-spin" aria-hidden="true" />
            ) : null}
            <span>Отправить</span>
          </button>
        </form>

        <button
          type="button"
          aria-controls="debug-panel"
          aria-expanded={isDebugPanelOpen}
          aria-label={isDebugPanelOpen ? "Скрыть debug-панель" : "Показать debug-панель"}
          onClick={() => {
            setIsIdleControlsOpen(false);
            setIsDebugPanelOpen(true);
          }}
          className="feed-fade-target conversation-fade-target absolute left-[327px] top-[685px] z-40 grid size-[58.203px] place-items-center overflow-hidden rounded-[24px] bg-[rgba(41,41,41,0.4)] text-white/90 shadow-[inset_0_2px_4px_rgba(255,255,255,0.09)] transition-colors hover:bg-[rgba(55,55,55,0.5)] focus:outline-none focus:ring-2 focus:ring-white/20"
        >
          <Bug className="size-[22px]" aria-hidden="true" />
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
            style={mainActionButtonStyle.chat}
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
            style={mainActionButtonStyle.feed}
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
            style={mainActionButtonStyle.travel}
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
    </main>
  );
}
