import type {
  LocalPetState,
  PetLifeStage,
  PetMood,
  PetStatKey,
  PetStatZeroSinceMap,
  PetStatsPatch,
  PetStatTickMap,
} from "./types";

const MIN_STAT = 0;
const MAX_STAT = 100;
const STAT_FULL_DECAY_HOURS = 24;
const STAT_DECAY_PER_HOUR = MAX_STAT / STAT_FULL_DECAY_HOURS;
export const PET_DEATH_AFTER_ZERO_MS = 24 * 3_600_000;
export const PET_TAPS_PER_HAPPINESS_REWARD = 5;
export const PET_TAP_HAPPINESS_REWARD = 15;
export const PET_STAT_KEYS = [
  "hunger",
  "happiness",
  "energy",
] as const satisfies readonly PetStatKey[];
const STAT_DECAY_PHASE_MS = {
  hunger: 0,
  happiness: 2 * 3_600_000,
  energy: 4 * 3_600_000,
} satisfies Record<PetStatKey, number>;

type PetStats = LocalPetState["stats"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isIsoDate(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

export function clampStat(value: unknown): number {
  const numericValue = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return Math.max(MIN_STAT, Math.min(MAX_STAT, numericValue));
}

export function normalizeStats(value: unknown): PetStats {
  const stats = isRecord(value) ? value : {};
  return {
    hunger: clampStat(stats.hunger ?? 80),
    happiness: clampStat(stats.happiness ?? 80),
    energy: clampStat(stats.energy ?? 80),
  };
}

export function staggeredStatTicks(now = new Date()): PetStatTickMap {
  return {
    hunger: new Date(now.getTime() + STAT_DECAY_PHASE_MS.hunger).toISOString(),
    happiness: new Date(now.getTime() + STAT_DECAY_PHASE_MS.happiness).toISOString(),
    energy: new Date(now.getTime() + STAT_DECAY_PHASE_MS.energy).toISOString(),
  };
}

export function normalizeStatTickMap(value: unknown, fallbackIso: string): PetStatTickMap {
  const ticks = isRecord(value) ? value : {};
  return PET_STAT_KEYS.reduce<PetStatTickMap>(
    (result, key) => {
      const tick = ticks[key];
      result[key] = isIsoDate(tick) ? tick : fallbackIso;
      return result;
    },
    {
      hunger: fallbackIso,
      happiness: fallbackIso,
      energy: fallbackIso,
    },
  );
}

export function normalizeZeroStatSinceMap(
  value: unknown,
  stats: PetStats,
  fallbackTicks: PetStatTickMap,
): PetStatZeroSinceMap {
  const zeroSince = isRecord(value) ? value : {};
  const result: PetStatZeroSinceMap = {};

  for (const key of PET_STAT_KEYS) {
    if (stats[key] > MIN_STAT) {
      continue;
    }
    result[key] = isIsoDate(zeroSince[key]) ? zeroSince[key] : fallbackTicks[key];
  }

  return result;
}

function deathAtFromZeroStats(
  zeroStatSinceAt: PetStatZeroSinceMap,
  now: Date,
): string | undefined {
  const thresholdTimes = PET_STAT_KEYS
    .map((key) => Date.parse(zeroStatSinceAt[key] ?? ""))
    .filter((value) => !Number.isNaN(value))
    .map((value) => value + PET_DEATH_AFTER_ZERO_MS)
    .filter((value) => now.getTime() > value);

  return thresholdTimes.length
    ? new Date(Math.min(...thresholdTimes)).toISOString()
    : undefined;
}

export function legacyStatsTick(ticks: PetStatTickMap): string {
  const timestamps = PET_STAT_KEYS.map((key) => Date.parse(ticks[key])).filter(
    (value) => !Number.isNaN(value),
  );
  return timestamps.length
    ? new Date(Math.min(...timestamps)).toISOString()
    : new Date().toISOString();
}

export function calculatePetStage(createdAt: string, now = new Date()): PetLifeStage {
  const createdTime = Date.parse(createdAt);
  if (Number.isNaN(createdTime)) {
    return "baby";
  }

  const ageDays = Math.max(0, (now.getTime() - createdTime) / 86_400_000);
  if (ageDays < 2) {
    return "baby";
  }
  if (ageDays < 7) {
    return "teen";
  }
  return "adult";
}

export function calculatePetMood(stats: PetStats): PetMood {
  if (stats.hunger < 30) {
    return "hungry";
  }
  if (stats.happiness < 30) {
    return "sad";
  }
  if (stats.happiness > 75 && stats.hunger > 60) {
    return "happy";
  }
  return "idle";
}

export function applyStatsPatch(
  state: LocalPetState,
  patch: PetStatsPatch | undefined,
): LocalPetState {
  if (!patch || !isRecord(patch.stats)) {
    return state;
  }

  const now = new Date();
  const nowIso = now.toISOString();
  const stats = { ...state.stats };
  const lastStatTickAt = normalizeStatTickMap(state.lastStatTickAt, state.lastStatsTickAt);
  const nextTicks = { ...lastStatTickAt };
  const zeroStatSinceAt = normalizeZeroStatSinceMap(
    state.zeroStatSinceAt,
    state.stats,
    lastStatTickAt,
  );
  let changed = false;

  for (const key of PET_STAT_KEYS) {
    const value = patch.stats[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      const patchedTick = patch.lastStatTickAt?.[key];
      stats[key] = clampStat(value);
      nextTicks[key] = isIsoDate(patchedTick) ? patchedTick : nowIso;
      if (stats[key] > MIN_STAT) {
        delete zeroStatSinceAt[key];
      } else if (state.stats[key] > MIN_STAT || !zeroStatSinceAt[key]) {
        zeroStatSinceAt[key] = nextTicks[key];
      }
      changed = true;
    }
  }

  if (!changed) {
    return state;
  }

  return {
    ...state,
    updatedAt: nowIso,
    lastStatsTickAt: legacyStatsTick(nextTicks),
    lastStatTickAt: nextTicks,
    zeroStatSinceAt,
    diedAt: state.diedAt ?? deathAtFromZeroStats(zeroStatSinceAt, now),
    stage: calculatePetStage(state.createdAt, now),
    mood: calculatePetMood(stats),
    stats,
  };
}

export function applyOfflineProgress(state: LocalPetState, now = new Date()): LocalPetState {
  if (state.diedAt) {
    return state;
  }

  const nowIso = now.toISOString();
  const lastStatTickAt = normalizeStatTickMap(state.lastStatTickAt, state.lastStatsTickAt);
  const nextTicks = { ...lastStatTickAt };
  const stats = { ...state.stats };
  const zeroStatSinceAt = normalizeZeroStatSinceMap(
    state.zeroStatSinceAt,
    state.stats,
    lastStatTickAt,
  );
  let changed = false;

  for (const key of PET_STAT_KEYS) {
    const lastTick = Date.parse(lastStatTickAt[key]);
    if (Number.isNaN(lastTick)) {
      nextTicks[key] = nowIso;
      changed = true;
      continue;
    }
    const elapsedHours = Math.max(0, (now.getTime() - lastTick) / 3_600_000);
    if (elapsedHours <= 0) {
      continue;
    }
    const previousValue = stats[key];
    stats[key] = clampStat(previousValue - elapsedHours * STAT_DECAY_PER_HOUR);
    if (stats[key] > MIN_STAT) {
      delete zeroStatSinceAt[key];
    } else if (previousValue > MIN_STAT) {
      const zeroTime = lastTick + (previousValue / STAT_DECAY_PER_HOUR) * 3_600_000;
      zeroStatSinceAt[key] = new Date(zeroTime).toISOString();
    } else if (!zeroStatSinceAt[key]) {
      zeroStatSinceAt[key] = lastStatTickAt[key];
    }
    nextTicks[key] = nowIso;
    changed = true;
  }

  const diedAt = deathAtFromZeroStats(zeroStatSinceAt, now);
  return {
    ...state,
    updatedAt: diedAt ? nowIso : state.updatedAt,
    lastStatsTickAt: legacyStatsTick(nextTicks),
    lastStatTickAt: nextTicks,
    zeroStatSinceAt,
    diedAt,
    stage: calculatePetStage(state.createdAt, now),
    mood: calculatePetMood(stats),
    stats: changed ? stats : state.stats,
  };
}

export function withPetInteraction(
  state: LocalPetState,
  updateStats: (stats: PetStats) => PetStats,
): LocalPetState {
  if (state.diedAt) {
    return state;
  }

  const now = new Date();
  const nowIso = now.toISOString();
  const stats = normalizeStats(updateStats(state.stats));
  const lastStatTickAt = normalizeStatTickMap(state.lastStatTickAt, state.lastStatsTickAt);
  const nextTicks = { ...lastStatTickAt };
  const zeroStatSinceAt = normalizeZeroStatSinceMap(
    state.zeroStatSinceAt,
    state.stats,
    lastStatTickAt,
  );
  for (const key of PET_STAT_KEYS) {
    if (stats[key] !== state.stats[key]) {
      nextTicks[key] = nowIso;
    }
    if (stats[key] > MIN_STAT) {
      delete zeroStatSinceAt[key];
    } else if (state.stats[key] > MIN_STAT || !zeroStatSinceAt[key]) {
      zeroStatSinceAt[key] = nowIso;
    }
  }
  return {
    ...state,
    updatedAt: nowIso,
    lastInteractionAt: nowIso,
    lastStatsTickAt: legacyStatsTick(nextTicks),
    lastStatTickAt: nextTicks,
    zeroStatSinceAt,
    diedAt: deathAtFromZeroStats(zeroStatSinceAt, now),
    stage: calculatePetStage(state.createdAt, now),
    mood: calculatePetMood(stats),
    stats,
  };
}

export function applyPetTap(state: LocalPetState): {
  state: LocalPetState;
  rewarded: boolean;
} {
  const currentProgress = Math.max(
    0,
    Math.min(PET_TAPS_PER_HAPPINESS_REWARD - 1, Math.floor(state.petTapProgress ?? 0)),
  );
  const nextProgress = (currentProgress + 1) % PET_TAPS_PER_HAPPINESS_REWARD;
  const rewarded = nextProgress === 0;
  const interactedState = withPetInteraction(state, (stats) => (
    rewarded
      ? { ...stats, happiness: stats.happiness + PET_TAP_HAPPINESS_REWARD }
      : stats
  ));

  return {
    state: { ...interactedState, petTapProgress: nextProgress },
    rewarded,
  };
}
