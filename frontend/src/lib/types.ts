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
