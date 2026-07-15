import type { LocalPetMemoryContext } from "./localPetMemoryTypes";
import { isSafeJsonRecord, safeJsonClone } from "./safeJsonValue";

export const PUSH_SNAPSHOT_BODY_BUDGET_BYTES = 240_000;

type SnapshotHistoryItem = {
  role: "user" | "pet";
  text: string;
  createdAt?: string;
};

type SnapshotPetContext = {
  name?: string;
  description: string;
  characterBible?: Record<string, unknown>;
  stage: string;
  mood: string;
  stats: { hunger: number; happiness: number; energy: number };
  assetImages?: unknown;
};

export type PushSnapshotPayload = {
  petId: string;
  snapshotWriterId?: string;
  snapshotRevision?: number;
  createdAt?: string;
  updatedAt?: string;
  lastStatsTickAt?: string;
  lastStatTickAt?: unknown;
  zeroStatSinceAt?: unknown;
  diedAt?: string;
  timezone?: string;
  memoryContext?: LocalPetMemoryContext;
  history: SnapshotHistoryItem[];
  recentAmbientReplies: string[];
  pet: SnapshotPetContext;
};

type CompactionStage = {
  characterBibleBytes: number;
  historyItems: number;
  historyTextLength: number;
  recentReplyItems: number;
  recentReplyTextLength: number;
  relevantMemoryItems: number;
  episodeItems: number;
  episodeMessageItems: number;
  episodeMessageTextLength: number;
};

const COMPACTION_STAGES: CompactionStage[] = [
  {
    characterBibleBytes: 96_000,
    historyItems: 10,
    historyTextLength: 1_500,
    recentReplyItems: 8,
    recentReplyTextLength: 400,
    relevantMemoryItems: 5,
    episodeItems: 2,
    episodeMessageItems: 5,
    episodeMessageTextLength: 800,
  },
  {
    characterBibleBytes: 48_000,
    historyItems: 8,
    historyTextLength: 1_000,
    recentReplyItems: 6,
    recentReplyTextLength: 300,
    relevantMemoryItems: 5,
    episodeItems: 2,
    episodeMessageItems: 3,
    episodeMessageTextLength: 600,
  },
  {
    characterBibleBytes: 20_000,
    historyItems: 6,
    historyTextLength: 700,
    recentReplyItems: 4,
    recentReplyTextLength: 240,
    relevantMemoryItems: 4,
    episodeItems: 1,
    episodeMessageItems: 3,
    episodeMessageTextLength: 500,
  },
  {
    characterBibleBytes: 8_000,
    historyItems: 4,
    historyTextLength: 500,
    recentReplyItems: 2,
    recentReplyTextLength: 180,
    relevantMemoryItems: 3,
    episodeItems: 0,
    episodeMessageItems: 0,
    episodeMessageTextLength: 0,
  },
];

function utf8ByteLength(value: string): number {
  let bytes = 0;
  for (const character of value) {
    const codePoint = character.codePointAt(0) ?? 0;
    bytes += codePoint <= 0x7f ? 1 : codePoint <= 0x7ff ? 2 : codePoint <= 0xffff ? 3 : 4;
  }
  return bytes;
}

export function serializedJsonBytes(value: unknown): number {
  try {
    const serialized = JSON.stringify(value);
    return serialized === undefined ? Number.POSITIVE_INFINITY : utf8ByteLength(serialized);
  } catch {
    return Number.POSITIVE_INFINITY;
  }
}

function clippedText(value: string, maxLength: number): string {
  return value.trim().slice(0, maxLength);
}

function cloneMemoryContext(memoryContext: LocalPetMemoryContext | undefined) {
  if (!memoryContext) {
    return undefined;
  }
  return {
    ...memoryContext,
    relevantMemories: memoryContext.relevantMemories.map((memory) => ({ ...memory })),
    episodes: memoryContext.episodes?.map((episode) => ({
      ...episode,
      messages: episode.messages.map((message) => ({ ...message })),
    })),
    proactiveCandidate: memoryContext.proactiveCandidate
      ? {
          ...memoryContext.proactiveCandidate,
          memoryIds: [...memoryContext.proactiveCandidate.memoryIds],
          episodeIds: memoryContext.proactiveCandidate.episodeIds
            ? [...memoryContext.proactiveCandidate.episodeIds]
            : undefined,
        }
      : undefined,
  };
}

function compactCharacterBible(
  characterBible: Record<string, unknown> | undefined,
  byteBudget: number,
): Record<string, unknown> | undefined {
  if (!characterBible || byteBudget <= 0) {
    return undefined;
  }
  const safelyCloned = safeJsonClone(characterBible, {
    maxDepth: 12,
    maxNodes: 1_500,
    maxStringLength: 2_000,
    maxArrayLength: 80,
    maxObjectKeys: 100,
  });
  if (!isSafeJsonRecord(safelyCloned)) {
    return undefined;
  }
  if (serializedJsonBytes(safelyCloned) <= byteBudget) {
    return safelyCloned;
  }

  const compacted: Record<string, unknown> = Object.create(null) as Record<
    string,
    unknown
  >;
  for (const [key, value] of Object.entries(safelyCloned)) {
    const candidate = { ...compacted, [key]: value };
    if (serializedJsonBytes(candidate) <= byteBudget) {
      compacted[key] = value;
    }
  }
  return Object.keys(compacted).length ? compacted : undefined;
}

