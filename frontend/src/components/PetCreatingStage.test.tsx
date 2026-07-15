import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { PetCreatingStage } from "./PetCreatingStage";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("uses a static loader instead of WebGL when reduced motion is requested", () => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockReturnValue({
      matches: true,
      media: "(prefers-reduced-motion: reduce)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }),
  });

  const { container } = render(<PetCreatingStage />);

  expect(container.querySelector("[data-creation-loader-static='true']"))
    .toBeInTheDocument();
  expect(container.querySelector("canvas")).not.toBeInTheDocument();
});
