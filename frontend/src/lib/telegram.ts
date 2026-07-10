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
const DEBUG_MENU_TELEGRAM_IDS = new Set([62943754]);
const DERIVED_ASSET_PILOT_TELEGRAM_IDS = new Set([62943754]);

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

export function canUseDerivedPetAssets(): boolean {
  const telegramUserId = getTelegramUserId();
  if (telegramUserId !== null) {
    return DERIVED_ASSET_PILOT_TELEGRAM_IDS.has(telegramUserId);
  }
  return process.env.NODE_ENV === "development" && !getTelegramInitData();
}

export function canUseDebugMenu(): boolean {
  const telegramUserId = getTelegramUserId();
  return telegramUserId !== null && DEBUG_MENU_TELEGRAM_IDS.has(telegramUserId);
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
    lockedTelegramViewportHeight = webApp.stableViewportHeight
      ? viewportHeight
      : Math.max(lockedTelegramViewportHeight ?? 0, viewportHeight);
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
