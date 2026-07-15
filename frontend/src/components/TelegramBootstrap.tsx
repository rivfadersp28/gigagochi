"use client";

import { useEffect } from "react";

import {
  flushPendingInteractiveTravelOperations,
  PENDING_INTERACTIVE_TRAVEL_OPERATIONS_EVENT,
} from "@/lib/pendingInteractiveTravelOperations";
import {
  expandTelegramWebApp,
  getTelegramWebApp,
  hapticImpact,
  readyTelegramWebApp,
  requestTelegramFullscreen,
  setTelegramViewportCssVars,
} from "@/lib/telegram";

const BUTTON_PRESS_SOUND_SRC = "/sounds/button-press.wav";
const PENDING_TRAVEL_RETRY_INTERVAL_MS = 30_000;

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

function playButtonPressSound(button: HTMLButtonElement) {
  if (button.dataset.buttonPressSound === "off") {
    return;
  }

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

function runButtonPressFeedback(button: HTMLButtonElement) {
  hapticImpact("light");
  playButtonPressSound(button);
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

    const handleClick = (event: MouseEvent) => {
      const button = getFeedbackButton(event.target);
      if (!event.isTrusted || event.button !== 0 || !button) {
        return;
      }

      runButtonPressFeedback(button);
    };

    document.addEventListener("click", handleClick, { capture: true });

    const flushPendingTravelOperations = () => {
      void flushPendingInteractiveTravelOperations().catch(() => undefined);
    };
    flushPendingTravelOperations();
    window.addEventListener("online", flushPendingTravelOperations);
    window.addEventListener(
      PENDING_INTERACTIVE_TRAVEL_OPERATIONS_EVENT,
      flushPendingTravelOperations,
    );
    const pendingTravelIntervalId = window.setInterval(
      flushPendingTravelOperations,
      PENDING_TRAVEL_RETRY_INTERVAL_MS,
    );

    return () => {
      window.removeEventListener("resize", updateViewport);
      webApp?.offEvent?.("viewportChanged", updateViewport);
      webApp?.offEvent?.("safeAreaChanged", updateViewport);
      webApp?.offEvent?.("contentSafeAreaChanged", updateViewport);
      webApp?.offEvent?.("fullscreenChanged", updateViewport);
      webApp?.offEvent?.("fullscreenFailed", updateViewport);
      document.removeEventListener("click", handleClick, { capture: true });
      window.removeEventListener("online", flushPendingTravelOperations);
      window.removeEventListener(
        PENDING_INTERACTIVE_TRAVEL_OPERATIONS_EVENT,
        flushPendingTravelOperations,
      );
      window.clearInterval(pendingTravelIntervalId);
    };
  }, []);

  return null;
}
