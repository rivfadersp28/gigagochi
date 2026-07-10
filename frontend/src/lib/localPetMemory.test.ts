import { beforeEach, describe, expect, it } from "vitest";

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
  writeLocalPetMemory,
} from "./localPetMemoryStorage";

describe("local pet memory", () => {
  beforeEach(() => window.localStorage.clear());

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
});
