import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearComplimentHistory,
  readComplimentHistory,
  rememberCompliment,
} from "./localPetCompliments";

describe("localPetCompliments", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
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

  it("keeps and retries a ledger write after storage becomes unavailable", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });

    expect(rememberCompliment("storage-pet", "Доброе сердце")).toEqual([
      "Доброе сердце",
    ]);
    expect(readComplimentHistory("storage-pet")).toEqual(["Доброе сердце"]);

    setItem.mockRestore();
    expect(readComplimentHistory("storage-pet")).toEqual(["Доброе сердце"]);
    expect(window.localStorage.getItem("tamagochi:v1:compliment-ledger")).toContain(
      "Доброе сердце",
    );
  });

  it("handles prototype-like pet identifiers as ordinary keys", () => {
    expect(rememberCompliment("__proto__", "Редкая честность")).toEqual([
      "Редкая честность",
    ]);
    expect(readComplimentHistory("__proto__")).toEqual(["Редкая честность"]);
  });

  it("keeps a per-pet clear authoritative while ledger persistence is blocked", () => {
    rememberCompliment("pet-clear", "Старый комплимент");
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });

    clearComplimentHistory("pet-clear");
    expect(readComplimentHistory("pet-clear")).toEqual([]);

    setItem.mockRestore();
    expect(readComplimentHistory("pet-clear")).toEqual([]);
    expect(window.localStorage.getItem("tamagochi:v1:compliment-ledger")).not.toContain(
      "Старый комплимент",
    );
  });
});
