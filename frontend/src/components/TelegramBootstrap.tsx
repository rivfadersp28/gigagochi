"use client";

import { useEffect } from "react";

import {
  expandTelegramWebApp,
  getTelegramWebApp,
  hapticImpact,
  readyTelegramWebApp,
  requestTelegramFullscreen,
  setTelegramViewportCssVars,
} from "@/lib/telegram";

const BUTTON_PRESS_SOUND_SRC = "/sounds/button-press.wav";

let buttonPressAudio: HTMLAudioElement | null = null;

function getFeedbackButton(target: EventTarget | null): HTMLButtonElement | null {
  if (!(target instanceof Element)) {
    return null;
  }

  const button = target.closest("button");
  if (
    !(button instanceof HTMLButtonElement) ||
    button.disabled ||
    button.getAttribute("aria-disabled") === "true"
  ) {
    return null;
  }

  return button;
}

function playButtonPressSound() {
  if (typeof Audio === "undefined") {
    return;
  }

  try {
    if (!buttonPressAudio) {
      buttonPressAudio = new Audio(BUTTON_PRESS_SOUND_SRC);
      buttonPressAudio.preload = "auto";
    }

    buttonPressAudio.currentTime = 0;
    void buttonPressAudio.play().catch(() => undefined);
  } catch {
    // Audio feedback is optional and can be blocked by the client.
  }
}

function runButtonPressFeedback() {
  hapticImpact("light");
  playButtonPressSound();
}

export function TelegramBootstrap() {
  useEffect(() => {
    setTelegramViewportCssVars();
    requestTelegramFullscreen();
    expandTelegramWebApp();
    readyTelegramWebApp();

    const updateViewport = () => setTelegramViewportCssVars();
    window.addEventListener("resize", updateViewport);

    const webApp = getTelegramWebApp();
    webApp?.onEvent?.("viewportChanged", updateViewport);
    webApp?.onEvent?.("safeAreaChanged", updateViewport);
    webApp?.onEvent?.("contentSafeAreaChanged", updateViewport);
    webApp?.onEvent?.("fullscreenChanged", updateViewport);
    webApp?.onEvent?.("fullscreenFailed", updateViewport);

    const handlePointerDown = (event: PointerEvent) => {
      if (event.button !== 0 || !getFeedbackButton(event.target)) {
        return;
      }

      runButtonPressFeedback();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.repeat || (event.key !== "Enter" && event.key !== " ")) {
        return;
      }

      if (getFeedbackButton(event.target)) {
        runButtonPressFeedback();
      }
    };

    document.addEventListener("pointerdown", handlePointerDown, { capture: true });
    document.addEventListener("keydown", handleKeyDown, { capture: true });

    return () => {
      window.removeEventListener("resize", updateViewport);
      webApp?.offEvent?.("viewportChanged", updateViewport);
      webApp?.offEvent?.("safeAreaChanged", updateViewport);
      webApp?.offEvent?.("contentSafeAreaChanged", updateViewport);
      webApp?.offEvent?.("fullscreenChanged", updateViewport);
      webApp?.offEvent?.("fullscreenFailed", updateViewport);
      document.removeEventListener("pointerdown", handlePointerDown, { capture: true });
      document.removeEventListener("keydown", handleKeyDown, { capture: true });
    };
  }, []);

  return null;
}
