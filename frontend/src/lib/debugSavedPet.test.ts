import { beforeEach, describe, expect, it } from "vitest";

import {
  readLocalChatHistory,
  readLocalPetState,
  resetLocalPetState,
  writeLocalChatHistory,
  writeLocalPetState,
} from "./localPetStorage";
import {
  createEmptyLocalPetMemory,
  readLocalPetMemory,
  resetLocalPetMemory,
  writeLocalPetMemory,
} from "./localPetMemoryStorage";
import { restoreDebugPetBundle } from "./debugSavedPet";
import type { LocalPetState } from "./types";

function pet(petId: string): LocalPetState {
  const timestamp = "2026-07-16T10:00:00.000Z";
  return {
    version: 2,
    petId,
    description: petId,
    createdAt: timestamp,
    updatedAt: timestamp,
    lastInteractionAt: timestamp,
    lastStatsTickAt: timestamp,
    lastStatTickAt: {
      hunger: timestamp,
      happiness: timestamp,
      energy: timestamp,
    },
    stage: "teen",
    mood: "idle",
    stats: { hunger: 80, happiness: 70, energy: 60 },
  };
}

beforeEach(() => {
  resetLocalPetState();
  resetLocalPetMemory();
  window.localStorage.clear();
});

describe("restoreDebugPetBundle", () => {
  it("replaces the active pet, chat, and memory", () => {
    const current = pet("pet-current");
    const saved = pet("pet-saved");
    writeLocalPetState(current);
    writeLocalChatHistory({
      version: 1,
      messages: [{ id: "old", role: "user", text: "старое", createdAt: current.createdAt }],
    });
    writeLocalPetMemory(createEmptyLocalPetMemory(current.petId), current.petId);

    const savedMemory = {
      ...createEmptyLocalPetMemory(saved.petId),
      summary: "Память сохранённого персонажа",
    };
    const restored = restoreDebugPetBundle({
      petId: saved.petId,
      pet: saved,
      chatHistory: {
        version: 1,
        messages: [{ id: "saved", role: "pet", text: "я вернулся", createdAt: saved.createdAt }],
      },
      memory: savedMemory,
    });

    expect(restored.petId).toBe("pet-saved");
    expect(readLocalPetState()?.petId).toBe("pet-saved");
    expect(readLocalChatHistory().messages.map((message) => message.id)).toEqual(["saved"]);
    expect(readLocalPetMemory("pet-saved").summary).toBe("Память сохранённого персонажа");
    expect(window.localStorage.getItem("tamagochi:v1:pet-memory:pet-current")).toBeNull();
  });
});
