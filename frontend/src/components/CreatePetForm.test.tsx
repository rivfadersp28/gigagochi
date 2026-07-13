import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CreatePetForm } from "./CreatePetForm";

const generatePetAssets = vi.fn();
const push = vi.fn();
const replace = vi.fn();
const create = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace }),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  generatePetAssets: (...args: unknown[]) => generatePetAssets(...args),
  resumePetGeneration: vi.fn(),
}));

vi.mock("@/lib/telegram", () => ({
  canUseDebugMenu: () => false,
  hapticNotification: vi.fn(),
}));

vi.mock("@/lib/useLocalPetState", () => ({
  useLocalPetState: () => ({
    create,
    pet: null,
    status: "empty",
  }),
}));

vi.mock("./PetCreatingStage", () => ({
  PetCreatingStage: () => <div>Создаём питомца</div>,
}));

describe("CreatePetForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    generatePetAssets.mockReturnValue(new Promise(() => undefined));
  });

  it("starts the default generation pipeline without provider controls", async () => {
    render(<CreatePetForm />);

    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Опиши своего друга"), {
      target: { value: "Дракон с медным фонарём" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать персонажа" }));

    await waitFor(() => {
      expect(generatePetAssets).toHaveBeenCalledWith(
        "Дракон с медным фонарём",
        expect.objectContaining({ onJobQueued: expect.any(Function) }),
      );
    });
  });
});
