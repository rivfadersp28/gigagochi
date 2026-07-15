import {
  cancelInteractiveTravel,
  captureInteractiveTravelFinale,
} from "./api";
import { parseInteractiveTravelResponse } from "./apiContracts";
import { ApiError } from "./apiTransport";
import type { InteractiveTravelState } from "./types";

export const PENDING_INTERACTIVE_TRAVEL_OPERATIONS_STORAGE_KEY =
  "gigagochi.pending-interactive-travel-operations.v1";
export const PENDING_INTERACTIVE_TRAVEL_OPERATIONS_EVENT =
  "gigagochi:pending-interactive-travel-operations";

const MAX_OPERATIONS = 8;
const TRAVEL_ID_PATTERN = /^interactive-travel-[A-Za-z0-9_-]+$/u;

export type PendingInteractiveTravelCancel = {
  kind: "cancel";
  travelId: string;
};

export type PendingInteractiveTravelCapture = {
  kind: "capture";
  travelId: string;
  travel: InteractiveTravelState;
};

export type PendingInteractiveTravelOperation =
  | PendingInteractiveTravelCancel
  | PendingInteractiveTravelCapture;

let activeFlush: Promise<void> | null = null;

function storage(): Storage | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function validTravelId(value: unknown): value is string {
  return typeof value === "string"
    && value.length >= 20
    && value.length <= 160
    && TRAVEL_ID_PATTERN.test(value);
}

function parseOperation(value: unknown): PendingInteractiveTravelOperation | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const operation = value as Record<string, unknown>;
  if (!validTravelId(operation.travelId)) {
    return null;
  }
  if (operation.kind === "cancel") {
    return { kind: "cancel", travelId: operation.travelId };
  }
  if (operation.kind !== "capture") {
    return null;
  }
  try {
    const travel = parseInteractiveTravelResponse({ travel: operation.travel }).travel;
    return travel.completed && travel.travelId === operation.travelId
      ? { kind: "capture", travelId: operation.travelId, travel }
      : null;
  } catch {
    return null;
  }
}

function bounded(
  operations: PendingInteractiveTravelOperation[],
): PendingInteractiveTravelOperation[] {
  const byTravelId = new Map<string, PendingInteractiveTravelOperation>();
  for (const operation of operations) {
    const current = byTravelId.get(operation.travelId);
    if (current?.kind === "capture" && operation.kind === "cancel") {
      continue;
    }
    byTravelId.delete(operation.travelId);
    byTravelId.set(operation.travelId, operation);
  }
  const result = [...byTravelId.values()];
  return result.length <= MAX_OPERATIONS
    ? result
    : [result[0], ...result.slice(-(MAX_OPERATIONS - 1))];
}

export function readPendingInteractiveTravelOperations(): PendingInteractiveTravelOperation[] {
  const store = storage();
  if (!store) {
    return [];
  }
  try {
    const value = JSON.parse(
      store.getItem(PENDING_INTERACTIVE_TRAVEL_OPERATIONS_STORAGE_KEY) ?? "[]",
    ) as unknown;
    return Array.isArray(value)
      ? bounded(value.map(parseOperation).filter(
          (operation): operation is PendingInteractiveTravelOperation => Boolean(operation),
        ))
      : [];
  } catch {
    return [];
  }
}

function writeOperations(operations: PendingInteractiveTravelOperation[]): boolean {
  const store = storage();
  if (!store) {
    return false;
  }
  const next = bounded(operations);
  try {
    if (!next.length) {
      store.removeItem(PENDING_INTERACTIVE_TRAVEL_OPERATIONS_STORAGE_KEY);
      return store.getItem(PENDING_INTERACTIVE_TRAVEL_OPERATIONS_STORAGE_KEY) === null;
    }
    const serialized = JSON.stringify(next);
    store.setItem(PENDING_INTERACTIVE_TRAVEL_OPERATIONS_STORAGE_KEY, serialized);
    return store.getItem(PENDING_INTERACTIVE_TRAVEL_OPERATIONS_STORAGE_KEY) === serialized;
  } catch {
    return false;
  }
}

