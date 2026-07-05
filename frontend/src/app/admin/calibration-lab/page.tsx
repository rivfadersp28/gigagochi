"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Check,
  Download,
  LoaderCircle,
  RefreshCw,
  Save,
  SkipForward,
  Sparkles,
  X,
} from "lucide-react";

import {
  createCalibrationRun,
  fetchCalibrationExport,
  getCalibrationLabStatus,
  getNextCalibrationTask,
  saveCalibrationVote,
} from "@/lib/adminCalibrationLabApi";
import { AdminGenerationLabApiError } from "@/lib/adminGenerationLabApi";
import type {
  CalibrationCandidate,
  CalibrationLabStatus,
  CalibrationPromptVariant,
  CalibrationTask,
  CalibrationTaskType,
  CalibrationVoteOutcome,
} from "@/lib/types";

type Notice = { tone: "neutral" | "success" | "error"; text: string } | null;

const TASK_TYPES: CalibrationTaskType[] = [
  "full_character_pairwise",
  "lore_pairwise",
  "dialogue_pairwise",
];

const PROMPT_VARIANTS: CalibrationPromptVariant[] = [
  "current",
  "tiny_story_cards",
  "game_dialogue_cards",
  "mixed_cards",
];

const TASK_TYPE_LABELS: Record<CalibrationTaskType, string> = {
  full_character_pairwise: "Full character",
  lore_pairwise: "Lore",
  dialogue_pairwise: "Dialogue",
};

const PROMPT_VARIANT_LABELS: Record<CalibrationPromptVariant, string> = {
  current: "current",
  tiny_story_cards: "tiny story cards",
  game_dialogue_cards: "game dialogue cards",
  mixed_cards: "mixed cards",
};

const POSITIVE_TAGS = [
  "живее",
  "связнее",
  "конкретнее",
  "лучше голос",
  "лучше мир",
  "лучше продолжает тему",
  "лучше дружба/отношения",
  "лучше крючки на будущее",
  "хочется продолжить",
];

