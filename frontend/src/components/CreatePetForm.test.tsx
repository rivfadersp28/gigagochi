import { readFileSync } from "node:fs";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearPendingPetGeneration,
  PENDING_GENERATION_STORAGE_KEY,
  readPendingPetGeneration,
  writePendingPetGeneration,
} from "@/lib/pendingPetGeneration";

import { CreatePetForm } from "./CreatePetForm";

const createPetFormStyles = readFileSync(
  "src/components/CreatePetForm.module.css",
  "utf8",
);
const tiltedGlassButtonStyles = readFileSync(
  "src/components/TiltedGlassButton.module.css",
  "utf8",
);
const screenAppBarStyles = readFileSync(
  "src/components/ScreenAppBar.module.css",
  "utf8",
);

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

  const push = vi.fn();
  const replace = vi.fn();
  return {
    MockApiError,
    generatePetAssets: vi.fn(),
    resumePetGeneration: vi.fn(),
    readLocalPetState: vi.fn(),
    push,
    replace,
    router: { push, replace },
    create: vi.fn(),
    hookPet: null as { petId: string } | null,
    hookStatus: "empty" as "empty" | "ready",
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => mocks.router,
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
    createDurably: mocks.create,
    pet: mocks.hookPet,
    status: mocks.hookStatus,
  }),
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

function submitCustomDescription(value: string) {
  fireEvent.click(screen.getByRole("button", { name: "Свой вариант" }));
  const input = screen.getByLabelText("Свой вариант персонажа");
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: "Enter" });
}

function completeQuestions() {
  fireEvent.click(screen.getByRole("button", { name: "Тото" }));
  fireEvent.click(screen.getByRole("button", { name: "Добрый" }));
  fireEvent.click(screen.getByRole("button", { name: "Пауков" }));
  fireEvent.click(screen.getByRole("button", { name: "Вантуз" }));
}

