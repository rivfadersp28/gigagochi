export type PetStage = "baby" | "teen" | "adult";
export type PetState = "idle" | "happy" | "sad" | "hungry";
export type PetLifeStage = "baby" | "teen" | "adult";
export type PetMood = "idle" | "happy" | "hungry" | "sad";

export type LocalPetAssetSet = {
  assetSetId: string;
  generatedAt: string;
  characterTemplate?: Record<string, unknown>;
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
  innerThought?: string;
  faceHint?: "happy" | "excited" | "curious" | "content" | "grumpy" | "sleepy";
  petPatch?: LocalChatPetPatch;
  debug?: {
    usedFallback?: boolean;
    validationFlags?: string[];
    promptDebug?: ChatPromptDebug[];
    liteToolCalls?: Record<string, unknown>[];
    liteOverlayPatch?: Record<string, unknown>;
    storyLibraryPatch?: Record<string, unknown>;
    storyLibraryDebug?: Record<string, unknown>;
    memoryDebug?: Record<string, unknown>;
  };
};

export type LiteFactExtractionResponse = {
  liteOverlayPatch?: Record<string, unknown>;
  debug?: LocalChatResponse["debug"];
};

export type TravelStoryScene = {
  index: number;
  arc: "beginning" | "exploration" | "discovery" | "reward" | "final";
  title: string;
  text: string;
  visualBrief: string;
};

export type TravelStory = {
  title: string;
  summary: string;
  scenes: TravelStoryScene[];
};

export type TravelSceneImage = {
  sceneIndex: number;
  imageUrl: string;
};

export type GenerateTravelResponse = {
  travelId: string;
  generatedAt: string;
  story: TravelStory;
  images: TravelSceneImage[];
  debug?: LocalChatResponse["debug"];
};
