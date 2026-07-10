import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PetCharacterMessage } from "./PetCharacterMessage";

const hapticImpactMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/telegram", () => ({
  hapticImpact: hapticImpactMock,
}));

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
    vi.useRealTimers();
    vi.restoreAllMocks();
    hapticImpactMock.mockReset();
  });

  it("plays rigid haptic feedback as each animated character appears", () => {
    vi.useFakeTimers();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: false }),
    });
    const originalAnimate = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "animate");
    Object.defineProperty(HTMLElement.prototype, "animate", {
      configurable: true,
      value: vi.fn().mockImplementation(
        () =>
          ({
            cancel: vi.fn(),
            finished: new Promise<Animation>(() => undefined),
          }) as unknown as Animation,
      ),
    });

    try {
      render(
        <PetCharacterMessage
          message={{ id: 1, text: "А Б", playSpeechAudio: false, hasNextPortion: false }}
          textTranslateY={0}
          speechEndTrimMs={0}
        />,
      );

      vi.advanceTimersByTime(0);
      expect(hapticImpactMock).toHaveBeenCalledTimes(1);
      expect(hapticImpactMock).toHaveBeenLastCalledWith("rigid");

      vi.advanceTimersByTime(23);
      expect(hapticImpactMock).toHaveBeenCalledTimes(1);

      vi.advanceTimersByTime(1);
      expect(hapticImpactMock).toHaveBeenCalledTimes(2);
      expect(hapticImpactMock).toHaveBeenLastCalledWith("rigid");
    } finally {
      if (originalAnimate) {
        Object.defineProperty(HTMLElement.prototype, "animate", originalAnimate);
      } else {
        delete (HTMLElement.prototype as Partial<HTMLElement>).animate;
      }
    }
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
        message={{ id: 1, text: longMessage, playSpeechAudio: false, hasNextPortion: false }}
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
        message={{
          id: 1,
          text: "Короткая фраза.",
          playSpeechAudio: false,
          hasNextPortion: false,
        }}
        textTranslateY={0}
        speechEndTrimMs={0}
        maxCurrentMessageLines={2}
      />,
    );

    expect(screen.getByLabelText("Короткая фраза.")).toHaveStyle({ fontSize: "26px" });
  });

  it("never reduces the font below twelve pixels", () => {
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      const fontSize = Number.parseFloat(this.style.fontSize || "26");
      return fontSize * 1.15 * 4;
    });

    render(
      <PetCharacterMessage
        message={{ id: 1, text: longMessage, playSpeechAudio: false, hasNextPortion: false }}
        textTranslateY={0}
        speechEndTrimMs={0}
        maxCurrentMessageLines={2}
      />,
    );

    expect(screen.getByLabelText(longMessage)).toHaveStyle({ fontSize: "12px" });
  });

  it("reduces the font when the final word with dots overflows horizontally", () => {
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      const fontSize = Number.parseFloat(this.style.fontSize || "26");
      return fontSize * 1.15 * 2;
    });
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(270);
    vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      const fontSize = Number.parseFloat(this.style.fontSize || "26");
      return fontSize > 14 ? 280 : 260;
    });

    render(
      <PetCharacterMessage
        message={{
          id: 1,
          text: "Сверхдлинноепредыдущееслово",
          playSpeechAudio: false,
          hasNextPortion: true,
        }}
        textTranslateY={0}
        speechEndTrimMs={0}
        maxCurrentMessageLines={2}
      />,
    );

    expect(screen.getByLabelText("Сверхдлинноепредыдущееслово")).toHaveStyle({
      fontSize: "14px",
    });
  });

  it("renders three continuation dots after a message portion", () => {
    const { container } = render(
      <PetCharacterMessage
        message={{ id: 1, text: "Не знаю", playSpeechAudio: false, hasNextPortion: true }}
        textTranslateY={0}
        speechEndTrimMs={0}
      />,
    );

    expect(
      container.querySelectorAll("[data-pet-message-continuation-dot]"),
    ).toHaveLength(3);
    expect(
      container.querySelector("[data-pet-message-continuation='true']"),
    ).toBeInTheDocument();
    expect(
      container.querySelector("[data-pet-message-continuation='true']"),
    ).toHaveAttribute("data-pet-message-continuation-delay", "220");
    expect(
      Array.from(
        container.querySelectorAll("[data-pet-message-continuation-enter-delay]"),
        (unit) => unit.getAttribute("data-pet-message-continuation-enter-delay"),
      ),
    ).toEqual(["220", "244", "268"]);
  });

  it("does not render continuation dots for the final portion", () => {
    const { container } = render(
      <PetCharacterMessage
        message={{
          id: 1,
          text: "Это последняя порция",
          playSpeechAudio: false,
          hasNextPortion: false,
        }}
        textTranslateY={0}
        speechEndTrimMs={0}
      />,
    );

    expect(
      container.querySelector("[data-pet-message-continuation='true']"),
    ).not.toBeInTheDocument();
  });
});