describe("CreatePetForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    clearPendingPetGeneration();
    window.localStorage.clear();
    mocks.readLocalPetState.mockReturnValue(null);
    mocks.create.mockReturnValue({ petId: "pet-1" });
    mocks.generatePetAssets.mockReturnValue(new Promise(() => undefined));
    mocks.resumePetGeneration.mockReturnValue(new Promise(() => undefined));
    mocks.hookPet = null;
    mocks.hookStatus = "empty";
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("shows Paper presets over the single background timeline", () => {
    const { container } = render(<CreatePetForm />);

    const video = container.querySelector("video");
    const audio = container.querySelector("audio[src='/creation/hopeful-piano-loop.m4a']");
    const screenElement = screen.getByRole("region", { name: "Создание питомца" });
    expect(video).toHaveAttribute("autoplay");
    expect(video).toHaveAttribute("data-background-phase", "initial");
    expect(video).toHaveProperty("muted", true);
    expect(video).toHaveAttribute("playsinline");
    expect(video?.querySelector("source")).toHaveAttribute(
      "src",
      "/creation/clouds-creation-timeline.mp4",
    );
    expect(audio).toHaveAttribute("src", "/creation/hopeful-piano-loop.m4a");
    expect(audio).toHaveAttribute("autoplay");
    expect(audio).toHaveAttribute("loop");
    expect(audio).toHaveProperty("volume", 0.32);
    expect(screenElement).toHaveAttribute(
      "data-button-press-sound-src",
      "/sounds/creation-button-plop.wav",
    );
    expect(container.querySelector("audio[src='/sounds/creation-button-plop.wav']"))
      .toHaveAttribute("preload", "auto");
    expect(container.querySelector("img[src*='video-filter-normal.webp']"))
      .toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Ледяного дракона" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Человек-яблоко" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Водяной дух" })).toBeInTheDocument();

  });

  it("opens the custom input without starting generation", () => {
    const { container } = render(<CreatePetForm />);

    fireEvent.click(screen.getByRole("button", { name: "Свой вариант" }));

    expect(screen.getByLabelText("Свой вариант персонажа")).toHaveFocus();
    expect(container.querySelector("video source")).toHaveAttribute(
      "src",
      "/creation/clouds-creation-timeline.mp4",
    );
    expect(mocks.generatePetAssets).not.toHaveBeenCalled();
  });

  it("returns from the custom input to the current creation stage", () => {
    render(<CreatePetForm />);

    fireEvent.click(screen.getByRole("button", { name: "Свой вариант" }));
    fireEvent.change(screen.getByLabelText("Свой вариант персонажа"), {
      target: { value: "Стальной унитаз" },
    });

    expect(screen.getByRole("button", { name: "Назад" })).toBeInTheDocument();
    expect(screenAppBarStyles).toContain("backdrop-filter: blur(18px)");
    expect(screenAppBarStyles).toContain("-webkit-backdrop-filter: blur(18px)");

    fireEvent.click(screen.getByRole("button", { name: "Назад" }));

    expect(screen.queryByLabelText("Свой вариант персонажа")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Кого хочешь создать?" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Свой вариант" })).toBeInTheDocument();
    expect(mocks.generatePetAssets).not.toHaveBeenCalled();
  });

  it("shows the Paper next button only for a non-empty custom answer", () => {
    render(<CreatePetForm />);

    fireEvent.click(screen.getByRole("button", { name: "Свой вариант" }));
    const input = screen.getByLabelText("Свой вариант персонажа");
    expect(screen.queryByRole("button", { name: "Далее" })).not.toBeInTheDocument();

    fireEvent.change(input, { target: { value: "   " } });
    expect(screen.queryByRole("button", { name: "Далее" })).not.toBeInTheDocument();

    fireEvent.change(input, { target: { value: "Стальной унитаз" } });
    expect(screen.getByRole("button", { name: "Далее" })).toHaveStyle({
      backdropFilter: "blur(10px)",
      "--tilted-glass-rotation": "2deg",
    });
    expect(createPetFormStyles).toContain(
      "animation: customNextButtonIn 300ms ease-out backwards",
    );
    expect(createPetFormStyles).toContain(
      "transform: translateX(-50%) rotate(var(--tilted-glass-rotation)) scale(0.9)",
    );
    expect(createPetFormStyles).toContain("transform: translateX(-50%) scale(0.9)");
    expect(tiltedGlassButtonStyles).toContain(
      "animation: tiltedGlassSpringIn 300ms cubic-bezier(0.34, 1.56, 0.64, 1) backwards",
    );
    expect(tiltedGlassButtonStyles).toContain(
      "transform: rotate(var(--tilted-glass-rotation)) scale(0.9)",
    );
    expect(createPetFormStyles).toContain(
      "color: color(display-p3 0.019 0.082 0.174)",
    );
    expect(createPetFormStyles).toMatch(
      /\.title\s*\{[^}]*height: 52px;[^}]*align-items: flex-end;/u,
    );
    expect(createPetFormStyles).toContain("scale(0.6)");
    expect(createPetFormStyles).toContain(
      "animation: optionsStageIn 300ms ease-out backwards",
    );
    expect(createPetFormStyles).toContain("outline: none");
  });

  it("stays on the dedicated creation page when a pet already exists", async () => {
    mocks.hookPet = { petId: "pet-existing" };
    mocks.hookStatus = "ready";

    render(<CreatePetForm redirectExistingPet={false} />);

    expect(screen.getByRole("button", { name: "Ледяного дракона" })).toBeInTheDocument();
    await act(async () => undefined);
    expect(mocks.replace).not.toHaveBeenCalled();
  });

  it("starts generation after the first answer and advances while it runs", async () => {
    const { container } = render(<CreatePetForm />);
    const backgroundVideo = container.querySelector("video") as HTMLVideoElement;
    const initialCustomButton = screen.getByRole("button", { name: "Свой вариант" });

    expect(initialCustomButton).toHaveStyle({
      "--tilted-glass-rotation": "2deg",
      backdropFilter: "blur(20px)",
    });

    fireEvent.click(screen.getByRole("button", { name: "Ледяного дракона" }));

    await waitFor(() => expect(mocks.generatePetAssets).toHaveBeenCalledOnce());
    expect(container.querySelector("video")).toBe(backgroundVideo);
    expect(container.querySelector("video source")).toHaveAttribute(
      "src",
      "/creation/clouds-creation-timeline.mp4",
    );
    expect(backgroundVideo).toHaveAttribute("data-background-phase", "transition");

    backgroundVideo.currentTime = 267 / 24;
    fireEvent.timeUpdate(backgroundVideo);

    expect(container.querySelector("video")).toBe(backgroundVideo);
    expect(backgroundVideo).toHaveAttribute("data-background-phase", "formed");
    expect(screen.getByRole("heading", { name: "Как его будут звать?" })).toBeInTheDocument();
    const nextCustomButton = screen.getByRole("button", { name: "Свой вариант" });
    expect(nextCustomButton).not.toBe(initialCustomButton);
    expect(nextCustomButton).toHaveStyle({
      "--tilted-glass-rotation": "2deg",
      backdropFilter: "blur(20px)",
    });

    const play = vi.spyOn(backgroundVideo, "play").mockResolvedValue();
    play.mockClear();
    backgroundVideo.currentTime = 447 / 24;
    fireEvent.ended(backgroundVideo);
    expect(backgroundVideo.currentTime).toBe(267 / 24);
    expect(play).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole("button", { name: "Тото" }));
    expect(screen.getByRole("heading", { name: "Какой у него характер?" })).toBeInTheDocument();
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it("keeps later answers local to the UI and finalizes only after all steps", async () => {
    const generation = deferred<Record<string, unknown>>();
    mocks.generatePetAssets.mockReturnValue(generation.promise);
    render(<CreatePetForm />);

    submitCustomDescription("мышонок");
    completeQuestions();

    expect(screen.getByRole("heading", { name: "Персонаж формируется..." })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Персонаж думает" })).toBeInTheDocument();
    expect(createPetFormStyles).toContain("animation: finalContentIn 300ms ease-out both");
    expect(mocks.create).not.toHaveBeenCalled();
    expect(readPendingPetGeneration()).toMatchObject({ description: "мышонок" });
    expect(window.localStorage.getItem(PENDING_GENERATION_STORAGE_KEY)).not.toContain("Тото");

    await act(async () => generation.resolve({ assetSetId: "assets-1" }));

    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith(
      "мышонок",
      { assetSetId: "assets-1" },
    ));
    expect(readPendingPetGeneration()).toBeNull();
    expect(mocks.push).toHaveBeenCalledWith("/pet/pet-1");
  });

  it("waits for the questions when generation finishes early", async () => {
    mocks.generatePetAssets.mockResolvedValue({ assetSetId: "assets-ready" });
    render(<CreatePetForm />);

    submitCustomDescription("дракон");
    await waitFor(() => expect(mocks.generatePetAssets).toHaveBeenCalledOnce());
    expect(mocks.create).not.toHaveBeenCalled();

    completeQuestions();

    await waitFor(() => expect(mocks.create).toHaveBeenCalledOnce());
  });

  it("persists the recovery marker before the paid POST", async () => {
    let markerAtCall: unknown;
    mocks.generatePetAssets.mockImplementation(() => {
      markerAtCall = readPendingPetGeneration();
      return new Promise(() => undefined);
    });
    render(<CreatePetForm />);

    submitCustomDescription("Дракон с медным фонарём");

    await waitFor(() => expect(mocks.generatePetAssets).toHaveBeenCalledOnce());
    const options = mocks.generatePetAssets.mock.calls[0]?.[1];
    expect(markerAtCall).toMatchObject({
      description: "Дракон с медным фонарём",
      requestKey: options.requestKey,
    });
    act(() => options.onJobQueued("job-1"));
    expect(readPendingPetGeneration()).toMatchObject({ jobId: "job-1" });
  });

  it("does not start a paid POST when the marker cannot be persisted", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });
    render(<CreatePetForm />);

    submitCustomDescription("мышонок");

    expect(await screen.findByText(/Не удалось сохранить прогресс/u)).toBeInTheDocument();
    expect(screen.getByLabelText("Свой вариант персонажа")).toBeEnabled();
    expect(mocks.generatePetAssets).not.toHaveBeenCalled();
  });

  it("resumes a durable pending request on the final waiting screen", async () => {
    writePendingPetGeneration({
      description: "мышонок",
      requestKey: "pet-request-reload-0001",
    });
    const { container } = render(<CreatePetForm />);

    await waitFor(() => expect(mocks.generatePetAssets).toHaveBeenCalledWith(
      "мышонок",
      expect.objectContaining({ requestKey: "pet-request-reload-0001" }),
    ));
    expect(screen.getByRole("heading", { name: "Персонаж формируется..." }))
      .toBeInTheDocument();
    expect(container.querySelector("video source")).toHaveAttribute(
      "src",
      "/creation/clouds-creation-timeline.mp4",
    );
    expect(container.querySelector("video")).toHaveAttribute(
      "data-background-phase",
      "formed",
    );
  });

  it("continues polling a durable queued job after reload", async () => {
    writePendingPetGeneration({
      description: "мышонок",
      requestKey: "pet-request-reload-0002",
      jobId: "job-2",
    });
    render(<CreatePetForm />);

    await waitFor(() => expect(mocks.resumePetGeneration).toHaveBeenCalledWith(
      "job-2",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
    expect(screen.getByRole("heading", { name: "Персонаж формируется..." }))
      .toBeInTheDocument();
    expect(mocks.generatePetAssets).not.toHaveBeenCalled();
  });

  it("opens a pet immediately when a resumed queued job is ready", async () => {
    writePendingPetGeneration({
      description: "мышонок",
      requestKey: "pet-request-reload-ready-0003",
      jobId: "job-ready-3",
    });
    mocks.resumePetGeneration.mockResolvedValue({ assetSetId: "assets-ready" });
    render(<CreatePetForm />);

    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith(
      "мышонок",
      { assetSetId: "assets-ready" },
    ));
    expect(mocks.push).toHaveBeenCalledWith("/pet/pet-1");
    expect(readPendingPetGeneration()).toBeNull();
    expect(mocks.generatePetAssets).not.toHaveBeenCalled();
  });

  it("adopts an active cross-device job without repeating the POST", async () => {
    mocks.generatePetAssets.mockRejectedValue(
      new mocks.MockApiError(
        "GENERATION_ALREADY_ACTIVE",
        false,
        "job-active",
        "мышонок",
      ),
    );
    mocks.resumePetGeneration.mockResolvedValue({ assetSetId: "assets-recovered" });
    render(<CreatePetForm />);

    submitCustomDescription("дракон");
    completeQuestions();

    await waitFor(() => expect(mocks.resumePetGeneration).toHaveBeenCalledWith(
      "job-active",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith(
      "мышонок",
      { assetSetId: "assets-recovered" },
    ));
    expect(mocks.generatePetAssets).toHaveBeenCalledOnce();
  });

  it("aborts background generation when the screen unmounts", async () => {
    const { unmount } = render(<CreatePetForm />);
    submitCustomDescription("мышонок");
    await waitFor(() => expect(mocks.generatePetAssets).toHaveBeenCalledOnce());
    const signal = mocks.generatePetAssets.mock.calls[0]?.[1].signal as AbortSignal;

    unmount();

    expect(signal.aborted).toBe(true);
    expect(readPendingPetGeneration()).not.toBeNull();
  });
});
