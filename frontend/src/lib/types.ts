export type PetStage = "baby" | "teen" | "adult";
export type PetState = "idle" | "happy" | "sad" | "hungry";
export type PetLifeStage = "baby" | "teen" | "adult";
export type PetMood = "idle" | "happy" | "hungry" | "sad";
export type PetStatKey = "hunger" | "happiness" | "energy";
export type ConversationHappinessDelta = -80 | -60 | -40 | -20 | 0 | 30 | 100;
export type PetStatTickMap = Record<PetStatKey, string>;
export type PetStatZeroSinceMap = Partial<Record<PetStatKey, string>>;
export type PetBackgroundGenerationStatus = "running" | "succeeded" | "failed";
export type PetBackgroundGenerationPhase =
  | "generating_sad_image"
  | "generating_sad_video"
  | "generating_happy_image"
  | "generating_happy_video"
  | "generating_kandinsky"
  | "completed";
export type PetStatsPatch = {
  stats?: Partial<Record<PetStatKey, number>>;
  lastStatsTickAt?: string | null;
  lastStatTickAt?: Partial<PetStatTickMap> | null;
};

export type LocalPetStaticAssetSet = {
  assetSetId: string;
  generatedAt: string;
  images: {
    baby: Record<PetMood, string>;
    teen: Record<PetMood, string>;
    adult: Record<PetMood, string>;
  };
  videoUrl?: string;
};

export type LocalPetAssetSet = LocalPetStaticAssetSet & {
  characterImageUrl?: string;
  characterTemplate?: Record<string, unknown>;
  characterBible?: Record<string, unknown>;
  videoUrl?: string;
  sadVideoUrl?: string;
  happyVideoUrl?: string;
  generationJobId?: string;
  backgroundGenerationStatus?: PetBackgroundGenerationStatus;
  backgroundGenerationPhase?: PetBackgroundGenerationPhase;
  backgroundGenerationError?: string;
  comparisonGenerationError?: string;
  backgroundGenerationUpdatedAt?: string;
  blinkImageUrl?: string;
  spriteSheetUrl?: string;
  kandinskyAssets?: LocalPetStaticAssetSet;
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
  };
  assetSet?: LocalPetAssetSet;
};

export type OutfitExperienceReceipt = {
  requestKey: string;
  status: "charged" | "refunded";
  amount: number;
};

export type LocalPetStateV2 = {
  version: 2;
  petId: string;
  experience?: number;
  petTapProgress?: number;
  introductionPending?: true;
  name?: string;
  description: string;
  createdAt: string;
  updatedAt: string;
  lastInteractionAt: string;
  lastStatsTickAt: string;
  lastStatTickAt: PetStatTickMap;
  zeroStatSinceAt?: PetStatZeroSinceMap;
  diedAt?: string;
  stage: PetLifeStage;
  mood: PetMood;
  stats: {
    hunger: number;
    happiness: number;
    energy: number;
  };
  /** Local-only idempotency receipts; intentionally excluded from push snapshots. */
  travelImpactReceipts?: string[];
  /** Local-only charge/refund ledger for accepted outfit generation jobs. */
  outfitExperienceReceipts?: OutfitExperienceReceipt[];
  assetSet?: LocalPetAssetSet;
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
  videoUrl?: string;
  sadVideoUrl?: string;
  happyVideoUrl?: string;
  generationJobId?: string;
  backgroundGenerationStatus?: PetBackgroundGenerationStatus;
  backgroundGenerationPhase?: PetBackgroundGenerationPhase;
  backgroundGenerationError?: string;
  comparisonGenerationError?: string;
  backgroundGenerationUpdatedAt?: string;
  blinkImageUrl?: string;
  spriteSheetUrl?: string;
  kandinskyAssets?: LocalPetStaticAssetSet;
};

export type ChatPromptDebug = {
  label?: string;
  model?: string;
  messages?: Record<string, unknown>[];
  tools?: unknown;
  tool_choice?: unknown;
  response_format?: unknown;
};

export type LocalChatPetPatch = {
  name?: string;
};

