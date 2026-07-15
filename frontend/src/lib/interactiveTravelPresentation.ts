import { splitPetReplySentences } from "./petReplySentences";
import { TEST_PET_ASSET_SET_ID } from "./testPetFixture";
import { assetSetForPet } from "./petAssetSet";
import type {
  InteractiveTravelPart,
  InteractiveTravelResult,
  InteractiveTravelState,
  LocalPetState,
} from "./types";

export const INTERACTIVE_TRAVEL_BUBBLE_MAX_CHARACTERS = 80;
const CHARACTER_FOREGROUND_CACHE_VERSION = "20260714-1";

export type ResolvedInteractiveTravelPart = InteractiveTravelPart & {
  result: InteractiveTravelResult;
};

function graphemes(text: string): string[] {
  if (typeof Intl.Segmenter === "function") {
    return Array.from(
      new Intl.Segmenter("ru", { granularity: "grapheme" }).segment(text),
      ({ segment }) => segment,
    );
  }
  return Array.from(text);
}

function splitOversizedSentence(sentence: string, maxCharacters: number): string[] {
  if (graphemes(sentence).length <= maxCharacters) {
    return [sentence];
  }

  const portions: string[] = [];
  let current = "";
  for (const word of sentence.split(/\s+/u)) {
    const candidate = current ? `${current} ${word}` : word;
    if (graphemes(candidate).length <= maxCharacters) {
      current = candidate;
      continue;
    }
    if (current) {
      portions.push(current);
      current = "";
    }

    const wordUnits = graphemes(word);
    while (wordUnits.length > maxCharacters) {
      portions.push(wordUnits.splice(0, maxCharacters).join(""));
    }
    current = wordUnits.join("");
  }
  if (current) {
    portions.push(current);
  }
  return portions;
}

type InteractiveTravelPartWithBackground = InteractiveTravelPart & {
  backgroundImageUrl?: string;
  backgroundVideoUrl?: string;
};

export function splitInteractiveTravelText(
  text: string,
  maxCharacters = INTERACTIVE_TRAVEL_BUBBLE_MAX_CHARACTERS,
): string[] {
  if (!Number.isFinite(maxCharacters) || maxCharacters < 1) {
    return [];
  }

  const boundedMaxCharacters = Math.max(1, Math.floor(maxCharacters));
  return splitPetReplySentences(text).flatMap((sentence) =>
    splitOversizedSentence(sentence, boundedMaxCharacters),
  );
}

export function patchInteractiveTravelPartVideo(
  travel: InteractiveTravelState,
  partNumber: number,
  backgroundVideoUrl: string,
): InteractiveTravelState {
  let changed = false;
  const parts = travel.parts.map((part) => {
    if (part.partNumber !== partNumber) {
      return part;
    }
    const current = part as InteractiveTravelPartWithBackground;
    if (current.backgroundVideoUrl === backgroundVideoUrl) {
      return part;
    }
    changed = true;
    return { ...part, backgroundVideoUrl };
  });
  return changed ? { ...travel, parts } : travel;
}

export function storyPortions(part: InteractiveTravelPart): string[] {
  return splitInteractiveTravelText(part.storyText);
}

export function resultPortions(part: InteractiveTravelPart): string[] {
  if (!part.result) {
    return [];
  }

  return [part.result.reaction, part.result.text].flatMap((text) =>
    splitInteractiveTravelText(text),
  );
}

export function currentPendingPart(
  travel: InteractiveTravelState,
): InteractiveTravelPart | null {
  return travel.parts.find((part) => !part.result) ?? null;
}

export function nextResolvedPart(
  travel: InteractiveTravelState,
  afterPartNumber = 0,
): ResolvedInteractiveTravelPart | null {
  const part = travel.parts.find(
    (candidate) => candidate.partNumber > afterPartNumber && Boolean(candidate.result),
  );
  return part?.result ? (part as ResolvedInteractiveTravelPart) : null;
}

export function interactiveTravelCharacterImageUrl(pet: LocalPetState): string | null {
  const assetSet = assetSetForPet(pet);
  if (assetSet?.characterImageUrl) {
    return assetSet.characterImageUrl;
  }
  if (assetSet?.assetSetId === TEST_PET_ASSET_SET_ID) {
    return "/test-pet/character-transparent.png";
  }

  const idleUrl = assetSet?.images[pet.stage]?.idle;
  if (!idleUrl) {
    return null;
  }

  const suffixIndex = idleUrl.search(/[?#]/u);
  const path = suffixIndex >= 0 ? idleUrl.slice(0, suffixIndex) : idleUrl;
  const suffix = suffixIndex >= 0 ? idleUrl.slice(suffixIndex) : "";
  const slashIndex = path.lastIndexOf("/");
  if (slashIndex < 0) {
    return null;
  }

  const foregroundUrl = `${path.slice(0, slashIndex + 1)}teen-idle-foreground.png${suffix}`;
  const hashIndex = foregroundUrl.indexOf("#");
  const hash = hashIndex >= 0 ? foregroundUrl.slice(hashIndex) : "";
  const urlWithoutHash = hashIndex >= 0 ? foregroundUrl.slice(0, hashIndex) : foregroundUrl;
  const separator = urlWithoutHash.includes("?") ? "&" : "?";
  return `${urlWithoutHash}${separator}travel_foreground_v=${CHARACTER_FOREGROUND_CACHE_VERSION}${hash}`;
}

export function patchInteractiveTravelPartBackground(
  travel: InteractiveTravelState,
  partNumber: number,
  backgroundImageUrl: string,
): InteractiveTravelState {
  let changed = false;
  const parts = travel.parts.map((part) => {
    if (part.partNumber !== partNumber) {
      return part;
    }

    const current = part as InteractiveTravelPartWithBackground;
    if (current.backgroundImageUrl === backgroundImageUrl) {
      return part;
    }
    changed = true;
    return { ...part, backgroundImageUrl };
  });

  return changed ? { ...travel, parts } : travel;
}
