import { afterEach, describe, expect, it, vi } from "vitest";

import {
  canUseDebugMenu,
  getTelegramUserId,
  setTelegramBackgroundColor,
} from "./telegram";

type TelegramTestWindow = Window & {
  Telegram?: {
    WebApp?: {
      initData?: string;
      initDataUnsafe?: Record<string, unknown>;
      setBackgroundColor?: (color: string) => void;
      setBottomBarColor?: (color: string) => void;
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
  delete (window as TelegramTestWindow).Telegram;
  document.documentElement.style.backgroundColor = "";
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

describe("debug menu access", () => {
  it("enables the debug menu for Sergey", () => {
    setTelegramUser(62943754);

    expect(canUseDebugMenu()).toBe(true);
  });

  it("enables the debug menu for Sonia", () => {
    setTelegramUser(625405535);

    expect(canUseDebugMenu()).toBe(true);
  });

  it("hides the debug menu from other Telegram users", () => {
    setTelegramUser(42);

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