function updateOperations(
  update: (
    current: PendingInteractiveTravelOperation[],
  ) => PendingInteractiveTravelOperation[],
): boolean {
  return writeOperations(update(readPendingInteractiveTravelOperations()));
}

function notifyPendingOperation(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(PENDING_INTERACTIVE_TRAVEL_OPERATIONS_EVENT));
  }
}

export function enqueueInteractiveTravelCapture(travel: InteractiveTravelState): boolean {
  const operation = parseOperation({ kind: "capture", travelId: travel.travelId, travel });
  if (!operation || operation.kind !== "capture") {
    return false;
  }
  const persisted = updateOperations((current) => [
    ...current.filter((item) => item.travelId !== operation.travelId),
    operation,
  ]);
  if (persisted) {
    notifyPendingOperation();
  }
  return persisted;
}

export function enqueueInteractiveTravelCancel(travelId: string): boolean {
  if (!validTravelId(travelId)) {
    return false;
  }
  const persisted = updateOperations((current) => {
    if (current.some((item) => item.travelId === travelId && item.kind === "capture")) {
      return current;
    }
    return [
      ...current.filter((item) => item.travelId !== travelId),
      { kind: "cancel" as const, travelId },
    ];
  });
  if (persisted) {
    notifyPendingOperation();
  }
  return persisted;
}

function operationSignature(operation: PendingInteractiveTravelOperation): string {
  return JSON.stringify(operation);
}

function clearPendingInteractiveTravelOperation(
  expected: PendingInteractiveTravelOperation,
): boolean {
  const signature = operationSignature(expected);
  return updateOperations((current) =>
    current.filter((operation) => operationSignature(operation) !== signature),
  );
}

export function clearPendingInteractiveTravelOperationsForTests(): void {
  activeFlush = null;
  try {
    storage()?.removeItem(PENDING_INTERACTIVE_TRAVEL_OPERATIONS_STORAGE_KEY);
  } catch {}
}

export function isTerminalInteractiveTravelOperationError(error: unknown): boolean {
  if (!(error instanceof ApiError)) {
    return false;
  }
  return error.status === 422 || [
    "INTERACTIVE_TRAVEL_OWNER_UNKNOWN",
    "INTERACTIVE_TRAVEL_OWNER_MISMATCH",
    "INTERACTIVE_TRAVEL_CANCELLED",
    "INTERACTIVE_TRAVEL_NOT_AVAILABLE",
    "INTERACTIVE_TRAVEL_NOT_COMPLETED",
    "INTERACTIVE_TRAVEL_STATE_CONFLICT",
    "INTERACTIVE_TRAVEL_ALREADY_COMPLETED",
  ].includes(error.code ?? "");
}

export function flushPendingInteractiveTravelOperations(): Promise<void> {
  if (activeFlush) {
    return activeFlush;
  }
  activeFlush = (async () => {
    const attempted = new Set<string>();
    let firstError: unknown;
    while (true) {
      const operation = readPendingInteractiveTravelOperations().find(
        (item) => !attempted.has(operationSignature(item)),
      );
      if (!operation) {
        break;
      }
      attempted.add(operationSignature(operation));
      try {
        if (operation.kind === "capture") {
          await captureInteractiveTravelFinale(operation.travel);
        } else {
          await cancelInteractiveTravel(operation.travelId);
        }
      } catch (error) {
        firstError ??= error;
        if (isTerminalInteractiveTravelOperationError(error)) {
          clearPendingInteractiveTravelOperation(operation);
        }
        continue;
      }
      clearPendingInteractiveTravelOperation(operation);
    }
    if (firstError !== undefined) {
      throw firstError;
    }
  })().finally(() => {
    activeFlush = null;
  });
  return activeFlush;
}
