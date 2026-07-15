import { readFileSync } from "node:fs";

import { expect, it } from "vitest";

const css = readFileSync("src/app/globals.css", "utf8");
const travelCss = readFileSync("src/components/InteractiveTravelScreen.module.css", "utf8");

function hexVariable(name: string) {
  const value = css.match(new RegExp(`--${name}:\\s*(#[0-9a-f]{6})`, "iu"))?.[1];
  if (!value) {
    throw new Error(`Missing hex color variable --${name}`);
  }
  return value;
}

function relativeLuminance(hex: string) {
  const channels = [1, 3, 5].map((start) => Number.parseInt(hex.slice(start, start + 2), 16) / 255);
  const linear = channels.map((channel) => (
    channel <= 0.04045
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4
  ));
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrast(foreground: string, background: string) {
  const light = Math.max(relativeLuminance(foreground), relativeLuminance(background));
  const dark = Math.min(relativeLuminance(foreground), relativeLuminance(background));
  return (light + 0.05) / (dark + 0.05);
}

it("keeps error tokens readable and the scene placeholder visually subdued", () => {
  expect(contrast(hexVariable("danger"), hexVariable("danger-bg"))).toBeGreaterThanOrEqual(4.5);
  expect(contrast(hexVariable("ink-faint"), hexVariable("control"))).toBeGreaterThanOrEqual(4.5);
  expect(css).toContain("background: #2b1116;");
  expect(css).toContain("color: rgba(255, 255, 255, 0.3);");
});

it("keeps travel controls readable over the brightest video frame", () => {
  // 94% #161616 over white composites to roughly #242424. The 72% placeholder
  // then composites to roughly #c2c2c2; muted #333 text at 72% becomes #707070.
  expect(contrast("#ffffff", "#242424")).toBeGreaterThanOrEqual(4.5);
  expect(contrast("#c2c2c2", "#242424")).toBeGreaterThanOrEqual(4.5);
  expect(contrast("#707070", "#ffffff")).toBeGreaterThanOrEqual(4.5);
  expect(travelCss.match(/background: rgb\(22 22 22 \/ 94%\);/gu)).toHaveLength(2);
  expect(travelCss).toContain("opacity: 0.72;");
});

it("keeps creation copy and focus indicators visible on variable dark media", () => {
  expect(contrast("#9c9c9c", "#000000")).toBeGreaterThanOrEqual(4.5);
  expect(contrast(hexVariable("main-focus-halo"), "#ffffff")).toBeGreaterThanOrEqual(3);
  expect(css).toContain("--main-focus-ring: rgba(120, 72, 139, 0.36);");
  expect(css).toContain("color: rgba(251, 251, 251, 0.62);");
  expect(css).toContain("color: rgba(255, 255, 255, 0.68);");
  expect(css).toContain("box-shadow: 0 0 0 8px var(--main-focus-halo);");
  expect(css).toContain(".create-pet-prompt:focus-visible");
});

it("keeps main actions and chat input as translucent scene controls", () => {
  expect(css).toContain("--main-scene-surface: rgba(255, 255, 255, 0.15);");
  expect(css).toContain("--main-scene-surface-hover: rgba(255, 255, 255, 0.2);");
  expect(css).toContain("background: rgba(255, 255, 255, 0.15);");
  expect(css).toContain("border: 0;");
  expect(css.match(/rgba\(255, 255, 255, 0\.68\)/gu)?.length).toBeGreaterThanOrEqual(5);
  expect(css).toContain("--tw-backdrop-blur: blur(18.5px)");
  expect(css).toContain("backdrop-filter: blur(18.5px);");
  expect(css.match(/\.travel-story-close\s*\{[^}]*display: grid;/u)).not.toBeNull();
});
