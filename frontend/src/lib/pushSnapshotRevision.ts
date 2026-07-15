export const PUSH_SNAPSHOT_REVISION_STORAGE_KEY = "gigagochi.push-snapshot-revision.v1";
export const PUSH_SNAPSHOT_WRITER_STORAGE_KEY = "gigagochi.push-snapshot-writer.v1";

const MAX_REVISION = Number.MAX_SAFE_INTEGER;
let lastIssuedRevision = 0;
let inMemoryWriterId: string | undefined;

function localStorage(): Storage | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function sessionStorage(): Storage | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function storedRevision(store: Storage | null): number {
  if (!store) {
    return 0;
  }
  try {
    const value = Number(store.getItem(PUSH_SNAPSHOT_REVISION_STORAGE_KEY));
    return Number.isSafeInteger(value) && value > 0 && value < MAX_REVISION ? value : 0;
  } catch {
    return 0;
  }
}

function validWriterId(value: string | null): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{16,120}$/.test(value);
}

function createWriterId(): string {
  try {
    const uuid = globalThis.crypto?.randomUUID?.();
    if (validWriterId(uuid)) {
      return uuid;
    }
  } catch {
    // Fall through to a non-cryptographic uniqueness fallback. This is an ordering
    // token, not a secret or authorization credential.
  }
  return `writer-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

function readWriterId(store: Storage | null): string | null {
  try {
    const persisted = store?.getItem(PUSH_SNAPSHOT_WRITER_STORAGE_KEY) ?? null;
    return validWriterId(persisted) ? persisted : null;
  } catch {
    return null;
  }
}

function persistWriterId(store: Storage | null, writerId: string) {
  try {
    store?.setItem(PUSH_SNAPSHOT_WRITER_STORAGE_KEY, writerId);
  } catch {
    // The in-memory writer remains usable for this tab lifetime.
  }
}

function snapshotWriterId(sessionStore: Storage | null): string {
  const writerId = inMemoryWriterId ?? readWriterId(sessionStore) ?? createWriterId();
  inMemoryWriterId = writerId;
  // The writer identifies one browser tab. Sharing it through localStorage makes
  // two tabs capable of issuing the same (writer, revision) pair after a racy
  // read-modify-write of the shared counter. sessionStorage survives reloads but
  // remains isolated per tab.
  persistWriterId(sessionStore, writerId);
  return writerId;
}

export interface PushSnapshotOrder {
  writerId: string;
  revision: number;
}

export function nextPushSnapshotOrder(): PushSnapshotOrder {
  const revisionStore = localStorage();
  const sessionStore = sessionStorage();
  const nowRevision = Math.max(1, Math.floor(Date.now()));
  const revision = Math.min(
    MAX_REVISION,
    Math.max(
      nowRevision,
      lastIssuedRevision + 1,
      storedRevision(revisionStore) + 1,
      storedRevision(sessionStore) + 1,
    ),
  );
  lastIssuedRevision = revision;
  try {
    revisionStore?.setItem(PUSH_SNAPSHOT_REVISION_STORAGE_KEY, String(revision));
  } catch {
    // Session storage below still keeps this writer monotonic across a page reload.
  }
  try {
    sessionStore?.setItem(PUSH_SNAPSHOT_REVISION_STORAGE_KEY, String(revision));
  } catch {
    // The in-memory counter still orders requests in this page lifetime.
  }
  return { writerId: snapshotWriterId(sessionStore), revision };
}
