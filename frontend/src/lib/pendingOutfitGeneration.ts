import type { LocalPetAssetSet } from "./types";

export const PENDING_OUTFIT_STORAGE_KEY = "gigagochi.pending-outfit.v1";
export const OUTFIT_FAILURE_NOTICE_STORAGE_KEY = "gigagochi.outfit-failure-notice.v1";

export type PendingOutfitGeneration = {
  version: 1;
  petId: string;
  requestKey: string;
  experienceLedger?: true;
  jobId?: string;
  displayItem: string;
  generationDescription: string;
  references: {
    idleImageUrl: string;
    sadImageUrl: string;
    happyImageUrl: string;
  };
  baseAssetSet: LocalPetAssetSet;
  createdAt: number;
};

export type OutfitFailureNotice = {
  version: 1;
  petId: string;
  requestKey: string;
  createdAt: number;
};

const RETENTION_MS = 48 * 60 * 60 * 1000;

function store(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function validText(value: unknown, maxLength: number): value is string {
  return typeof value === "string" && Boolean(value.trim()) && value.length <= maxLength;
}

function parse(value: unknown): PendingOutfitGeneration | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Partial<PendingOutfitGeneration>;
  const refs = record.references;
  const base = record.baseAssetSet;
  if (
    record.version !== 1
    || !validText(record.petId, 128)
    || !validText(record.requestKey, 96)
    || (record.experienceLedger !== undefined && record.experienceLedger !== true)
    || !validText(record.displayItem, 80)
    || !validText(record.generationDescription, 300)
    || !refs
    || !validText(refs.idleImageUrl, 2048)
    || !validText(refs.sadImageUrl, 2048)
    || !validText(refs.happyImageUrl, 2048)
    || !base
    || !validText(base.assetSetId, 128)
    || typeof record.createdAt !== "number"
    || Date.now() - record.createdAt > RETENTION_MS
    || record.createdAt - Date.now() > 5 * 60 * 1000
    || (record.jobId !== undefined && !validText(record.jobId, 120))
  ) return null;
  return record as PendingOutfitGeneration;
}

export function readPendingOutfitGeneration(): PendingOutfitGeneration | null {
  const storage = store();
  if (!storage) return null;
  try {
    const raw = storage.getItem(PENDING_OUTFIT_STORAGE_KEY);
    if (!raw) return null;
    const pending = parse(JSON.parse(raw) as unknown);
    if (!pending) storage.removeItem(PENDING_OUTFIT_STORAGE_KEY);
    return pending;
  } catch {
    return null;
  }
}

export function writePendingOutfitGeneration(pending: PendingOutfitGeneration): boolean {
  const storage = store();
  const normalized = parse(pending);
  if (!storage || !normalized) return false;
  try {
    storage.setItem(PENDING_OUTFIT_STORAGE_KEY, JSON.stringify(normalized));
    return true;
  } catch {
    return false;
  }
}

export function setPendingOutfitJobId(requestKey: string, jobId: string): boolean {
  const pending = readPendingOutfitGeneration();
  if (!pending || pending.requestKey !== requestKey) return false;
  return writePendingOutfitGeneration({ ...pending, jobId });
}

export function clearPendingOutfitGeneration(requestKey?: string): void {
  const storage = store();
  if (!storage) return;
  const pending = readPendingOutfitGeneration();
  if (requestKey && pending?.requestKey !== requestKey) return;
  try {
    storage.removeItem(PENDING_OUTFIT_STORAGE_KEY);
  } catch {
    // A later app open will retry cleanup.
  }
}

export function readOutfitFailureNotice(): OutfitFailureNotice | null {
  const storage = store();
  if (!storage) return null;
  try {
    const raw = storage.getItem(OUTFIT_FAILURE_NOTICE_STORAGE_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<OutfitFailureNotice>;
    if (
      value.version !== 1
      || !validText(value.petId, 128)
      || !validText(value.requestKey, 96)
      || typeof value.createdAt !== "number"
      || Date.now() - value.createdAt > RETENTION_MS
      || value.createdAt - Date.now() > 5 * 60 * 1000
    ) {
      storage.removeItem(OUTFIT_FAILURE_NOTICE_STORAGE_KEY);
      return null;
    }
    return value as OutfitFailureNotice;
  } catch {
    return null;
  }
}

export function writeOutfitFailureNotice(notice: OutfitFailureNotice): boolean {
  const storage = store();
  if (!storage) return false;
  try {
    storage.setItem(OUTFIT_FAILURE_NOTICE_STORAGE_KEY, JSON.stringify(notice));
    return true;
  } catch {
    return false;
  }
}

export function clearOutfitFailureNotice(requestKey?: string): void {
  const storage = store();
  if (!storage) return;
  const notice = readOutfitFailureNotice();
  if (requestKey && notice?.requestKey !== requestKey) return;
  try {
    storage.removeItem(OUTFIT_FAILURE_NOTICE_STORAGE_KEY);
  } catch {
    // The notice is harmless if it survives until the next app open.
  }
}
