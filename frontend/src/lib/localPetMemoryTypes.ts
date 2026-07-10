export type UserMemoryKind =
  | "user_fact"
  | "preference"
  | "event"
  | "deadline"
  | "relationship"
  | "routine"
  | "goal"
  | "promise"
  | "emotion"
  | "boundary";

export type LocalPetLearningStatus = "pending" | "promoted" | "pruned";

export type LocalPetMemoryClass = "core" | "fact" | "episode";

export type LocalPetLearning = {
  id: string;
  status: LocalPetLearningStatus;
  observation: string;
  patternKey?: string;
  kind?: UserMemoryKind;
  confidence: number;
  importance: number;
  recurrenceCount: number;
  firstSeenAt: string;
  lastSeenAt: string;
  sourceMessageIds: string[];
  occurredAt?: string;
  dueAt?: string;
};

export type LocalPetUserMemory = {
  id: string;
  kind: UserMemoryKind;
  text: string;
  normalizedKey: string;
  confidence: number;
  importance: number;
  memoryClass: LocalPetMemoryClass;
  recordedAt: string;
  occurredAt?: string;
  createdAt: string;
  updatedAt: string;
  lastMentionedAt?: string;
  mentionCount: number;
  sourceLearningIds: string[];
  dueAt?: string;
  expiresAt?: string;
  tags: string[];
};

export type LocalPetProactiveLogItem = {
  id: string;
  createdAt: string;
  memoryIds: string[];
  text: string;
  deliveredVia: "local_open" | "telegram_push";
};

export type LocalChatMemoryEpisode = {
  id: string;
  messages: {
    role: "user" | "pet";
    text: string;
    createdAt?: string;
  }[];
};

export type LocalPetMemoryStateV1 = {
  version: 2;
  petId: string;
  createdAt: string;
  updatedAt: string;
  lastExtractionAt?: string;
  lastConsolidationAt?: string;
  lastProactiveAt?: string;
  userProfile?: string;
  summary?: string;
  learnings: LocalPetLearning[];
  memories: LocalPetUserMemory[];
  proactiveLog: LocalPetProactiveLogItem[];
};

export type MemoryOperation =
  | {
      type: "capture_learning";
      observation: string;
      patternKey?: string;
      kind?: UserMemoryKind;
      confidence: number;
      importance: number;
      occurredAt?: string;
      dueAt?: string;
    }
  | {
      type: "remember_user_fact";
      kind: UserMemoryKind;
      text: string;
      normalizedKey: string;
      confidence: number;
      importance: number;
      occurredAt?: string;
      dueAt?: string;
      expiresAt?: string;
      tags?: string[];
    }
  | {
      type: "replace_user_fact";
      kind: UserMemoryKind;
      text: string;
      normalizedKey: string;
      confidence: number;
      importance: number;
      occurredAt?: string;
      dueAt?: string;
      expiresAt?: string;
      tags?: string[];
    }
  | {
      type: "forget_user_fact";
      normalizedKey?: string;
      matchText?: string;
    };

export type MemoryConsolidationOperation =
  | {
      type: "promote_learning";
      learningId: string;
      memory: Omit<Extract<MemoryOperation, { type: "remember_user_fact" }>, "type">;
    }
  | {
      type: "prune_learning";
      learningId: string;
      reason?: string;
    }
  | {
      type: "rewrite_summary";
      content: string;
    }
  | {
      type: "rewrite_user_profile";
      content: string;
    };

export type LocalPetMemoryContext = {
  summary?: string;
  userProfile?: string;
  relevantMemories: {
    id: string;
    kind: UserMemoryKind;
    text: string;
    memoryClass?: LocalPetMemoryClass;
    recordedAt?: string;
    occurredAt?: string;
    lastMentionedAt?: string;
    dueAt?: string;
  }[];
  episodes?: LocalChatMemoryEpisode[];
  proactiveCandidate?: {
    memoryIds: string[];
    episodeIds?: string[];
    reason: string;
  };
};

export type MemoryExtractionResponse = {
  operations: MemoryOperation[];
  debug?: {
    promptDebug?: import("./types").ChatPromptDebug[];
    memoryDebug?: Record<string, unknown>;
  };
};

export type MemoryConsolidationResponse = {
  operations: MemoryConsolidationOperation[];
  debug?: MemoryExtractionResponse["debug"];
};
