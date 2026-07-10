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
});
