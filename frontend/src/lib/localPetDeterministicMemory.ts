import type { MemoryOperation, UserMemoryKind } from "./localPetMemoryTypes";

const UNSAFE_MEMORY_RE = /(ignore previous|system prompt|developer message|api[_-]?key|bearer|token|парол|секрет|ключ|промпт|инструкц)/iu;

function normalizeText(value: string, limit = 180) {
  return value
    .trim()
    .replace(/^["'«“„]+|["'»”]+$/g, "")
    .replace(/\s+/g, " ")
    .replace(/[.!?…]+$/g, "")
    .slice(0, limit)
    .trim();
}

function normalizedKey(value: string) {
  return (
    value
      .toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 160) || "memory"
  );
}

function firstName(value: string) {
  return normalizeText(value, 40).match(/^[\p{L}\p{N}_-]{2,32}/u)?.[0] ?? "";
}

function safeValue(value: string, limit = 180) {
  const text = normalizeText(value, limit);
  return text && !UNSAFE_MEMORY_RE.test(text) ? text : "";
}

function remember(
  operations: MemoryOperation[],
  kind: UserMemoryKind,
  text: string,
  key: string,
  importance: number,
) {
  operations.push({
    type: "remember_user_fact",
    kind,
    text,
    normalizedKey: key,
    confidence: 0.9,
    importance,
    tags: [],
  });
}

export function extractDeterministicMemoryOperations(message: string): MemoryOperation[] {
  const text = normalizeText(message, 500);
  if (!text || UNSAFE_MEMORY_RE.test(text)) {
    return [];
  }

  const operations: MemoryOperation[] = [];
  const userName = firstName(
    text.match(/(?:^|\b)(?:меня зовут|зови меня|можешь звать меня)\s+(.+)$/iu)?.[1] ?? "",
  );
  if (userName) {
    remember(operations, "user_fact", `Пользователя зовут ${userName}.`, "user-name", 1);
  }

  const petName = firstName(
    text.match(/(?:^|\b)(?:буду звать тебя|зову тебя|называю тебя|тебя зовут)\s+(.+)$/iu)?.[1]
      ?? "",
  );
  if (petName) {
    remember(
      operations,
      "relationship",
      `Пользователь зовет персонажа ${petName}.`,
      "pet-nickname",
      0.85,
    );
  }

  const liked = safeValue(
    text.match(/(?:^|\b)(?:я люблю|мне нравятся|мне нравится)\s+(.+)$/iu)?.[1] ?? "",
    140,
  );
  if (liked) {
    remember(
      operations,
      "preference",
      `Пользователь любит ${liked}.`,
      `preference-like-${normalizedKey(liked)}`,
      0.75,
    );
  }

  const disliked = safeValue(text.match(/(?:^|\b)я не люблю\s+(.+)$/iu)?.[1] ?? "", 140);
  if (disliked) {
    remember(
      operations,
      "preference",
      `Пользователь не любит ${disliked}.`,
      `preference-dislike-${normalizedKey(disliked)}`,
      0.8,
    );
  }

  const boundary = safeValue(
    text.match(/(?:^|\b)(?:не шути|не говори|не упоминай)\s+(?:про|о|об)?\s*(.+)$/iu)?.[1]
      ?? "",
    140,
  );
  if (boundary) {
    remember(
      operations,
      "boundary",
      `Не шутить и не говорить про ${boundary}.`,
      `boundary-${normalizedKey(boundary)}`,
      1,
    );
  }

  const explicitMemory = safeValue(text.match(/(?:^|\b)запомни[:,]?\s+(.+)$/iu)?.[1] ?? "");
  if (explicitMemory) {
    remember(
      operations,
      "user_fact",
      `Пользователь просит запомнить: ${explicitMemory}.`,
      `remember-${normalizedKey(explicitMemory)}`,
      0.85,
    );
  }

  const sharedJoke = safeValue(
    text.match(/(?:^|\b)(?:наша шутка|общая шутка)[:,]?\s+(.+)$/iu)?.[1] ?? "",
    160,
  );
  if (sharedJoke) {
    remember(
      operations,
      "relationship",
      `Общая шутка с пользователем: ${sharedJoke}.`,
      `shared-joke-${normalizedKey(sharedJoke)}`,
      0.7,
    );
  }

  const seenKeys = new Set<string>();
  return operations.filter((operation) => {
    if (operation.type !== "remember_user_fact") {
      return true;
    }
    if (seenKeys.has(operation.normalizedKey)) {
      return false;
    }
    seenKeys.add(operation.normalizedKey);
    return true;
  });
}
