import { afterEach, describe, expect, it, vi } from "vitest";

import {
  canUseDebugMenu,
  canUseInteractiveTravel,
  setTelegramViewportCssVars,
  setTelegramServerCapabilities,
  setTelegramBackgroundColor,
} from "./telegram";

type TelegramTestWindow = Window & {
  Telegram?: {
    WebApp?: {
      initData?: string;
      initDataUnsafe?: Record<string, unknown>;
      setBackgroundColor?: (color: string) => void;
      setBottomBarColor?: (color: string) => void;
      viewportHeight?: number;
      stableViewportHeight?: number;
    };
  };
};

function setTelegramUser(id: number) {
  (window as TelegramTestWindow).Telegram = {
    WebApp: {
      initData: "signed-init-data",
      initDataUnsafe: { user: { id } },
    },
  };
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  delete (window as TelegramTestWindow).Telegram;
  setTelegramServerCapabilities(null);
  document.documentElement.style.backgroundColor = "";
  document.documentElement.style.removeProperty("--tma-viewport-height");
  document.body.style.backgroundColor = "";
});

describe("Telegram background color", () => {
  it("colors the WebView and Android bottom bar", () => {
    const setBackgroundColor = vi.fn();
    const setBottomBarColor = vi.fn();
    (window as TelegramTestWindow).Telegram = {
      WebApp: {
        initData: "signed-init-data",
        setBackgroundColor,
        setBottomBarColor,
      },
    };

    setTelegramBackgroundColor("#434137");

    expect(document.documentElement.style.backgroundColor).toBe("rgb(67, 65, 55)");
    expect(document.body.style.backgroundColor).toBe("rgb(67, 65, 55)");
    expect(setBackgroundColor).toHaveBeenCalledWith("#434137");
    expect(setBottomBarColor).toHaveBeenCalledWith("#434137");
  });
});

describe("Telegram viewport fallback", () => {
  it("keeps keyboard shrinkage locked but resets after an orientation change", () => {
    const webApp = { initData: "signed-init-data", viewportHeight: 800 };
    (window as TelegramTestWindow).Telegram = { WebApp: webApp };
    vi.stubGlobal("innerWidth", 400);

    setTelegramViewportCssVars();
    expect(document.documentElement.style.getPropertyValue("--tma-viewport-height"))
      .toBe("800px");

    webApp.viewportHeight = 420;
    setTelegramViewportCssVars();
    expect(document.documentElement.style.getPropertyValue("--tma-viewport-height"))
      .toBe("800px");

    vi.stubGlobal("innerWidth", 800);
    webApp.viewportHeight = 400;
    setTelegramViewportCssVars();
    expect(document.documentElement.style.getPropertyValue("--tma-viewport-height"))
      .toBe("400px");
  });
});

describe("debug menu access", () => {
  it("uses capabilities returned by the authenticated backend", () => {
    setTelegramUser(62943754);
    setTelegramServerCapabilities({
      telegramUserId: 62943754,
      debugMenu: true,
      interactiveTravel: false,
    });

    expect(canUseDebugMenu()).toBe(true);
    expect(canUseInteractiveTravel()).toBe(false);
  });

  it("does not retain a hardcoded Telegram allowlist", () => {
    setTelegramUser(625405535);

    expect(canUseDebugMenu()).toBe(false);
  });

  it("hides the debug menu from other Telegram users", () => {
    setTelegramUser(42);
    setTelegramServerCapabilities({
      telegramUserId: 62943754,
      debugMenu: true,
      interactiveTravel: true,
    });

    expect(canUseDebugMenu()).toBe(false);
  });

  it("enables the debug menu in a local dev browser", () => {
    expect(canUseDebugMenu()).toBe(true);
  });

  it("hides the debug menu outside Telegram in production", () => {
    vi.stubEnv("NODE_ENV", "production");

    expect(canUseDebugMenu()).toBe(false);
  });
});
