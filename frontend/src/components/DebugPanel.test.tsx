import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LocalPetState } from "@/lib/types";

import { DebugPanel } from "./DebugPanel";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

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

describe("DebugPanel pet life controls", () => {
  it("kills a living pet and enables resurrection only after death", () => {
    const onKillPet = vi.fn();
    const onRevivePet = vi.fn();
    const { rerender } = render(
      <DebugPanel
        pet={pet()}
        isOpen
        onClose={() => undefined}
        onKillPet={onKillPet}
        onRevivePet={onRevivePet}
      />,
    );

    const killButton = screen.getByRole("button", { name: "Убить персонажа" });
    const reviveButton = screen.getByRole("button", {
      name: "Воскресить последнего персонажа",
    });
    expect(killButton).toBeEnabled();
    expect(reviveButton).toBeDisabled();
    fireEvent.click(killButton);
    expect(onKillPet).toHaveBeenCalledOnce();

    rerender(
      <DebugPanel
        pet={{ ...pet(), diedAt: "2026-07-10T11:00:00.000Z" }}
        isOpen
        isPetDead
        onClose={() => undefined}
        onKillPet={onKillPet}
        onRevivePet={onRevivePet}
      />,
    );
    expect(screen.getByRole("button", { name: "Убить персонажа" })).toBeDisabled();
    const enabledReviveButton = screen.getByRole("button", {
      name: "Воскресить последнего персонажа",
    });
    expect(enabledReviveButton).toBeEnabled();
    fireEvent.click(enabledReviveButton);
    expect(onRevivePet).toHaveBeenCalledOnce();
  });
});

describe("DebugPanel generation stats", () => {
  it("loads personal generation durations on demand", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        windowDays: 30,
        totalJobs: 1,
        activeJobs: 0,
        failedJobs: 0,
        normal: {
          count: 1,
          averageSeconds: 120,
          medianSeconds: 120,
          p95Seconds: 120,
          minSeconds: 120,
          maxSeconds: 120,
        },
        full: {
          count: 1,
          averageSeconds: 600,
          medianSeconds: 600,
          p95Seconds: 600,
          minSeconds: 600,
          maxSeconds: 600,
        },
        recent: [{
          jobId: "job-123456",
          ownerName: "Сергей",
          queuedAt: "2026-07-12T10:00:00Z",
          status: "completed",
          normalSeconds: 120,
          fullSeconds: 600,
        }],
      }), { status: 200, headers: { "Content-Type": "application/json" } }),
    ));

    render(<DebugPanel pet={pet()} isOpen onClose={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Генерация" }));

    await waitFor(() => expect(screen.getByText("До входа")).toBeInTheDocument());
    expect(screen.getAllByText("2 мин 0 с").length).toBeGreaterThan(0);
    expect(screen.getByText("Сергей")).toBeInTheDocument();
  });
});
