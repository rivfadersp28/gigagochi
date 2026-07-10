import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LocalPetState } from "@/lib/types";

import { DebugPanel } from "./DebugPanel";

function pet(): LocalPetState {
  const timestamp = "2026-07-10T10:00:00.000Z";
  return {
    version: 2,
    petId: "pet-1",
    description: "мышонок",
    createdAt: timestamp,
    updatedAt: timestamp,
    lastInteractionAt: timestamp,
    lastStatsTickAt: timestamp,
    lastStatTickAt: {
      hunger: timestamp,
      happiness: timestamp,
      energy: timestamp,
    },
    stage: "teen",
    mood: "idle",
    stats: { hunger: 80, happiness: 80, energy: 80 },
  };
}

describe("DebugPanel visual mode selector", () => {
  it("selects a mode and clears the override on a repeated click", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <DebugPanel
        pet={pet()}
        isOpen
        onClose={() => undefined}
        canShowSadAsset
        canShowHappyAsset
        visualModeOverride={null}
        onVisualModeOverrideChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Счастливый" }));
    expect(onChange).toHaveBeenLastCalledWith("happy");

    rerender(
      <DebugPanel
        pet={pet()}
        isOpen
        onClose={() => undefined}
        canShowSadAsset
        canShowHappyAsset
        visualModeOverride="happy"
        onVisualModeOverrideChange={onChange}
      />,
    );
    expect(screen.getByRole("button", { name: "Счастливый" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    fireEvent.click(screen.getByRole("button", { name: "Счастливый" }));
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("disables generated variants until both asset parts are ready", () => {
    render(
      <DebugPanel
        pet={pet()}
        isOpen
        onClose={() => undefined}
        onVisualModeOverrideChange={() => undefined}
      />,
    );

    expect(screen.getByRole("button", { name: "Грустный" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Счастливый" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Нормальный" })).toBeEnabled();
  });
});
