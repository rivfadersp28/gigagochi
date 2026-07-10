const sentenceFallbackPattern = /.*?(?:[.!?…]+(?:["'»”)]*)?(?=\s|$)|$)/gu;
const ellipsisSentencePattern = /.*?…+(?:["'»”)]*)?(?=\s|$)|.+$/gu;

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
      return sentences;
    }
  }

  return fallbackSentenceSplit(clean);
}
