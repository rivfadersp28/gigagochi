import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatProgressRing } from "./StatProgressRing";

describe("StatProgressRing", () => {
  it("updates the visible arc and tone when the value changes", () => {
    const { container, rerender } = render(
      <StatProgressRing value={25} kind="mood" />,
    );
    const ring = container.querySelector(".stat-progress-ring");
    const progress = container.querySelector(".stat-progress-ring__progress");
    const initialOffset = Number(progress?.getAttribute("stroke-dashoffset"));

    expect(ring).toHaveClass("stat-progress-ring--low");

    rerender(<StatProgressRing value={75} kind="mood" />);

    expect(ring).toHaveClass("stat-progress-ring--high");
    expect(Number(progress?.getAttribute("stroke-dashoffset"))).toBeLessThan(initialOffset);
  });
});
