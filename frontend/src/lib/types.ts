export type PetStatus = "generating" | "ready" | "failed";
export type PetStage = "baby" | "teen" | "adult";
export type PetState = "idle" | "happy" | "sad" | "hungry";
export type MessageRole = "user" | "assistant";
export type PetLifeStage = "baby" | "teen" | "adult";
export type PetMood = "idle" | "happy" | "hungry" | "sad";
export type AdminGenerateMode = "profile_only" | "full_assets";

export type AnonymousUser = {
  id: string;
  created_at: string;
};

export type CreatePetResponse = {
  id: string;
  status: PetStatus;
};

export type PetImage = {
  stage: PetStage;
  state: PetState;
  image_url: string;
};

export type Pet = {
  id: string;
  status: PetStatus;
  current_stage: PetStage;
  current_state: PetState;
  hunger: number;
  mood: number;
  image_url: string | null;
  images: PetImage[];
  created_at: string;
  generation_error: string | null;
  intro_message: ChatMessage | null;
};

export type FeedResponse = {
  id: string;
  hunger: number;
  mood: number;
  current_stage: PetStage;
  current_state: PetState;
  image_url: string | null;
};

export type ChatMessage = {
  id: string;
  role: MessageRole;
  content: string;
  created_at: string;
};

export type MessagesResponse = {
  messages: ChatMessage[];
};

export type ChatVisualContext = {
  selected_stage?: PetStage;
  selected_state?: PetState;
};

export type ChatResponse = {
  reply: string;
  mood: number;
  hunger: number;
  current_stage: PetStage;
  current_state: PetState;
  image_url: string | null;
};

export type LocalPetAssetSet = {
  assetSetId: string;
  generatedAt: string;
  characterBible?: Record<string, unknown>;
  images: {
    baby: Record<PetMood, string>;
    teen: Record<PetMood, string>;
    adult: Record<PetMood, string>;
  };
  spriteSheetUrl?: string;
};

export type CanonMemoryFactType =
  | "world_fact"
  | "home_fact"
  | "friend_fact"
  | "family_fact"
  | "origin_fact"
  | "preference_fact"
  | "fear_fact"
  | "habit_fact"
  | "voice_fact"
  | "milestone";

export type CanonMemoryFact = {
  id: string;
  type: CanonMemoryFactType;
  text: string;
  source: "model" | "user" | "system";
  confidence: number;
  importance: number;
  useCount: number;
  decayScore: number;
  createdAt: string;
  updatedAt: string;
  lastUsedAt?: string;
  lastReinforcedAt?: string;
  relatedThreadId?: string;
  pinned?: boolean;
};

export type RelationshipEvent = {
  id: string;
  text: string;
  importance: number;
  createdAt: string;
  updatedAt: string;
};

export type UserFact = {
  id: string;
  text: string;
  confidence: number;
  importance: number;
  createdAt: string;
  updatedAt: string;
  lastUsedAt?: string;
};

export type RelationshipMemory = {
  userName?: string;
  preferredAddress?: string;
  trust: number;
  attachment: number;
  familiarity: number;
  sharedEvents: RelationshipEvent[];
  userFacts: UserFact[];
  boundaries: string[];
  lastWarmMomentAt?: string;
};

export type ConversationThread = {
  id: string;
  topic: string;
  summary: string;
  status: "open" | "paused" | "resolved";
  priority: number;
  createdAt: string;
  updatedAt: string;
  lastMentionedAt?: string;
  suggestedFollowUp?: string;
  lastQuestionAskedAt?: string;
};

export type ReflectionMemory = {
  id: string;
  text: string;
  scope: "self" | "user" | "relationship" | "world";
  sourceEventIds: string[];
  confidence: number;
  importance: number;
  createdAt: string;
  updatedAt: string;
  lastUsedAt?: string;
};

export type ActiveGoal = {
  id: string;
  kind:
    | "learn_about_user"
    | "share_lore"
    | "seek_care"
    | "return_to_thread"
    | "play"
    | "comfort_user";
  text: string;
  priority: number;
  status: "active" | "paused" | "completed" | "expired";
  createdAt: string;
  updatedAt: string;
  expiresAt?: string;
  relatedThreadId?: string;
};

export type DevelopmentState = {
  trust: number;
  attachment: number;
  curiosity: number;
  confidence: number;
  loneliness: number;
  playfulness: number;
  lastDevelopmentReason?: string;
};

export type PetEvent = {
  id: string;
  kind:
    | "user_message"
    | "pet_reply"
    | "memory_accepted"
    | "relationship"
    | "development"
    | "thread"
    | "goal"
    | "care"
    | "reflection";
  text: string;
  importance: number;
  createdAt: string;
  relatedMemoryId?: string;
};

export type RejectedMemoryCandidate = {
  id: string;
  type: CanonMemoryFactType | "user_fact" | "relationship_event";
  text: string;
  reason: string;
  confidence: number;
  importance: number;
  createdAt: string;
};

