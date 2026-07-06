import type {
  LocalPetMemoryContext,
  LocalPetMemoryStateV1,
  LocalPetUserMemory,
} from "./localPetMemoryTypes";
import type { LocalChatMessage, LocalPetState } from "./types";

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
const MAX_RECALL_MEMORIES = 5;
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
  "the",
  "and",
  "you",
  "your",
  "with",
]);

function localDateKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(
    date.getDate(),
  ).padStart(2, "0")}`;
}

function sameLocalDay(left: Date, right: Date) {
  return localDateKey(left) === localDateKey(right);
}

function tomorrowLocalDay(now: Date) {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
}

function isExpired(memory: LocalPetUserMemory, now: Date) {
  return Boolean(memory.expiresAt && Date.parse(memory.expiresAt) < now.getTime());
}

function dueDate(memory: LocalPetUserMemory) {
  if (!memory.dueAt) {
    return null;
  }
  const date = new Date(memory.dueAt);
  return Number.isNaN(date.getTime()) ? null : date;
}

function tokenize(text: string) {
  return new Set(
    text
      .toLowerCase()
      .match(/[\p{L}\p{N}]{3,}/gu)
      ?.filter((word) => !STOP_WORDS.has(word)) ?? [],
  );
}

function keywordOverlapScore(memory: LocalPetUserMemory, queryTokens: Set<string>) {
  if (!queryTokens.size) {
    return 0;
  }
  const memoryTokens = tokenize(`${memory.text} ${memory.tags.join(" ")}`);
  let score = 0;
  queryTokens.forEach((token) => {
    if (memoryTokens.has(token)) {
      score += 1;
    }
  });
  return score;
}

function recallScore(memory: LocalPetUserMemory, message: string, now: Date) {
  if (isExpired(memory, now)) {
    return -Infinity;
  }
  let score = memory.importance * 25 + memory.confidence * 10;
  const due = dueDate(memory);
  if (due) {
    if (sameLocalDay(due, now)) {
      score += 100;
    } else {
      const diff = due.getTime() - now.getTime();
      if (diff >= 0 && diff <= DAY_MS) {
        score += 90;
      } else if (diff > 0 && diff <= DAY_MS * 3) {
        score += 35;
      }
    }
  }
  score += keywordOverlapScore(memory, tokenize(message)) * 30;
  const lastMentionedAt = memory.lastMentionedAt ? Date.parse(memory.lastMentionedAt) : 0;
  const staleDays = lastMentionedAt ? (now.getTime() - lastMentionedAt) / DAY_MS : 30;
  if (memory.importance >= 0.75 && staleDays >= 3) {
    score += 20;
  }
  return score;
}

function contextFromMemories(
  memory: LocalPetMemoryStateV1,
  relevantMemories: LocalPetUserMemory[],
  proactiveCandidate?: LocalPetMemoryContext["proactiveCandidate"],
): LocalPetMemoryContext {
  return {
    summary: memory.summary,
    userProfile: memory.userProfile,
    relevantMemories: relevantMemories.slice(0, MAX_RECALL_MEMORIES).map((item) => ({
      id: item.id,
      kind: item.kind,
      text: item.text,
      dueAt: item.dueAt,
    })),
    proactiveCandidate,
  };
}

export function buildMemoryContextForMessage(
  memory: LocalPetMemoryStateV1,
  message: string,
  now = new Date(),
): LocalPetMemoryContext {
  const relevantMemories = memory.memories
    .map((item) => ({ item, score: recallScore(item, message, now) }))
    .filter(({ score }) => score > 0)
    .sort((left, right) => right.score - left.score)
    .map(({ item }) => item)
    .slice(0, MAX_RECALL_MEMORIES);

  return contextFromMemories(memory, relevantMemories);
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

function proactiveScore(memory: LocalPetUserMemory, now: Date) {
  if (isExpired(memory, now)) {
    return -Infinity;
  }
  const due = dueDate(memory);
  if (due && sameLocalDay(due, now)) {
    return 1000 + memory.importance * 100;
  }
  if (due && sameLocalDay(due, tomorrowLocalDay(now))) {
    return 800 + memory.importance * 100;
  }
  const lastMentionedAt = memory.lastMentionedAt ? Date.parse(memory.lastMentionedAt) : 0;
  const staleDays = lastMentionedAt ? (now.getTime() - lastMentionedAt) / DAY_MS : 30;
  if (memory.importance >= 0.75 && staleDays >= 3) {
    return 500 + memory.importance * 100;
  }
  if (memory.kind === "preference" || memory.kind === "relationship") {
    return 250 + memory.importance * 100;
  }
  return -Infinity;
}

function proactiveReason(memory: LocalPetUserMemory, now: Date) {
  const due = dueDate(memory);
  if (due && sameLocalDay(due, now)) {
    return `у пользователя сегодня важное событие: ${memory.text}`;
  }
  if (due && sameLocalDay(due, tomorrowLocalDay(now))) {
    return `у пользователя завтра важное событие: ${memory.text}`;
  }
  return memory.text;
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
  const selected = memory.memories
    .map((item) => ({ item, score: proactiveScore(item, now) }))
    .filter(({ score }) => score > 0)
    .sort((left, right) => right.score - left.score)
    .map(({ item }) => item)
    .slice(0, 3);
  if (!selected.length) {
    return null;
  }
  return contextFromMemories(memory, selected, {
    memoryIds: selected.map((item) => item.id),
    reason: proactiveReason(selected[0], now).slice(0, 280),
  });
}
