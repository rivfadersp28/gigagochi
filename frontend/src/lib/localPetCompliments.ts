const COMPLIMENT_LEDGER_STORAGE_KEY = "tamagochi:v1:compliment-ledger";
const MAX_COMPLIMENT_KEYS_PER_PET = 500;
const MAX_COMPLIMENT_KEY_LENGTH = 120;

type ComplimentLedger = {
  version: 1;
  pets: Record<string, string[]>;
};

function storage() {
  return typeof window === "undefined" ? null : window.localStorage;
}

function cleanKey(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const clean = value.trim().replace(/\s+/g, " ").slice(0, MAX_COMPLIMENT_KEY_LENGTH);
  return clean || null;
}

function normalizeLedger(value: unknown): ComplimentLedger {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { version: 1, pets: {} };
  }
  const petsValue = (value as { pets?: unknown }).pets;
  if (!petsValue || typeof petsValue !== "object" || Array.isArray(petsValue)) {
    return { version: 1, pets: {} };
  }

  const pets: Record<string, string[]> = {};
  for (const [petId, keys] of Object.entries(petsValue)) {
    if (!petId || !Array.isArray(keys)) {
      continue;
    }
    const seen = new Set<string>();
    pets[petId] = keys
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

function readLedger(): ComplimentLedger {
  const store = storage();
  if (!store) {
    return { version: 1, pets: {} };
  }
  try {
    const raw = store.getItem(COMPLIMENT_LEDGER_STORAGE_KEY);
    return raw ? normalizeLedger(JSON.parse(raw)) : { version: 1, pets: {} };
  } catch {
    return { version: 1, pets: {} };
  }
}

export function readComplimentHistory(petId: string): string[] {
  return [...(readLedger().pets[petId] ?? [])];
}

export function rememberCompliment(petId: string, complimentKey: string): string[] {
  const key = cleanKey(complimentKey);
  if (!petId || !key) {
    return readComplimentHistory(petId);
  }

  const ledger = readLedger();
  const previous = ledger.pets[petId] ?? [];
  const normalizedKey = key.toLocaleLowerCase("ru");
  const keys = previous.some((item) => item.toLocaleLowerCase("ru") === normalizedKey)
    ? previous
    : [...previous, key].slice(-MAX_COMPLIMENT_KEYS_PER_PET);
  const nextLedger: ComplimentLedger = {
    version: 1,
    pets: { ...ledger.pets, [petId]: keys },
  };
  storage()?.setItem(COMPLIMENT_LEDGER_STORAGE_KEY, JSON.stringify(nextLedger));
  return [...keys];
}
