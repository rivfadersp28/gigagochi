import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { extractDeterministicMemoryOperations } from "./localPetDeterministicMemory";
import {
  buildAmbientMemoryContext,
  buildMemoryContextForMessage,
  buildMemorySnapshotContext,
} from "./localPetMemoryRecall";
import {
  applyMemoryOperations,
  createEmptyLocalPetMemory,
  localPetMemoryStorageKey,
  readLocalPetMemory,
  resetLocalPetMemory,
  writeLocalPetMemory,
} from "./localPetMemoryStorage";
import { resetLocalPetState, writeLocalPetState } from "./localPetStorage";
import type { LocalPetState } from "./types";

function activatePet(petId: string): LocalPetState {
  const now = "2026-07-15T12:00:00.000Z";
  return writeLocalPetState({
    version: 2,
    petId,
    description: petId,
    createdAt: now,
    updatedAt: now,
    lastInteractionAt: now,
    lastStatsTickAt: now,
    lastStatTickAt: { hunger: now, happiness: now, energy: now },
    stage: "baby",
    mood: "idle",
    stats: { hunger: 100, happiness: 100, energy: 100 },
  });
}

describe("local pet memory", () => {
  beforeEach(() => {
    resetLocalPetMemory();
    resetLocalPetState();
    window.localStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("recalls preferences for an explicit preference question", () => {
    const memory = applyMemoryOperations(
      createEmptyLocalPetMemory("pet-1"),
      extractDeterministicMemoryOperations("Я люблю кофе с корицей"),
    );

    const context = buildMemoryContextForMessage([], "Что я люблю?", new Date(), memory);

    expect(context.relevantMemories.map((item) => item.text)).toContain(
      "Пользователь любит кофе с корицей.",
    );
  });

  it("keeps summary and user profile in chat and push snapshots", () => {
    const memory = {
      ...createEmptyLocalPetMemory("pet-1"),
      summary: "Мы обсуждали переезд.",
      userProfile: "Пользователь любит спокойные короткие ответы.",
    };

    expect(buildMemoryContextForMessage([], "Привет", new Date(), memory)).toMatchObject({
      summary: memory.summary,
      userProfile: memory.userProfile,
    });
    expect(buildMemorySnapshotContext(memory)).toMatchObject({
      summary: memory.summary,
      userProfile: memory.userProfile,
    });
  });

  it("gives ambient only soft memory and prioritizes the user name", () => {
    const memory = applyMemoryOperations(createEmptyLocalPetMemory("pet-1"), [
      {
        type: "remember_user_fact",
        kind: "deadline",
        text: "У пользователя завтра экзамен.",
        normalizedKey: "exam",
        confidence: 1,
        importance: 1,
      },
      {
        type: "remember_user_fact",
        kind: "preference",
        text: "Пользователь любит чай.",
        normalizedKey: "tea",
        confidence: 1,
        importance: 0.8,
      },
      {
        type: "remember_user_fact",
        kind: "user_fact",
        text: "Пользователя зовут Серёга.",
        normalizedKey: "user-name",
        confidence: 1,
        importance: 1,
      },
    ]);

    const context = buildAmbientMemoryContext(memory);

    expect(context.summary).toBeUndefined();
    expect(context.userProfile).toBeUndefined();
    expect(context.relevantMemories.map((item) => item.text)).toEqual([
      "Пользователя зовут Серёга.",
      "Пользователь любит чай.",
    ]);
  });

  it("remembers a short Russian self-introduction as the canonical user name", () => {
    const memory = applyMemoryOperations(
      createEmptyLocalPetMemory("pet-1"),
      extractDeterministicMemoryOperations("Я серега"),
    );

    expect(memory.memories).toHaveLength(1);
    expect(memory.memories[0]).toMatchObject({
      normalizedKey: "user-name",
      text: "Пользователя зовут серега.",
      memoryClass: "core",
    });
  });

  it("recalls the user name for a natural 'who am I' question", () => {
    const memory = applyMemoryOperations(
      createEmptyLocalPetMemory("pet-1"),
      extractDeterministicMemoryOperations("я Серега"),
    );

    const context = buildMemoryContextForMessage([], "кто я", new Date(), memory);

    expect(context.relevantMemories.map((item) => item.text)).toContain(
      "Пользователя зовут Серега.",
    );
  });

  it("normalizes provider user-name aliases to the canonical key", () => {
    const memory = applyMemoryOperations(createEmptyLocalPetMemory("pet-1"), [
      {
        type: "remember_user_fact",
        kind: "user_fact",
        text: "Серёга",
        normalizedKey: "user_name",
        confidence: 1,
        importance: 1,
      },
    ]);

    expect(memory.memories[0]).toMatchObject({
      normalizedKey: "user-name",
      memoryClass: "core",
    });
  });

  it("stores tomorrow exam as a deadline and recalls it by topic", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-13T12:00:00.000Z"));

    const memory = applyMemoryOperations(
      createEmptyLocalPetMemory("pet-1"),
      extractDeterministicMemoryOperations("у меня завтра экзамен"),
    );
    const context = buildMemoryContextForMessage(
      [],
      "что насчет экзамена?",
      new Date("2026-07-13T12:00:00.000Z"),
      memory,
    );

    expect(memory.memories[0]).toMatchObject({
      kind: "deadline",
      normalizedKey: "deadline-экзамен",
      memoryClass: "fact",
    });
    expect(memory.memories[0]?.dueAt).toBeTruthy();
    expect(Date.parse(memory.memories[0]?.dueAt ?? "")).toBeGreaterThan(
      Date.parse("2026-07-13T12:00:00.000Z"),
    );
    expect(context.relevantMemories.map((item) => item.text)).toContain(
      "У пользователя завтра экзамен.",
    );
  });

  it("coerces provider event memories with dueAt into deadlines", () => {
    const memory = applyMemoryOperations(createEmptyLocalPetMemory("pet-1"), [
      {
        type: "remember_user_fact",
        kind: "event",
        text: "У меня завтра экзамен",
        normalizedKey: "exam_tomorrow",
        confidence: 1,
        importance: 1,
        dueAt: "2026-07-14T00:00:00.000Z",
      },
    ]);

    expect(memory.memories[0]).toMatchObject({
      kind: "deadline",
      normalizedKey: "exam-tomorrow",
      memoryClass: "fact",
    });
  });

  it("replaces stale preference wording even with lower confidence", () => {
    const initial = applyMemoryOperations(createEmptyLocalPetMemory("pet-1"), [
      {
        type: "remember_user_fact",
        kind: "preference",
        text: "Пользователь любит кофе.",
        normalizedKey: "drink",
        confidence: 0.99,
        importance: 0.8,
      },
    ]);
    const corrected = applyMemoryOperations(initial, [
      {
        type: "replace_user_fact",
        kind: "preference",
        text: "Пользователь любит чай.",
        normalizedKey: "drink",
        confidence: 0.7,
        importance: 0.8,
      },
    ]);

    expect(corrected.memories).toHaveLength(1);
    expect(corrected.memories[0]?.text).toBe("Пользователь любит чай.");
  });

  it("forgets a natural-language preference and persists the deletion", () => {
    const petId = "pet-1";
    const remembered = applyMemoryOperations(
      createEmptyLocalPetMemory(petId),
      extractDeterministicMemoryOperations("Я люблю кофе"),
    );
    const forgotten = applyMemoryOperations(
      remembered,
      extractDeterministicMemoryOperations("Забудь, что я люблю кофе"),
    );
    writeLocalPetMemory(forgotten);

    expect(readLocalPetMemory(petId).memories).toEqual([]);
  });

  it("removes an old preference when the user explicitly corrects it", () => {
    const remembered = applyMemoryOperations(
      createEmptyLocalPetMemory("pet-1"),
      extractDeterministicMemoryOperations("Я люблю кофе"),
    );
    const corrected = applyMemoryOperations(
      remembered,
      extractDeterministicMemoryOperations("Раньше я любил кофе, теперь люблю чай"),
    );

    expect(corrected.memories.map((item) => item.text)).toEqual([
      "Пользователь любит чай.",
    ]);
  });

  it("lazily migrates v1 memories without rebuilding the character", () => {
    const petId = "legacy-pet";
    window.localStorage.setItem(localPetMemoryStorageKey(petId), JSON.stringify({
      version: 1,
      petId,
      createdAt: "2025-01-01T00:00:00.000Z",
      updatedAt: "2025-01-01T00:00:00.000Z",
      learnings: [],
      proactiveLog: [],
      memories: [{
        id: "legacy-name",
        kind: "user_fact",
        text: "Пользователя зовут Сергей.",
        normalizedKey: "user-name",
        confidence: 1,
        importance: 1,
        createdAt: "2025-01-01T00:00:00.000Z",
        updatedAt: "2025-01-01T00:00:00.000Z",
        mentionCount: 0,
        sourceLearningIds: [],
        tags: [],
      }],
    }));

    const migrated = readLocalPetMemory(petId);

    expect(migrated.version).toBe(2);
    expect(migrated.memories[0]).toMatchObject({
      memoryClass: "core",
      recordedAt: "2025-01-01T00:00:00.000Z",
    });
    expect(migrated.memories[0]?.occurredAt).toBeUndefined();
  });

  it("does not recall an old episode spontaneously but keeps explicit recall", () => {
    const memory = applyMemoryOperations(createEmptyLocalPetMemory("pet-1"), [{
      type: "remember_user_fact",
      kind: "event",
      text: "Питомец нашёл медный ключ у башни.",
      normalizedKey: "tower-key-event",
      confidence: 1,
      importance: 0.8,
      occurredAt: "2025-01-01T12:00:00.000Z",
    }]);
    const now = new Date("2026-07-10T12:00:00.000Z");

    expect(buildMemoryContextForMessage([], "Расскажи про медный ключ", now, memory)
      .relevantMemories).toEqual([]);
    expect(buildMemoryContextForMessage([], "Помнишь медный ключ?", now, memory)
      .relevantMemories.map((item) => item.id)).toEqual([memory.memories[0]?.id]);
  });

  it("recalls an episode from the requested relative day", () => {
    const memory = applyMemoryOperations(createEmptyLocalPetMemory("pet-1"), [{
      type: "remember_user_fact",
      kind: "event",
      text: "Питомец ходил к старой башне.",
      normalizedKey: "old-tower-event",
      confidence: 1,
      importance: 0.8,
      occurredAt: "2026-07-08T10:00:00.000Z",
    }]);

    const context = buildMemoryContextForMessage(
      [],
      "Что ты делал позавчера?",
      new Date("2026-07-10T12:00:00.000Z"),
      memory,
    );

    expect(context.relevantMemories[0]).toMatchObject({
      memoryClass: "episode",
      occurredAt: "2026-07-08T10:00:00.000Z",
    });
  });

  it("does not throw when memory persistence exceeds the WebView quota", () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });

    expect(() => writeLocalPetMemory(createEmptyLocalPetMemory("pet-quota"))).not.toThrow();
    setItem.mockRestore();
  });

  it("keeps the latest memory across reads while localStorage is unavailable", () => {
    const petId = "pet-session";
    activatePet(petId);
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("storage unavailable", "SecurityError");
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });
    const memory = applyMemoryOperations(createEmptyLocalPetMemory(petId), [{
      type: "remember_user_fact",
      kind: "user_fact",
      text: "Пользователя зовут Сергей.",
      normalizedKey: "user-name",
      confidence: 1,
      importance: 1,
    }]);

    expect(writeLocalPetMemory(memory, petId)).toBe(true);
    expect(readLocalPetMemory(petId).memories[0]?.normalizedKey).toBe("user-name");
    expect(readLocalPetMemory(petId).memories[0]?.text).toBe("Пользователя зовут Сергей.");

    getItem.mockRestore();
    setItem.mockRestore();
    expect(readLocalPetMemory(petId).memories[0]?.normalizedKey).toBe("user-name");
    expect(window.localStorage.getItem(localPetMemoryStorageKey(petId))).toContain("Сергей");
  });

  it("keeps a failed reset authoritative and rejects a late write for the old pet", () => {
    const oldPetId = "pet-old";
    activatePet(oldPetId);
    const lateOldMemory = applyMemoryOperations(createEmptyLocalPetMemory(oldPetId), [{
      type: "remember_user_fact",
      kind: "user_fact",
      text: "Старая память.",
      normalizedKey: "old-memory",
      confidence: 1,
      importance: 1,
    }]);
    expect(writeLocalPetMemory(lateOldMemory, oldPetId)).toBe(true);

    const nativeRemoveItem = Storage.prototype.removeItem;
    const removeItem = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(function (
      this: Storage,
      key: string,
    ) {
      if (key === localPetMemoryStorageKey(oldPetId)) {
        throw new DOMException("storage unavailable", "SecurityError");
      }
      return nativeRemoveItem.call(this, key);
    });
    resetLocalPetMemory(oldPetId);
    expect(readLocalPetMemory(oldPetId).memories).toEqual([]);
    expect(window.localStorage.getItem(localPetMemoryStorageKey(oldPetId))).toContain("Старая");

    const newPetId = "pet-new";
    activatePet(newPetId);
    expect(writeLocalPetMemory(lateOldMemory, oldPetId)).toBe(false);
    const newMemory = applyMemoryOperations(createEmptyLocalPetMemory(newPetId), [{
      type: "remember_user_fact",
      kind: "preference",
      text: "Новый питомец помнит чай.",
      normalizedKey: "drink",
      confidence: 1,
      importance: 1,
    }]);
    expect(writeLocalPetMemory(newMemory, newPetId)).toBe(true);
    expect(readLocalPetMemory(newPetId).memories[0]?.text).toBe("Новый питомец помнит чай.");
    expect(readLocalPetMemory(oldPetId).memories).toEqual([]);

    removeItem.mockRestore();
    expect(readLocalPetMemory(oldPetId).memories).toEqual([]);
    expect(window.localStorage.getItem(localPetMemoryStorageKey(oldPetId))).toBeNull();
    expect(readLocalPetMemory(newPetId).memories[0]?.text).toBe("Новый питомец помнит чай.");
  });
});
