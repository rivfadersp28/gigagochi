import { afterEach, describe, expect, it, vi } from "vitest";

import {
  canUseDebugMenu,
  canUseDerivedPetAssets,
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

describe("derived asset pilot", () => {
  it("enables derived pet assets for Sergey", () => {
    setTelegramUser(62943754);

    expect(getTelegramUserId()).toBe(62943754);
    expect(canUseDerivedPetAssets()).toBe(true);
  });

  it("keeps other Telegram users on normal assets", () => {
    setTelegramUser(42);

    expect(getTelegramUserId()).toBe(42);
    expect(canUseDerivedPetAssets()).toBe(false);
  });
});

describe("debug menu access", () => {
  it("enables the debug menu for Sergey", () => {
    setTelegramUser(62943754);

    expect(canUseDebugMenu()).toBe(true);
  });

  it("hides the debug menu from other Telegram users", () => {
    setTelegramUser(42);

    expect(canUseDebugMenu()).toBe(false);
  });

  it("hides the debug menu outside Telegram", () => {
    expect(canUseDebugMenu()).toBe(false);
  });
});