const NEGATIVE_TAGS = [
  "слишком абстрактно",
  "звучит как ИИ",
  "нет характера",
  "нет мира",
  "нет причины",
  "рандомные факты",
  "перегружено именами",
  "слишком эпично",
  "слишком сухо",
  "противоречит описанию",
  "плохой русский",
  "слишком длинно",
  "не отвечает на вопрос",
  "повторяется",
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nestedValue(value: unknown, key: string): unknown {
  return isRecord(value) ? value[key] : undefined;
}

function jsonText(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (Array.isArray(value)) {
    return value.map((item) => formatValue(item)).join(", ");
  }
  if (isRecord(value)) {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

function candidateLabel(index: number): string {
  return String.fromCharCode("A".charCodeAt(0) + index);
}

function normalizeCount(value: string): number {
  const parsed = Number.parseInt(value, 10);
  return Math.min(50, Math.max(1, Number.isFinite(parsed) ? parsed : 5));
}

function errorMessage(error: unknown): string {
  if (error instanceof AdminGenerationLabApiError) {
    return `${error.code ?? "REQUEST_FAILED"}: ${error.message}`;
  }
  if (error instanceof DOMException && error.name === "AbortError") {
    return "REQUEST_ABORTED";
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Unknown error";
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tag = target.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable;
}

function toggleListItem<T extends string>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter((value) => value !== item) : [...list, item];
}

function downloadText(filename: string, text: string): void {
  const blob = new Blob([text], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function FieldRow({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="grid grid-cols-[122px_minmax(0,1fr)] gap-2 border-t border-black/10 py-1.5 first:border-t-0">
      <dt className="text-[11px] font-semibold uppercase text-black/45">{label}</dt>
      <dd className="min-w-0 whitespace-pre-wrap break-words text-xs leading-relaxed text-black">
        {formatValue(value)}
      </dd>
    </div>
  );
}

function MiniButton({
  children,
  active = false,
  disabled = false,
  onClick,
}: {
  children: React.ReactNode;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`inline-flex h-8 items-center justify-center gap-1.5 rounded-md border px-2.5 text-xs font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:cursor-not-allowed disabled:opacity-45 ${
        active
          ? "border-black bg-black text-white"
          : "border-black/15 bg-white text-black hover:bg-black/[0.04]"
      }`}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function TagToggle({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`rounded-md border px-2 py-1 text-left text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 ${
        active
          ? "border-emerald-700 bg-emerald-50 text-emerald-900"
          : "border-black/12 bg-white text-black/70 hover:bg-black/[0.035]"
      }`}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

function CharacterFields({ characterBible }: { characterBible: Record<string, unknown> }) {
  const lore = isRecord(characterBible.lore) ? characterBible.lore : {};
  const signature = characterBible.signature;
  const innerLife = nestedValue(lore, "inner_life");
  const relationships = nestedValue(lore, "relationships");
  return (
    <dl>
      <FieldRow label="species" value={characterBible.species} />
      <FieldRow label="signature" value={nestedValue(signature, "core_gimmick") ?? signature} />
      <FieldRow label="personality" value={characterBible.personality} />
      <FieldRow label="world" value={nestedValue(nestedValue(lore, "world"), "story")} />
      <FieldRow label="home" value={nestedValue(nestedValue(lore, "home"), "story")} />
      <FieldRow label="origin" value={nestedValue(nestedValue(lore, "origin"), "story")} />
      <FieldRow label="relations" value={nestedValue(relationships, "story")} />
      <FieldRow label="want" value={nestedValue(innerLife, "core_want")} />
      <FieldRow label="conflict" value={nestedValue(innerLife, "inner_conflict")} />
      <FieldRow label="friends" value={nestedValue(relationships, "friends")} />
      <FieldRow label="story seeds" value={nestedValue(lore, "story_seeds")} />
    </dl>
  );
}

function CandidateCard({
  candidate,
  index,
  selected,
  onSelect,
}: {
  candidate: CalibrationCandidate;
  index: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const bible = isRecord(candidate.characterBible) ? candidate.characterBible : null;

  return (
    <article
      className={`min-w-0 rounded-md border bg-white ${
        selected ? "border-emerald-700 ring-2 ring-emerald-700/18" : "border-black/14"
      }`}
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-black/10 px-3 py-2.5">
        <h2 className="text-sm font-semibold text-black">Candidate {candidateLabel(index)}</h2>
        <span className="rounded-md border border-black/12 bg-black/[0.03] px-1.5 py-0.5 font-mono text-[11px] text-black/65">
          {PROMPT_VARIANT_LABELS[candidate.promptVariant]}
        </span>
        <span className="rounded-md border border-black/12 bg-white px-1.5 py-0.5 font-mono text-[11px] text-black/65">
          score {candidate.autoScore}
        </span>
        <button
          type="button"
          className={`ml-auto inline-flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-xs font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 ${
            selected
              ? "border-emerald-700 bg-emerald-700 text-white"
              : "border-black bg-black text-white hover:bg-black/85"
          }`}
          onClick={onSelect}
        >
          <Check className="size-3.5" aria-hidden="true" />
          Выбрать
        </button>
      </header>

      <div className="px-3 py-2.5">
        {candidate.qualityFlags.length ? (
          <div className="mb-2 flex flex-wrap gap-1">
            {candidate.qualityFlags.map((flag) => (
              <span
                key={flag}
                className="rounded-md border border-amber-700/25 bg-amber-50 px-1.5 py-0.5 font-mono text-[10px] text-amber-900"
              >
                {flag}
              </span>
            ))}
          </div>
        ) : null}

        {bible ? <CharacterFields characterBible={bible} /> : null}

        {candidate.turns.length ? (
          <section className="mt-3 border-t border-black/10 pt-2">
            <h3 className="mb-1 text-[11px] font-semibold uppercase text-black/45">benchmark</h3>
            <div>
              {candidate.turns.map((turn, turnIndex) => (
                <div
                  key={`${turn.question}-${turnIndex}`}
                  className="border-t border-black/10 py-1.5 first:border-t-0"
                >
                  <p className="text-[11px] font-semibold text-black/55">{turn.question}</p>
                  <p className="mt-0.5 whitespace-pre-wrap break-words text-xs leading-relaxed text-black">
                    {turn.reply}
                  </p>
                  <div className="mt-1 flex flex-wrap gap-1 font-mono text-[10px] text-black/45">
                    <span>q {turn.qualityScore ?? "—"}</span>
                    {turn.qualityFlags.map((flag) => (
                      <span key={flag}>{flag}</span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        <details className="mt-3 border-t border-black/10 pt-2">
          <summary className="cursor-pointer text-xs font-semibold text-black/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700">
            raw JSON
          </summary>
          <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border border-black/10 bg-black/[0.025] p-2 font-mono text-[11px] leading-relaxed text-black">
            {jsonText(candidate)}
          </pre>
        </details>

        {Object.keys(candidate.debug ?? {}).length ? (
          <details className="mt-2 border-t border-black/10 pt-2">
            <summary className="cursor-pointer text-xs font-semibold text-black/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700">
              debug prompts
            </summary>
            <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border border-black/10 bg-black/[0.025] p-2 font-mono text-[11px] leading-relaxed text-black">
              {jsonText(candidate.debug)}
            </pre>
          </details>
        ) : null}
      </div>
    </article>
  );
}

function QueueHeader({
  currentTask,
  status,
  activeRunId,
}: {
  currentTask: CalibrationTask | null;
  status: CalibrationLabStatus | null;
  activeRunId: string | null;
}) {
  return (
    <section className="rounded-md border border-black/14 bg-white px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold text-black">Review queue</h2>
        <span className="rounded-md border border-black/12 bg-black/[0.03] px-1.5 py-0.5 font-mono text-[11px] text-black/60">
          tasks {status?.taskCount ?? "—"}
        </span>
        <span className="rounded-md border border-black/12 bg-black/[0.03] px-1.5 py-0.5 font-mono text-[11px] text-black/60">
          votes {status?.voteCount ?? "—"}
        </span>
        {activeRunId ? (
          <span className="truncate rounded-md border border-black/12 bg-black/[0.03] px-1.5 py-0.5 font-mono text-[11px] text-black/60">
            {activeRunId}
          </span>
        ) : null}
      </div>
      {currentTask ? (
        <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-[170px_minmax(0,1fr)]">
          <dt className="font-semibold uppercase text-black/45">task</dt>
          <dd className="min-w-0 break-words font-mono text-black/70">{currentTask.taskId}</dd>
          <dt className="font-semibold uppercase text-black/45">type</dt>
          <dd className="text-black/70">{TASK_TYPE_LABELS[currentTask.taskType]}</dd>
          <dt className="font-semibold uppercase text-black/45">source</dt>
          <dd className="min-w-0 break-words text-black">{currentTask.description}</dd>
        </dl>
      ) : (
        <p className="mt-2 text-sm text-black/55">Нет активного задания.</p>
      )}
    </section>
  );
}

export default function AdminCalibrationLabPage() {
  const [taskType, setTaskType] = useState<CalibrationTaskType>("full_character_pairwise");
  const [countInput, setCountInput] = useState("1");
  const [candidatesPerTask, setCandidatesPerTask] = useState<2 | 3>(2);
  const [descriptions, setDescriptions] = useState("маленький дракон с мягкими крыльями");
  const [promptVariants, setPromptVariants] = useState<CalibrationPromptVariant[]>([
    "current",
    "mixed_cards",
  ]);
  const [includeDebug, setIncludeDebug] = useState(true);
  const [autoFilterBadCandidates, setAutoFilterBadCandidates] = useState(true);
  const [status, setStatus] = useState<CalibrationLabStatus | null>(null);
  const [currentTask, setCurrentTask] = useState<CalibrationTask | null>(null);
  const [history, setHistory] = useState<CalibrationTask[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<CalibrationVoteOutcome | null>(null);
  const [winnerCandidateId, setWinnerCandidateId] = useState<string | null>(null);
  const [positiveTags, setPositiveTags] = useState<string[]>([]);
  const [negativeTags, setNegativeTags] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isLoadingNext, setIsLoadingNext] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const taskStartedAtRef = useRef(0);

  const selectedCandidateIndex = useMemo(
    () => currentTask?.candidates.findIndex((item) => item.candidateId === winnerCandidateId) ?? -1,
    [currentTask, winnerCandidateId],
  );

  const resetDecision = useCallback(() => {
    setOutcome(null);
    setWinnerCandidateId(null);
    setPositiveTags([]);
    setNegativeTags([]);
    setNote("");
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const nextStatus = await getCalibrationLabStatus();
      setStatus(nextStatus);
    } catch (error: unknown) {
      setNotice({ tone: "error", text: errorMessage(error) });
    }
  }, []);

  const setTask = useCallback(
    (task: CalibrationTask | null) => {
      setCurrentTask(task);
      taskStartedAtRef.current = Date.now();
      resetDecision();
    },
    [resetDecision],
  );

  const loadNext = useCallback(
    async (options: { keepRun?: boolean; pushCurrent?: boolean } = {}) => {
      setIsLoadingNext(true);
      setNotice(null);
      try {
        const nextTask = await getNextCalibrationTask({
          taskType,
          runId: options.keepRun ? activeRunId ?? undefined : undefined,
        });
        if (options.pushCurrent && currentTask) {
          setHistory((items) => [...items, currentTask]);
        }
        setTask(nextTask);
        if (!nextTask) {
          setNotice({ tone: "neutral", text: "Нет неразмеченных заданий для текущего фильтра." });
        }
        await refreshStatus();
      } catch (error: unknown) {
        setNotice({ tone: "error", text: errorMessage(error) });
      } finally {
        setIsLoadingNext(false);
      }
    },
    [activeRunId, currentTask, refreshStatus, setTask, taskType],
  );

  const waitForGeneratedTask = useCallback(
    async (runId: string): Promise<CalibrationTask | null> => {
      for (let attempt = 0; attempt < 60; attempt += 1) {
        const nextTask = await getNextCalibrationTask({ taskType, runId });
        if (nextTask) {
          return nextTask;
        }
        if (attempt % 3 === 0) {
          await refreshStatus();
        }
        await wait(2000);
      }
      return null;
    },
    [refreshStatus, taskType],
  );

  useEffect(() => {
    let ignore = false;
    getCalibrationLabStatus()
      .then((nextStatus) => {
        if (!ignore) {
          setStatus(nextStatus);
        }
      })
      .catch((error: unknown) => {
        if (!ignore) {
          setNotice({ tone: "error", text: errorMessage(error) });
        }
      });
    return () => {
      ignore = true;
    };
  }, []);

  const chooseCandidate = useCallback(
    (candidate: CalibrationCandidate) => {
      setOutcome("winner");
      setWinnerCandidateId(candidate.candidateId);
      setNotice(null);
    },
    [],
  );

  const chooseSystemOutcome = useCallback((nextOutcome: Exclude<CalibrationVoteOutcome, "winner">) => {
    setOutcome(nextOutcome);
    setWinnerCandidateId(null);
    setNotice(null);
  }, []);

  const generateBatch = useCallback(async () => {
    const inputLines = descriptions
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    if (!inputLines.length) {
      setNotice({ tone: "error", text: "Введите хотя бы одно описание." });
      return;
    }
    if (!promptVariants.length) {
      setNotice({ tone: "error", text: "Выберите хотя бы один prompt variant." });
      return;
    }

    const count = normalizeCount(countInput);
    setCountInput(String(count));
    setIsGenerating(true);
    setNotice(null);
    try {
      const run = await createCalibrationRun({
        taskType,
        descriptions: inputLines,
        count,
        candidatesPerTask,
        promptVariants,
        includeDebug,
        autoFilterBadCandidates,
      });
      setActiveRunId(run.runId);
      setHistory([]);
      setTask(null);
      setNotice({
        tone: "neutral",
        text: `Run ${run.runId} создан. Жду первую готовую task; генерация идет в фоне.`,
      });
      const firstTask = await waitForGeneratedTask(run.runId);
      setTask(firstTask);
      setNotice({
        tone: firstTask ? "success" : "neutral",
        text: firstTask
          ? `Создан run ${run.runId}: ${run.taskIds.length} tasks. Первая task готова.`
          : `Run ${run.runId} еще генерируется. Нажми Load next через минуту.`,
      });
      await refreshStatus();
    } catch (error: unknown) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setIsGenerating(false);
    }
  }, [
    autoFilterBadCandidates,
    candidatesPerTask,
    countInput,
    descriptions,
    includeDebug,
    promptVariants,
    refreshStatus,
    setTask,
    taskType,
    waitForGeneratedTask,
  ]);

  const saveVote = useCallback(async () => {
    if (!currentTask || !outcome) {
      setNotice({ tone: "error", text: "Сначала выберите исход оценки." });
      return;
    }
    if (outcome === "winner" && !winnerCandidateId) {
      setNotice({ tone: "error", text: "Для winner нужен выбранный candidate." });
      return;
    }
    setIsSaving(true);
    setNotice(null);
    try {
      await saveCalibrationVote({
        taskId: currentTask.taskId,
        winnerCandidateId: outcome === "winner" ? winnerCandidateId : null,
        outcome,
        positiveTags,
        negativeTags,
        note,
        latencyMs: Math.max(0, Date.now() - taskStartedAtRef.current),
      });
      const nextTask = await getNextCalibrationTask({
        taskType,
        runId: activeRunId ?? undefined,
      });
      setHistory((items) => [...items, currentTask]);
      setTask(nextTask);
      setNotice({
        tone: "success",
        text: nextTask ? "Vote сохранен." : "Vote сохранен. В этом run больше нет задач.",
      });
      await refreshStatus();
    } catch (error: unknown) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setIsSaving(false);
    }
  }, [
    activeRunId,
    currentTask,
    negativeTags,
    note,
    outcome,
    positiveTags,
    refreshStatus,
    setTask,
    taskType,
    winnerCandidateId,
  ]);

  const goBack = useCallback(() => {
    setHistory((items) => {
      const previous = items.at(-1);
      if (!previous) {
        return items;
      }
      setTask(previous);
      return items.slice(0, -1);
    });
  }, [setTask]);

  const exportData = useCallback(async (kind: "votes" | "winners") => {
    setIsExporting(true);
    setNotice(null);
    try {
      const text = await fetchCalibrationExport(kind, "jsonl");
      downloadText(`calibration_${kind}.jsonl`, text);
      setNotice({ tone: "success", text: `${kind} export готов.` });
    } catch (error: unknown) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setIsExporting(false);
    }
  }, []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (isEditableTarget(event.target) || isSaving || !currentTask) {
        return;
      }
      const key = event.key.toLowerCase();
      if (["1", "2", "3"].includes(key)) {
        const index = Number(key) - 1;
        const candidate = currentTask.candidates[index];
        if (candidate) {
          event.preventDefault();
          chooseCandidate(candidate);
        }
      } else if (key === "t") {
        event.preventDefault();
        chooseSystemOutcome("tie");
      } else if (key === "x") {
        event.preventDefault();
        chooseSystemOutcome("reject_all");
      } else if (key === "s") {
        event.preventDefault();
        chooseSystemOutcome("skip");
      } else if (event.key === "Enter" && outcome) {
        event.preventDefault();
        void saveVote();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [chooseCandidate, chooseSystemOutcome, currentTask, isSaving, outcome, saveVote]);

  return (
    <main className="min-h-dvh bg-[#f7f7f3] text-black">
      <div className="mx-auto max-w-[1800px] px-4 py-4">
        <header className="flex flex-wrap items-center gap-2 border-b border-black/14 pb-3">
          <h1 className="text-base font-semibold text-black">Calibration Lab</h1>
          <span className="rounded-md border border-black/14 bg-white px-2 py-1 font-mono text-xs text-black/65">
            {status ? `${status.storage}: ${status.taskCount}/${status.voteCount}` : "checking"}
          </span>
          <button
            type="button"
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-black/15 bg-white px-2 text-xs font-semibold text-black hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700"
            onClick={() => void refreshStatus()}
          >
            <RefreshCw className="size-3.5" aria-hidden="true" />
            Refresh
          </button>
          <div className="ml-auto flex flex-wrap gap-2">
            <button
              type="button"
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-black/15 bg-white px-2 text-xs font-semibold text-black hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:opacity-45"
              onClick={() => void exportData("votes")}
              disabled={isExporting}
            >
              <Download className="size-3.5" aria-hidden="true" />
              Export votes
            </button>
            <button
              type="button"
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-black/15 bg-white px-2 text-xs font-semibold text-black hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:opacity-45"
              onClick={() => void exportData("winners")}
              disabled={isExporting}
            >
              <Download className="size-3.5" aria-hidden="true" />
              Export winners
            </button>
          </div>
        </header>

        <section className="grid gap-3 border-b border-black/14 py-3 xl:grid-cols-[minmax(380px,620px)_minmax(0,1fr)]">
          <div>
            <label htmlFor="calibration-descriptions" className="mb-1.5 block text-xs font-semibold text-black/60">
              descriptions
            </label>
            <textarea
              id="calibration-descriptions"
              className="min-h-32 w-full resize-y rounded-md border border-black/18 bg-white p-2 text-sm leading-relaxed text-black focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700"
              value={descriptions}
              onChange={(event) => setDescriptions(event.target.value)}
              disabled={isGenerating}
            />
          </div>

          <div className="grid content-start gap-3">
            <div className="flex flex-wrap items-center gap-2">
              {TASK_TYPES.map((item) => (
                <MiniButton
                  key={item}
                  active={taskType === item}
                  disabled={isGenerating}
                  onClick={() => setTaskType(item)}
                >
                  {TASK_TYPE_LABELS[item]}
                </MiniButton>
              ))}
              <label className="ml-auto flex h-8 items-center gap-1.5 text-xs font-semibold text-black/65">
                count
                <input
                  type="number"
                  min={1}
                  max={50}
                  className="h-8 w-16 rounded-md border border-black/18 bg-white px-2 font-mono text-xs text-black tabular-nums focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700"
                  value={countInput}
                  onBlur={(event) => setCountInput(String(normalizeCount(event.target.value)))}
                  onChange={(event) => setCountInput(event.target.value)}
                  disabled={isGenerating}
                />
              </label>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold uppercase text-black/45">candidates</span>
              <MiniButton active={candidatesPerTask === 2} onClick={() => setCandidatesPerTask(2)}>
                2
              </MiniButton>
              <MiniButton active={candidatesPerTask === 3} onClick={() => setCandidatesPerTask(3)}>
                3
              </MiniButton>
              <label className="ml-auto inline-flex h-8 items-center gap-2 rounded-md border border-black/12 bg-white px-2 text-xs font-semibold text-black/70">
                <input
                  type="checkbox"
                  className="size-4 accent-emerald-700"
                  checked={includeDebug}
                  onChange={(event) => setIncludeDebug(event.target.checked)}
                  disabled={isGenerating}
                />
                include debug
              </label>
              <label className="inline-flex h-8 items-center gap-2 rounded-md border border-black/12 bg-white px-2 text-xs font-semibold text-black/70">
                <input
                  type="checkbox"
                  className="size-4 accent-emerald-700"
                  checked={autoFilterBadCandidates}
                  onChange={(event) => setAutoFilterBadCandidates(event.target.checked)}
                  disabled={isGenerating}
                />
                auto-filter bad
              </label>
            </div>

            <div className="flex flex-wrap gap-2">
              {PROMPT_VARIANTS.map((variant) => (
                <TagToggle
                  key={variant}
                  label={PROMPT_VARIANT_LABELS[variant]}
                  active={promptVariants.includes(variant)}
                  onClick={() => setPromptVariants((items) => toggleListItem(items, variant))}
                />
              ))}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="inline-flex h-9 items-center gap-1.5 rounded-md border border-black bg-black px-3 text-xs font-semibold text-white hover:bg-black/85 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:cursor-not-allowed disabled:opacity-45"
                onClick={() => void generateBatch()}
                disabled={isGenerating}
              >
                {isGenerating ? (
                  <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <Sparkles className="size-3.5" aria-hidden="true" />
                )}
                {isGenerating ? "Waiting for first task" : "Generate batch"}
              </button>
              <button
                type="button"
                className="inline-flex h-9 items-center gap-1.5 rounded-md border border-black/15 bg-white px-3 text-xs font-semibold text-black hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:opacity-45"
                onClick={() => void loadNext({ keepRun: false })}
                disabled={isLoadingNext}
              >
                <RefreshCw className="size-3.5" aria-hidden="true" />
                Load next
              </button>
              {notice ? (
                <p
                  className={`text-xs ${
                    notice.tone === "error"
                      ? "text-red-700"
                      : notice.tone === "success"
                        ? "text-emerald-800"
                        : "text-black/60"
                  }`}
                  role={notice.tone === "error" ? "alert" : "status"}
                >
                  {notice.text}
                </p>
              ) : null}
            </div>
          </div>
        </section>

        <div className="grid gap-3 py-3 xl:grid-cols-[minmax(0,1fr)_360px]">
          <section className="grid min-w-0 gap-3">
            <QueueHeader currentTask={currentTask} status={status} activeRunId={activeRunId} />
            {currentTask ? (
              <div
                className={`grid gap-3 ${
                  currentTask.candidates.length >= 3 ? "2xl:grid-cols-3" : "lg:grid-cols-2"
                }`}
              >
                {currentTask.candidates.map((candidate, index) => (
                  <CandidateCard
                    key={candidate.candidateId}
                    candidate={candidate}
                    index={index}
                    selected={winnerCandidateId === candidate.candidateId}
                    onSelect={() => chooseCandidate(candidate)}
                  />
                ))}
              </div>
            ) : (
              <div className="rounded-md border border-black/14 bg-white p-4 text-sm text-black/55">
                Нет задания для сравнения.
              </div>
            )}
          </section>

          <aside className="xl:sticky xl:top-3 xl:self-start">
            <section className="rounded-md border border-black/14 bg-white">
              <header className="border-b border-black/10 px-3 py-2.5">
                <h2 className="text-sm font-semibold text-black">Оценка</h2>
                {selectedCandidateIndex >= 0 ? (
                  <p className="mt-1 text-xs text-black/55">
                    Выбран Candidate {candidateLabel(selectedCandidateIndex)}
                  </p>
                ) : (
                  <p className="mt-1 text-xs text-black/55">Исход еще не выбран.</p>
                )}
              </header>

              <div className="grid gap-3 px-3 py-3">
                <div className="grid grid-cols-2 gap-2">
                  {currentTask?.candidates.map((candidate, index) => (
                    <MiniButton
                      key={candidate.candidateId}
                      active={winnerCandidateId === candidate.candidateId && outcome === "winner"}
                      disabled={!currentTask}
                      onClick={() => chooseCandidate(candidate)}
                    >
                      {candidateLabel(index)} лучше
                    </MiniButton>
                  ))}
                  <MiniButton active={outcome === "tie"} onClick={() => chooseSystemOutcome("tie")}>
                    Одинаково
                  </MiniButton>
                  <MiniButton
                    active={outcome === "reject_all"}
                    onClick={() => chooseSystemOutcome("reject_all")}
                  >
                    <X className="size-3.5" aria-hidden="true" />
                    Отклонить все
                  </MiniButton>
                  <MiniButton active={outcome === "skip"} onClick={() => chooseSystemOutcome("skip")}>
                    <SkipForward className="size-3.5" aria-hidden="true" />
                    Пропустить
                  </MiniButton>
                </div>

                <div>
                  <h3 className="mb-1.5 text-[11px] font-semibold uppercase text-black/45">
                    positive tags
                  </h3>
                  <div className="flex flex-wrap gap-1.5">
                    {POSITIVE_TAGS.map((tag) => (
                      <TagToggle
                        key={tag}
                        label={tag}
                        active={positiveTags.includes(tag)}
                        onClick={() => setPositiveTags((items) => toggleListItem(items, tag))}
                      />
                    ))}
                  </div>
                </div>

                <div>
                  <h3 className="mb-1.5 text-[11px] font-semibold uppercase text-black/45">
                    negative tags
                  </h3>
                  <div className="flex flex-wrap gap-1.5">
                    {NEGATIVE_TAGS.map((tag) => (
                      <TagToggle
                        key={tag}
                        label={tag}
                        active={negativeTags.includes(tag)}
                        onClick={() => setNegativeTags((items) => toggleListItem(items, tag))}
                      />
                    ))}
                  </div>
                </div>

                <label className="grid gap-1.5 text-xs font-semibold text-black/60">
                  note
                  <textarea
                    className="min-h-24 resize-y rounded-md border border-black/18 bg-white p-2 text-sm font-normal leading-relaxed text-black focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700"
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                  />
                </label>

                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md border border-black/15 bg-white px-3 text-xs font-semibold text-black hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:opacity-45"
                    onClick={goBack}
                    disabled={!history.length}
                  >
                    <ArrowLeft className="size-3.5" aria-hidden="true" />
                    Назад
                  </button>
                  <button
                    type="button"
                    className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md border border-emerald-800 bg-emerald-800 px-3 text-xs font-semibold text-white hover:bg-emerald-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:cursor-not-allowed disabled:opacity-45"
                    onClick={() => void saveVote()}
                    disabled={!currentTask || !outcome || isSaving}
                  >
                    {isSaving ? (
                      <LoaderCircle className="size-3.5 animate-spin" aria-hidden="true" />
                    ) : (
                      <Save className="size-3.5" aria-hidden="true" />
                    )}
                    Сохранить
                  </button>
                </div>
              </div>
            </section>
          </aside>
        </div>
      </div>
    </main>
  );
}
