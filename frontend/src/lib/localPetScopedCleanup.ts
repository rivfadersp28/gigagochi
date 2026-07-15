import { clearDebugPanelEvents } from "./debugPanelStorage";
import {
  clearLocalInteractiveTravel,
  readLocalInteractiveTravel,
} from "./localInteractiveTravel";
import { clearRecentAmbientReplies } from "./localPetAmbientMemory";
import { clearComplimentHistory } from "./localPetCompliments";
import { clearPetIntroduction } from "./localPetIntroduction";
import { resetLocalPetMemory } from "./localPetMemoryStorage";
import { clearPendingPetGeneration } from "./pendingPetGeneration";
import {
  enqueueInteractiveTravelCancel,
  enqueueInteractiveTravelCapture,
} from "./pendingInteractiveTravelOperations";
import { clearPetTapThanksForSession } from "./petTapThanks";

export function clearLocalPetScopedData(petId: string) {
  const normalizedPetId = petId.trim().slice(0, 120);
  if (!normalizedPetId) {
    return;
  }

  const interactiveTravel = readLocalInteractiveTravel(normalizedPetId)?.travel;
  if (interactiveTravel?.completed) {
    enqueueInteractiveTravelCapture(interactiveTravel);
  } else if (interactiveTravel) {
    enqueueInteractiveTravelCancel(interactiveTravel.travelId);
  }

  resetLocalPetMemory(normalizedPetId);
  clearRecentAmbientReplies(normalizedPetId);
  clearPetIntroduction(normalizedPetId);
  clearLocalInteractiveTravel(normalizedPetId);
  clearComplimentHistory(normalizedPetId);
  clearPetTapThanksForSession(normalizedPetId);

  // The MVP has exactly one active pet. These stores predate per-pet keys.
  clearPendingPetGeneration();
  clearDebugPanelEvents();
}
