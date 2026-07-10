import type {
  LocalChatMemoryEpisode,
  LocalPetMemoryContext,
  LocalPetMemoryStateV1,
  LocalPetUserMemory,
  UserMemoryKind,
} from "./localPetMemoryTypes";
import type { LocalChatMessage, LocalPetState } from "./types";

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
const RECENT_DIRECT_HISTORY_MESSAGES = 12;
const MAX_RECALL_EPISODES = 3;
const MAX_RELEVANT_MEMORIES = 5;
const EPISODE_SPONTANEOUS_MAX_AGE_DAYS = 30;
const EPISODE_MENTION_COOLDOWN_DAYS = 14;
const AMBIENT_MEMORY_KINDS = new Set<UserMemoryKind>([
  "user_fact",
  "preference",
  "relationship",
  "routine",
  "emotion",
  "boundary",
]);
const EPISODE_CONTEXT_RADIUS = 2;
const RUSSIAN_SUFFIXES = [
  "иями", "ями", "ами", "ого", "ему", "ому", "ыми", "ими",
  "ая", "яя", "ое", "ее", "ые", "ие", "ой", "ей", "ам", "ям",
  "ах", "ях", "ов", "ев", "ом", "ем", "ую", "юю", "ить", "ать", "ять",
  "ет", "ит", "ют", "ут", "ешь", "ишь", "ы", "и", "а", "я", "у", "ю",
];
const STOP_WORDS = new Set([
  "меня",
  "мне",
  "мой",
  "моя",
  "мои",
  "это",
  "что",
  "как",
  "зовут",
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
const MEMORY_RECALL_RE = /(помнишь|запомнил|как меня зовут|как я тебя зову|что я люблю|что мне нравится|что я просил|что ты обещал)/iu;
const TEMPORAL_RECALL_RE = /(сегодня|вчера|позавчера|на прошлой неделе|\d+\s+(?:день|дня|дней)\s+назад)/iu;
const PREFERENCE_RECALL_RE = /(что я люблю|что мне нравится|мои предпочтения|чего я не люблю)/iu;
const GOAL_RECALL_RE = /(моя цель|чего я хочу|что я планировал|мои планы)/iu;
const STYLE_PREFERENCE_QUERY_RE = /(расскажи|скажи|ответь|повесели|развесели|придумай)/iu;
const STYLE_PREFERENCE_MEMORY_RE = /(коротк|длинн|ответ|стиль|говори|общайся)/iu;
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
const STANDALONE_CHAT_INTENT_RE = new RegExp(
  `^(?:${[
    "ты\\s+как",
    "как\\s+дела",
    "как\\s+ты",
    "расскажи",
    "повесели",
    "развесели",
    "придумай",
    "привет",
    "ку",
    "хай",
  ].join("|")})(?:$|\\s|[?.!,])`,
  "iu",
);
const DIALOGUE_HOOK_REPLY_RE = new RegExp(
  `^(?:${[
    "да",
    "нет",
    "ага",
    "угу",
    "ок",
    "хочу",
    "не\\s+хочу",
    "видел",
    "не\\s+видел",
    "не\\s+знаю",
    "покажи",
    "давай",
    "интересно",
    "расскажи\\s+(?:еще|ещё|подробнее)",
    "продолжай",
    "продолжи",
    "почему\\s+так",
    "куда",
    "кто",
    "что",
    "где",
    "почему",
    "зачем",
    "как\\s+именно",
    "и\\s+что",
    "а\\s+что",
    "а\\s+ты",
  ].join("|")})(?:$|\\s|[?.!,])`,
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

function normalizeToken(token: string) {
  if (!/[а-яё]/u.test(token) || token.length < 6) {
    return token;
  }
  const suffix = RUSSIAN_SUFFIXES.find(
    (candidate) => token.endsWith(candidate) && token.length - candidate.length >= 4,
  );
  return suffix ? token.slice(0, -suffix.length) : token;
}

function tokenize(text: string) {
  return new Set(
    text
      .toLowerCase()
      .match(/[\p{L}\p{N}]{3,}/gu)
      ?.filter((word) => !STOP_WORDS.has(word))
      .map(normalizeToken) ?? [],
  );
}

export function isIdentityDialogueQuestion(text: string) {
  const compact = text.trim().replace(/\s+/g, " ");
  return compact.length <= 160 && IDENTITY_DIALOGUE_RE.test(compact);
}

export function shouldUseDialogueHookContext(text: string) {
  const compact = text.trim().replace(/\s+/g, " ");
  if (!compact || compact.length > 120 || isIdentityDialogueQuestion(compact)) {
    return false;
  }
  if (STANDALONE_CHAT_INTENT_RE.test(compact)) {
    return false;
  }
  return DIALOGUE_HOOK_REPLY_RE.test(compact);
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

function isMemoryRecallQuestion(text: string) {
  const compact = text.trim().replace(/\s+/g, " ");
  return compact.length <= 200 && MEMORY_RECALL_RE.test(compact);
}

function isExplicitRecallQuestion(text: string) {
  return isMemoryRecallQuestion(text)
    || isIdentityDialogueQuestion(text)
    || TEMPORAL_RECALL_RE.test(text);
}

function relativeDayOffset(text: string): number | null {
  if (/позавчера/iu.test(text)) {
    return 2;
  }
  if (/вчера/iu.test(text)) {
    return 1;
  }
  if (/сегодня/iu.test(text)) {
    return 0;
  }
  const match = text.match(/(\d+)\s+(?:день|дня|дней)\s+назад/iu);
  return match ? Number(match[1]) : null;
}

function temporalQueryRange(text: string, now: Date): { start: number; end: number } | null {
  const dayOffset = relativeDayOffset(text);
  if (dayOffset !== null) {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    start.setDate(start.getDate() - dayOffset);
    const end = new Date(start);
    end.setDate(end.getDate() + 1);
    return { start: start.getTime(), end: end.getTime() };
  }
  if (/на прошлой неделе/iu.test(text)) {
    const start = new Date(now);
    start.setHours(0, 0, 0, 0);
    const mondayOffset = (start.getDay() + 6) % 7;
    start.setDate(start.getDate() - mondayOffset - 7);
    const end = new Date(start);
    end.setDate(end.getDate() + 7);
    return { start: start.getTime(), end: end.getTime() };
  }
  return null;
}

function isInRange(value: string | undefined, range: { start: number; end: number } | null) {
  if (!value || !range) {
    return false;
  }
  const timestamp = Date.parse(value);
  return !Number.isNaN(timestamp) && timestamp >= range.start && timestamp < range.end;
}

function memoryTime(memory: LocalPetUserMemory) {
  for (const value of [memory.occurredAt, memory.recordedAt, memory.createdAt]) {
    const timestamp = value ? Date.parse(value) : Number.NaN;
    if (!Number.isNaN(timestamp)) {
      return timestamp;
    }
  }
  return 0;
}

function isMemoryActive(memory: LocalPetUserMemory, now: Date) {
  return !memory.expiresAt || Date.parse(memory.expiresAt) > now.getTime();
}

function memorySelectionScore(
  memory: LocalPetUserMemory,
  message: string,
  queryTokens: Set<string>,
  temporalRange: { start: number; end: number } | null,
) {
  if (isInRange(memory.occurredAt, temporalRange)) {
    return 4 + memory.importance;
  }
  const overlap = tokenOverlapScore(memory.text, queryTokens);
  if (overlap > 0) {
    return overlap + memory.importance;
  }
  if (isMemoryRecallQuestion(message)) {
    if (PREFERENCE_RECALL_RE.test(message) && memory.kind === "preference") {
      return 3 + memory.importance;
    }
    if (GOAL_RECALL_RE.test(message) && memory.kind === "goal") {
      return 3 + memory.importance;
    }
    if (
      memory.normalizedKey === "user-name"
      || memory.normalizedKey === "pet-nickname"
      || memory.kind === "promise"
      || memory.kind === "boundary"
    ) {
      return 2 + memory.importance;
    }
  }
  if (
    memory.kind === "preference"
    && STYLE_PREFERENCE_QUERY_RE.test(message)
    && STYLE_PREFERENCE_MEMORY_RE.test(memory.text)
  ) {
    return 1.5 + memory.importance;
  }
  return 0;
}

function canRecallMemorySpontaneously(
  memory: LocalPetUserMemory,
  message: string,
  now: Date,
) {
  if (memory.memoryClass !== "episode" || isExplicitRecallQuestion(message)) {
    return true;
  }
  const ageDays = Math.max(0, (now.getTime() - memoryTime(memory)) / DAY_MS);
  if (ageDays > EPISODE_SPONTANEOUS_MAX_AGE_DAYS) {
    return false;
  }
  const lastMentionedAt = memory.lastMentionedAt ? Date.parse(memory.lastMentionedAt) : Number.NaN;
  return Number.isNaN(lastMentionedAt)
    || now.getTime() - lastMentionedAt >= EPISODE_MENTION_COOLDOWN_DAYS * DAY_MS;
}

function memoryContextItem(item: LocalPetUserMemory) {
  return {
    id: item.id,
    kind: item.kind,
    text: item.text,
    memoryClass: item.memoryClass,
    recordedAt: item.recordedAt,
    occurredAt: item.occurredAt,
    lastMentionedAt: item.lastMentionedAt,
    dueAt: item.dueAt,
  };
}

function selectRelevantMemories(
  memory: LocalPetMemoryStateV1 | undefined,
  message: string,
  queryTokens: Set<string>,
  now: Date,
): LocalPetMemoryContext["relevantMemories"] {
  if (!memory?.memories.length) {
    return [];
  }
  const temporalRange = temporalQueryRange(message, now);
  return memory.memories
    .filter((item) => isMemoryActive(item, now) && canRecallMemorySpontaneously(item, message, now))
    .map((item) => ({
      item,
      score: memorySelectionScore(item, message, queryTokens, temporalRange),
    }))
    .filter(({ score }) => score > 0)
    .sort((left, right) => right.score - left.score || memoryTime(right.item) - memoryTime(left.item))
    .slice(0, MAX_RELEVANT_MEMORIES)
    .map(({ item }) => memoryContextItem(item));
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
  memory?: LocalPetMemoryStateV1,
): LocalPetMemoryContext {
  const queryTokens = tokenize(message);
  const relevantMemories = selectRelevantMemories(memory, message, queryTokens, now);
  const stableContext = {
    summary: memory?.summary,
    userProfile: memory?.userProfile,
    relevantMemories,
  };
  if (!queryTokens.size || history.length <= RECENT_DIRECT_HISTORY_MESSAGES) {
    return stableContext;
  }

  const searchableCount = Math.max(0, history.length - RECENT_DIRECT_HISTORY_MESSAGES);
  const temporalRange = temporalQueryRange(message, now);
  const explicitRecall = isExplicitRecallQuestion(message);
  const windows = history.slice(0, searchableCount).flatMap((historyMessage, index) => {
    const overlap = tokenOverlapScore(historyMessage.text, queryTokens);
    const temporalMatch = isInRange(historyMessage.createdAt, temporalRange);
    if (overlap <= 0 && !temporalMatch) {
      return [];
    }
    const ageDays = Math.max(0, (now.getTime() - messageTime(historyMessage)) / DAY_MS);
    if (ageDays > EPISODE_SPONTANEOUS_MAX_AGE_DAYS && !explicitRecall) {
      return [];
    }
    const recencyBoost = Math.max(0, 4 - ageDays) * 0.1;
    return [{
      start: Math.max(0, index - EPISODE_CONTEXT_RADIUS),
      end: Math.min(history.length, index + EPISODE_CONTEXT_RADIUS + 1),
      score: overlap + (temporalMatch ? 4 : 0) + recencyBoost,
      latestAt: messageTime(historyMessage),
    }];
  });

  const selectedWindows = mergeWindows(windows)
    .sort((left, right) => right.score - left.score || right.latestAt - left.latestAt)
    .slice(0, MAX_RECALL_EPISODES)
    .sort((left, right) => left.start - right.start);

  return {
    ...stableContext,
    episodes: selectedWindows.map((window) => episodeFromWindow(history, window)),
  };
}

export function buildMemorySnapshotContext(
  memory: LocalPetMemoryStateV1,
  now = new Date(),
): LocalPetMemoryContext {
  return {
    summary: memory.summary,
    userProfile: memory.userProfile,
    relevantMemories: memory.memories
      .filter((item) => isMemoryActive(item, now))
      .slice(0, MAX_RELEVANT_MEMORIES)
      .map(memoryContextItem),
  };
}

export function buildAmbientMemoryContext(
  memory: LocalPetMemoryStateV1,
  now = new Date(),
): LocalPetMemoryContext {
  return {
    relevantMemories: memory.memories
      .filter((item) => (
        isMemoryActive(item, now)
        && item.memoryClass !== "episode"
        && AMBIENT_MEMORY_KINDS.has(item.kind)
      ))
      .sort((left, right) => {
        const leftIsName = left.normalizedKey === "user-name" ? 1 : 0;
        const rightIsName = right.normalizedKey === "user-name" ? 1 : 0;
        return (
          rightIsName - leftIsName
          || right.importance - left.importance
          || memoryTime(right) - memoryTime(left)
        );
      })
      .slice(0, MAX_RELEVANT_MEMORIES)
      .map(memoryContextItem),
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
    summary: memory.summary,
    userProfile: memory.userProfile,
    relevantMemories: [],
    episodes: [episode],
    proactiveCandidate: {
      memoryIds: [],
      episodeIds: [episode.id],
      reason: lastUserText ? `продолжить недавний разговор: ${lastUserText.slice(0, 180)}` : "продолжить недавний разговор",
    },
  };
}
