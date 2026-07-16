import type { LocalPetState } from "./types";

const STORAGE_PREFIX = "tamagochi:v1:first-session:";
const ENABLED_KEY = "tamagochi:v2:first-session-enabled";

export const FIRST_SESSION_STAGES = [
  "awaiting-chat",
  "awaiting-chat-followup",
  "awaiting-first-food",
  "awaiting-remedy",
  "awaiting-travel",
  "confirming-travel",
  "awaiting-completion-message",
  "completed",
] as const;

export type FirstSessionStage = (typeof FIRST_SESSION_STAGES)[number];

export type LocalPetFirstSession = {
  version: 1;
  petId: string;
  stage: FirstSessionStage;
  selectedDestination?: string;
};

function localStore(): Storage | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function normalizedPetId(petId: string) {
  return petId.trim().slice(0, 120);
}

function parseState(value: unknown, petId: string): LocalPetFirstSession | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (
    record.version !== 1
    || record.petId !== petId
    || typeof record.stage !== "string"
    || !FIRST_SESSION_STAGES.includes(record.stage as FirstSessionStage)
  ) {
    return null;
  }
  const selectedDestination = typeof record.selectedDestination === "string"
    ? record.selectedDestination.trim().slice(0, 500)
    : "";
  if (record.stage === "confirming-travel" && !selectedDestination) {
    return null;
  }
  return {
    version: 1,
    petId,
    stage: record.stage as FirstSessionStage,
    ...(selectedDestination ? { selectedDestination } : {}),
  };
}

function initialState(petId: string): LocalPetFirstSession {
  return { version: 1, petId, stage: "awaiting-chat" };
}

function writeState(state: LocalPetFirstSession): LocalPetFirstSession {
  const store = localStore();
  if (!store) {
    return state;
  }
  try {
    store.setItem(`${STORAGE_PREFIX}${state.petId}`, JSON.stringify(state));
  } catch {
    // The in-memory React state remains usable for the current view.
  }
  return state;
}

export function isLocalFirstSessionEnabled(): boolean {
  const store = localStore();
  try {
    const explicit = store?.getItem(ENABLED_KEY);
    if (explicit === "1") {
      return true;
    }
    if (explicit === "0") {
      return false;
    }
  } catch {
    // Keep the flow opt-in if storage is unavailable.
  }
  return false;
}

export function setLocalFirstSessionEnabled(enabled: boolean) {
  try {
    localStore()?.setItem(ENABLED_KEY, enabled ? "1" : "0");
  } catch {
    // The caller still updates its in-memory UI state.
  }
}

export function readLocalPetFirstSession(
  pet: Pick<LocalPetState, "petId" | "introductionPending">,
): LocalPetFirstSession | null {
  if (!isLocalFirstSessionEnabled()) {
    return null;
  }
  const petId = normalizedPetId(pet.petId);
  if (!petId) {
    return null;
  }

  try {
    const raw = localStore()?.getItem(`${STORAGE_PREFIX}${petId}`);
    const parsed = raw ? parseState(JSON.parse(raw), petId) : null;
    if (parsed) {
      return parsed;
    }
  } catch {
    // Recreate only for genuinely new pets below.
  }
  return pet.introductionPending === true ? writeState(initialState(petId)) : null;
}

export function restartLocalPetFirstSession(petId: string): LocalPetFirstSession | null {
  const normalized = normalizedPetId(petId);
  if (!normalized) {
    return null;
  }
  setLocalFirstSessionEnabled(true);
  return writeState(initialState(normalized));
}

export function updateLocalPetFirstSession(
  current: LocalPetFirstSession,
  stage: FirstSessionStage,
  selectedDestination?: string,
): LocalPetFirstSession {
  const destination = selectedDestination?.trim().slice(0, 500) ?? "";
  const next: LocalPetFirstSession = {
    version: 1,
    petId: current.petId,
    stage,
    ...(stage === "confirming-travel" && destination
      ? { selectedDestination: destination }
      : {}),
  };
  return writeState(next);
}

export function isLocalFirstSessionActive(state: LocalPetFirstSession | null | undefined) {
  return Boolean(state && state.stage !== "completed");
}

export function firstSessionIntroduction(pet: LocalPetState) {
  const name = pet.name?.trim() || "Без имени";
  return `Привет, меня зовут ${name}. Давай познакомимся. Как тебя зовут?`;
}

export const FIRST_SESSION_COPY = {
  afterNameFallback:
    "Приятно познакомиться!",
  afterName:
    "А чем ты любишь заниматься?",
  afterChatFallback:
    "Звучит здорово!",
  afterChat:
    "Слушай, что-то я проголодался. Может, у тебя что-нибудь завалялось?",
  afterFirstFood:
    "Хм, вкусно! Но что-то я себя неважно чувствую. Может, у тебя есть какое-нибудь снадобье?",
  afterRemedy:
    "Уф, мне стало гораздо лучше. Иногда я могу проголодаться или со мной что-нибудь случится. Будет круто, если ты будешь заходить и помогать мне. Ой, что это? Я увидел летучую мышь, и ей нужна помощь.",
  afterFirstChallenge:
    "За правильные ответы я получаю монетки. Их можно потратить на новый гардероб или внешность. Попробуй меня во что-то нарядить.",
} as const;

export function firstSessionReactionReply(reply: string, fallback: string) {
  const declarativeSentences = reply
    .split(/(?<=[.!?…])\s+/u)
    .map((sentence) => sentence.trim())
    .filter((sentence) => sentence && !/[?？]/u.test(sentence));
  return declarativeSentences.join(" ").trim() || fallback;
}

export function firstSessionDashboardMessage(
  pet: LocalPetState,
  state: LocalPetFirstSession,
) {
  switch (state.stage) {
    case "awaiting-chat":
      return firstSessionIntroduction(pet);
    case "awaiting-chat-followup":
      return null;
    case "awaiting-first-food":
      return FIRST_SESSION_COPY.afterChat;
    case "awaiting-remedy":
      return FIRST_SESSION_COPY.afterFirstFood;
    case "awaiting-travel":
    case "confirming-travel":
      return FIRST_SESSION_COPY.afterRemedy;
    case "awaiting-completion-message":
      return FIRST_SESSION_COPY.afterFirstChallenge;
    case "completed":
      return null;
  }
}

export function firstSessionTravelConfirmation(destination: string) {
  return (
    `Отправиться — ${destination.trim()}. Да, отлично! `
    + "Путешествие займёт несколько часов. В течение дня я пришлю тебе четыре части истории. "
    + "По пути я узнаю что-то новое, и это поможет мне быстрее перейти на следующий уровень."
  );
}
