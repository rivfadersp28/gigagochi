import { startInteractiveTravel } from "./api";
import { ApiError } from "./apiTransport";
import {
  enqueueInteractiveTravelCancel,
  flushPendingInteractiveTravelOperations,
  readPendingInteractiveTravelOperations,
} from "./pendingInteractiveTravelOperations";
import type { InteractiveTravelResponse, LocalPetState } from "./types";

const ACTIVE_TRAVEL_CODE = "INTERACTIVE_TRAVEL_ALREADY_ACTIVE";
const TRAVEL_ID_PATTERN = /^interactive-travel-[A-Za-z0-9_-]+$/u;

type InteractiveTravelStartOptions = {
  includeDebug?: boolean;
};

function recoverableTravelId(error: unknown): string | null {
  if (
    !(error instanceof ApiError)
    || error.status !== 409
    || error.code !== ACTIVE_TRAVEL_CODE
  ) {
    return null;
  }
  const travelId = error.travelId;
  return travelId
    && travelId.length >= 20
    && travelId.length <= 160
    && TRAVEL_ID_PATTERN.test(travelId)
    ? travelId
    : null;
}

/**
 * Recovers only the explicit new-pet conflict reported by the owner-bound API.
 * An ambiguous/network failure is never retried, and the recovered start is attempted once.
 */
export async function startInteractiveTravelWithRecovery(
  destination: string,
  pet: LocalPetState,
  options: InteractiveTravelStartOptions = {},
): Promise<InteractiveTravelResponse> {
  const start = () => startInteractiveTravel(destination, pet, options);
  try {
    return await start();
  } catch (error) {
    const activeTravelId = recoverableTravelId(error);
    if (!activeTravelId) {
      throw error;
    }
    if (!enqueueInteractiveTravelCancel(activeTravelId)) {
      throw new ApiError(
        "Не удалось сохранить безопасную отмену старого путешествия. Проверьте хранилище и попробуйте снова.",
        "INTERACTIVE_TRAVEL_RECOVERY_NOT_DURABLE",
      );
    }
    let flushError: unknown;
    try {
      await flushPendingInteractiveTravelOperations();
    } catch (error) {
      flushError = error;
    }
    const cancellationStillPending = readPendingInteractiveTravelOperations().some(
      (operation) => operation.kind === "cancel" && operation.travelId === activeTravelId,
    );
    if (cancellationStillPending && flushError !== undefined) {
      throw flushError;
    }
    return start();
  }
}
