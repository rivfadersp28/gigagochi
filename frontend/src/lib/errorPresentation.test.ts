import { afterEach, describe, expect, it } from "vitest";

import { ApiError } from "./apiTransport";
import { presentError } from "./errorPresentation";

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

describe("error presentation", () => {
  it("hides diagnostics from ordinary users", () => {
    setTelegramUser(42);
    const error = new ApiError("Сервис временно недоступен", "CHAT_FAILED", 502, {
      diagnostic: { providerMessage: "secret provider detail" },
    });

    expect(presentError(error, "fallback")).toEqual({
      message: "Сервис временно недоступен",
    });
  });

  it("shows diagnostics to Sergey", () => {
    setTelegramUser(62943754);
    const error = new ApiError("Сервис временно недоступен", "CHAT_FAILED", 502, {
      requestId: "req-1",
      diagnostic: { providerMessage: "unsupported tool" },
    });

    const presented = presentError(error, "fallback");
    expect(presented.message).toBe("Сервис временно недоступен");
    expect(presented.technicalDetails).toContain("CHAT_FAILED");
    expect(presented.technicalDetails).toContain("unsupported tool");
    expect(presented.technicalDetails).toContain("req-1");
  });
});
