const LOCK_PREFIX = "tamagochi:v1:pet-mutation";
const DEFAULT_ACQUIRE_TIMEOUT_MS = 10_000;
const MAX_ACQUIRE_TIMEOUT_MS = 15 * 60_000;

export type LocalPetMutationScope = "conversation" | "memory" | "travel";

export type LocalPetMutationGuard = {
  assertOwned: () => void;
};

export class LocalPetMutationLockError extends Error {
  constructor(message = "Local pet data is being updated in another tab") {
    super(message);
    this.name = "LocalPetMutationLockError";
  }
}

function resourceName(petId: string, scope: LocalPetMutationScope) {
  return `${LOCK_PREFIX}:${scope}:${petId}`;
}

export async function withLocalPetMutationLock<T>(
  petId: string,
  scope: LocalPetMutationScope,
  mutation: (guard: LocalPetMutationGuard) => Promise<T> | T,
  options: { acquireTimeoutMs?: number } = {},
): Promise<T> {
  const requestedTimeout = options.acquireTimeoutMs ?? DEFAULT_ACQUIRE_TIMEOUT_MS;
  const acquireTimeoutMs = Number.isFinite(requestedTimeout) && requestedTimeout > 0
    ? Math.min(requestedTimeout, MAX_ACQUIRE_TIMEOUT_MS)
    : DEFAULT_ACQUIRE_TIMEOUT_MS;
  const locks = typeof navigator === "undefined" ? undefined : navigator.locks;
  if (!locks) {
    return mutation({ assertOwned: () => undefined });
  }

  const abortController = new AbortController();
  let acquired = false;
  const timeoutId = window.setTimeout(() => {
    abortController.abort(new LocalPetMutationLockError());
  }, acquireTimeoutMs);
  try {
    return await locks.request(
      resourceName(petId, scope),
      { mode: "exclusive", signal: abortController.signal },
      async () => {
        acquired = true;
        window.clearTimeout(timeoutId);
        return mutation({ assertOwned: () => undefined });
      },
    );
  } catch (error) {
    if (!acquired && abortController.signal.aborted) {
      throw new LocalPetMutationLockError();
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}
