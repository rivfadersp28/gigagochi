const KEY_PREFIX = "tamagochi:v1:ambient-replies:";
const MAX_ITEMS = 10;

function normalize(value: unknown): string {
  return typeof value === "string" ? value.trim().replace(/\s+/g, " ").slice(0, 260) : "";
}

export function readRecentAmbientReplies(petId: string): string[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const parsed = JSON.parse(window.localStorage.getItem(`${KEY_PREFIX}${petId}`) ?? "[]");
    return Array.isArray(parsed) ? parsed.map(normalize).filter(Boolean).slice(-MAX_ITEMS) : [];
  } catch {
    return [];
  }
}

export function appendRecentAmbientReply(petId: string, reply: string): string[] {
  const text = normalize(reply);
  const next = [...readRecentAmbientReplies(petId), text].filter(Boolean).slice(-MAX_ITEMS);
  if (typeof window !== "undefined") {
    window.localStorage.setItem(`${KEY_PREFIX}${petId}`, JSON.stringify(next));
  }
  return next;
}
