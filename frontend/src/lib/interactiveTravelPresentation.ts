import { splitPetReplySentences } from "./petReplySentences";
import { TEST_PET_ASSET_SET_ID } from "./testPetFixture";
import { assetSetForPet } from "./petAssetSet";
import type {
  InteractiveTravelPart,
  InteractiveTravelResult,
  InteractiveTravelState,
  LocalPetState,
} from "./types";

const CHARACTER_FOREGROUND_CACHE_VERSION = "20260714-1";

export type ResolvedInteractiveTravelPart = InteractiveTravelPart & {
  result: InteractiveTravelResult;
};

type InteractiveTravelPartWithBackground = InteractiveTravelPart & {
  backgroundImageUrl?: string;
  backgroundVideoUrl?: string;
};

export function splitInteractiveTravelText(text: string): string[] {
  return splitPetReplySentences(text);
}

export function interactiveTravelTextParagraphs(text: string): string[] {
  const blocks = text
    .split(/\n\s*\n/u)
    .map((block) => block.trim())
    .filter(Boolean);

  if (typeof Intl.Segmenter !== "function") {
    return blocks;
  }

  const segmenter = new Intl.Segmenter("ru", { granularity: "sentence" });
  return blocks.flatMap((block) =>
    [...segmenter.segment(block)]
      .map(({ segment }) => segment.trim())
      .filter(Boolean),
  );
}

export function storyAndChallengeParagraphs(part: InteractiveTravelPart): string[] {
  return [part.storyText, part.challenge].flatMap(interactiveTravelTextParagraphs);
}

export function resolvedResultParagraphs(part: InteractiveTravelPart): string[] {
  return part.result ? interactiveTravelTextParagraphs(part.result.text) : [];
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
