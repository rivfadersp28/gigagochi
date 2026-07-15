import { beforeEach, describe, expect, it } from "vitest";

import {
  claimPetTapThanksForSession,
  clearPetTapThanksForSession,
  petTapThanksReply,
} from "./petTapThanks";

describe("pet tap thanks", () => {
  beforeEach(() => window.sessionStorage.clear());

  it("can be claimed only once per pet in a browser session", () => {
    expect(claimPetTapThanksForSession("pet-1")).toBe(true);
    expect(claimPetTapThanksForSession("pet-1")).toBe(false);
    expect(claimPetTapThanksForSession("pet-2")).toBe(true);
  });

  it("selects different predefined replies", () => {
    expect(petTapThanksReply(0)).toBe("Приятно!");
    expect(petTapThanksReply(0.99)).toBe("Мне нравится!");
  });

  it("clears the deleted pet session claim", () => {
    expect(claimPetTapThanksForSession("pet-clear")).toBe(true);
    clearPetTapThanksForSession("pet-clear");
    expect(claimPetTapThanksForSession("pet-clear")).toBe(true);
  });
});
