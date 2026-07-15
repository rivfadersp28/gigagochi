export const PENDING_GENERATION_STORAGE_KEY = "gigagochi.pending-generation.v1";

export type PendingPetGeneration = {
  description: string;
  requestKey: string;
  jobId?: string;
  createdAt: number;
};

type PendingPetGenerationInput = Omit<PendingPetGeneration, "createdAt"> & {
  createdAt?: number;
};

const REQUEST_KEY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,95}$/;
const JOB_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$/;
const RETENTION_MS = 47 * 60 * 60 * 1000;
const MAX_FUTURE_SKEW_MS = 5 * 60 * 1000;

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeDescription(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  return value.trim().slice(0, 300) || null;
}

function normalizeJobId(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const jobId = value.trim();
  return JOB_ID_PATTERN.test(jobId) ? jobId : undefined;
}

function legacyRequestKey(jobId: string): string {
  let checksum = 0;
  for (const character of jobId) {
    checksum = (checksum * 131 + (character.codePointAt(0) ?? 0)) % 4_294_967_291;
  }
  return `pet-legacy:${jobId.slice(0, 60)}:${checksum.toString(36)}`;
}

function parsePending(value: unknown): PendingPetGeneration | null {
  if (!isRecord(value)) {
    return null;
  }
  const description = normalizeDescription(value.description);
  const jobId = normalizeJobId(value.jobId);
  let requestKey = typeof value.requestKey === "string" ? value.requestKey : "";
  let createdAt = typeof value.createdAt === "number" && Number.isFinite(value.createdAt)
    ? Math.floor(value.createdAt)
    : 0;

  // Released v1 records contained only { jobId, description }. They are GET-only
  // recovery markers, so assigning a stable key cannot cause another paid POST.
  if (!requestKey && jobId) {
    requestKey = legacyRequestKey(jobId);
    createdAt = Date.now();
  }
  const age = Date.now() - createdAt;
  if (
    !description
    || !REQUEST_KEY_PATTERN.test(requestKey)
    || createdAt <= 0
    || age >= RETENTION_MS
    || age < -MAX_FUTURE_SKEW_MS
  ) {
    return null;
  }
  return {
    description,
    requestKey,
    ...(jobId ? { jobId } : {}),
    createdAt,
  };
}

function persist(marker: PendingPetGeneration): boolean {
  const store = storage();
  if (!store) {
    return false;
  }
  const serialized = JSON.stringify(marker);
  try {
    store.setItem(PENDING_GENERATION_STORAGE_KEY, serialized);
    return store.getItem(PENDING_GENERATION_STORAGE_KEY) === serialized;
  } catch {
    return false;
  }
}

export function createPetGenerationRequestKey(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return `pet-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

export function writePendingPetGeneration(pending: PendingPetGenerationInput): boolean {
  const marker = parsePending({
    ...pending,
    createdAt: pending.createdAt ?? Date.now(),
  });
  return marker ? persist(marker) : false;
}

export function readPendingPetGeneration(
  expectedRequestKey?: string,
): PendingPetGeneration | null {
  const store = storage();
  if (!store) {
    return null;
  }
  try {
    const raw = store.getItem(PENDING_GENERATION_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const marker = parsePending(JSON.parse(raw) as unknown);
    if (!marker) {
      store.removeItem(PENDING_GENERATION_STORAGE_KEY);
      return null;
    }
    if (raw !== JSON.stringify(marker)) {
      persist(marker);
    }
    return expectedRequestKey === undefined || marker.requestKey === expectedRequestKey
      ? marker
      : null;
  } catch {
    return null;
  }
}

export function pendingPetGenerationForDescription(
  description: string,
): PendingPetGeneration {
  const normalized = normalizeDescription(description);
  if (!normalized) {
    throw new Error("pet generation description is empty");
  }
  const current = readPendingPetGeneration();
  if (current?.description === normalized) {
    return current;
  }
  return {
    description: normalized,
    requestKey: createPetGenerationRequestKey(),
    createdAt: Date.now(),
  };
}

export function setPendingPetGenerationJobId(requestKey: string, jobId: string): boolean {
  const pending = readPendingPetGeneration(requestKey);
  const normalizedJobId = normalizeJobId(jobId);
  return Boolean(
    pending
    && normalizedJobId
    && persist({ ...pending, jobId: normalizedJobId }),
  );
}

export function adoptPendingPetGenerationJob(
  requestKey: string,
  jobId: string,
  description: string,
): boolean {
  const pending = readPendingPetGeneration(requestKey);
  const normalizedJobId = normalizeJobId(jobId);
  const normalizedDescription = normalizeDescription(description);
  return Boolean(
    pending
    && normalizedJobId
    && normalizedDescription
    && persist({
      ...pending,
      description: normalizedDescription,
      jobId: normalizedJobId,
    }),
  );
}

export function clearPendingPetGeneration(expectedRequestKey?: string): boolean {
  const store = storage();
  if (!store) {
    return false;
  }
  const current = readPendingPetGeneration();
  if (expectedRequestKey !== undefined && current?.requestKey !== expectedRequestKey) {
    return false;
  }
  try {
    store.removeItem(PENDING_GENERATION_STORAGE_KEY);
    return store.getItem(PENDING_GENERATION_STORAGE_KEY) === null;
  } catch {
    return false;
  }
}
