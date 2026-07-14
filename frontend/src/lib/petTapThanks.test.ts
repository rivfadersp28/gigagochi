import { beforeEach, describe, expect, it } from "vitest";

import { claimPetTapThanksForSession, petTapThanksReply } from "./petTapThanks";

describe("pet tap thanks", () => {
  beforeEach(() => window.sessionStorage.clear());

  it("can be claimed only once per pet in a browser session", () => {
    expect(claimPetTapThanksForSession("pet-1")).toBe(true);
    expect(claimPetTapThanksForSession("pet-1")).toBe(false);
    expect(claimPetTapThanksForSession("pet-2")).toBe(true);
  });

  it("selects different predefined replies", () => {
    expect(petTapThanksReply(0)).toBe("Мне приятно!");
    expect(petTapThanksReply(0.99)).toBe("Ещё немножко — это так приятно!");
  });
});