function compactMemoryContext(
  memoryContext: LocalPetMemoryContext | undefined,
  stage: CompactionStage,
): LocalPetMemoryContext | undefined {
  if (!memoryContext) {
    return undefined;
  }
  const episodes = (memoryContext.episodes ?? [])
    .slice(0, stage.episodeItems)
    .map((episode) => ({
      ...episode,
      messages: episode.messages
        .slice(-stage.episodeMessageItems)
        .map((message) => ({
          ...message,
          text: clippedText(message.text, stage.episodeMessageTextLength),
        }))
        .filter((message) => message.text),
    }))
    .filter((episode) => episode.messages.length);
  return {
    ...memoryContext,
    relevantMemories: memoryContext.relevantMemories
      .slice(0, stage.relevantMemoryItems)
      .map((memory) => ({ ...memory })),
    episodes: episodes.length ? episodes : undefined,
    proactiveCandidate: memoryContext.proactiveCandidate
      ? {
          ...memoryContext.proactiveCandidate,
          memoryIds: [...memoryContext.proactiveCandidate.memoryIds],
          episodeIds: memoryContext.proactiveCandidate.episodeIds
            ? [...memoryContext.proactiveCandidate.episodeIds]
            : undefined,
        }
      : undefined,
  };
}

function compactForStage(
  payload: PushSnapshotPayload,
  characterBible: Record<string, unknown> | undefined,
  memoryContext: LocalPetMemoryContext | undefined,
  history: SnapshotHistoryItem[],
  recentAmbientReplies: string[],
  stage: CompactionStage,
): PushSnapshotPayload {
  const compactedBible = compactCharacterBible(characterBible, stage.characterBibleBytes);
  return {
    ...payload,
    pet: {
      ...payload.pet,
      ...(compactedBible ? { characterBible: compactedBible } : { characterBible: undefined }),
    },
    memoryContext: compactMemoryContext(memoryContext, stage),
    history: history.slice(-stage.historyItems).map((message) => ({
      ...message,
      text: clippedText(message.text, stage.historyTextLength),
    })),
    recentAmbientReplies: recentAmbientReplies
      .slice(-stage.recentReplyItems)
      .map((reply) => clippedText(reply, stage.recentReplyTextLength))
      .filter(Boolean),
  };
}

function optionalText(value: unknown, maxLength: number): string | undefined {
  return typeof value === "string" && value.trim()
    ? value.trim().slice(0, maxLength)
    : undefined;
}

function safeStat(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(0, Math.min(100, Math.round(value)))
    : 0;
}

function timestampMap(value: unknown): Record<string, string> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  const source = value as Record<string, unknown>;
  const result: Record<string, string> = {};
  for (const key of ["hunger", "happiness", "energy"]) {
    const timestamp = optionalText(source[key], 80);
    if (timestamp) {
      result[key] = timestamp;
    }
  }
  return result;
}

function minimalPayload(payload: PushSnapshotPayload): PushSnapshotPayload {
  const name = optionalText(payload.pet.name, 80);
  return {
    petId: optionalText(payload.petId, 120) ?? "local-pet",
    snapshotWriterId: optionalText(payload.snapshotWriterId, 120),
    snapshotRevision:
      typeof payload.snapshotRevision === "number" && Number.isSafeInteger(payload.snapshotRevision)
        ? Math.max(1, payload.snapshotRevision)
        : undefined,
    createdAt: optionalText(payload.createdAt, 80),
    updatedAt: optionalText(payload.updatedAt, 80),
    lastStatsTickAt: optionalText(payload.lastStatsTickAt, 80),
    lastStatTickAt: timestampMap(payload.lastStatTickAt),
    zeroStatSinceAt: timestampMap(payload.zeroStatSinceAt),
    diedAt: optionalText(payload.diedAt, 80),
    timezone: optionalText(payload.timezone, 80),
    pet: {
      ...(name ? { name } : {}),
      description: optionalText(payload.pet.description, 300) ?? "Персонаж",
      stage: payload.pet.stage === "teen" || payload.pet.stage === "adult" ? payload.pet.stage : "baby",
      mood:
        payload.pet.mood === "happy"
        || payload.pet.mood === "hungry"
        || payload.pet.mood === "sad"
          ? payload.pet.mood
          : "idle",
      stats: {
        hunger: safeStat(payload.pet.stats?.hunger),
        happiness: safeStat(payload.pet.stats?.happiness),
        energy: safeStat(payload.pet.stats?.energy),
      },
    },
    history: [],
    recentAmbientReplies: [],
  };
}

export function compactPushSnapshotPayload(
  payload: PushSnapshotPayload,
): PushSnapshotPayload {
  const history = payload.history.map((message) => ({ ...message }));
  const recentAmbientReplies = [...payload.recentAmbientReplies];
  const memoryContext = cloneMemoryContext(payload.memoryContext);
  const characterBible = compactCharacterBible(
    payload.pet.characterBible,
    PUSH_SNAPSHOT_BODY_BUDGET_BYTES,
  );
  const clonedPayload: PushSnapshotPayload = {
    ...payload,
    pet: {
      ...payload.pet,
      ...(characterBible ? { characterBible } : { characterBible: undefined }),
    },
    history,
    recentAmbientReplies,
    memoryContext,
  };
  if (serializedJsonBytes(clonedPayload) <= PUSH_SNAPSHOT_BODY_BUDGET_BYTES) {
    return clonedPayload;
  }

  for (const stage of COMPACTION_STAGES) {
    const compacted = compactForStage(
      clonedPayload,
      characterBible,
      memoryContext,
      history,
      recentAmbientReplies,
      stage,
    );
    if (serializedJsonBytes(compacted) <= PUSH_SNAPSHOT_BODY_BUDGET_BYTES) {
      return compacted;
    }
  }

  return minimalPayload(clonedPayload);
}
