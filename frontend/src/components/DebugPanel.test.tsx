import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LocalPetAssetSet, LocalPetState } from "@/lib/types";

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

function generatedAssetSet(
  overrides: Partial<LocalPetAssetSet> = {},
): LocalPetAssetSet {
  const images = {
    baby: { idle: "/idle.png", happy: "", hungry: "/idle.png", sad: "" },
    teen: { idle: "/idle.png", happy: "", hungry: "/idle.png", sad: "" },
    adult: { idle: "/idle.png", happy: "", hungry: "/idle.png", sad: "" },
  };
  return {
    assetSetId: "assets-1",
    generatedAt: "2026-07-10T10:00:00.000Z",
    images,
    generationJobId: "job-1",
    backgroundGenerationStatus: "running",
    backgroundGenerationPhase: "generating_sad_image",
    ...overrides,
  };
}

describe("DebugPanel visual mode selector", () => {
  it("switches to Kandinsky only after comparison assets are ready", () => {
    const onProviderChange = vi.fn();
    const { rerender } = render(
      <DebugPanel
        pet={pet()}
        isOpen
        onClose={() => undefined}
        visualProvider="openai"
        onVisualProviderChange={onProviderChange}
      />,
    );

    expect(screen.getByRole("button", { name: "OpenAI" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Kandinsky" })).toBeDisabled();

    rerender(
      <DebugPanel
        pet={pet()}
        isOpen
        onClose={() => undefined}
        visualProvider="openai"
        canShowKandinskyAssets
        onVisualProviderChange={onProviderChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Kandinsky" }));
    expect(onProviderChange).toHaveBeenCalledWith("kandinsky");
  });

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

describe("DebugPanel background generation status", () => {
  it("shows Kandinsky as a parallel running pipeline", () => {
    render(
      <DebugPanel
        pet={{ ...pet(), assetSet: generatedAssetSet() }}
        isOpen
        onClose={() => undefined}
      />,
    );

    const progress = screen.getByRole("region", { name: "Фоновая генерация" });
    expect(within(progress).getByText("Основные ассеты")).toBeInTheDocument();
    expect(within(progress).getByText("1/4")).toBeInTheDocument();
    expect(within(progress).getByText("Kandinsky")).toBeInTheDocument();
    expect(within(progress).getByRole("status")).toHaveTextContent("Генерируется");
  });

  it("shows ready Kandinsky assets while the primary pipeline is still running", () => {
    const kandinskyAssets = generatedAssetSet({
      assetSetId: "kandinsky-1",
      generationJobId: undefined,
      backgroundGenerationStatus: undefined,
      backgroundGenerationPhase: undefined,
    });
    render(
      <DebugPanel
        pet={{
          ...pet(),
          assetSet: generatedAssetSet({ kandinskyAssets }),
        }}
        isOpen
        onClose={() => undefined}
      />,
    );

    const progress = screen.getByRole("region", { name: "Фоновая генерация" });
    expect(within(progress).getByRole("status")).toHaveTextContent("Готово");
  });

  it("shows a Kandinsky generation error", () => {
    render(
      <DebugPanel
        pet={{
          ...pet(),
          assetSet: generatedAssetSet({
            comparisonGenerationError: "Провайдер недоступен",
          }),
        }}
        isOpen
        onClose={() => undefined}
      />,
    );

    const progress = screen.getByRole("region", { name: "Фоновая генерация" });
    expect(within(progress).getByRole("status")).toHaveTextContent("Ошибка");
    expect(within(progress).getByText("Провайдер недоступен")).toBeInTheDocument();
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
