"use client";

import { useEffect, useState } from "react";

import {
  canUseTmaApi,
  fetchTelegramCapabilities,
  type TelegramCapabilities,
} from "./api";
import {
  canUseDebugMenu,
  canUseInteractiveTravel,
  getTelegramUserId,
  setTelegramServerCapabilities,
} from "./telegram";

let cachedCapabilities: TelegramCapabilities | null = null;
let pendingCapabilities: Promise<TelegramCapabilities> | null = null;
const CAPABILITY_RETRY_DELAYS_MS = [1_000, 4_000, 15_000, 60_000] as const;

function currentCapabilities(): TelegramCapabilities {
  return cachedCapabilities ?? {
    telegramUserId: getTelegramUserId() ?? 0,
    debugMenu: canUseDebugMenu(),
    interactiveTravel: canUseInteractiveTravel(),
  };
}

function loadCapabilities() {
  if (cachedCapabilities) {
    return Promise.resolve(cachedCapabilities);
  }
  pendingCapabilities ??= fetchTelegramCapabilities()
    .then((capabilities) => {
      cachedCapabilities = capabilities;
      setTelegramServerCapabilities(capabilities);
      return capabilities;
    })
    .finally(() => {
      pendingCapabilities = null;
    });
  return pendingCapabilities;
}

export function useTelegramCapabilities(): TelegramCapabilities {
  const [capabilities, setCapabilities] = useState(currentCapabilities);

  useEffect(() => {
    if (!canUseTmaApi() || getTelegramUserId() === null) {
      return;
    }
    let active = true;
    let retryAttempt = 0;
    let retryTimeoutId: number | null = null;
    const load = () => {
      void loadCapabilities()
        .then((loaded) => {
          if (active) {
            setCapabilities(loaded);
          }
        })
        .catch(() => {
          if (!active) {
            return;
          }
          const delay = CAPABILITY_RETRY_DELAYS_MS[
            Math.min(retryAttempt, CAPABILITY_RETRY_DELAYS_MS.length - 1)
          ];
          retryAttempt += 1;
          retryTimeoutId = window.setTimeout(() => {
            retryTimeoutId = null;
            load();
          }, delay);
        });
    };
    const retryWhenOnline = () => {
      if (retryTimeoutId !== null) {
        window.clearTimeout(retryTimeoutId);
        retryTimeoutId = null;
      }
      retryAttempt = 0;
      load();
    };
    window.addEventListener("online", retryWhenOnline);
    load();
    return () => {
      active = false;
      if (retryTimeoutId !== null) {
        window.clearTimeout(retryTimeoutId);
      }
      window.removeEventListener("online", retryWhenOnline);
    };
  }, []);

  return capabilities;
}

export function resetTelegramCapabilitiesForTests() {
  cachedCapabilities = null;
  pendingCapabilities = null;
  setTelegramServerCapabilities(null);
}
