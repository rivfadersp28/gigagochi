import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./apiTransport";
import {
  interactiveTravelPartFixture,
  interactiveTravelPlanFixture,
} from "./interactiveTravelTestFixtures";
import {
  clearPendingInteractiveTravelOperationsForTests,
  enqueueInteractiveTravelCancel,
  enqueueInteractiveTravelCapture,
  flushPendingInteractiveTravelOperations,
  PENDING_INTERACTIVE_TRAVEL_OPERATIONS_STORAGE_KEY,
  readPendingInteractiveTravelOperations,
} from "./pendingInteractiveTravelOperations";
import type { InteractiveTravelState } from "./types";

const apiMocks = vi.hoisted(() => ({
  cancelInteractiveTravel: vi.fn(),
  captureInteractiveTravelFinale: vi.fn(),
}));

vi.mock("./api", () => apiMocks);

function completedTravel(
  travelId = "interactive-travel-queue-1",
): InteractiveTravelState {
  const plan = interactiveTravelPlanFixture();
  return {
    travelId,
    generatedAt: "2026-07-15T12:00:00Z",
    destination: "облачный город",
    overallTitle: "Часы облачного города",
    plan,
    parts: ([0, 1, 2, 3] as const).map((index) =>
      interactiveTravelPartFixture(plan, index, true),
    ),
    completed: true,
    outcomeValence: "positive",
  };
}

describe("pending interactive travel operations", () => {
  beforeEach(() => {
    window.localStorage.clear();
    clearPendingInteractiveTravelOperationsForTests();
    apiMocks.cancelInteractiveTravel.mockReset().mockResolvedValue(undefined);
    apiMocks.captureInteractiveTravelFinale.mockReset().mockResolvedValue(undefined);
  });

  afterEach(() => {
    clearPendingInteractiveTravelOperationsForTests();
    vi.restoreAllMocks();
  });

  it("stores one compact capture and never lets cancel replace it", () => {
    const travel = completedTravel();
    expect(enqueueInteractiveTravelCapture(travel)).toBe(true);
    expect(enqueueInteractiveTravelCancel(travel.travelId)).toBe(true);

    expect(readPendingInteractiveTravelOperations()).toEqual([
      { kind: "capture", travelId: travel.travelId, travel },
    ]);
    const stored = JSON.parse(window.localStorage.getItem(
      PENDING_INTERACTIVE_TRAVEL_OPERATIONS_STORAGE_KEY,
    ) ?? "null") as unknown[];
    expect(Array.isArray(stored)).toBe(true);
    expect(stored[0]).not.toHaveProperty("operationId");
    expect(stored[0]).not.toHaveProperty("queuedAt");
  });

  it("deduplicates by travel and bounds the single array", () => {
    for (let index = 0; index < 10; index += 1) {
      enqueueInteractiveTravelCancel(`interactive-travel-${index}`);
    }

    expect(readPendingInteractiveTravelOperations().map((item) => item.travelId)).toEqual([
      "interactive-travel-0",
      "interactive-travel-3",
      "interactive-travel-4",
      "interactive-travel-5",
      "interactive-travel-6",
      "interactive-travel-7",
      "interactive-travel-8",
      "interactive-travel-9",
    ]);
  });

  it("clears acknowledged work and retains transient failures", async () => {
    const capture = completedTravel("interactive-travel-capture");
    enqueueInteractiveTravelCapture(capture);
    enqueueInteractiveTravelCancel("interactive-travel-cancel");
    apiMocks.cancelInteractiveTravel.mockRejectedValue(
      new ApiError("offline", "NETWORK_ERROR"),
    );

    await expect(flushPendingInteractiveTravelOperations()).rejects.toMatchObject({
      code: "NETWORK_ERROR",
    });

    expect(apiMocks.captureInteractiveTravelFinale).toHaveBeenCalledWith(capture);
    expect(readPendingInteractiveTravelOperations()).toEqual([
      { kind: "cancel", travelId: "interactive-travel-cancel" },
    ]);
  });

  it("drops a terminal stale operation", async () => {
    enqueueInteractiveTravelCancel("interactive-travel-stale");
    apiMocks.cancelInteractiveTravel.mockRejectedValue(new ApiError(
      "stale",
      "INTERACTIVE_TRAVEL_STATE_CONFLICT",
      409,
    ));

    await expect(flushPendingInteractiveTravelOperations()).rejects.toMatchObject({
      code: "INTERACTIVE_TRAVEL_STATE_CONFLICT",
    });
    expect(readPendingInteractiveTravelOperations()).toEqual([]);
  });

  it("coalesces concurrent flushes into one request", async () => {
    enqueueInteractiveTravelCancel("interactive-travel-single-flight");
    let release!: () => void;
    apiMocks.cancelInteractiveTravel.mockReturnValue(new Promise<void>((resolve) => {
      release = resolve;
    }));

    const first = flushPendingInteractiveTravelOperations();
    const second = flushPendingInteractiveTravelOperations();
    expect(second).toBe(first);
    expect(apiMocks.cancelInteractiveTravel).toHaveBeenCalledOnce();
    release();
    await first;
  });

  it("does not clear a newer capture when an older acknowledgement arrives", async () => {
    const firstTravel = completedTravel("interactive-travel-updated");
    enqueueInteractiveTravelCapture(firstTravel);
    let release!: () => void;
    apiMocks.captureInteractiveTravelFinale
      .mockReturnValueOnce(new Promise<void>((resolve) => {
        release = resolve;
      }))
      .mockResolvedValue(undefined);

    const flush = flushPendingInteractiveTravelOperations();
    await vi.waitFor(() => expect(apiMocks.captureInteractiveTravelFinale).toHaveBeenCalledOnce());
    const updatedTravel = {
      ...firstTravel,
      parts: firstTravel.parts.map((part, index) =>
        index === 0 ? { ...part, backgroundImageUrl: "/scene.png" } : part,
      ),
    };
    enqueueInteractiveTravelCapture(updatedTravel);
    release();
    await flush;

    expect(apiMocks.captureInteractiveTravelFinale).toHaveBeenCalledTimes(2);
    expect(apiMocks.captureInteractiveTravelFinale).toHaveBeenLastCalledWith(updatedTravel);
    expect(readPendingInteractiveTravelOperations()).toEqual([]);
  });

  it("fails closed without an in-memory durability fallback", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });

    expect(enqueueInteractiveTravelCancel("interactive-travel-storage-blocked")).toBe(false);
    expect(readPendingInteractiveTravelOperations()).toEqual([]);
  });
});
