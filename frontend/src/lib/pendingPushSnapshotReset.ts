import { unregisterPetPushSnapshot } from "./api";

export const PENDING_PUSH_SNAPSHOT_RESET_STORAGE_KEY =
  "gigagochi.pending-push-snapshot-reset.v1";

const MAX_PENDING_RESETS = 8;
let activeFlush: Promise<void> | null = null;

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

function normalizedPetId(value: unknown): string {
  if (typeof value !== "string") {
    return "";
  }
  const petId = value.trim();
  return petId && petId.length <= 120 ? petId : "";
}

function boundedPetIds(values: unknown): string[] {
  if (!Array.isArray(values)) {
    return [];
  }
  const unique = values.reduce<string[]>((result, value) => {
    const petId = normalizedPetId(value);
    if (petId && !result.includes(petId)) {
      result.push(petId);
    }
    return result;
  }, []);
  return unique.length <= MAX_PENDING_RESETS
    ? unique
    : [unique[0], ...unique.slice(-(MAX_PENDING_RESETS - 1))];
}

export function isPendingPushSnapshotResetStorageKey(key: string | null): boolean {
  return key === PENDING_PUSH_SNAPSHOT_RESET_STORAGE_KEY;
}

export function pendingPushSnapshotResetPetIds(): string[] {
  const store = storage();
  if (!store) {
    return [];
  }
  try {
    return boundedPetIds(JSON.parse(
      store.getItem(PENDING_PUSH_SNAPSHOT_RESET_STORAGE_KEY) ?? "[]",
    ) as unknown);
  } catch {
    return [];
  }
}

function writePetIds(values: string[]): boolean {
  const store = storage();
  if (!store) {
    return false;
  }
  const petIds = boundedPetIds(values);
  try {
    if (!petIds.length) {
      store.removeItem(PENDING_PUSH_SNAPSHOT_RESET_STORAGE_KEY);
      return store.getItem(PENDING_PUSH_SNAPSHOT_RESET_STORAGE_KEY) === null;
    }
    const serialized = JSON.stringify(petIds);
    store.setItem(PENDING_PUSH_SNAPSHOT_RESET_STORAGE_KEY, serialized);
    return store.getItem(PENDING_PUSH_SNAPSHOT_RESET_STORAGE_KEY) === serialized;
  } catch {
    return false;
  }
}

export function queuePushSnapshotReset(value: string): boolean {
  const petId = normalizedPetId(value);
  if (!petId) {
    return false;
  }
  return writePetIds([...pendingPushSnapshotResetPetIds(), petId]);
}

export function clearPendingPushSnapshotReset(value: string): boolean {
  const petId = normalizedPetId(value);
  return petId
    ? writePetIds(pendingPushSnapshotResetPetIds().filter((item) => item !== petId))
    : false;
}

export function flushPendingPushSnapshotResets(): Promise<void> {
  if (activeFlush) {
    return activeFlush;
  }
  activeFlush = Promise.resolve().then(async () => {
    const attempted = new Set<string>();
    while (true) {
      const petId = pendingPushSnapshotResetPetIds().find(
        (item) => !attempted.has(item),
      );
      if (!petId) {
        break;
      }
      attempted.add(petId);
      try {
        const response = await unregisterPetPushSnapshot(petId, { keepalive: true });
        if (!response.unregistered || response.petId !== petId) {
          continue;
        }
      } catch {
        continue;
      }
      clearPendingPushSnapshotReset(petId);
    }
  }).finally(() => {
    activeFlush = null;
  });
  return activeFlush;
}
