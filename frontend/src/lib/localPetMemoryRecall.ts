import type {
  LocalChatMemoryEpisode,
  LocalPetMemoryContext,
  LocalPetMemoryStateV1,
} from "./localPetMemoryTypes";
import type { LocalChatMessage, LocalPetState } from "./types";

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
const RECENT_DIRECT_HISTORY_MESSAGES = 12;
const MAX_RECALL_EPISODES = 3;
const EPISODE_CONTEXT_RADIUS = 2;
const STOP_WORDS = new Set([
  "меня",
  "мне",
  "мой",
  "моя",
  "мои",
  "это",
  "что",
  "как",
  "где",
  "когда",
  "почему",
  "тебя",
  "тебе",
  "твой",
  "твоя",
  "про",
  "для",
  "или",
  "еще",
  "ещё",
  "the",
  "and",
  "you",
  "your",
  "with",
]);
const IDENTITY_DIALOGUE_RE = new RegExp(
  [
    "кто\\s+ты",
    "ты\\s+кто",
    "что\\s+ты\\s+такое",
    "расскажи\\s+о\\s+себе",
    "как\\s+тебя\\s+зовут",
    "как\\s+звать",
  ].join("|"),
  "iu",
);

function localDateKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
    date.getDate(),
  ).padStart(2, "0")}`;
}

function sameLocalDay(left: Date, right: Date) {
  return localDateKey(left) === localDateKey(right);
}

function tokenize(text: string) {
  return new Set(
    text
      .toLowerCase()
      .match(/[\p{L}\p{N}]{3,}/gu)
      ?.filter((word) => !STOP_WORDS.has(word)) ?? [],
  );
}

export function isIdentityDialogueQuestion(text: string) {
  const compact = text.trim().replace(/\s+/g, " ");
  return compact.length <= 160 && IDENTITY_DIALOGUE_RE.test(compact);
}

function messageTime(message: LocalChatMessage) {
  const timestamp = Date.parse(message.createdAt);
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function tokenOverlapScore(text: string, queryTokens: Set<string>) {
  if (!queryTokens.size) {
    return 0;
  }
  const tokens = tokenize(text);
  let score = 0;
  queryTokens.forEach((token) => {
    if (tokens.has(token)) {
      score += 1;
    }
  });
  return score;
}

function mergeWindows(
  windows: { start: number; end: number; score: number; latestAt: number }[],
) {
  return windows
    .sort((left, right) => left.start - right.start || left.end - right.end)
    .reduce<{ start: number; end: number; score: number; latestAt: number }[]>((result, item) => {
      const previous = result[result.length - 1];
      if (!previous || item.start > previous.end + 1) {
        result.push(item);
        return result;
      }
      previous.end = Math.max(previous.end, item.end);
      previous.score = Math.max(previous.score, item.score);
      previous.latestAt = Math.max(previous.latestAt, item.latestAt);
      return result;
    }, []);
}

function episodeId(history: LocalChatMessage[], start: number, end: number) {
  const first = history[start];
  const last = history[end - 1];
  return `episode:${first?.id ?? start}:${last?.id ?? end}`;
}

function episodeFromWindow(
  history: LocalChatMessage[],
  window: { start: number; end: number },
): LocalChatMemoryEpisode {
  return {
    id: episodeId(history, window.start, window.end),
    messages: history.slice(window.start, window.end).map((message) => ({
      role: message.role,
      text: message.text,
      createdAt: message.createdAt,
    })),
  };
}

export function buildMemoryContextForMessage(
  history: LocalChatMessage[],
  message: string,
  now = new Date(),
): LocalPetMemoryContext {
  const queryTokens = tokenize(message);
  if (!queryTokens.size || history.length <= RECENT_DIRECT_HISTORY_MESSAGES) {
    return { relevantMemories: [] };
  }

  const searchableCount = Math.max(0, history.length - RECENT_DIRECT_HISTORY_MESSAGES);
  const windows = history.slice(0, searchableCount).flatMap((historyMessage, index) => {
    const overlap = tokenOverlapScore(historyMessage.text, queryTokens);
    if (overlap <= 0) {
      return [];
    }
    const ageDays = Math.max(0, (now.getTime() - messageTime(historyMessage)) / DAY_MS);
    const recencyBoost = Math.max(0, 4 - ageDays) * 0.1;
    return [{
      start: Math.max(0, index - EPISODE_CONTEXT_RADIUS),
      end: Math.min(history.length, index + EPISODE_CONTEXT_RADIUS + 1),
      score: overlap + recencyBoost,
      latestAt: messageTime(historyMessage),
    }];
  });

  const selectedWindows = mergeWindows(windows)
    .sort((left, right) => right.score - left.score || right.latestAt - left.latestAt)
    .slice(0, MAX_RECALL_EPISODES)
    .sort((left, right) => left.start - right.start);

  return {
    relevantMemories: [],
    episodes: selectedWindows.map((window) => episodeFromWindow(history, window)),
  };
}

function hasLocalProactiveToday(memory: LocalPetMemoryStateV1, now: Date) {
  return Boolean(memory.lastProactiveAt && sameLocalDay(new Date(memory.lastProactiveAt), now));
}

function petAgeHours(pet: LocalPetState, now: Date) {
  const createdAt = Date.parse(pet.createdAt);
  return Number.isNaN(createdAt) ? 0 : (now.getTime() - createdAt) / HOUR_MS;
}

function hasRecentPetMessage(history: LocalChatMessage[], now: Date) {
  return history.some((message) => {
    if (message.role !== "pet") {
      return false;
    }
    const createdAt = Date.parse(message.createdAt);
    return !Number.isNaN(createdAt) && now.getTime() - createdAt < 30 * 60_000;
  });
}

export function buildDailyProactiveMemoryContext(
  pet: LocalPetState,
  memory: LocalPetMemoryStateV1,
  history: LocalChatMessage[],
  now = new Date(),
): LocalPetMemoryContext | null {
  if (hasLocalProactiveToday(memory, now) || petAgeHours(pet, now) < 6) {
    return null;
  }
  if (hasRecentPetMessage(history, now)) {
    return null;
  }

  let lastUserIndex = -1;
  for (let index = history.length - 1; index >= 0; index -= 1) {
    if (history[index]?.role === "user") {
      lastUserIndex = index;
      break;
    }
  }
  if (lastUserIndex < 0) {
    return null;
  }
  const lastUserText = history[lastUserIndex]?.text.trim();
  if (lastUserText && isIdentityDialogueQuestion(lastUserText)) {
    return null;
  }

  const start = Math.max(0, lastUserIndex - EPISODE_CONTEXT_RADIUS);
  const end = Math.min(history.length, lastUserIndex + EPISODE_CONTEXT_RADIUS + 1);
  const episode = episodeFromWindow(history, { start, end });

  return {
    relevantMemories: [],
    episodes: [episode],
    proactiveCandidate: {
      memoryIds: [],
      episodeIds: [episode.id],
      reason: lastUserText ? `продолжить недавний разговор: ${lastUserText.slice(0, 180)}` : "продолжить недавний разговор",
    },
  };
}
