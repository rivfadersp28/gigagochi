import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import {
  resetTelegramCapabilitiesForTests,
  useTelegramCapabilities,
} from "./useTelegramCapabilities";

const mocks = vi.hoisted(() => ({
  fetchTelegramCapabilities: vi.fn(),
  setTelegramServerCapabilities: vi.fn(),
}));

vi.mock("./api", () => ({
  canUseTmaApi: () => true,
  fetchTelegramCapabilities: (...args: unknown[]) =>
    mocks.fetchTelegramCapabilities(...args),
}));

vi.mock("./telegram", () => ({
  canUseDebugMenu: () => false,
  canUseInteractiveTravel: () => false,
  getTelegramUserId: () => 42,
  setTelegramServerCapabilities: (...args: unknown[]) =>
    mocks.setTelegramServerCapabilities(...args),
}));

beforeEach(() => {
  resetTelegramCapabilitiesForTests();
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("uses the authenticated backend as the capability authority", async () => {
  const serverCapabilities = {
    telegramUserId: 42,
    debugMenu: true,
    interactiveTravel: true,
  };
  mocks.fetchTelegramCapabilities.mockResolvedValue(serverCapabilities);

  const { result } = renderHook(() => useTelegramCapabilities());

  await waitFor(() => expect(result.current).toEqual(serverCapabilities));
  expect(mocks.setTelegramServerCapabilities).toHaveBeenCalledWith(serverCapabilities);
});

it("recovers pilot capabilities after a transient initial failure", async () => {
  vi.useFakeTimers();
  const serverCapabilities = {
    telegramUserId: 42,
    debugMenu: false,
    interactiveTravel: true,
  };
  mocks.fetchTelegramCapabilities
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(serverCapabilities);

  const { result } = renderHook(() => useTelegramCapabilities());
  await act(async () => Promise.resolve());
  expect(mocks.fetchTelegramCapabilities).toHaveBeenCalledOnce();

  await act(async () => {
    await vi.advanceTimersByTimeAsync(1_000);
  });

  expect(result.current).toEqual(serverCapabilities);
  expect(mocks.fetchTelegramCapabilities).toHaveBeenCalledTimes(2);
});

it("retries immediately when connectivity returns", async () => {
  vi.useFakeTimers();
  const serverCapabilities = {
    telegramUserId: 42,
    debugMenu: true,
    interactiveTravel: true,
  };
  mocks.fetchTelegramCapabilities
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(serverCapabilities);
  const { result } = renderHook(() => useTelegramCapabilities());
  await act(async () => Promise.resolve());

  await act(async () => {
    window.dispatchEvent(new Event("online"));
    await Promise.resolve();
  });

  expect(result.current).toEqual(serverCapabilities);
  expect(mocks.fetchTelegramCapabilities).toHaveBeenCalledTimes(2);
});
