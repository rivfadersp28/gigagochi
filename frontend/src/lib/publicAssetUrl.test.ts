import { describe, expect, it } from "vitest";

import { isSafePublicAssetUrl, resolveApiAssetUrl } from "./publicAssetUrl";

describe("public asset URL policy", () => {
  it.each([
    "/static/generated/pet.png",
    "generated/pet.png",
    `${window.location.origin}/static/generated/pet.png`,
  ])("accepts an app or configured API asset URL: %s", (url) => {
    expect(isSafePublicAssetUrl(url)).toBe(true);
  });

  it.each([
    "https://evil.example/pet.png",
    "javascript:alert(1)",
    "data:image/png;base64,AAAA",
    "//evil.example/pet.png",
    "https://user:password@evil.example/pet.png",
    "http://127.0.0.1:8000/static/generated/pet.png",
    "generated\\pet.png",
    "generated\0pet.png",
  ])("rejects an untrusted asset URL: %s", (url) => {
    expect(isSafePublicAssetUrl(url)).toBe(false);
  });

  it("keeps API-root relative assets on the production-safe same origin by default", () => {
    expect(resolveApiAssetUrl("/static/generated/pet.png")).toBe(
      "/static/generated/pet.png",
    );
  });
});
