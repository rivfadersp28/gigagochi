import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HomeRedirect } from "./HomeRedirect";

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  pet: null as { petId: string } | null,
  status: "loading" as "loading" | "empty" | "ready" | "error",
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));

vi.mock("@/lib/useLocalPetState", () => ({
  useLocalPetState: () => ({ pet: mocks.pet, status: mocks.status }),
}));

describe("HomeRedirect", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.pet = null;
    mocks.status = "loading";
  });

  afterEach(cleanup);

  it("routes an existing pet to its dashboard", async () => {
    mocks.pet = { petId: "pet-1" };
    mocks.status = "ready";

    render(<HomeRedirect />);

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/pet/pet-1"));
  });

  it("routes an empty state to the dedicated creation page", async () => {
    mocks.status = "empty";

    render(<HomeRedirect />);

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/create"));
  });
});
