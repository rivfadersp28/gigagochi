import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ApiError } from "./apiTransport";
import {
  interactiveTravelPartFixture,
  interactiveTravelPlanFixture,
} from "./interactiveTravelTestFixtures";
import { startInteractiveTravelWithRecovery } from "./interactiveTravelStartRecovery";
import {
  clearPendingInteractiveTravelOperationsForTests,
  readPendingInteractiveTravelOperations,
} from "./pendingInteractiveTravelOperations";
import type { InteractiveTravelResponse, LocalPetState } from "./types";

const apiMocks = vi.hoisted(() => ({
  cancelInteractiveTravel: vi.fn(),
  captureInteractiveTravelFinale: vi.fn(),
  startInteractiveTravel: vi.fn(),
}));

vi.mock("./api", () => apiMocks);

const now = "2026-07-15T12:00:00.000Z";
const pet: LocalPetState = {
  version: 2,
  petId: "pet-new",
  description: "дракон",
  createdAt: now,
  updatedAt: now,
  lastInteractionAt: now,
  lastStatsTickAt: now,
  lastStatTickAt: { hunger: now, happiness: now, energy: now },
  stage: "baby",
  mood: "idle",
  stats: { hunger: 80, happiness: 80, energy: 80 },
};

const responsePlan = interactiveTravelPlanFixture();
const response = {
  travel: {
    travelId: "interactive-travel-new-session",
    generatedAt: now,
    destination: "маяк",
    overallTitle: "Путь к маяку",
    introReaction: { text: "В путь!", tone: "enthusiastic" },
    plan: responsePlan,
    parts: [interactiveTravelPartFixture(responsePlan, 0)],
    completed: false,
  },
} as InteractiveTravelResponse;

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  clearPendingInteractiveTravelOperationsForTests();
  apiMocks.cancelInteractiveTravel.mockResolvedValue(undefined);
  apiMocks.captureInteractiveTravelFinale.mockResolvedValue(undefined);
});

afterEach(() => {
  clearPendingInteractiveTravelOperationsForTests();
  vi.restoreAllMocks();
});

it("durably cancels the owner-bound stale session and retries start once", async () => {
  const activeTravelId = "interactive-travel-old-session";
  apiMocks.startInteractiveTravel
    .mockRejectedValueOnce(new ApiError(
      "Сначала отмените текущее путешествие.",
      "INTERACTIVE_TRAVEL_ALREADY_ACTIVE",
      409,
      { travelId: activeTravelId },
    ))
    .mockResolvedValueOnce(response);
  const options = { includeDebug: false };

  await expect(startInteractiveTravelWithRecovery("маяк", pet, options))
    .resolves.toBe(response);

  expect(apiMocks.startInteractiveTravel).toHaveBeenCalledTimes(2);
  expect(apiMocks.startInteractiveTravel).toHaveBeenNthCalledWith(1, "маяк", pet, options);
  expect(apiMocks.startInteractiveTravel).toHaveBeenNthCalledWith(2, "маяк", pet, options);
  expect(apiMocks.cancelInteractiveTravel).toHaveBeenCalledOnce();
  expect(apiMocks.cancelInteractiveTravel).toHaveBeenCalledWith(activeTravelId);
  expect(readPendingInteractiveTravelOperations()).toEqual([]);
});

it("does not recover an unvalidated travel id", async () => {
  const conflict = new ApiError(
    "conflict",
    "INTERACTIVE_TRAVEL_ALREADY_ACTIVE",
    409,
    { travelId: "../foreign" },
  );
  apiMocks.startInteractiveTravel.mockRejectedValueOnce(conflict);

  await expect(startInteractiveTravelWithRecovery("маяк", pet)).rejects.toBe(conflict);
  expect(apiMocks.startInteractiveTravel).toHaveBeenCalledOnce();
  expect(apiMocks.cancelInteractiveTravel).not.toHaveBeenCalled();
});

it("never cancels a same-pet start that the backend reports as in progress", async () => {
  const inProgress = new ApiError(
    "Путешествие уже создаётся.",
    "INTERACTIVE_TRAVEL_START_IN_PROGRESS",
    409,
    { travelId: "interactive-travel-same-pet-start" },
  );
  apiMocks.startInteractiveTravel.mockRejectedValueOnce(inProgress);

  await expect(startInteractiveTravelWithRecovery("маяк", pet)).rejects.toBe(inProgress);
  expect(apiMocks.startInteractiveTravel).toHaveBeenCalledOnce();
  expect(apiMocks.cancelInteractiveTravel).not.toHaveBeenCalled();
  expect(readPendingInteractiveTravelOperations()).toEqual([]);
});

it("keeps the outbox and does not retry when cancellation is ambiguous", async () => {
  const activeTravelId = "interactive-travel-cancel-ambiguous";
  const cancelError = new ApiError("offline", "NETWORK_ERROR");
  apiMocks.startInteractiveTravel.mockRejectedValueOnce(new ApiError(
    "conflict",
    "INTERACTIVE_TRAVEL_ALREADY_ACTIVE",
    409,
    { travelId: activeTravelId },
  ));
  apiMocks.cancelInteractiveTravel.mockRejectedValueOnce(cancelError);

  await expect(startInteractiveTravelWithRecovery("маяк", pet)).rejects.toBe(cancelError);
  expect(apiMocks.startInteractiveTravel).toHaveBeenCalledOnce();
  expect(readPendingInteractiveTravelOperations()).toMatchObject([
    { kind: "cancel", travelId: activeTravelId },
  ]);
});

it("does not cancel or retry when the cancellation intent is not durable", async () => {
  apiMocks.startInteractiveTravel.mockRejectedValueOnce(new ApiError(
    "conflict",
    "INTERACTIVE_TRAVEL_ALREADY_ACTIVE",
    409,
    { travelId: "interactive-travel-storage-blocked" },
  ));
  const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("blocked", "SecurityError");
  });

  await expect(startInteractiveTravelWithRecovery("маяк", pet)).rejects.toMatchObject({
    code: "INTERACTIVE_TRAVEL_RECOVERY_NOT_DURABLE",
  });
  expect(apiMocks.startInteractiveTravel).toHaveBeenCalledOnce();
  expect(apiMocks.cancelInteractiveTravel).not.toHaveBeenCalled();
  setItem.mockRestore();
});

it("never makes a third start attempt when the bounded retry fails", async () => {
  const retryError = new ApiError("retry failed", "NETWORK_ERROR");
  apiMocks.startInteractiveTravel
    .mockRejectedValueOnce(new ApiError(
      "conflict",
      "INTERACTIVE_TRAVEL_ALREADY_ACTIVE",
      409,
      { travelId: "interactive-travel-retry-once" },
    ))
    .mockRejectedValueOnce(retryError);

  await expect(startInteractiveTravelWithRecovery("маяк", pet)).rejects.toBe(retryError);
  expect(apiMocks.startInteractiveTravel).toHaveBeenCalledTimes(2);
});
