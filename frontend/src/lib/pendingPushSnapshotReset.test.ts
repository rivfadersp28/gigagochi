import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { unregisterPetPushSnapshot } from "./api";
import {
  clearPendingPushSnapshotReset,
  flushPendingPushSnapshotResets,
  PENDING_PUSH_SNAPSHOT_RESET_STORAGE_KEY,
  pendingPushSnapshotResetPetIds,
  queuePushSnapshotReset,
} from "./pendingPushSnapshotReset";

vi.mock("./api", () => ({
  unregisterPetPushSnapshot: vi.fn(),
}));

const unregisterMock = vi.mocked(unregisterPetPushSnapshot);

describe("pending push snapshot reset", () => {
  beforeEach(() => {
    window.localStorage.clear();
    unregisterMock.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("keeps one bounded array of pet ids", () => {
    for (let index = 0; index < 10; index += 1) {
      expect(queuePushSnapshotReset(`pet-${index}`)).toBe(true);
    }
    queuePushSnapshotReset("pet-9");

    expect(pendingPushSnapshotResetPetIds()).toEqual([
      "pet-0",
      "pet-3",
      "pet-4",
      "pet-5",
      "pet-6",
      "pet-7",
      "pet-8",
      "pet-9",
    ]);
    expect(JSON.parse(window.localStorage.getItem(
      PENDING_PUSH_SNAPSHOT_RESET_STORAGE_KEY,
    ) ?? "null")).toEqual(pendingPushSnapshotResetPetIds());
  });

  it("clears only the exact reset acknowledged by the backend", async () => {
    queuePushSnapshotReset("pet-failed");
    queuePushSnapshotReset("pet-removed");
    unregisterMock.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({
      unregistered: true,
      petId: "pet-removed",
    });

    await flushPendingPushSnapshotResets();

    expect(unregisterMock).toHaveBeenCalledTimes(2);
    expect(pendingPushSnapshotResetPetIds()).toEqual(["pet-failed"]);
  });

  it("retains a reset when the acknowledgement names another pet", async () => {
    queuePushSnapshotReset("pet-expected");
    unregisterMock.mockResolvedValue({ unregistered: true, petId: "pet-other" });

    await flushPendingPushSnapshotResets();

    expect(pendingPushSnapshotResetPetIds()).toEqual(["pet-expected"]);
  });

  it("coalesces simultaneous flushes", async () => {
    queuePushSnapshotReset("pet-1");
    unregisterMock.mockResolvedValue({ unregistered: true, petId: "pet-1" });

    await Promise.all([flushPendingPushSnapshotResets(), flushPendingPushSnapshotResets()]);

    expect(unregisterMock).toHaveBeenCalledOnce();
    expect(pendingPushSnapshotResetPetIds()).toEqual([]);
  });

  it("drains a reset queued while a flush is in flight", async () => {
    queuePushSnapshotReset("pet-first");
    let acknowledgeFirst!: (value: { unregistered: boolean; petId: string }) => void;
    unregisterMock
      .mockImplementationOnce(() => new Promise((resolve) => {
        acknowledgeFirst = resolve;
      }))
      .mockResolvedValueOnce({ unregistered: true, petId: "pet-second" });

    const flush = flushPendingPushSnapshotResets();
    await vi.waitFor(() => expect(unregisterMock).toHaveBeenCalledOnce());
    queuePushSnapshotReset("pet-second");
    acknowledgeFirst({ unregistered: true, petId: "pet-first" });
    await flush;

    expect(unregisterMock).toHaveBeenNthCalledWith(2, "pet-second", { keepalive: true });
    expect(pendingPushSnapshotResetPetIds()).toEqual([]);
  });

  it("does not pretend an in-memory fallback is durable", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });

    expect(queuePushSnapshotReset("pet-blocked")).toBe(false);
    expect(pendingPushSnapshotResetPetIds()).toEqual([]);
  });

  it("clears one id without touching the rest", () => {
    queuePushSnapshotReset("pet-first");
    queuePushSnapshotReset("pet-second");

    expect(clearPendingPushSnapshotReset("pet-first")).toBe(true);
    expect(pendingPushSnapshotResetPetIds()).toEqual(["pet-second"]);
  });
});
