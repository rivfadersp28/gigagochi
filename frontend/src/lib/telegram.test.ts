import { afterEach, describe, expect, it } from "vitest";

import {
  canUseDebugMenu,
  canUseDerivedPetAssets,
  getTelegramUserId,
} from "./telegram";

type TelegramTestWindow = Window & {
  Telegram?: {
    WebApp?: {
      initData?: string;
      initDataUnsafe?: Record<string, unknown>;
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
