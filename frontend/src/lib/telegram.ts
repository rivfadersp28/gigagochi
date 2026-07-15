"use client";

import { useEffect, useRef } from "react";

import { APP_BACKGROUND_COLOR } from "@/lib/theme";

type TelegramBackButton = {
  isVisible?: boolean;
  show: () => void;
  hide: () => void;
  onClick: (callback: () => void) => void;
  offClick: (callback: () => void) => void;
};

type TelegramHapticFeedback = {
  impactOccurred?: (style: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
  notificationOccurred?: (type: "error" | "success" | "warning") => void;
};

type TelegramSafeArea = {
  top?: number;
  right?: number;
  bottom?: number;
  left?: number;
};

export type TelegramWebApp = {
  initData?: string;
  initDataUnsafe?: Record<string, unknown>;
  themeParams?: Record<string, string>;
  platform?: string;
  version?: string;
  viewportHeight?: number;
  stableViewportHeight?: number;
  isFullscreen?: boolean;
  safeAreaInset?: TelegramSafeArea;
  contentSafeAreaInset?: TelegramSafeArea;
  BackButton?: TelegramBackButton;
  HapticFeedback?: TelegramHapticFeedback;
  ready?: () => void;
  expand?: () => void;
  requestFullscreen?: () => void;
  isVersionAtLeast?: (version: string) => boolean;
  setHeaderColor?: (color: string) => void;
  setBackgroundColor?: (color: string) => void;
  setBottomBarColor?: (color: string) => void;
  onEvent?: (eventType: string, callback: (event?: unknown) => void) => void;
  offEvent?: (eventType: string, callback: (event?: unknown) => void) => void;
};

type TelegramWindow = Window &
  typeof globalThis & {
    Telegram?: {
      WebApp?: TelegramWebApp;
    };
  };

let lockedTelegramViewportHeight: number | null = null;
let lockedTelegramViewportWidth: number | null = null;
let lockedTelegramViewportWebApp: TelegramWebApp | null = null;
const LOCAL_DEBUG_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"]);
type TelegramServerCapabilities = {
  telegramUserId: number;
  debugMenu: boolean;
  interactiveTravel: boolean;
};
let serverCapabilities: TelegramServerCapabilities | null = null;

function telegramWindow() {
  if (typeof window === "undefined") {
    return null;
  }
  return window as TelegramWindow;
}

export function getTelegramWebApp(): TelegramWebApp | null {
  return telegramWindow()?.Telegram?.WebApp ?? null;
}

export function getTelegramInitData(): string {
  return getTelegramWebApp()?.initData ?? "";
}

export function getTelegramInitDataUnsafe(): Record<string, unknown> | undefined {
  return getTelegramWebApp()?.initDataUnsafe;
}

export function getTelegramUserId(): number | null {
  const user = getTelegramInitDataUnsafe()?.user;
  if (!user || typeof user !== "object" || Array.isArray(user)) {
    return null;
  }
  const id = (user as Record<string, unknown>).id;
  if (typeof id === "number" && Number.isSafeInteger(id)) {
    return id;
  }
  if (typeof id === "string" && /^\d+$/.test(id)) {
    const parsed = Number(id);
    return Number.isSafeInteger(parsed) ? parsed : null;
  }
  return null;
}

export function setTelegramServerCapabilities(capabilities: TelegramServerCapabilities | null) {
  serverCapabilities = capabilities;
}

export function canUseDebugMenu(): boolean {
  const telegramUserId = getTelegramUserId();
  if (telegramUserId !== null) {
    return serverCapabilities?.telegramUserId === telegramUserId
      && serverCapabilities.debugMenu;
  }
  if (typeof window === "undefined" || process.env.NODE_ENV === "production") {
    return false;
  }
  return LOCAL_DEBUG_HOSTS.has(window.location.hostname);
}

export function canUseInteractiveTravel(): boolean {
  const telegramUserId = getTelegramUserId();
  if (telegramUserId !== null) {
    return serverCapabilities?.telegramUserId === telegramUserId
      && serverCapabilities.interactiveTravel;
  }
  if (typeof window === "undefined" || process.env.NODE_ENV === "production") {
    return false;
  }
  return LOCAL_DEBUG_HOSTS.has(window.location.hostname);
}

export function getTelegramThemeParams(): Record<string, string> {
  return getTelegramWebApp()?.themeParams ?? {};
}

export function getTelegramPlatform(): string {
  return getTelegramWebApp()?.platform ?? "browser";
}

export function readyTelegramWebApp() {
  getTelegramWebApp()?.ready?.();
}

export function expandTelegramWebApp() {
  getTelegramWebApp()?.expand?.();
}

export function setTelegramBackgroundColor(color: string) {
  if (typeof document !== "undefined") {
    document.documentElement.style.backgroundColor = color;
    document.body.style.backgroundColor = color;
  }

  const webApp = getTelegramWebApp();
  if (!webApp?.initData) {
    return;
  }

  try {
    webApp.setBackgroundColor?.(color);
    webApp.setBottomBarColor?.(color);
  } catch {
    // Telegram color APIs are optional across clients.
  }
}

export function requestTelegramFullscreen() {
  const webApp = getTelegramWebApp();
  if (!webApp?.initData) {
    return;
  }

  try {
    webApp.setHeaderColor?.(APP_BACKGROUND_COLOR);
    webApp.setBackgroundColor?.(APP_BACKGROUND_COLOR);
    webApp.setBottomBarColor?.(APP_BACKGROUND_COLOR);
    webApp.expand?.();
    if (webApp.isFullscreen) {
      return;
    }
    const supportsFullscreen =
      typeof webApp.requestFullscreen === "function" &&
      (typeof webApp.isVersionAtLeast !== "function" || webApp.isVersionAtLeast("8.0"));
    if (supportsFullscreen) {
      webApp.requestFullscreen?.();
    }
  } catch {
    // Fullscreen is optional and can be rejected on unsupported Telegram clients.
  }
}

export function hapticImpact(style: "light" | "medium" | "heavy" | "rigid" | "soft" = "light") {
  const webApp = getTelegramWebApp();
  if (!webApp?.initData) {
    return;
  }

  try {
    webApp.HapticFeedback?.impactOccurred?.(style);
  } catch {
    // Haptics are optional across Telegram clients.
  }
}

export function hapticNotification(type: "error" | "success" | "warning") {
  const webApp = getTelegramWebApp();
  if (!webApp?.initData) {
    return;
  }

  try {
    webApp.HapticFeedback?.notificationOccurred?.(type);
  } catch {
    // Haptics are optional across Telegram clients.
  }
}

export function setTelegramViewportCssVars() {
  const webApp = getTelegramWebApp();
  if (!webApp || typeof document === "undefined") {
    return;
  }

  const root = document.documentElement;
  const viewportHeight = webApp.stableViewportHeight ?? webApp.viewportHeight;
  if (viewportHeight) {
    const viewportWidth = window.innerWidth > 0 ? window.innerWidth : null;
    if (lockedTelegramViewportWebApp !== webApp) {
      lockedTelegramViewportHeight = null;
      lockedTelegramViewportWidth = null;
      lockedTelegramViewportWebApp = webApp;
    }
    const previousLandscape = lockedTelegramViewportWidth !== null
      && lockedTelegramViewportHeight !== null
      && lockedTelegramViewportWidth > lockedTelegramViewportHeight;
    const currentLandscape = viewportWidth !== null && viewportWidth > viewportHeight;
    const orientationChanged = lockedTelegramViewportWidth !== null
      && previousLandscape !== currentLandscape;
    const widthChangedSubstantially = viewportWidth !== null
      && lockedTelegramViewportWidth !== null
      && Math.abs(viewportWidth - lockedTelegramViewportWidth)
        >= Math.max(80, lockedTelegramViewportWidth * 0.2);
    lockedTelegramViewportHeight = (
      webApp.stableViewportHeight
      || orientationChanged
      || widthChangedSubstantially
    )
      ? viewportHeight
      : Math.max(lockedTelegramViewportHeight ?? 0, viewportHeight);
    lockedTelegramViewportWidth = viewportWidth;
    root.style.setProperty("--tma-viewport-height", `${lockedTelegramViewportHeight}px`);
  }

  const contentSafeArea = webApp.contentSafeAreaInset;
  if (contentSafeArea) {
    root.style.setProperty("--tg-content-safe-area-inset-top", `${contentSafeArea.top ?? 0}px`);
    root.style.setProperty("--tg-content-safe-area-inset-bottom", `${contentSafeArea.bottom ?? 0}px`);
  }

  const safeArea = webApp.safeAreaInset;
  if (safeArea) {
    root.style.setProperty("--tg-safe-area-inset-top", `${safeArea.top ?? 0}px`);
    root.style.setProperty("--tg-safe-area-inset-right", `${safeArea.right ?? 0}px`);
    root.style.setProperty("--tg-safe-area-inset-bottom", `${safeArea.bottom ?? 0}px`);
    root.style.setProperty("--tg-safe-area-inset-left", `${safeArea.left ?? 0}px`);
  }
}

export function useTelegramBackButton(onBack: () => void, enabled = true) {
  const onBackRef = useRef(onBack);

  useEffect(() => {
    onBackRef.current = onBack;
  }, [onBack]);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    const backButton = getTelegramWebApp()?.BackButton;
    if (!backButton) {
      return;
    }

    const handleBackClick = () => onBackRef.current();

    backButton.show();
    backButton.onClick(handleBackClick);

    return () => {
      backButton.offClick(handleBackClick);
      backButton.hide();
    };
  }, [enabled]);
}
