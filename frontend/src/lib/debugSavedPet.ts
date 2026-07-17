import {
  activateDebugSavedPet,
  saveDebugPet,
  type DebugSavedPetBundle,
} from "./api";
import {
  clearLocalChatHistory,
  readLocalChatHistory,
  readLocalPetState,
  resetLocalPetState,
  writeLocalChatHistory,
  writeLocalPetStateDurably,
} from "./localPetStorage";
import { clearLocalPetScopedData } from "./localPetScopedCleanup";
import {
  readLocalPetMemory,
  writeLocalPetMemory,
} from "./localPetMemoryStorage";
import type { LocalPetState } from "./types";

export function currentDebugPetBundle(pet: LocalPetState): DebugSavedPetBundle {
  return {
    petId: pet.petId,
    pet,
    chatHistory: readLocalChatHistory(),
    memory: readLocalPetMemory(pet.petId),
  };
}

export async function ensureCurrentDebugPetSaved(pet: LocalPetState) {
  return saveDebugPet(currentDebugPetBundle(pet));
}

export function restoreDebugPetBundle(bundle: DebugSavedPetBundle): LocalPetState {
  const currentPet = readLocalPetState();
  if (currentPet) {
    clearLocalPetScopedData(currentPet.petId);
    if (!resetLocalPetState(currentPet.petId)) {
      throw new Error("Не удалось удалить данные текущего персонажа.");
    }
  } else {
    clearLocalChatHistory();
  }

  const restoredPet = writeLocalPetStateDurably(bundle.pet);
  if (!restoredPet || restoredPet.petId !== bundle.petId) {
    throw new Error("Не удалось сохранить восстановленного персонажа на устройстве.");
  }
  writeLocalChatHistory(bundle.chatHistory);
  if (!writeLocalPetMemory(bundle.memory, bundle.petId)) {
    throw new Error("Не удалось восстановить память персонажа.");
  }
  return restoredPet;
}

export async function activateAndRestoreDebugPet(): Promise<LocalPetState> {
  const response = await activateDebugSavedPet();
  return restoreDebugPetBundle(response.bundle);
}
