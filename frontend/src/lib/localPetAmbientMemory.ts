const KEY_PREFIX = "tamagochi:v1:ambient-replies:";
const SESSION_KEY_PREFIX = "tamagochi:v1:ambient-shown:";
const MAX_ITEMS = 30;

export function hasShownAmbientReplyInSession(petId: string): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    return window.sessionStorage.getItem(`${SESSION_KEY_PREFIX}${petId}`) === "1";
  } catch {
    return false;
  }
}

export function markAmbientReplyShownInSession(petId: string) {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.setItem(`${SESSION_KEY_PREFIX}${petId}`, "1");
  } catch {
    // The in-memory dashboard guard still prevents duplicates until unmount.
  }
}

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
