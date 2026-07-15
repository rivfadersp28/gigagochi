import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { DraggableFoodToken } from "./DraggableFoodToken";

it("cancels a drag without feeding the pet", () => {
  const onDrop = vi.fn(() => true);
  const onActivate = vi.fn(() => true);
  render(
    <DraggableFoodToken
      food={{ id: "berry-bowl", label: "ягоды", src: "/berry.png", rotation: 0 }}
      disabled={false}
      onDrop={onDrop}
      onActivate={onActivate}
    />,
  );
  const button = screen.getByRole("button", { name: "Дать персонажу ягоды" });
  Object.defineProperties(button, {
    setPointerCapture: { configurable: true, value: vi.fn() },
    hasPointerCapture: { configurable: true, value: vi.fn(() => true) },
    releasePointerCapture: { configurable: true, value: vi.fn() },
  });

  fireEvent.pointerDown(button, { pointerId: 4, clientX: 10, clientY: 20 });
  fireEvent.pointerMove(button, { pointerId: 4, clientX: 80, clientY: 90 });
  fireEvent.pointerCancel(button, { pointerId: 4, clientX: 80, clientY: 90 });
  fireEvent.click(button);

  expect(onDrop).not.toHaveBeenCalled();
  expect(onActivate).not.toHaveBeenCalled();
});
