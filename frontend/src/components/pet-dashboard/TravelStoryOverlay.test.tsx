import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { TravelStoryOverlay } from "./TravelStoryOverlay";

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class ResizeObserver {
      observe() {}
      disconnect() {}
    },
  );
});

afterEach(cleanup);

describe("TravelStoryOverlay", () => {
  it("renders the archive dialog and its story cards", () => {
    render(
      <TravelStoryOverlay
        stories={[
          {
            id: "story-1",
            title: "Кочка под туманом",
            text: "Мышонок выбрался из канавы.",
            imageUrl: "/static/generated/story.png",
          },
        ]}
        onClose={() => undefined}
      />,
    );

    expect(screen.getByRole("dialog", { name: "Истории" })).toBeInTheDocument();
    expect(screen.getByText("Мышонок выбрался из канавы.")).toBeInTheDocument();
  });

  it("pages to the next story from the keyboard", () => {
    const clientHeightSpy = vi
      .spyOn(HTMLElement.prototype, "clientHeight", "get")
      .mockReturnValue(800);

    render(
      <TravelStoryOverlay
        stories={[
          { id: "story-1", title: "Первая", text: "Первая история" },
          { id: "story-2", title: "Вторая", text: "Вторая история" },
        ]}
        onClose={() => undefined}
      />,
    );

    const scroller = screen.getByLabelText("Истории: листайте вверх или вниз");
    const scrollTo = vi.fn();
    Object.defineProperty(scroller, "scrollTo", { value: scrollTo });

    fireEvent.keyDown(scroller, { key: "PageDown" });

    expect(scrollTo).toHaveBeenCalledWith({ top: 464, behavior: "smooth" });
    clientHeightSpy.mockRestore();
  });
});
