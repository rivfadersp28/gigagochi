import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearPendingPetGeneration,
  PENDING_GENERATION_STORAGE_KEY,
  readPendingPetGeneration,
  writePendingPetGeneration,
} from "@/lib/pendingPetGeneration";

import { CreatePetForm } from "./CreatePetForm";

const mocks = vi.hoisted(() => {
  class MockApiError extends Error {
    code?: string;
    generationTerminal?: boolean;
    activeJobId?: string;
    activeDescription?: string;

    constructor(
      code?: string,
      generationTerminal = false,
      activeJobId?: string,
      activeDescription?: string,
    ) {
      super(code);
      this.code = code;
      this.generationTerminal = generationTerminal;
      this.activeJobId = activeJobId;
      this.activeDescription = activeDescription;
    }
  }

  return {
    MockApiError,
    generatePetAssets: vi.fn(),
    resumePetGeneration: vi.fn(),
    readLocalPetState: vi.fn(),
    push: vi.fn(),
    replace: vi.fn(),
    create: vi.fn(),
    hookPet: null as { petId: string } | null,
    hookStatus: "empty" as "empty" | "ready",
  };
});
const {
  MockApiError,
  generatePetAssets,
  resumePetGeneration,
  readLocalPetState,
  push,
  create,
} = mocks;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
}));

vi.mock("@/lib/api", () => ({
  ApiError: mocks.MockApiError,
  generatePetAssets: (...args: unknown[]) => mocks.generatePetAssets(...args),
  resumePetGeneration: (...args: unknown[]) => mocks.resumePetGeneration(...args),
}));

vi.mock("@/lib/localPetStorage", () => ({
  readLocalPetState: () => mocks.readLocalPetState(),
}));

vi.mock("@/lib/telegram", () => ({
  canUseDebugMenu: () => false,
  hapticNotification: vi.fn(),
}));

vi.mock("@/lib/useTelegramCapabilities", () => ({
  useTelegramCapabilities: () => ({ debugMenu: false, interactiveTravel: false }),
}));

vi.mock("@/lib/useLocalPetState", () => ({
  useLocalPetState: () => ({
    create: mocks.create,
    createDurably: (...args: unknown[]) => mocks.create(...args),
    pet: mocks.hookPet,
    status: mocks.hookStatus,
  }),
}));

