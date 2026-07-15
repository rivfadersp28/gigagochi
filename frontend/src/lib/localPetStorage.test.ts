import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { applyOfflineProgress, applyPetTap, applyStatsPatch } from "./localPetStats";
import {
  appendLocalChatMessages,
  applyLiteOverlayPatch,
  applyStoryLibraryPatch,
  CHAT_HISTORY_STORAGE_KEY,
  clearLocalChatHistory,
  commitLocalPetStateMutation,
  createLocalPetState,
  PET_STATE_STORAGE_KEY,
  readLocalChatHistory,
  readLocalPetSettings,
  readLocalPetState,
  resetLocalPetState,
  SETTINGS_STORAGE_KEY,
  writeLocalChatHistory,
  writeLocalPetSettings,
  writeLocalPetState,
  writeLocalPetStateDurably,
} from "./localPetStorage";
import type { LocalPetState } from "./types";

function petState(): LocalPetState {
  const tick = "2026-07-09T06:00:00.000Z";
  return {
    version: 2,
    petId: "pet-1",
    description: "листолицый питомец",
    createdAt: "2026-07-01T06:00:00.000Z",
    updatedAt: tick,
    lastInteractionAt: tick,
    lastStatsTickAt: tick,
    lastStatTickAt: {
      hunger: tick,
      happiness: tick,
      energy: tick,
    },
    stage: "baby",
    mood: "happy",
    stats: {
      hunger: 80,
      happiness: 80,
      energy: 80,
    },
  };
}

