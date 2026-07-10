import { beforeEach, describe, expect, it } from "vitest";

import { readComplimentHistory, rememberCompliment } from "./localPetCompliments";

describe("localPetCompliments", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("keeps compliment meanings isolated by pet", () => {
    rememberCompliment("pet-1", "  редкая   смелость и доброе сердце  ");

    expect(readComplimentHistory("pet-1")).toEqual([
      "редкая смелость и доброе сердце",
    ]);
    expect(readComplimentHistory("pet-2")).toEqual([]);
  });

  it("does not duplicate the same normalized meaning", () => {
    rememberCompliment("pet-1", "Необычная красота");
    rememberCompliment("pet-1", "необычная красота");

    expect(readComplimentHistory("pet-1")).toEqual(["Необычная красота"]);
  });
});
