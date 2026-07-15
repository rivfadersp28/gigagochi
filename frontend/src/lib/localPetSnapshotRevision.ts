const MAX_TRACKED_PETS = 64;
const MAX_REVISION = Number.MAX_SAFE_INTEGER - 1;

type SnapshotDirtyListener = (petId: string, revision: number) => void;

const revisionByPetId = new Map<string, number>();
const listeners = new Set<SnapshotDirtyListener>();
const pendingNotifications = new Map<string, number>();
let notificationScheduled = false;

function normalizedPetId(petId: string): string {
  return petId.trim().slice(0, 120);
}

function rememberRevision(petId: string, revision: number) {
  revisionByPetId.delete(petId);
  revisionByPetId.set(petId, revision);
  while (revisionByPetId.size > MAX_TRACKED_PETS) {
    const oldestPetId = revisionByPetId.keys().next().value;
    if (typeof oldestPetId !== "string") {
      break;
    }
    revisionByPetId.delete(oldestPetId);
  }
}

function flushNotifications() {
  notificationScheduled = false;
  const notifications = [...pendingNotifications];
  pendingNotifications.clear();
  for (const [petId, revision] of notifications) {
    for (const listener of listeners) {
      listener(petId, revision);
    }
  }
}

function scheduleNotification(petId: string, revision: number) {
  pendingNotifications.set(petId, revision);
  if (notificationScheduled) {
    return;
  }
  notificationScheduled = true;
  if (typeof queueMicrotask === "function") {
    queueMicrotask(flushNotifications);
  } else {
    void Promise.resolve().then(flushNotifications);
  }
}

export function readLocalPetSnapshotRevision(petId: string): number {
  const normalized = normalizedPetId(petId);
  if (!normalized) {
    return 0;
  }
  const revision = revisionByPetId.get(normalized) ?? 0;
  if (revisionByPetId.has(normalized)) {
    rememberRevision(normalized, revision);
  }
  return revision;
}

export function markLocalPetSnapshotDirty(petId: string): number {
  const normalized = normalizedPetId(petId);
  if (!normalized) {
    return 0;
  }
  const previous = revisionByPetId.get(normalized) ?? 0;
  const revision = previous >= MAX_REVISION ? 1 : previous + 1;
  rememberRevision(normalized, revision);
  scheduleNotification(normalized, revision);
  return revision;
}

export function subscribeLocalPetSnapshotDirty(listener: SnapshotDirtyListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