export type LocalChatResponse = {
  reply: string;
  moodHint?: PetMood;
  happinessDelta?: ConversationHappinessDelta;
  complimentKey?: string;
  innerThought?: string;
  faceHint?: "happy" | "excited" | "curious" | "content" | "grumpy" | "sleepy";
  petPatch?: LocalChatPetPatch;
  storyLibraryPatch?: Record<string, unknown>;
  debug?: {
    usedFallback?: boolean;
    validationFlags?: string[];
    promptDebug?: ChatPromptDebug[];
    structuredReplyDebug?: Record<string, unknown>;
    liteToolCalls?: Record<string, unknown>[];
    liteOverlayPatch?: Record<string, unknown>;
    storyLibraryPatch?: Record<string, unknown>;
    storyLibraryDebug?: Record<string, unknown>;
    contextRoutingDebug?: Record<string, unknown>;
    memoryDebug?: Record<string, unknown>;
  };
};

export type LiteFactExtractionResponse = {
  liteOverlayPatch?: Record<string, unknown>;
  debug?: LocalChatResponse["debug"];
};

export type InteractiveTravelAdviceAssessment = "helpful" | "harmful" | "ambiguous";
export type InteractiveTravelReactionTone =
  | "enthusiastic"
  | "confused"
  | "worried"
  | "amused"
  | "indignant"
  | "determined"
  | "surprised";

export type InteractiveTravelStatImpact = {
  stat: PetStatKey;
  amount: number;
  reason: string;
};

export type InteractiveTravelResult = {
  text: string;
  adviceAssessment: InteractiveTravelAdviceAssessment;
  reaction: string;
  reactionTone: InteractiveTravelReactionTone;
  consequence: string;
  outcomeValence: "positive" | "negative";
  experienceGained?: number;
  statImpacts: InteractiveTravelStatImpact[];
};

export type InteractiveTravelIntroReaction = {
  text: string;
  tone: InteractiveTravelReactionTone;
};

export type InteractiveTravelTransition = {
  elapsedHours: number;
  summary: string;
  departureHook?: string;
  continuityAnchor?: string;
};

export type InteractiveTravelPart = {
  partNumber: number;
  title: string;
  storyText: string;
  transition?: InteractiveTravelTransition;
  challenge: string;
  actionSuggestions: string[];
  backgroundImageUrl?: string;
  backgroundVideoUrl?: string;
  answer?: string;
  result?: InteractiveTravelResult;
};

export type InteractiveTravelTaskPlan = {
  taskId: string;
  leadIn: string;
  situation: string;
  question: string;
  choices: [string, string, string, string];
  correctChoice: string;
  explanation?: string;
  choiceOutcomes?: [string, string, string, string];
};

export type InteractiveTravelPlan = {
  version: "task-bank-location-v4";
  tasks: [
    InteractiveTravelTaskPlan,
    InteractiveTravelTaskPlan,
    InteractiveTravelTaskPlan,
    InteractiveTravelTaskPlan,
  ];
};

export type InteractiveTravelState = {
  travelId: string;
  generatedAt: string;
  destination: string;
  overallTitle: string;
  introReaction?: InteractiveTravelIntroReaction;
  generationStatus?: "generating" | "ready" | "failed";
  generationError?: string;
  plan: InteractiveTravelPlan | null;
  parts: InteractiveTravelPart[];
  completed: boolean;
  outcomeValence?: "positive" | "negative";
  statImpact?: InteractiveTravelStatImpact;
};

export type InteractiveTravelResponse = {
  travel: InteractiveTravelState;
  debug?: LocalChatResponse["debug"];
};

export type InteractiveTravelDemoResponse = {
  demoId: string;
  travel: InteractiveTravelState;
};

export type InteractiveTravelSuggestionsResponse = {
  destinations: string[];
  debug?: LocalChatResponse["debug"];
};

export type InteractiveTravelIllustrationResponse = {
  partNumber: number;
  imageUrl: string;
};

export type InteractiveTravelAnimationResponse = {
  partNumber: number;
  videoUrl: string;
};

export type TravelVideoPrototypeStatus =
  | "queued"
  | "writing"
  | "illustrating"
  | "animating"
  | "ready"
  | "failed";

export type TravelVideoPrototype = {
  jobId: string;
  status: TravelVideoPrototypeStatus;
  prompt: string;
  title?: string;
  scenario?: string;
  imageUrl?: string;
  videoUrl?: string;
  error?: string;
  createdAt: string;
  updatedAt: string;
};
