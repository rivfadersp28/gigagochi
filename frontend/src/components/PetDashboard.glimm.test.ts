import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const source = readFileSync("src/components/PetDashboard.tsx", "utf8");

describe("PetDashboard Glimm scene transition", () => {
  it("keeps the incoming video hidden until Glimm reveals the replacement scene", () => {
    expect(source).toContain("const { sweep } = useGlimm()");
    expect(source).toContain("revealWhenReady={revealSceneVideo}");
    expect(source).toContain("sweep(reveal)");
  });
});