vi.mock("./PetCreatingStage", () => ({
  PetCreatingStage: () => <div>Создаём питомца</div>,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function submitDescription(value: string) {
  fireEvent.change(screen.getByLabelText("Опиши своего друга"), {
    target: { value },
  });
  fireEvent.click(screen.getByRole("button", { name: "Создать персонажа" }));
}

describe("CreatePetForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearPendingPetGeneration();
    window.localStorage.clear();
    readLocalPetState.mockReturnValue(null);
    create.mockReturnValue({ petId: "pet-1" });
    generatePetAssets.mockReturnValue(new Promise(() => undefined));
    resumePetGeneration.mockReturnValue(new Promise(() => undefined));
    mocks.hookPet = null;
    mocks.hookStatus = "empty";
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("persists a request key before POST and then attaches the returned job id", async () => {
    let markerAtCall: unknown;
    generatePetAssets.mockImplementation(() => {
      markerAtCall = readPendingPetGeneration();
      return new Promise(() => undefined);
    });
    render(<CreatePetForm />);

    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    submitDescription("Дракон с медным фонарём");

    await waitFor(() => expect(generatePetAssets).toHaveBeenCalledTimes(1));
    const options = generatePetAssets.mock.calls[0]?.[1];
    expect(markerAtCall).toMatchObject({
      description: "Дракон с медным фонарём",
      requestKey: options.requestKey,
    });
    expect(options).toEqual(expect.objectContaining({
      requestKey: expect.any(String),
      onJobQueued: expect.any(Function),
    }));

    act(() => options.onJobQueued("job-1"));
    expect(readPendingPetGeneration()).toMatchObject({
      requestKey: options.requestKey,
      jobId: "job-1",
    });
  });

  it("does not start a paid POST when the recovery marker cannot be persisted", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });
    render(<CreatePetForm />);

    submitDescription("мышонок");

    expect(await screen.findByText(/Не удалось сохранить прогресс/)).toBeInTheDocument();
    expect(generatePetAssets).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Создать персонажа" })).toBeEnabled();
    setItem.mockRestore();
  });

  it("does not auto-resume a marker that was never durable", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });
    expect(writePendingPetGeneration({
      description: "мышонок",
      requestKey: "pet-request-memory-only-0001",
    })).toBe(false);

    render(<CreatePetForm />);

    expect(screen.getByLabelText("Опиши своего друга")).toBeEnabled();
    expect(generatePetAssets).not.toHaveBeenCalled();
    expect(resumePetGeneration).not.toHaveBeenCalled();
    setItem.mockRestore();
  });

  it("reposts the same key after reload when the 202 response was lost", async () => {
    writePendingPetGeneration({
      description: "мышонок",
      requestKey: "pet-request-reload-0001",
    });

    render(<CreatePetForm />);

    await waitFor(() => {
      expect(generatePetAssets).toHaveBeenCalledWith(
        "мышонок",
        expect.objectContaining({ requestKey: "pet-request-reload-0001" }),
      );
    });
    expect(resumePetGeneration).not.toHaveBeenCalled();
  });

  it("does not resume a stale marker after a pet already exists", async () => {
    writePendingPetGeneration({
      description: "мышонок",
      requestKey: "pet-request-stale-0001",
    });
    readLocalPetState.mockReturnValue({ petId: "pet-existing" });

    render(<CreatePetForm />);

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/pet/pet-existing"));
    expect(generatePetAssets).not.toHaveBeenCalled();
    expect(resumePetGeneration).not.toHaveBeenCalled();
    expect(readPendingPetGeneration()).toBeNull();
  });

  it("clears a crash-window marker when the hydrated hook already has the pet", async () => {
    writePendingPetGeneration({
      description: "мышонок",
      requestKey: "pet-request-crash-window-0001",
    });
    mocks.hookPet = { petId: "pet-existing" };
    mocks.hookStatus = "ready";

    render(<CreatePetForm />);

    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/pet/pet-existing"));
    expect(readPendingPetGeneration()).toBeNull();
    expect(generatePetAssets).not.toHaveBeenCalled();
    expect(resumePetGeneration).not.toHaveBeenCalled();
  });

  it("continues polling after reload when the job id is known", async () => {
    writePendingPetGeneration({
      description: "мышонок",
      requestKey: "pet-request-reload-0002",
      jobId: "job-2",
    });

    render(<CreatePetForm />);

    await waitFor(() => expect(resumePetGeneration).toHaveBeenCalledWith(
      "job-2",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
    expect(generatePetAssets).not.toHaveBeenCalled();
  });

  it("attaches a cross-device active job and resumes it after submit conflict", async () => {
    let markerAtResume: ReturnType<typeof readPendingPetGeneration> = null;
    generatePetAssets.mockRejectedValue(
      new MockApiError(
        "GENERATION_ALREADY_ACTIVE",
        false,
        "job-active-submit",
        "мышонок",
      ),
    );
    resumePetGeneration.mockImplementation(() => {
      const requestKey = generatePetAssets.mock.calls[0]?.[1].requestKey;
      markerAtResume = readPendingPetGeneration(requestKey);
      return Promise.resolve({ assetSetId: "assets-recovered" });
    });
    render(<CreatePetForm />);

    submitDescription("дракон");

    await waitFor(() => expect(resumePetGeneration).toHaveBeenCalledWith(
      "job-active-submit",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
    expect(markerAtResume).toMatchObject({
      description: "мышонок",
      jobId: "job-active-submit",
    });
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith("мышонок", { assetSetId: "assets-recovered" });
    expect(generatePetAssets).toHaveBeenCalledOnce();
  });

  it("attaches a cross-device active job during automatic reload recovery", async () => {
    writePendingPetGeneration({
      description: "дракон",
      requestKey: "pet-request-cross-device-0001",
    });
    generatePetAssets.mockRejectedValue(
      new MockApiError(
        "GENERATION_ALREADY_ACTIVE",
        false,
        "job-active-reload",
        "мышонок",
      ),
    );
    resumePetGeneration.mockResolvedValue({ assetSetId: "assets-recovered" });

    render(<CreatePetForm />);

    await waitFor(() => expect(resumePetGeneration).toHaveBeenCalledWith(
      "job-active-reload",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
    expect(generatePetAssets).toHaveBeenCalledWith(
      "дракон",
      expect.objectContaining({ requestKey: "pet-request-cross-device-0001" }),
    );
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith("мышонок", { assetSetId: "assets-recovered" });
  });

  it("does not poll an adopted active job until its recovery marker is durable", async () => {
    const nativeSetItem = window.localStorage.setItem.bind(window.localStorage);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation((key, value) => {
      if (value.includes('"jobId"')) {
        throw new DOMException("quota exceeded", "QuotaExceededError");
      }
      nativeSetItem(key, value);
    });
    generatePetAssets.mockRejectedValue(
      new MockApiError(
        "GENERATION_ALREADY_ACTIVE",
        false,
        "job-active-not-durable",
        "мышонок",
      ),
    );
    render(<CreatePetForm />);

    submitDescription("дракон");

    expect(await screen.findByText(/Не удалось сохранить прогресс/)).toBeInTheDocument();
    expect(resumePetGeneration).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(PENDING_GENERATION_STORAGE_KEY)).not.toContain(
      '"jobId"',
    );
  });

  it.each([
    ["missing", undefined],
    ["malformed", "x".repeat(301)],
  ])("does not adopt an active job when its description is %s", async (_label, activeDescription) => {
    generatePetAssets.mockRejectedValue(
      new MockApiError(
        "GENERATION_ALREADY_ACTIVE",
        false,
        "job-active-without-description",
        activeDescription,
      ),
    );
    render(<CreatePetForm />);

    submitDescription("мышонок");

    await waitFor(() => expect(screen.getByLabelText("Опиши своего друга")).toBeEnabled());
    expect(resumePetGeneration).not.toHaveBeenCalled();
    expect(generatePetAssets).toHaveBeenCalledOnce();
    expect(readPendingPetGeneration()).toMatchObject({ description: "мышонок" });
    expect(readPendingPetGeneration()?.jobId).toBeUndefined();
  });

  it("keeps the marker and restores the prompt after an auto-resume network failure", async () => {
    writePendingPetGeneration({
      description: "мышонок",
      requestKey: "pet-request-reload-0003",
      jobId: "job-3",
    });
    resumePetGeneration.mockRejectedValue(new Error("network lost"));

    render(<CreatePetForm />);

    await waitFor(() => expect(screen.getByLabelText("Опиши своего друга")).toHaveValue("мышонок"));
    expect(readPendingPetGeneration()).toMatchObject({
      requestKey: "pet-request-reload-0003",
      jobId: "job-3",
    });
  });

  it("clears the marker after a confirmed missing job", async () => {
    writePendingPetGeneration({
      description: "мышонок",
      requestKey: "pet-request-reload-0004",
      jobId: "job-missing",
    });
    resumePetGeneration.mockRejectedValue(new MockApiError("GENERATION_JOB_NOT_FOUND"));

    render(<CreatePetForm />);

    await waitFor(() => expect(screen.getByLabelText("Опиши своего друга")).toBeEnabled());
    expect(readPendingPetGeneration()).toBeNull();
  });

  it("keeps the key after a network failure but rotates it after a changed prompt", async () => {
    generatePetAssets.mockRejectedValueOnce(new Error("network lost"));
    render(<CreatePetForm />);
    submitDescription("мышонок");

    await waitFor(() => expect(screen.getByLabelText("Опиши своего друга")).toBeEnabled());
    const firstKey = generatePetAssets.mock.calls[0]?.[1].requestKey;
    generatePetAssets.mockRejectedValueOnce(new Error("network lost again"));
    fireEvent.click(screen.getByRole("button", { name: "Создать персонажа" }));
    await waitFor(() => expect(generatePetAssets).toHaveBeenCalledTimes(2));
    expect(generatePetAssets.mock.calls[1]?.[1].requestKey).toBe(firstKey);

    await waitFor(() => expect(screen.getByLabelText("Опиши своего друга")).toBeEnabled());
    generatePetAssets.mockReturnValue(new Promise(() => undefined));
    submitDescription("дракон");
    await waitFor(() => expect(generatePetAssets).toHaveBeenCalledTimes(3));
    expect(generatePetAssets.mock.calls[2]?.[1].requestKey).not.toBe(firstKey);
  });

  it("rotates the key after a confirmed terminal generation failure", async () => {
    generatePetAssets.mockRejectedValueOnce(new MockApiError("GENERATION_FAILED", true));
    render(<CreatePetForm />);
    submitDescription("мышонок");

    await waitFor(() => expect(screen.getByLabelText("Опиши своего друга")).toBeEnabled());
    const failedKey = generatePetAssets.mock.calls[0]?.[1].requestKey;
    expect(readPendingPetGeneration()).toBeNull();

    generatePetAssets.mockReturnValue(new Promise(() => undefined));
    fireEvent.click(screen.getByRole("button", { name: "Создать персонажа" }));
    await waitFor(() => expect(generatePetAssets).toHaveBeenCalledTimes(2));
    expect(generatePetAssets.mock.calls[1]?.[1].requestKey).not.toBe(failedKey);
  });

  it("fences a late result after a newer request replaces the single marker", async () => {
    const generation = deferred<Record<string, unknown>>();
    generatePetAssets.mockReturnValue(generation.promise);
    render(<CreatePetForm />);
    submitDescription("мышонок");
    await waitFor(() => expect(generatePetAssets).toHaveBeenCalledTimes(1));

    writePendingPetGeneration({
      description: "дракон",
      requestKey: "pet-request-newer-0001",
    });
    await act(async () => generation.resolve({ assetSetId: "late-assets" }));

    await waitFor(() => expect(screen.getByLabelText("Опиши своего друга")).toBeEnabled());
    expect(create).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
    expect(readPendingPetGeneration()).toMatchObject({
      requestKey: "pet-request-newer-0001",
    });
  });

  it("clears the marker after success", async () => {
    generatePetAssets.mockResolvedValue({ assetSetId: "assets-1" });
    render(<CreatePetForm />);
    submitDescription("мышонок");

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(readPendingPetGeneration()).toBeNull();
    expect(window.localStorage.getItem(PENDING_GENERATION_STORAGE_KEY)).toBeNull();
    expect(push).toHaveBeenCalledWith("/pet/pet-1");
  });

  it("keeps the recovery marker when the paid pet state is not durable", async () => {
    generatePetAssets.mockResolvedValue({ assetSetId: "assets-1" });
    create.mockReturnValueOnce(null);
    render(<CreatePetForm />);
    submitDescription("мышонок");

    expect(await screen.findByText(/Не удалось сохранить прогресс/)).toBeInTheDocument();
    expect(readPendingPetGeneration()).toMatchObject({ description: "мышонок" });
    expect(push).not.toHaveBeenCalled();
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("aborts generation polling when the form unmounts", async () => {
    const { unmount } = render(<CreatePetForm />);
    submitDescription("мышонок");
    await waitFor(() => expect(generatePetAssets).toHaveBeenCalledOnce());
    const signal = generatePetAssets.mock.calls[0]?.[1].signal as AbortSignal;
    expect(signal.aborted).toBe(false);

    unmount();

    expect(signal.aborted).toBe(true);
    expect(readPendingPetGeneration()).not.toBeNull();
  });
});
