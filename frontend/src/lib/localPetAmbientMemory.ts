import { markLocalPetSnapshotDirty } from "./localPetSnapshotRevision";

const KEY_PREFIX = "tamagochi:v1:ambient-replies:";
const SESSION_KEY_PREFIX = "tamagochi:v1:ambient-shown:";
const MAX_ITEMS = 30;
const MAX_SESSION_PETS = 64;

type SessionReplies = {
  replies: string[];
  persistence: "write" | "clear" | null;
};

const sessionRepliesByPet = new Map<string, SessionReplies>();

export function localPetAmbientStorageKey(petId: string) {
  return `${KEY_PREFIX}${petId}`;
}

export function isLocalPetAmbientStorageKey(key: string | null, petId: string) {
  return key === null || key === localPetAmbientStorageKey(petId);
}

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
  if (typeof value !== "string") {
    return "";
  }
  const text = value.trim().replace(/\s+/g, " ").slice(0, 260);
  if (
    /\bjson\b/i.test(text)
    || /json\s*schema|json[- ]?схем/i.test(text)
    || /строг(?:ий|ого|ому|им|ом|ая|ой|ую|ое|ие|их|ими)?\s+формат/i.test(text)
    || /формат\s+(?:ответа|вывода)|схем(?:а|е|у|ой|ы)\s+ответа/i.test(text)
  ) {
    return "";
  }
  return text;
}

function storage(): Storage | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function rememberSessionReplies(petId: string, entry: SessionReplies) {
  sessionRepliesByPet.delete(petId);
  sessionRepliesByPet.set(petId, entry);
  while (sessionRepliesByPet.size > MAX_SESSION_PETS) {
    const oldestPetId = sessionRepliesByPet.keys().next().value;
    if (typeof oldestPetId !== "string") {
      break;
    }
    sessionRepliesByPet.delete(oldestPetId);
  }
}

function flushSessionReplies(petId: string, store: Storage | null) {
  const entry = sessionRepliesByPet.get(petId);
  if (!entry?.persistence || !store) {
    return;
  }
  try {
    if (entry.persistence === "write") {
      store.setItem(localPetAmbientStorageKey(petId), JSON.stringify(entry.replies));
      entry.persistence = null;
    } else {
      store.removeItem(localPetAmbientStorageKey(petId));
      sessionRepliesByPet.delete(petId);
    }
  } catch {
    // The session copy/tombstone stays authoritative until persistence recovers.
  }
}

export function readRecentAmbientReplies(petId: string): string[] {
  if (typeof window === "undefined") {
    return [];
  }
  const store = storage();
  flushSessionReplies(petId, store);
  const sessionEntry = sessionRepliesByPet.get(petId);
  if (sessionEntry?.persistence || !store) {
    return [...(sessionEntry?.replies ?? [])];
  }
  try {
    const parsed = JSON.parse(store.getItem(localPetAmbientStorageKey(petId)) ?? "[]");
    const replies = Array.isArray(parsed)
      ? parsed.slice(-MAX_ITEMS).map(normalize).filter(Boolean)
      : [];
    rememberSessionReplies(petId, { replies, persistence: null });
    return [...replies];
  } catch {
    return [...(sessionEntry?.replies ?? [])];
  }
}

export function appendRecentAmbientReply(petId: string, reply: string): string[] {
  const text = normalize(reply);
  const next = [...readRecentAmbientReplies(petId), text].filter(Boolean).slice(-MAX_ITEMS);
  if (typeof window === "undefined") {
    return next;
  }
  const entry = { replies: next, persistence: "write" as const };
  rememberSessionReplies(petId, entry);
  flushSessionReplies(petId, storage());
  markLocalPetSnapshotDirty(petId);
  return [...next];
}

export function clearRecentAmbientReplies(petId: string) {
  const entry = { replies: [], persistence: "clear" as const };
  rememberSessionReplies(petId, entry);
  flushSessionReplies(petId, storage());
  markLocalPetSnapshotDirty(petId);
}
