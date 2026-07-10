import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PetCharacterMessage } from "./PetCharacterMessage";

const longMessage = "Это длинное предложение, которое должно остаться в одной порции.";

describe("PetCharacterMessage", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: true }),
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("reduces the font in one-pixel steps until the sentence fits two lines", () => {
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      const fontSize = Number.parseFloat(this.style.fontSize || "26");
      const renderedLines = fontSize > 18 ? 3 : 2;
      return fontSize * 1.15 * renderedLines;
    });

    render(
      <PetCharacterMessage
        message={{ id: 1, text: longMessage, playSpeechAudio: false }}
        textTranslateY={0}
        speechEndTrimMs={0}
        maxCurrentMessageLines={2}
      />,
    );

    expect(screen.getByLabelText(longMessage)).toHaveStyle({ fontSize: "18px" });
  });

  it("keeps the default font size when the sentence already fits", () => {
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      const fontSize = Number.parseFloat(this.style.fontSize || "26");
      return fontSize * 1.15 * 2;
    });

    render(
      <PetCharacterMessage
        message={{ id: 1, text: "Короткая фраза.", playSpeechAudio: false }}
        textTranslateY={0}
        speechEndTrimMs={0}
        maxCurrentMessageLines={2}
      />,
    );

    expect(screen.getByLabelText("Короткая фраза.")).toHaveStyle({ fontSize: "26px" });
  });

  it("can reduce the font down to twelve pixels for a long sentence", () => {
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      const fontSize = Number.parseFloat(this.style.fontSize || "26");
      return fontSize * 1.15 * 4;
    });

    render(
      <PetCharacterMessage
        message={{ id: 1, text: longMessage, playSpeechAudio: false }}
        textTranslateY={0}
        speechEndTrimMs={0}
        maxCurrentMessageLines={2}
      />,
    );

    expect(screen.getByLabelText(longMessage)).toHaveStyle({ fontSize: "12px" });
  });
});
