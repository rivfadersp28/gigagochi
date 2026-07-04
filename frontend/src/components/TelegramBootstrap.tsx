"use client";

import { useEffect } from "react";

import {
  expandTelegramWebApp,
  getTelegramWebApp,
  readyTelegramWebApp,
  setTelegramViewportCssVars,
} from "@/lib/telegram";

export function TelegramBootstrap() {
  useEffect(() => {
    setTelegramViewportCssVars();
    expandTelegramWebApp();
    readyTelegramWebApp();

    const updateViewport = () => setTelegramViewportCssVars();
    window.addEventListener("resize", updateViewport);

    const webApp = getTelegramWebApp();
    webApp?.onEvent?.("viewportChanged", updateViewport);
    webApp?.onEvent?.("safeAreaChanged", updateViewport);
    webApp?.onEvent?.("contentSafeAreaChanged", updateViewport);

    return () => {
      window.removeEventListener("resize", updateViewport);
      webApp?.offEvent?.("viewportChanged", updateViewport);
      webApp?.offEvent?.("safeAreaChanged", updateViewport);
      webApp?.offEvent?.("contentSafeAreaChanged", updateViewport);
    };
  }, []);

  return null;
}
