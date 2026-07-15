import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  nextPushSnapshotOrder,
  PUSH_SNAPSHOT_REVISION_STORAGE_KEY,
  PUSH_SNAPSHOT_WRITER_STORAGE_KEY,
} from "./pushSnapshotRevision";

describe("push snapshot revision", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.removeItem(PUSH_SNAPSHOT_REVISION_STORAGE_KEY);
    window.localStorage.removeItem(PUSH_SNAPSHOT_WRITER_STORAGE_KEY);
    window.sessionStorage.removeItem(PUSH_SNAPSHOT_REVISION_STORAGE_KEY);
    window.sessionStorage.removeItem(PUSH_SNAPSHOT_WRITER_STORAGE_KEY);
  });

  it("stays monotonic within one tab and keeps its writer out of shared storage", () => {
    const first = nextPushSnapshotOrder();
    const second = nextPushSnapshotOrder();

    expect(second.writerId).toBe(first.writerId);
    expect(second.revision).toBeGreaterThan(first.revision);
    expect(window.localStorage.getItem(PUSH_SNAPSHOT_REVISION_STORAGE_KEY)).toBe(
      String(second.revision),
    );
    expect(window.sessionStorage.getItem(PUSH_SNAPSHOT_REVISION_STORAGE_KEY)).toBe(
      String(second.revision),
    );
    expect(window.sessionStorage.getItem(PUSH_SNAPSHOT_WRITER_STORAGE_KEY)).toBe(first.writerId);
    expect(window.localStorage.getItem(PUSH_SNAPSHOT_WRITER_STORAGE_KEY)).toBeNull();
  });

  it("keeps an in-memory monotonic counter when storage writes fail", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });

    const first = nextPushSnapshotOrder();
    const second = nextPushSnapshotOrder();

    expect(second.writerId).toBe(first.writerId);
    expect(second.revision).toBeGreaterThan(first.revision);
  });

  it("continues the same session writer after reload when localStorage is unavailable", async () => {
    const nativeGetItem = Storage.prototype.getItem;
    const nativeSetItem = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (this === window.localStorage) {
        throw new DOMException("storage unavailable", "SecurityError");
      }
      return nativeGetItem.call(this, key);
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (
      this: Storage,
      key: string,
      value: string,
    ) {
      if (this === window.localStorage) {
        throw new DOMException("storage unavailable", "SecurityError");
      }
      return nativeSetItem.call(this, key, value);
    });
    vi.resetModules();
    const beforeReload = await import("./pushSnapshotRevision");
    const first = beforeReload.nextPushSnapshotOrder();

    vi.resetModules();
    const afterReload = await import("./pushSnapshotRevision");
    const second = afterReload.nextPushSnapshotOrder();

    expect(second.writerId).toBe(first.writerId);
    expect(second.revision).toBeGreaterThan(first.revision);
    expect(window.sessionStorage.getItem(PUSH_SNAPSHOT_REVISION_STORAGE_KEY)).toBe(
      String(second.revision),
    );
  });

  it("keeps a writer isolated to its tab instead of adopting a shared legacy writer", async () => {
    const sharedWriterId = "writer-shared-1234567890";
    const tabWriterId = "writer-current-tab-123456";
    window.localStorage.setItem(PUSH_SNAPSHOT_WRITER_STORAGE_KEY, sharedWriterId);
    window.sessionStorage.setItem(PUSH_SNAPSHOT_WRITER_STORAGE_KEY, tabWriterId);

    vi.resetModules();
    const currentTab = await import("./pushSnapshotRevision");
    const order = currentTab.nextPushSnapshotOrder();

    expect(order.writerId).toBe(tabWriterId);
    expect(window.sessionStorage.getItem(PUSH_SNAPSHOT_WRITER_STORAGE_KEY)).toBe(
      tabWriterId,
    );
  });

  it("gives colliding cross-tab revisions different globally orderable writer ids", async () => {
    vi.spyOn(Date, "now").mockReturnValue(1_750_000_000_000);
    const randomUuid = vi.spyOn(globalThis.crypto, "randomUUID");
    randomUuid.mockReturnValueOnce("11111111-1111-4111-8111-111111111111");
    window.localStorage.setItem(PUSH_SNAPSHOT_REVISION_STORAGE_KEY, "100");
    window.sessionStorage.clear();
    vi.resetModules();
    const tabA = await import("./pushSnapshotRevision");
    const orderA = tabA.nextPushSnapshotOrder();

    // Simulate another tab that read the same shared high-water value before A's
    // write became visible. Equal revisions are safe because writers differ.
    window.localStorage.setItem(PUSH_SNAPSHOT_REVISION_STORAGE_KEY, "100");
    window.sessionStorage.clear();
    randomUuid.mockReturnValueOnce("22222222-2222-4222-8222-222222222222");
    vi.resetModules();
    const tabB = await import("./pushSnapshotRevision");
    const orderB = tabB.nextPushSnapshotOrder();

    expect(orderB.revision).toBe(orderA.revision);
    expect(orderB.writerId).not.toBe(orderA.writerId);
    expect(`${orderB.revision}:${orderB.writerId}`).not.toBe(
      `${orderA.revision}:${orderA.writerId}`,
    );
  });

  it("migrates an older persisted counter into the timestamp-based order", async () => {
    vi.spyOn(Date, "now").mockReturnValue(1_750_000_000_000);
    window.localStorage.setItem(PUSH_SNAPSHOT_REVISION_STORAGE_KEY, "42");
    window.sessionStorage.clear();
    vi.resetModules();
    const freshTab = await import("./pushSnapshotRevision");

    expect(freshTab.nextPushSnapshotOrder().revision).toBe(1_750_000_000_000);
  });
});
