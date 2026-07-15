import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { SmoothBackgroundVideo } from "./SmoothBackgroundVideo";

function mockReducedMotion(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches,
      media: "(prefers-reduced-motion: reduce)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it("shows a static decoded frame when reduced motion is requested", () => {
  mockReducedMotion(true);

  const { container } = render(<SmoothBackgroundVideo src="/scene.mp4" />);

  const video = container.querySelector("video");
  expect(video).not.toBeNull();
  expect(video).not.toHaveAttribute("autoplay");
  expect(video).not.toHaveAttribute("loop");
});

it("cancels a pending decoded-frame callback on unmount", () => {
  vi.useFakeTimers();
  mockReducedMotion(false);
  const requestFrame = vi.fn(() => 17);
  const cancelFrame = vi.fn();
  Object.defineProperty(HTMLVideoElement.prototype, "requestVideoFrameCallback", {
    configurable: true,
    value: requestFrame,
  });
  Object.defineProperty(HTMLVideoElement.prototype, "cancelVideoFrameCallback", {
    configurable: true,
    value: cancelFrame,
  });
  const onReady = vi.fn();
  const { container, unmount } = render(
    <SmoothBackgroundVideo src="/scene.mp4" onReady={onReady} />,
  );

  fireEvent.loadedData(container.querySelector("video") as HTMLVideoElement);
  unmount();
  vi.runAllTimers();

  expect(requestFrame).toHaveBeenCalledOnce();
  expect(cancelFrame).toHaveBeenCalledWith(17);
  expect(onReady).not.toHaveBeenCalled();
});

it("removes and stops a superseded unready layer after a rapid source switch", () => {
  mockReducedMotion(false);
  const callbacks = new Map<HTMLVideoElement, VideoFrameRequestCallback>();
  Object.defineProperty(HTMLVideoElement.prototype, "requestVideoFrameCallback", {
    configurable: true,
    value: vi.fn(function (
      this: HTMLVideoElement,
      callback: VideoFrameRequestCallback,
    ) {
      callbacks.set(this, callback);
      return callbacks.size;
    }),
  });
  Object.defineProperty(HTMLVideoElement.prototype, "cancelVideoFrameCallback", {
    configurable: true,
    value: vi.fn(),
  });
  const pause = vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  const load = vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined);
  const { container, rerender } = render(<SmoothBackgroundVideo src="/a.mp4" />);
  const first = container.querySelector("video") as HTMLVideoElement;
  fireEvent.loadedData(first);
  callbacks.get(first)?.(0, {} as VideoFrameCallbackMetadata);

  rerender(<SmoothBackgroundVideo src="/b.mp4" />);
  const second = [...container.querySelectorAll("video")].find(
    (video) => video.getAttribute("src") === "/b.mp4",
  ) as HTMLVideoElement;
  rerender(<SmoothBackgroundVideo src="/c.mp4" />);
  expect([...container.querySelectorAll("video")].map((video) => video.getAttribute("src")))
    .toEqual(["/a.mp4", "/c.mp4"]);

  const third = [...container.querySelectorAll("video")].find(
    (video) => video.getAttribute("src") === "/c.mp4",
  ) as HTMLVideoElement;
  fireEvent.error(third);
  fireEvent.loadedData(second);
  callbacks.get(second)?.(0, {} as VideoFrameCallbackMetadata);

  expect(pause).toHaveBeenCalledWith();
  expect(load).toHaveBeenCalledWith();
  expect([...container.querySelectorAll("video")].map((video) => video.getAttribute("src")))
    .toEqual(["/a.mp4"]);
});
