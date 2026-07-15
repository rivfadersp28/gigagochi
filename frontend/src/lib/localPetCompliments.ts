const COMPLIMENT_LEDGER_STORAGE_KEY = "tamagochi:v1:compliment-ledger";
const MAX_COMPLIMENT_KEYS_PER_PET = 500;
const MAX_COMPLIMENT_KEY_LENGTH = 120;
const MAX_PETS = 32;
const MAX_PET_ID_LENGTH = 120;

type ComplimentLedger = {
  version: 1;
  pets: Record<string, string[]>;
};

let sessionLedger: ComplimentLedger | null = null;
let pendingWrite = false;

function emptyLedger(): ComplimentLedger {
  return { version: 1, pets: Object.create(null) as Record<string, string[]> };
}

function storage() {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function cleanKey(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const clean = value.trim().replace(/\s+/g, " ").slice(0, MAX_COMPLIMENT_KEY_LENGTH);
  return clean || null;
}

function cleanPetId(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const clean = value.trim().slice(0, MAX_PET_ID_LENGTH);
  return clean || null;
}

function normalizeLedger(value: unknown): ComplimentLedger {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return emptyLedger();
  }
  const petsValue = (value as { pets?: unknown }).pets;
  if (!petsValue || typeof petsValue !== "object" || Array.isArray(petsValue)) {
    return emptyLedger();
  }

  const pets = Object.create(null) as Record<string, string[]>;
  for (const [rawPetId, keys] of Object.entries(petsValue).slice(-MAX_PETS)) {
    const petId = cleanPetId(rawPetId);
    if (!petId || !Array.isArray(keys)) {
      continue;
    }
    const seen = new Set<string>();
    pets[petId] = keys
      .slice(-MAX_COMPLIMENT_KEYS_PER_PET)
      .flatMap((key) => cleanKey(key) ?? [])
      .filter((key) => {
        const normalized = key.toLocaleLowerCase("ru");
        if (seen.has(normalized)) {
          return false;
        }
        seen.add(normalized);
        return true;
      })
      .slice(-MAX_COMPLIMENT_KEYS_PER_PET);
  }
  return { version: 1, pets };
}

function flushPendingWrite(store: Storage | null) {
  if (!pendingWrite || !sessionLedger || !store) {
    return;
  }
  try {
    store.setItem(COMPLIMENT_LEDGER_STORAGE_KEY, JSON.stringify(sessionLedger));
    pendingWrite = false;
  } catch {
    // The bounded session ledger stays authoritative until persistence recovers.
  }
}

function readLedger(): ComplimentLedger {
  const store = storage();
  flushPendingWrite(store);
  if (pendingWrite || !store) {
    return sessionLedger ?? emptyLedger();
  }
  try {
    const raw = store.getItem(COMPLIMENT_LEDGER_STORAGE_KEY);
    sessionLedger = raw ? normalizeLedger(JSON.parse(raw)) : emptyLedger();
    return sessionLedger;
  } catch {
    return sessionLedger ?? emptyLedger();
  }
}

export function readComplimentHistory(petId: string): string[] {
  const normalizedPetId = cleanPetId(petId);
  if (!normalizedPetId) {
    return [];
  }
  const keys = readLedger().pets[normalizedPetId];
  return Array.isArray(keys) ? [...keys] : [];
}

export function rememberCompliment(petId: string, complimentKey: string): string[] {
  const normalizedPetId = cleanPetId(petId);
  const key = cleanKey(complimentKey);
  if (!normalizedPetId || !key) {
    return normalizedPetId ? readComplimentHistory(normalizedPetId) : [];
  }

  const ledger = readLedger();
  const storedPrevious = ledger.pets[normalizedPetId];
  const previous = Array.isArray(storedPrevious) ? storedPrevious : [];
  const normalizedKey = key.toLocaleLowerCase("ru");
  const keys = previous.some((item) => item.toLocaleLowerCase("ru") === normalizedKey)
    ? previous
    : [...previous, key].slice(-MAX_COMPLIMENT_KEYS_PER_PET);
  const petEntries = Object.entries(ledger.pets)
    .filter(([storedPetId]) => storedPetId !== normalizedPetId)
    .concat([[normalizedPetId, keys] as [string, string[]]])
    .slice(-MAX_PETS);
  const nextLedger: ComplimentLedger = {
    version: 1,
    pets: Object.fromEntries(petEntries),
  };
  sessionLedger = nextLedger;
  pendingWrite = true;
  flushPendingWrite(storage());
  return [...keys];
}

export function clearComplimentHistory(petId: string) {
  const normalizedPetId = cleanPetId(petId);
  if (!normalizedPetId) {
    return;
  }
  const ledger = readLedger();
  const pets = { ...ledger.pets };
  delete pets[normalizedPetId];
  sessionLedger = { version: 1, pets };
  pendingWrite = true;
  flushPendingWrite(storage());
}
