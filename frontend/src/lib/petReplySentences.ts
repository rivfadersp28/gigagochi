const sentenceFallbackPattern = /.*?(?:[.!?…]+(?:["'»”)]*)?(?=\s|$)|$)/gu;
const ellipsisSentencePattern = /.*?…+(?:["'»”)]*)?(?=\s|$)|.+$/gu;
type PetReplyPortionOptions = {
  maxPortions: number;
};

function fallbackSentenceSplit(text: string): string[] {
  return Array.from(text.matchAll(sentenceFallbackPattern), (match) => match[0].trim()).filter(
    Boolean,
  );
}

function splitIntlSegmentOnEllipsis(text: string): string[] {
  return Array.from(text.matchAll(ellipsisSentencePattern), (match) => match[0].trim()).filter(
    Boolean,
  );
}

function withoutTerminalPeriod(portion: string): string {
  return portion.replace(/(^|[^.])\.(?:(["'»”)]*))$/u, "$1$2");
}

export function splitPetReplySentences(text: string): string[] {
  const clean = text.replace(/\s+/gu, " ").trim();
  if (!clean) {
    return [];
  }

  if (typeof Intl.Segmenter === "function") {
    const segmenter = new Intl.Segmenter("ru", { granularity: "sentence" });
    const sentences = Array.from(segmenter.segment(clean), ({ segment }) => segment.trim())
      .filter(Boolean)
      .flatMap(splitIntlSegmentOnEllipsis);
    if (sentences.length > 0) {
      return sentences.map(withoutTerminalPeriod);
    }
  }

  return fallbackSentenceSplit(clean).map(withoutTerminalPeriod);
}

export function splitPetReplyPortions(
  text: string,
  { maxPortions }: PetReplyPortionOptions,
): string[] {
  if (maxPortions < 1) {
    return [];
  }

  const clean = text.replace(/\s+/gu, " ").trim();
  if (!clean) {
    return [];
  }

  const sentences = splitPetReplySentences(clean);
  if (sentences.length <= maxPortions) {
    return sentences;
  }

  return [
    ...sentences.slice(0, maxPortions - 1),
    sentences.slice(maxPortions - 1).join(" "),
  ];
}