export type PetMemoryStateV1 = {
  schemaVersion: 1;
  canon: CanonMemoryFact[];
  relationship: RelationshipMemory;
  threads: ConversationThread[];
  reflections: ReflectionMemory[];
  activeGoals: ActiveGoal[];
  development: DevelopmentState;
  events: PetEvent[];
  rejectedCandidates: RejectedMemoryCandidate[];
};

export type RelationshipMemoryPatch = {
  userName?: string;
  clearUserName?: boolean;
  preferredAddress?: string;
  clearPreferredAddress?: boolean;
  trust?: number;
  attachment?: number;
  familiarity?: number;
  sharedEventUpserts?: RelationshipEvent[];
  sharedEventDeletes?: string[];
  userFactUpserts?: UserFact[];
  userFactDeletes?: string[];
  boundaryUpserts?: string[];
  boundaryDeletes?: string[];
  lastWarmMomentAt?: string;
};

export type AppliedDevelopmentPatch = Partial<DevelopmentState>;

export type PetMemoryPatch = {
  canonUpserts?: CanonMemoryFact[];
  canonDeletes?: string[];
  relationshipPatch?: RelationshipMemoryPatch;
  threadUpserts?: ConversationThread[];
  threadDeletes?: string[];
  reflectionUpserts?: ReflectionMemory[];
  reflectionDeletes?: string[];
  activeGoalUpserts?: ActiveGoal[];
  activeGoalDeletes?: string[];
  developmentPatch?: AppliedDevelopmentPatch;
  eventAppends?: PetEvent[];
  rejectedCandidateAppends?: RejectedMemoryCandidate[];
};

export type LocalPetStateV1 = {
  version: 1;
  petId: string;
  name?: string;
  description: string;
  createdAt: string;
  updatedAt: string;
  lastInteractionAt: string;
  stage: PetLifeStage;
  mood: PetMood;
  stats: {
    hunger: number;
    happiness: number;
    energy: number;
    cleanliness: number;
  };
  assetSet?: LocalPetAssetSet;
  loreMemories?: string[];
};

export type LocalPetStateV2 = {
  version: 2;
  petId: string;
  name?: string;
  description: string;
  createdAt: string;
  updatedAt: string;
  lastInteractionAt: string;
  stage: PetLifeStage;
  mood: PetMood;
  stats: {
    hunger: number;
    happiness: number;
    energy: number;
    cleanliness: number;
  };
  assetSet?: LocalPetAssetSet;
  memory: PetMemoryStateV1;
  loreMemories?: string[];
};

export type LocalPetState = LocalPetStateV2;

export type LocalChatMessage = {
  id: string;
  role: "user" | "pet";
  text: string;
  createdAt: string;
};

export type LocalChatHistoryV1 = {
  version: 1;
  messages: LocalChatMessage[];
};

export type GeneratePetResponse = {
  assetSetId: string;
  generatedAt: string;
  characterBible?: Record<string, unknown>;
  images: LocalPetAssetSet["images"];
  spriteSheetUrl?: string;
};

export type LocalChatResponse = {
  reply: string;
  moodHint?: PetMood;
  loreMemoriesToSave?: string[];
  memoryPatch?: PetMemoryPatch;
  debug?: {
    usedFallback?: boolean;
    validationFlags?: string[];
    rejectedMemoryCount?: number;
    proactivityFlags?: string[];
  };
};

export type AdminGenerateOneRequest = {
  description: string;
  mode: AdminGenerateMode;
  slotId?: string;
  includeDebugPrompts?: boolean;
  includeSelfIntroBenchmark?: boolean;
  includeConversationBenchmark?: boolean;
};

export type AdminBenchmarkTurn = {
  question: string;
  reply: string;
  moodHint?: PetMood | null;
  usedFallback: boolean;
  validationFlags: string[];
  qualityScore?: number | null;
  qualityPassed?: boolean | null;
  qualityFlags?: string[];
};

export type AdminBenchmarkResponse = AdminBenchmarkTurn & {
  turns?: AdminBenchmarkTurn[] | null;
};

export type AdminDebugMessage = {
  role: string;
  content: string;
};

export type AdminGenerateDebug = {
  chatModel: string;
  imageModel?: string | null;
  imageSize?: string | null;
  imageQuality?: string | null;
  characterBiblePrompt?: string | null;
  spriteSheetPrompt?: string | null;
  selfIntroBenchmarkMessages?: AdminDebugMessage[] | null;
};

export type AdminGenerateOneResponse = {
  slotId?: string;
  description: string;
  mode: AdminGenerateMode;
  status: "ready";
  generatedAt: string;
  durationMs: number;
  assetSetId?: string | null;
  spriteSheetUrl?: string | null;
  images?: LocalPetAssetSet["images"] | null;
  characterBible: Record<string, unknown>;
  benchmark?: AdminBenchmarkResponse | null;
  debug?: AdminGenerateDebug | null;
};

export type AdminGenerateError = {
  slotId?: string | null;
  description: string;
  status: "failed";
  code: string;
  message: string;
  durationMs: number;
};

export type AdminGenerationLabStatus = {
  status: string;
};
