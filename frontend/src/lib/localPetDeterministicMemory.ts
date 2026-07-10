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
  const rawForgetText = safeValue(
    text.match(/(?:^|\b)(?:забудь|не запоминай|больше не помни)(?:\s+про|\s+о|[:,])?\s*(.+)$/iu)?.[1]
      ?? "",
    140,
  );
  const forgetAll = /^(?:вс[её]\s+(?:обо\s+мне|про\s+меня)|всю\s+память\s+обо\s+мне)$/iu.test(
    rawForgetText,
  );
  const forgetName = /^(?:мо[её]\s+имя|как\s+меня\s+зовут)$/iu.test(rawForgetText);
  const forgetText = safeValue(
    rawForgetText.replace(
      /^(?:то,?\s+)?(?:что\s+)?(?:я\s+)?(?:больше\s+|теперь\s+)?(?:не\s+)?(?:люблю|предпочитаю|мне\s+нравится|мне\s+нравятся)\s+/iu,
      "",
    ),
    140,
  );
  if (forgetAll) {
    operations.push({ type: "forget_user_fact", normalizedKey: "*" });
  } else if (forgetName) {
    operations.push({ type: "forget_user_fact", normalizedKey: "user-name" });
  } else if (forgetText) {
    operations.push({
      type: "forget_user_fact",
      matchText: forgetText,
    });
  }
  const userName = firstName(
    text.match(
      /(?:^|\b)(?:меня\s+(?:теперь\s+|сейчас\s+)?зовут|зови меня|можешь звать меня)\s+(.+)$/iu,
    )?.[1] ?? "",
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

  const preferenceCorrection = text.match(
    /(?:раньше|до этого)\s+(?:я\s+)?(?:любил|любила|предпочитал|предпочитала)\s+(.+?)[,;]\s*(?:а\s+)?(?:теперь|сейчас)\s+(?:я\s+)?(?:люблю|предпочитаю)\s+(.+)$/iu,
  );
  const previousPreference = safeValue(preferenceCorrection?.[1] ?? "", 140);
  const nextPreference = safeValue(preferenceCorrection?.[2] ?? "", 140);
  if (previousPreference && nextPreference) {
    operations.push({
      type: "forget_user_fact",
      matchText: previousPreference,
    });
    remember(
      operations,
      "preference",
      `Пользователь любит ${nextPreference}.`,
      `preference-${normalizedKey(nextPreference)}`,
      0.85,
    );
  }

  const liked = safeValue(
    text.match(
      /(?:^|\b)(?:(?:теперь|сейчас)\s+)?(?:я люблю|мне нравятся|мне нравится)\s+(.+)$/iu,
    )?.[1] ?? "",
    140,
  );
  if (liked && !preferenceCorrection) {
    remember(
      operations,
      "preference",
      `Пользователь любит ${liked}.`,
      `preference-${normalizedKey(liked)}`,
      0.75,
    );
  }

  const disliked = safeValue(
    text.match(/(?:^|\b)я\s+(?:(?:больше|теперь|сейчас)\s+)?не люблю\s+(.+)$/iu)?.[1] ?? "",
    140,
  );
  if (disliked) {
    remember(
      operations,
      "preference",
      `Пользователь не любит ${disliked}.`,
      `preference-${normalizedKey(disliked)}`,
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
    if (operation.type !== "remember_user_fact" && operation.type !== "replace_user_fact") {
      return true;
    }
    if (seenKeys.has(operation.normalizedKey)) {
      return false;
    }
    seenKeys.add(operation.normalizedKey);
    return true;
  });
}
