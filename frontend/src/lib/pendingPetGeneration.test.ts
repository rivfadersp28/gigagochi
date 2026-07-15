import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PENDING_GENERATION_STORAGE_KEY,
  adoptPendingPetGenerationJob,
  clearPendingPetGeneration,
  pendingPetGenerationForDescription,
  readPendingPetGeneration,
  setPendingPetGenerationJobId,
  writePendingPetGeneration,
} from "./pendingPetGeneration";

describe("pending pet generation", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps one request key for the same description and replaces it for a new request", () => {
    const first = pendingPetGenerationForDescription("  лесной мышонок  ");
    expect(writePendingPetGeneration(first)).toBe(true);

    expect(pendingPetGenerationForDescription("лесной мышонок")).toEqual(first);
    const changed = pendingPetGenerationForDescription("грозовой дракон");
    expect(changed.requestKey).not.toBe(first.requestKey);
    expect(writePendingPetGeneration(changed)).toBe(true);
    expect(readPendingPetGeneration()).toEqual(changed);
    expect(readPendingPetGeneration(first.requestKey)).toBeNull();
  });

  it("persists and reads back the request key before a paid POST", () => {
    const pending = pendingPetGenerationForDescription("мышонок");

    expect(writePendingPetGeneration(pending)).toBe(true);
    expect(JSON.parse(
      window.localStorage.getItem(PENDING_GENERATION_STORAGE_KEY) ?? "null",
    )).toEqual(pending);
    expect(setPendingPetGenerationJobId(pending.requestKey, "job-1")).toBe(true);
    expect(readPendingPetGeneration()).toEqual({ ...pending, jobId: "job-1" });
  });

  it("adopts the backend owner-active job in the same durable record", () => {
    const pending = pendingPetGenerationForDescription("дракон");
    writePendingPetGeneration(pending);

    expect(adoptPendingPetGenerationJob(
      pending.requestKey,
      "job-active",
      " мышонок ",
    )).toBe(true);
    expect(readPendingPetGeneration()).toEqual({
      ...pending,
      description: "мышонок",
      jobId: "job-active",
    });
  });

  it("reads the released v1 job marker and upgrades it in place", () => {
    window.localStorage.setItem(
      PENDING_GENERATION_STORAGE_KEY,
      JSON.stringify({ description: "мышонок", jobId: "legacy-job" }),
    );

    const first = readPendingPetGeneration();
    const second = readPendingPetGeneration();

    expect(first).toMatchObject({ description: "мышонок", jobId: "legacy-job" });
    expect(first?.requestKey).toMatch(/^[A-Za-z0-9][A-Za-z0-9._:-]{7,95}$/);
    expect(first?.requestKey).toBe(second?.requestKey);
    expect(JSON.parse(
      window.localStorage.getItem(PENDING_GENERATION_STORAGE_KEY) ?? "null",
    )).toEqual(first);
  });

  it("fails closed when the exact marker cannot be read back", () => {
    const pending = pendingPetGenerationForDescription("мышонок");
    vi.spyOn(Storage.prototype, "getItem").mockReturnValue("overwritten");

    expect(writePendingPetGeneration(pending)).toBe(false);
  });

  it("has no in-memory durability fallback when storage rejects the write", () => {
    const pending = pendingPetGenerationForDescription("мышонок");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });

    expect(writePendingPetGeneration(pending)).toBe(false);
    expect(readPendingPetGeneration()).toBeNull();
  });

  it("does not let an old callback clear a newer request", () => {
    const first = pendingPetGenerationForDescription("мышонок");
    writePendingPetGeneration(first);
    const second = pendingPetGenerationForDescription("дракон");
    writePendingPetGeneration(second);

    expect(clearPendingPetGeneration(first.requestKey)).toBe(false);
    expect(readPendingPetGeneration()).toEqual(second);
    expect(clearPendingPetGeneration(second.requestKey)).toBe(true);
    expect(readPendingPetGeneration()).toBeNull();
  });

  it("expires before the backend two-day idempotency proof", () => {
    vi.spyOn(Date, "now").mockReturnValue(2_000_000_000_000);
    window.localStorage.setItem(PENDING_GENERATION_STORAGE_KEY, JSON.stringify({
      description: "мышонок",
      requestKey: "pet-request-expired-0001",
      createdAt: Date.now() - 47 * 60 * 60 * 1000,
    }));

    expect(readPendingPetGeneration()).toBeNull();
    expect(window.localStorage.getItem(PENDING_GENERATION_STORAGE_KEY)).toBeNull();
  });
});
