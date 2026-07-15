const UNSAFE_OBJECT_KEYS = new Set(["__proto__", "constructor", "prototype"]);

export type SafeJsonCloneOptions = {
  maxDepth?: number;
  maxNodes?: number;
  maxStringLength?: number;
  maxArrayLength?: number;
  maxObjectKeys?: number;
};

const DEFAULT_OPTIONS: Required<SafeJsonCloneOptions> = {
  maxDepth: 12,
  maxNodes: 1_500,
  maxStringLength: 2_000,
  maxArrayLength: 80,
  maxObjectKeys: 100,
};

function normalizedOptions(
  options: SafeJsonCloneOptions,
): Required<SafeJsonCloneOptions> {
  return {
    maxDepth: Math.max(0, Math.floor(options.maxDepth ?? DEFAULT_OPTIONS.maxDepth)),
    maxNodes: Math.max(0, Math.floor(options.maxNodes ?? DEFAULT_OPTIONS.maxNodes)),
    maxStringLength: Math.max(
      0,
      Math.floor(options.maxStringLength ?? DEFAULT_OPTIONS.maxStringLength),
    ),
    maxArrayLength: Math.max(
      0,
      Math.floor(options.maxArrayLength ?? DEFAULT_OPTIONS.maxArrayLength),
    ),
    maxObjectKeys: Math.max(
      0,
      Math.floor(options.maxObjectKeys ?? DEFAULT_OPTIONS.maxObjectKeys),
    ),
  };
}

export function safeJsonClone(
  value: unknown,
  options: SafeJsonCloneOptions = {},
): unknown {
  const limits = normalizedOptions(options);
  const seen = new Set<object>();
  const budget = { remaining: limits.maxNodes };

  const clone = (input: unknown, depth: number): unknown => {
    if (budget.remaining <= 0) {
      return undefined;
    }
    budget.remaining -= 1;

    if (input === null || typeof input === "boolean") {
      return input;
    }
    if (typeof input === "string") {
      return input.length <= limits.maxStringLength ? input : undefined;
    }
    if (typeof input === "number") {
      return Number.isFinite(input) ? input : undefined;
    }
    if (!input || typeof input !== "object" || depth >= limits.maxDepth) {
      return undefined;
    }
    if (seen.has(input)) {
      return undefined;
    }

    seen.add(input);
    try {
      if (Array.isArray(input)) {
        const result: unknown[] = [];
        for (const item of input.slice(0, limits.maxArrayLength)) {
          const cloned = clone(item, depth + 1);
          if (cloned !== undefined) {
            result.push(cloned);
          }
        }
        return result.length > 0 || input.length === 0 ? result : undefined;
      }

      const result: Record<string, unknown> = Object.create(null) as Record<
        string,
        unknown
      >;
      let acceptedKeys = 0;
      for (const [key, item] of Object.entries(input)) {
        if (UNSAFE_OBJECT_KEYS.has(key)) {
          continue;
        }
        if (acceptedKeys >= limits.maxObjectKeys) {
          break;
        }
        const cloned = clone(item, depth + 1);
        if (cloned !== undefined) {
          result[key] = cloned;
          acceptedKeys += 1;
        }
      }
      return acceptedKeys > 0 || Object.keys(input).length === 0 ? result : undefined;
    } catch {
      return undefined;
    } finally {
      seen.delete(input);
    }
  };

  return clone(value, 0);
}

export function isSafeJsonRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