describe("localPetStats", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    resetLocalPetState();
  });

  it("creates a new pet with full stats", () => {
    expect(createLocalPetState("мышонок").stats).toEqual({
      hunger: 100,
      happiness: 100,
      energy: 100,
    });
    expect(createLocalPetState("мышонок").petTapProgress).toBe(0);
  });

  it("migrates legacy pet state and bounds persisted travel receipts", () => {
    const legacy = {
      version: 1,
      petId: "pet-legacy",
      description: "мышонок",
      createdAt: "2026-07-09T06:00:00.000Z",
      updatedAt: "2026-07-09T06:00:00.000Z",
      lastInteractionAt: "2026-07-09T06:00:00.000Z",
      stage: "baby",
      mood: "idle",
      stats: { hunger: 50, happiness: 50, energy: 50 },
    };
    window.localStorage.setItem(PET_STATE_STORAGE_KEY, JSON.stringify(legacy));

    const migrated = readLocalPetState();

    expect(migrated?.version).toBe(2);
    expect(migrated?.travelImpactReceipts).toBeUndefined();

    window.localStorage.setItem(PET_STATE_STORAGE_KEY, JSON.stringify({
      ...migrated,
      travelImpactReceipts: [
        ...Array.from({ length: 70 }, (_, index) => `travel-${index}:1`),
        "invalid",
        "travel-69:1",
      ],
    }));
    const bounded = readLocalPetState();
    expect(bounded?.travelImpactReceipts).toHaveLength(64);
    expect(bounded?.travelImpactReceipts?.[0]).toBe("travel-6:1");
    expect(bounded?.travelImpactReceipts?.at(-1)).toBe("travel-69:1");
  });

  it("rejects whitespace pet and chat identifiers from corrupted storage", () => {
    window.localStorage.setItem(
      PET_STATE_STORAGE_KEY,
      JSON.stringify({ ...petState(), petId: "   " }),
    );
    window.localStorage.setItem(
      CHAT_HISTORY_STORAGE_KEY,
      JSON.stringify({
        version: 1,
        messages: [
          { id: "   ", role: "user", text: "Потерянная реплика", createdAt: "invalid" },
          { id: " valid-id ", role: "pet", text: "Ответ", createdAt: "invalid" },
        ],
      }),
    );

    expect(readLocalPetState()).toBeNull();
    expect(readLocalChatHistory().messages).toMatchObject([
      { id: "valid-id", role: "pet", text: "Ответ" },
    ]);
  });

  it("rejects a corrupted pet identifier that can alter a route path", () => {
    window.localStorage.setItem(
      PET_STATE_STORAGE_KEY,
      JSON.stringify({ ...petState(), petId: "../admin" }),
    );

    expect(readLocalPetState()).toBeNull();
  });

  it("adds 15 happiness on every fifth pet tap and clamps at 100", () => {
    let state: LocalPetState = { ...petState(), petTapProgress: 0 };

    for (let tap = 1; tap <= 4; tap += 1) {
      const result = applyPetTap(state);
      state = result.state;
      expect(result.rewarded).toBe(false);
      expect(state.stats.happiness).toBe(80);
      expect(state.petTapProgress).toBe(tap);
    }

    const fifthTap = applyPetTap(state);
    expect(fifthTap.rewarded).toBe(true);
    expect(fifthTap.state.stats.happiness).toBe(95);
    expect(fifthTap.state.petTapProgress).toBe(0);

    const nearMaximum = { ...state, petTapProgress: 4, stats: { ...state.stats, happiness: 95 } };
    expect(applyPetTap(nearMaximum).state.stats.happiness).toBe(100);
  });

  it("spends outfit experience atomically and rejects an insufficient balance", () => {
    writeLocalPetState({ ...petState(), experience: 2 });

    const spent = commitLocalPetStateMutation(
      { kind: "spend-experience", amount: 2 },
      "pet-1",
    );
    const rejected = commitLocalPetStateMutation(
      { kind: "spend-experience", amount: 2 },
      "pet-1",
    );

    expect(spent?.pet.experience).toBe(0);
    expect(rejected?.rejected).toBe(true);
    expect(readLocalPetState()?.experience).toBe(0);
  });

  it("decays each stat from its own tick and advances the life stage", () => {
    const progressed = applyOfflineProgress(
      petState(),
      new Date("2026-07-10T06:00:00.000Z"),
    );

    expect(progressed.stats).toEqual({ hunger: 0, happiness: 0, energy: 0 });
    expect(progressed.stage).toBe("adult");
    expect(progressed.mood).toBe("hungry");
  });

  it("applies a partial server patch without resetting unrelated stat ticks", () => {
    const state = petState();
    const updated = applyStatsPatch(state, {
      stats: { energy: 35 },
      lastStatTickAt: { energy: "2026-07-09T10:00:00.000Z" },
    });

    expect(updated.stats).toEqual({ hunger: 80, happiness: 80, energy: 35 });
    expect(updated.lastStatTickAt.hunger).toBe(state.lastStatTickAt.hunger);
    expect(updated.lastStatTickAt.energy).toBe("2026-07-09T10:00:00.000Z");
  });

  it("ignores a server stat patch older than the local per-stat tick", () => {
    const state = petState();
    const updated = applyStatsPatch(state, {
      stats: { energy: 1 },
      lastStatTickAt: { energy: "2026-07-08T10:00:00.000Z" },
    });

    expect(updated).toBe(state);
  });

  it("dies only after spending more than 24 continuous hours at zero", () => {
    const zeroThreshold = new Date("2026-07-11T01:12:00.000Z");
    const atThreshold = applyOfflineProgress(petState(), zeroThreshold);

    expect(atThreshold.stats.hunger).toBe(0);
    expect(atThreshold.zeroStatSinceAt?.hunger).toBe("2026-07-10T01:12:00.000Z");
    expect(atThreshold.diedAt).toBeUndefined();

    const afterThreshold = applyOfflineProgress(
      atThreshold,
      new Date("2026-07-11T01:12:00.001Z"),
    );
    expect(afterThreshold.diedAt).toBe("2026-07-11T01:12:00.000Z");
  });

  it("clears a stale zero timer when the parameter is restored", () => {
    const state = {
      ...petState(),
      zeroStatSinceAt: { hunger: "2026-07-01T00:00:00.000Z" },
    };

    const progressed = applyOfflineProgress(
      state,
      new Date("2026-07-09T06:00:00.000Z"),
    );

    expect(progressed.zeroStatSinceAt?.hunger).toBeUndefined();
    expect(progressed.diedAt).toBeUndefined();
  });

  it("bounds accumulated lite-overlay facts while preserving the newest ones", () => {
    const images = {
      idle: "idle.png",
      happy: "happy.png",
      hungry: "hungry.png",
      sad: "sad.png",
    };
    const facts = Array.from({ length: 90 }, (_, index) => ({
      sphere: "world",
      kind: "world_fact",
      text: `fact-${index}`,
    }));
    const state = createLocalPetState("мышонок", {
      assetSetId: "asset-1",
      generatedAt: "2026-07-09T06:00:00.000Z",
      images: { baby: images, teen: images, adult: images },
      characterBible: {
        extensions: {
          lite_overlay: {
            facts,
            spheres: { world: { facts } },
          },
        },
      },
    });

    const updated = applyLiteOverlayPatch(state, {
      facts: Array.from({ length: 10 }, (_, offset) => ({
        sphere: "world",
        kind: "world_fact",
        text: `fact-${90 + offset}`,
      })),
    });
    const bible = updated.assetSet?.characterBible as {
      extensions?: {
        lite_overlay?: {
          facts: Array<{ text: string }>;
          spheres: { world: { facts: Array<{ text: string }> } };
        };
      };
    };
    const overlay = bible.extensions?.lite_overlay;

    expect(overlay?.facts).toHaveLength(80);
    expect(overlay?.facts[0].text).toBe("fact-20");
    expect(overlay?.facts.at(-1)?.text).toBe("fact-99");
    expect(overlay?.spheres.world.facts).toHaveLength(40);
    expect(overlay?.spheres.world.facts[0].text).toBe("fact-60");
    expect(overlay?.spheres.world.facts.at(-1)?.text).toBe("fact-99");
  });

  it("drops unknown nested overlay fields before they reach persistence", () => {
    const images = {
      idle: "idle.png",
      happy: "happy.png",
      hungry: "hungry.png",
      sad: "sad.png",
    };
    const state = createLocalPetState("мышонок", {
      assetSetId: "asset-1",
      generatedAt: "2026-07-09T06:00:00.000Z",
      images: { baby: images, teen: images, adult: images },
      characterBible: {},
    });

    const withFacts = applyLiteOverlayPatch(state, {
      facts: [{
        sphere: "invalid",
        kind: "invalid",
        text: "x".repeat(700),
        pathHint: "p".repeat(200),
        source: "s".repeat(200),
        createdAt: "t".repeat(200),
        unknown: "drop-me",
      }],
      worldSeed: { source: "source", createdAt: "now", unknown: "drop-me" },
      unknown: "drop-me",
    });
    const withStory = applyStoryLibraryPatch(withFacts, {
      bricks: [{
        id: "brick-1",
        pool: "personal",
        name: "Встреча",
        text: "История",
        attributes: JSON.parse(
          '{"safe":"ok","__proto__":{"polluted":true},"constructor":{"prototype":{"polluted":true}}}',
        ),
        basedOnBrickIds: Array.from({ length: 12 }, (_, index) => `brick-${index}`),
        unknown: "drop-me",
      }],
      unknown: "drop-me",
    });
    const extensions = withStory.assetSet?.characterBible?.extensions as {
      lite_overlay?: Record<string, unknown>;
      story_library_overlay?: { bricks: Record<string, unknown>[] };
    };
    const liteOverlay = extensions.lite_overlay ?? {};
    const fact = (liteOverlay.facts as Record<string, unknown>[])[0];
    const brick = extensions.story_library_overlay?.bricks[0] ?? {};

    expect(Object.keys(liteOverlay).sort()).toEqual([
      "facts",
      "spheres",
      "updatedAt",
      "worldSeed",
    ]);
    expect(Object.keys(fact).sort()).toEqual([
      "createdAt",
      "kind",
      "pathHint",
      "source",
      "sphere",
      "text",
    ]);
    expect(String(fact.text)).toHaveLength(500);
    expect(Object.hasOwn(brick, "unknown")).toBe(false);
    expect(brick.basedOnBrickIds).toHaveLength(8);
    expect(Object.hasOwn(brick.attributes as object, "__proto__")).toBe(false);
    expect(Object.hasOwn(brick.attributes as object, "constructor")).toBe(false);
  });

  it("bounds persisted chat history by UTF-8 bytes while keeping newest messages", () => {
    writeLocalPetState(petState());
    const history = writeLocalChatHistory({
      version: 1,
      messages: Array.from({ length: 300 }, (_, index) => ({
        id: `message-${index}`,
        role: index % 2 === 0 ? "user" as const : "pet" as const,
        text: "я".repeat(8_000),
        createdAt: "2026-07-09T06:00:00.000Z",
      })),
    });
    const persisted = window.localStorage.getItem(CHAT_HISTORY_STORAGE_KEY) ?? "";

    expect(new TextEncoder().encode(persisted).byteLength).toBeLessThanOrEqual(512_000);
    expect(history.messages.length).toBeLessThan(300);
    expect(history.messages.at(-1)?.id).toBe("message-299");
  });

  it("keeps mutations usable when localStorage quota is exhausted", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });
    const state = petState();

    const written = writeLocalPetState(state);
    expect(written).toMatchObject(state);
    expect(() => writeLocalChatHistory({ version: 1, messages: [] })).not.toThrow();
    expect(readLocalPetState()).toEqual(written);

    setItem.mockRestore();
  });

  it("rejects a paid pet result when its full state is not durable", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation((key) => {
      if (key === PET_STATE_STORAGE_KEY) {
        throw new DOMException("quota exceeded", "QuotaExceededError");
      }
    });
    const state = {
      ...petState(),
      assetSet: {
        assetSetId: "paid-assets-1",
        generatedAt: "2026-07-09T06:00:00.000Z",
        images: Object.fromEntries(
          ["baby", "teen", "adult"].map((stage) => [
            stage,
            Object.fromEntries(
              ["idle", "happy", "hungry", "sad"].map((mood) => [
                mood,
                `/static/generated/paid-assets-1/${stage}-${mood}.png`,
              ]),
            ),
          ]),
        ) as NonNullable<LocalPetState["assetSet"]>["images"],
      },
    };

    expect(writeLocalPetStateDurably(state)).toBeNull();
    expect(readLocalPetState()).toBeNull();
    expect(window.localStorage.getItem(PET_STATE_STORAGE_KEY)).toBeNull();
    setItem.mockRestore();
  });

  it("rejects a same-timestamp snapshot overwritten by another tab", () => {
    const state = {
      ...petState(),
      assetSet: {
        assetSetId: "paid-assets-collision",
        generatedAt: "2026-07-09T06:00:00.000Z",
        images: {
          baby: { idle: "/i.png", happy: "/h.png", hungry: "/u.png", sad: "/s.png" },
          teen: { idle: "/i.png", happy: "/h.png", hungry: "/u.png", sad: "/s.png" },
          adult: { idle: "/i.png", happy: "/h.png", hungry: "/u.png", sad: "/s.png" },
        },
      },
    };
    const conflicting = { ...state, stats: { ...state.stats, hunger: 1 } };
    const nativeSetItem = window.localStorage.setItem.bind(window.localStorage);
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation((key, value) => {
      nativeSetItem(
        key,
        key === PET_STATE_STORAGE_KEY ? JSON.stringify(conflicting) : value,
      );
    });

    expect(writeLocalPetStateDurably(state)).toBeNull();
    expect(JSON.parse(window.localStorage.getItem(PET_STATE_STORAGE_KEY) ?? "null"))
      .toMatchObject({ stats: { hunger: 1 } });
    setItem.mockRestore();
  });

  it("keeps chat history across reads while localStorage is unavailable", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });
    const createdAt = "2026-07-09T06:00:00.000Z";

    appendLocalChatMessages([
      { id: "user-1", role: "user", text: "Привет", createdAt },
    ]);
    appendLocalChatMessages([
      { id: "pet-1", role: "pet", text: "Привет!", createdAt },
    ]);

    expect(readLocalChatHistory().messages.map((message) => message.id)).toEqual([
      "user-1",
      "pet-1",
    ]);
    getItem.mockRestore();
    setItem.mockRestore();
    expect(readLocalChatHistory().messages.map((message) => message.id)).toEqual([
      "user-1",
      "pet-1",
    ]);

    clearLocalChatHistory();
    expect(readLocalChatHistory().messages).toEqual([]);
    expect(window.localStorage.getItem(CHAT_HISTORY_STORAGE_KEY)).toBeNull();
  });

  it("keeps and retries settings when localStorage rejects writes", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });

    writeLocalPetSettings({ includePromptDebug: true });
    expect(readLocalPetSettings()).toEqual({ includePromptDebug: true });

    setItem.mockRestore();
    expect(readLocalPetSettings()).toEqual({ includePromptDebug: true });
    expect(JSON.parse(window.localStorage.getItem(SETTINGS_STORAGE_KEY) ?? "null")).toEqual({
      includePromptDebug: true,
    });
  });

  it("keeps dialogue hooks and consecutive valid pet messages durable", () => {
    const createdAt = "2026-07-09T06:00:00.000Z";
    writeLocalChatHistory({
      version: 1,
      messages: [
        { id: "hook", role: "pet", text: "Я кое-что вспомнил", createdAt },
        { id: "user", role: "user", text: "Рассказывай", createdAt },
        { id: "reply", role: "pet", text: "История", createdAt },
        { id: "ambient", role: "pet", text: "И ещё мысль", createdAt },
      ],
    });

    expect(readLocalChatHistory().messages.map((message) => message.id)).toEqual([
      "hook",
      "user",
      "reply",
      "ambient",
    ]);
  });

  it("retries every reset key before persisting a new pet session", () => {
    const createdAt = "2026-07-09T06:00:00.000Z";
    writeLocalPetState(petState());
    writeLocalChatHistory({
      version: 1,
      messages: [{ id: "old-user", role: "user", text: "Старый чат", createdAt }],
    });
    writeLocalPetSettings({ includePromptDebug: true });
    const nativeRemoveItem = Storage.prototype.removeItem;
    const removeItem = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (key === CHAT_HISTORY_STORAGE_KEY || key === SETTINGS_STORAGE_KEY) {
        throw new DOMException("storage unavailable", "SecurityError");
      }
      return nativeRemoveItem.call(this, key);
    });

    resetLocalPetState();
    const newPet = writeLocalPetState({ ...petState(), petId: "pet-2" });
    writeLocalChatHistory({
      version: 1,
      messages: [{ id: "new-user", role: "user", text: "Новый чат", createdAt }],
    });

    expect(readLocalPetState()).toEqual(newPet);
    expect(readLocalChatHistory().messages.map((message) => message.id)).toEqual(["new-user"]);
    expect(readLocalPetSettings()).toEqual({ includePromptDebug: false });
    expect(window.localStorage.getItem(CHAT_HISTORY_STORAGE_KEY)).toContain("old-user");
    expect(window.localStorage.getItem(SETTINGS_STORAGE_KEY)).toContain("true");
    removeItem.mockRestore();

    expect(readLocalPetState()).toEqual(newPet);
    expect(readLocalChatHistory().messages.map((message) => message.id)).toEqual(["new-user"]);
    expect(readLocalPetSettings()).toEqual({ includePromptDebug: false });
    expect(window.localStorage.getItem(SETTINGS_STORAGE_KEY)).toBeNull();
  });
});
