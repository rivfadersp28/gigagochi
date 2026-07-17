import { beforeEach, describe, expect, it } from "vitest";

import {
  clearTravelVideoPrototypeRequest,
  prepareTravelVideoPrototypeRequest,
  readPendingTravelVideoPrototype,
  setPendingTravelVideoPrototypeJobId,
} from "./pendingTravelVideoPrototype";

describe("pending travel video prototype", () => {
  beforeEach(() => window.localStorage.clear());

  it("keeps the accepted job until delivery polling reaches a terminal state", () => {
    const requestKey = prepareTravelVideoPrototypeRequest("pet-1", "К морю");
    const jobId = "travel-video-prototype-0123456789abcdef0123456789abcdef";

    expect(setPendingTravelVideoPrototypeJobId("pet-1", requestKey, jobId)).toMatchObject({
      requestKey,
      jobId,
    });
    expect(readPendingTravelVideoPrototype("pet-1")?.jobId).toBe(jobId);

    clearTravelVideoPrototypeRequest("pet-1", requestKey);
    expect(readPendingTravelVideoPrototype("pet-1")).toBeNull();
  });
});
