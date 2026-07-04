"use client";

/* eslint-disable @next/next/no-img-element */
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Clipboard, ExternalLink, Play, RefreshCw, Square } from "lucide-react";

import {
  ADMIN_API_URL,
  AdminGenerationLabApiError,
  generateAdminCharacter,
  getAdminGenerationLabStatus,
} from "@/lib/adminGenerationLabApi";
import type {
  AdminGenerateDebug,
  AdminGenerateError,
  AdminGenerateMode,
  AdminGenerateOneResponse,
  PetLifeStage,
  PetMood,
} from "@/lib/types";

type AdminSlotState =
  | { status: "queued"; slotId: string; description: string }
  | { status: "generating"; slotId: string; description: string; startedAt: number }
  | { status: "ready"; slotId: string; result: AdminGenerateOneResponse }
  | { status: "failed"; slotId: string; description: string; error: AdminGenerateError | string };

type AdminQueuedSlotState = Extract<AdminSlotState, { status: "queued" }>;

type BackendStatus =
  | { state: "checking"; label: string }
  | { state: "ready"; label: string }
  | { state: "failed"; label: string; code?: string };

const STAGES: PetLifeStage[] = ["baby", "teen", "adult"];
const MOODS: PetMood[] = ["idle", "happy", "hungry", "sad"];
const MAX_PARALLEL_ADMIN_GENERATIONS = 1;

const MODE_LABELS: Record<AdminGenerateMode, string> = {
  profile_only: "profile only",
  full_assets: "full assets",
};

const STATUS_CLASS: Record<AdminSlotState["status"], string> = {
  queued: "border-black/20 bg-white text-black",
  generating: "border-blue-600/30 bg-blue-50 text-blue-800",
  ready: "border-emerald-700/25 bg-emerald-50 text-emerald-800",
  failed: "border-red-700/25 bg-red-50 text-red-800",
};

function normalizeCountInput(value: string): number {
  const parsed = Number.parseInt(value, 10);
  return Math.min(10, Math.max(1, Number.isFinite(parsed) ? parsed : 1));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

function jsonText(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function sectionValue(record: Record<string, unknown>, key: string): unknown {
  return record[key];
}

function nestedValue(value: unknown, key: string): unknown {
  return isRecord(value) ? value[key] : undefined;
}

function resultForSlot(slot: AdminSlotState): AdminGenerateOneResponse | null {
  return slot.status === "ready" ? slot.result : null;
}

function durationLabel(slot: AdminSlotState): string {
  if (slot.status === "ready") {
    return `${slot.result.durationMs} ms`;
  }
  if (slot.status === "generating") {
    return `started ${new Date(slot.startedAt).toLocaleTimeString()}`;
  }
  if (slot.status === "failed" && typeof slot.error !== "string") {
    return `${slot.error.durationMs} ms`;
  }
  return "—";
}

function errorText(error: AdminGenerateError | string): string {
  if (typeof error === "string") {
    return error;
  }
  return `${error.code}: ${error.message}`;
}

function promptText(debug: AdminGenerateDebug | null | undefined): string {
  if (!debug) {
    return "";
  }
  const chunks = [
    `chatModel: ${debug.chatModel}`,
    debug.imageModel ? `imageModel: ${debug.imageModel}` : null,
    debug.imageSize ? `imageSize: ${debug.imageSize}` : null,
    debug.imageQuality ? `imageQuality: ${debug.imageQuality}` : null,
    debug.characterBiblePrompt
      ? `\n# characterBiblePrompt\n${debug.characterBiblePrompt}`
      : null,
    debug.spriteSheetPrompt ? `\n# spriteSheetPrompt\n${debug.spriteSheetPrompt}` : null,
    debug.selfIntroBenchmarkMessages
      ? `\n# selfIntroBenchmarkMessages\n${jsonText(debug.selfIntroBenchmarkMessages)}`
      : null,
  ];
  return chunks.filter(Boolean).join("\n");
}

function folderUrl(assetSetId: string | null | undefined): string | null {
  return assetSetId ? `${ADMIN_API_URL}/static/generated/${assetSetId}/` : null;
}

function copyErrorFromUnknown(error: unknown): string {
  if (error instanceof DOMException && error.name === "AbortError") {
    return "REQUEST_ABORTED: Запрос отменен";
  }
  if (error instanceof AdminGenerationLabApiError) {
    return `${error.code ?? "REQUEST_FAILED"}: ${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Unknown error";
}

function buildAbortError(slot: AdminSlotState): AdminGenerateError {
  const result = resultForSlot(slot);
  const description = result?.description ?? ("description" in slot ? slot.description : "");
  const durationMs =
    slot.status === "generating" ? Math.max(0, Date.now() - slot.startedAt) : result?.durationMs ?? 0;
  return {
    slotId: slot.slotId,
    description,
    status: "failed",
    code: "REQUEST_ABORTED",
    message: "Запрос отменен на frontend.",
    durationMs,
  };
}

function FieldRow({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="grid grid-cols-[128px_minmax(0,1fr)] gap-2 border-t border-black/10 py-1.5 first:border-t-0">
      <dt className="text-xs font-medium text-black/55">{label}</dt>
      <dd className="min-w-0 whitespace-pre-wrap break-words text-xs text-black">{formatValue(value)}</dd>
    </div>
  );
}

function DetailsBlock({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="border-t border-black/10 py-2" open={defaultOpen}>
      <summary className="cursor-pointer text-xs font-semibold text-black focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600">
        {title}
      </summary>
      <div className="pt-2">{children}</div>
    </details>
  );
}

function PreBlock({ value }: { value: string }) {
  return (
    <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border border-black/10 bg-black/[0.03] p-2 font-mono text-[11px] leading-relaxed text-black">
      {value}
    </pre>
  );
}

function ActionButton({
  children,
  onClick,
  disabled = false,
  title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      className="inline-flex h-8 items-center gap-1.5 rounded-md border border-black/15 bg-white px-2 text-xs font-medium text-black hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 disabled:cursor-not-allowed disabled:opacity-45"
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      {children}
    </button>
  );
}

function ExternalAction({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex h-8 items-center gap-1.5 rounded-md border border-black/15 bg-white px-2 text-xs font-medium text-black hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
    >
      {children}
    </a>
  );
}

function VisualPreview({ result }: { result: AdminGenerateOneResponse }) {
  if (!result.images) {
    return null;
  }
  const babyIdle = result.images.baby.idle;
  return (
    <section className="border-t border-black/10 py-2">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-semibold uppercase text-black/55">visual preview</h3>
        {result.spriteSheetUrl ? (
          <ExternalAction href={result.spriteSheetUrl}>
            <ExternalLink className="size-3.5" aria-hidden="true" />
            Open sprite
          </ExternalAction>
        ) : null}
        {folderUrl(result.assetSetId) ? (
          <ExternalAction href={folderUrl(result.assetSetId) ?? "#"}>
            <ExternalLink className="size-3.5" aria-hidden="true" />
            Open folder URL
          </ExternalAction>
        ) : null}
      </div>
      <div className="grid gap-2 xl:grid-cols-[112px_minmax(0,1fr)]">
        <div className="flex size-28 items-center justify-center rounded-md border border-black/10 bg-white">
          <img
            src={babyIdle}
            alt="baby idle generated preview"
            className="max-h-24 max-w-24 object-contain"
          />
        </div>
        <div className="grid grid-cols-4 gap-1.5 sm:grid-cols-6 lg:grid-cols-12 xl:grid-cols-12">
          {STAGES.flatMap((stage) =>
            MOODS.map((mood) => (
              <figure
                key={`${stage}-${mood}`}
                className="min-w-0 rounded-md border border-black/10 bg-white p-1"
              >
                <img
                  src={result.images?.[stage][mood] ?? ""}
                  alt={`${stage} ${mood} generated pet`}
                  className="mx-auto size-14 object-contain"
                />
                <figcaption className="truncate pt-1 text-center font-mono text-[10px] text-black/55">
                  {stage}/{mood}
                </figcaption>
              </figure>
            )),
          )}
        </div>
      </div>
    </section>
  );
}

function CharacterSummary({ characterBible }: { characterBible: Record<string, unknown> }) {
  const lore = isRecord(characterBible.lore) ? characterBible.lore : {};
  const signature = characterBible.signature;
  const world = lore.world;
  const home = lore.home;
  const origin = lore.origin;
  const relationships = lore.relationships;
  const innerLife = lore.inner_life;
  const voice = lore.voice;
  const storySeeds = lore.story_seeds;
  const signatureBehavior = nestedValue(signature, "behavioral_hook");
  const signatureUserBond = nestedValue(signature, "relationship_hook");
  return (
    <section className="border-t border-black/10 py-2">
      <h3 className="mb-1.5 text-xs font-semibold uppercase text-black/55">identity</h3>
      <dl>
        <FieldRow label="species" value={sectionValue(characterBible, "species")} />
        <FieldRow label="signature" value={nestedValue(signature, "core_gimmick") ?? signature} />
        {signatureBehavior ? <FieldRow label="behavior" value={signatureBehavior} /> : null}
        {signatureUserBond ? <FieldRow label="user bond" value={signatureUserBond} /> : null}
        <FieldRow label="personality" value={sectionValue(characterBible, "personality")} />
        <FieldRow label="visual anchor" value={sectionValue(characterBible, "signature_features")} />
      </dl>
      <h3 className="mt-3 mb-1.5 text-xs font-semibold uppercase text-black/55">lore foundation</h3>
      <dl>
        <FieldRow label="world" value={nestedValue(world, "story") ?? world} />
        <FieldRow label="home" value={nestedValue(home, "story") ?? home} />
        <FieldRow label="origin" value={nestedValue(origin, "story") ?? origin} />
        <FieldRow label="relationships" value={nestedValue(relationships, "story") ?? relationships} />
        <FieldRow label="core want" value={nestedValue(innerLife, "core_want")} />
        <FieldRow label="inner conflict" value={nestedValue(innerLife, "inner_conflict")} />
        <FieldRow label="voice" value={nestedValue(voice, "speech_pattern") ?? voice} />
        <FieldRow label="growth_arc" value={lore.growth_arc} />
        <FieldRow label="story_seeds" value={storySeeds} />
      </dl>
    </section>
  );
}

function BenchmarkBlock({ result }: { result: AdminGenerateOneResponse }) {
  if (!result.benchmark) {
    return null;
  }
  return (
    <DetailsBlock title="Self-intro benchmark" defaultOpen>
      <dl>
        <FieldRow label="question" value={result.benchmark.question} />
        <FieldRow label="reply" value={result.benchmark.reply} />
        <FieldRow label="moodHint" value={result.benchmark.moodHint ?? "—"} />
        <FieldRow label="fallback" value={String(result.benchmark.usedFallback)} />
        <FieldRow label="flags" value={result.benchmark.validationFlags} />
        <FieldRow label="quality score" value={result.benchmark.qualityScore ?? "—"} />
        <FieldRow
          label="quality passed"
          value={
            result.benchmark.qualityPassed === null ||
            result.benchmark.qualityPassed === undefined
              ? "—"
              : String(result.benchmark.qualityPassed)
          }
        />
        <FieldRow label="quality flags" value={result.benchmark.qualityFlags ?? []} />
      </dl>
      {result.benchmark.turns?.length ? (
        <div className="mt-3 grid gap-2">
          {result.benchmark.turns.map((turn, index) => (
            <div key={`${turn.question}-${index}`} className="rounded-md border border-black/10 p-2">
              <dl>
                <FieldRow label={`turn ${index + 1}`} value={turn.question} />
                <FieldRow label="reply" value={turn.reply} />
                <FieldRow label="quality score" value={turn.qualityScore ?? "—"} />
                <FieldRow label="quality flags" value={turn.qualityFlags ?? []} />
                <FieldRow label="fallback" value={String(turn.usedFallback)} />
              </dl>
            </div>
          ))}
        </div>
      ) : null}
    </DetailsBlock>
  );
}

function SlotView({
  index,
  slot,
  showImages,
  showRawJson,
  onCopy,
}: {
  index: number;
  slot: AdminSlotState;
  showImages: boolean;
  showRawJson: boolean;
  onCopy: (label: string, value: string) => void;
}) {
  const result = resultForSlot(slot);
  const description = result?.description ?? ("description" in slot ? slot.description : "");
  const debugPromptText = promptText(result?.debug);

  return (
    <article className="min-w-0 rounded-md border border-black/15 bg-white">
      <header className="border-b border-black/10 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-black">Slot #{index + 1}</h2>
          <span
            className={`rounded-md border px-1.5 py-0.5 font-mono text-[11px] tabular-nums ${STATUS_CLASS[slot.status]}`}
          >
            {slot.status}
          </span>
          <span className="font-mono text-[11px] text-black/55 tabular-nums">
            {durationLabel(slot)}
          </span>
          {result ? (
            <span className="font-mono text-[11px] text-black/55">{MODE_LABELS[result.mode]}</span>
          ) : null}
          {result?.assetSetId ? (
            <span className="truncate font-mono text-[11px] text-black/55">
              asset {result.assetSetId}
            </span>
          ) : null}
        </div>
        <p className="mt-2 break-words text-xs leading-relaxed text-black/75">{description}</p>
      </header>

      <div className="p-3">
        {slot.status === "queued" ? (
          <p className="text-xs text-black/55">Queued</p>
        ) : null}

        {slot.status === "generating" ? (
          <p className="text-xs text-black/55" role="status" aria-live="polite">
            Generating character data and assets…
          </p>
        ) : null}

        {slot.status === "failed" ? (
          <DetailsBlock title="Errors" defaultOpen>
            <div className="flex items-start gap-2 rounded-md border border-red-700/20 bg-red-50 p-2 text-xs text-red-800">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <p className="min-w-0 break-words">{errorText(slot.error)}</p>
            </div>
            {typeof slot.error !== "string" ? <PreBlock value={jsonText(slot.error)} /> : null}
          </DetailsBlock>
        ) : null}

        {result ? (
          <>
            <div className="flex flex-wrap gap-2">
              <ActionButton onClick={() => onCopy("JSON", jsonText(result))}>
                <Clipboard className="size-3.5" aria-hidden="true" />
                Copy JSON
              </ActionButton>
              <ActionButton
                onClick={() => onCopy("benchmark reply", result.benchmark?.reply ?? "")}
                disabled={!result.benchmark?.reply}
              >
                <Clipboard className="size-3.5" aria-hidden="true" />
                Copy benchmark
              </ActionButton>
              <ActionButton
                onClick={() => onCopy("prompts", debugPromptText)}
                disabled={!debugPromptText}
              >
                <Clipboard className="size-3.5" aria-hidden="true" />
                Copy prompts
              </ActionButton>
            </div>

            {showImages ? <VisualPreview result={result} /> : null}
            <CharacterSummary characterBible={result.characterBible} />
            <BenchmarkBlock result={result} />

            <DetailsBlock title="Raw characterBible">
              <PreBlock value={jsonText(result.characterBible)} />
            </DetailsBlock>

            {result.debug ? (
              <DetailsBlock title="Debug prompts">
                <PreBlock value={debugPromptText || jsonText(result.debug)} />
              </DetailsBlock>
            ) : null}

            {showRawJson ? (
              <DetailsBlock title="Raw response">
                <PreBlock value={jsonText(result)} />
              </DetailsBlock>
            ) : null}
          </>
        ) : null}
      </div>
    </article>
  );
}

export default function AdminGenerationLabPage() {
  const [descriptions, setDescriptions] = useState("маленький дракон с мягкими крыльями");
  const [countInput, setCountInput] = useState("10");
  const [includeDebugPrompts, setIncludeDebugPrompts] = useState(true);
  const [includeSelfIntroBenchmark, setIncludeSelfIntroBenchmark] = useState(true);
  const [includeConversationBenchmark, setIncludeConversationBenchmark] = useState(false);
  const [showRawJson, setShowRawJson] = useState(false);
  const [slots, setSlots] = useState<AdminSlotState[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const [backendStatus, setBackendStatus] = useState<BackendStatus>({
    state: "checking",
    label: "checking",
  });

  const controllersRef = useRef<Map<string, AbortController>>(new Map());
  const activeRunIdRef = useRef<string | null>(null);

  const refreshBackendStatus = useCallback(() => {
    const controller = new AbortController();
    setBackendStatus({ state: "checking", label: "checking" });
    getAdminGenerationLabStatus(controller.signal)
      .then((status) => {
        setBackendStatus({ state: "ready", label: status.status });
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        if (error instanceof AdminGenerationLabApiError) {
          setBackendStatus({
            state: "failed",
            label: error.message,
            code: error.code,
          });
          return;
        }
        setBackendStatus({ state: "failed", label: "backend unavailable" });
      });
    return controller;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    getAdminGenerationLabStatus(controller.signal)
      .then((status) => {
        setBackendStatus({ state: "ready", label: status.status });
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        if (error instanceof AdminGenerationLabApiError) {
          setBackendStatus({
            state: "failed",
            label: error.message,
            code: error.code,
          });
          return;
        }
        setBackendStatus({ state: "failed", label: "backend unavailable" });
      });
    return () => controller.abort();
  }, []);

  const updateSlot = useCallback((slotId: string, nextSlot: AdminSlotState) => {
    setSlots((current) => current.map((slot) => (slot.slotId === slotId ? nextSlot : slot)));
  }, []);

  const stopGeneration = useCallback(() => {
    activeRunIdRef.current = null;
    controllersRef.current.forEach((controller) => controller.abort());
    controllersRef.current.clear();
    setIsRunning(false);
    setSlots((current) =>
      current.map((slot) =>
        slot.status === "queued" || slot.status === "generating"
          ? {
              status: "failed",
              slotId: slot.slotId,
              description: slot.description,
              error: buildAbortError(slot),
            }
          : slot,
      ),
    );
  }, []);

  const copyToClipboard = useCallback((label: string, value: string) => {
    if (!value) {
      return;
    }
    navigator.clipboard
      .writeText(value)
      .then(() => {
        setCopyStatus(`Copied ${label}`);
        window.setTimeout(() => setCopyStatus(null), 1400);
      })
      .catch(() => {
        setCopyStatus(`Copy failed: ${label}`);
        window.setTimeout(() => setCopyStatus(null), 1800);
      });
  }, []);

  const startGeneration = useCallback(async () => {
    if (isRunning) {
      stopGeneration();
    }

    const inputLines = descriptions
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    if (inputLines.length === 0) {
      setFormError("Введите хотя бы одно описание.");
      return;
    }

    const safeCount = normalizeCountInput(countInput);
    setCountInput(String(safeCount));
    setFormError(null);

    const runId = String(Date.now());
    activeRunIdRef.current = runId;
    controllersRef.current.clear();

    const nextSlots: AdminQueuedSlotState[] = Array.from({ length: safeCount }, (_, index) => {
      const description = inputLines[index % inputLines.length];
      return {
        status: "queued",
        slotId: `${runId}-${index + 1}`,
        description,
      };
    });

    setSlots(nextSlots);
    setIsRunning(true);

    let nextSlotIndex = 0;
    const runSlot = async (slot: AdminQueuedSlotState) => {
      if (activeRunIdRef.current !== runId) {
        return;
      }
      const controller = new AbortController();
      controllersRef.current.set(slot.slotId, controller);
      updateSlot(slot.slotId, {
        status: "generating",
        slotId: slot.slotId,
        description: slot.description,
        startedAt: Date.now(),
      });

      try {
        const result = await generateAdminCharacter(
          {
            description: slot.description,
            mode: "profile_only",
            slotId: slot.slotId,
            includeDebugPrompts,
            includeSelfIntroBenchmark,
            includeConversationBenchmark,
          },
          controller.signal,
        );
        if (activeRunIdRef.current !== runId) {
          return;
        }
        updateSlot(slot.slotId, {
          status: "ready",
          slotId: slot.slotId,
          result,
        });
      } catch (error: unknown) {
        if (activeRunIdRef.current === runId) {
          const apiError =
            error instanceof AdminGenerationLabApiError ? error.payload : undefined;
          updateSlot(slot.slotId, {
            status: "failed",
            slotId: slot.slotId,
            description: slot.description,
            error: apiError ?? copyErrorFromUnknown(error),
          });
        }
      } finally {
        controllersRef.current.delete(slot.slotId);
      }
    };

    const workerCount = Math.min(MAX_PARALLEL_ADMIN_GENERATIONS, nextSlots.length);
    await Promise.all(
      Array.from({ length: workerCount }, async () => {
        while (activeRunIdRef.current === runId) {
          const slot = nextSlots[nextSlotIndex];
          nextSlotIndex += 1;
          if (!slot) {
            return;
          }
          await runSlot(slot);
        }
      }),
    );

    if (activeRunIdRef.current === runId) {
      activeRunIdRef.current = null;
      setIsRunning(false);
    }
  }, [
    countInput,
    descriptions,
    includeConversationBenchmark,
    includeDebugPrompts,
    includeSelfIntroBenchmark,
    isRunning,
    stopGeneration,
    updateSlot,
  ]);

  const readyCount = slots.filter((slot) => slot.status === "ready").length;
  const failedCount = slots.filter((slot) => slot.status === "failed").length;

  return (
    <main className="min-h-dvh bg-white text-black">
      <div className="mx-auto max-w-[1800px] px-4 py-4">
        <header className="flex flex-wrap items-center gap-3 border-b border-black/15 pb-3">
          <h1 className="text-base font-semibold text-black">Admin Generation Lab</h1>
          <div
            className="inline-flex items-center gap-2 rounded-md border border-black/15 px-2 py-1 font-mono text-xs tabular-nums text-black/70"
            aria-live="polite"
          >
            <span
              className={`size-2 rounded-full ${
                backendStatus.state === "ready"
                  ? "bg-emerald-600"
                  : backendStatus.state === "checking"
                    ? "bg-black/35"
                    : "bg-red-600"
              }`}
              aria-hidden="true"
            />
            backend:{" "}
            {backendStatus.state === "failed" && backendStatus.code
              ? backendStatus.code
              : backendStatus.label}
          </div>
          <button
            type="button"
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-black/15 bg-white px-2 text-xs font-medium text-black hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
            onClick={() => refreshBackendStatus()}
          >
            <RefreshCw className="size-3.5" aria-hidden="true" />
            Refresh
          </button>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <div className="flex h-8 items-center gap-1.5 rounded-md border border-black/15 bg-black/[0.03] px-2 text-xs font-medium text-black/70">
              mode
              <span className="font-mono text-black">profile_only</span>
            </div>

            <label className="flex items-center gap-1.5 text-xs font-medium text-black/70" htmlFor="count">
              count
              <input
                id="count"
                type="number"
                min={1}
                max={10}
                className="h-8 w-16 rounded-md border border-black/20 bg-white px-2 font-mono text-xs text-black tabular-nums focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
                value={countInput}
                onBlur={(event) => setCountInput(String(normalizeCountInput(event.target.value)))}
                onChange={(event) => setCountInput(event.target.value)}
                disabled={isRunning}
              />
            </label>

            <button
              type="button"
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-black bg-black px-3 text-xs font-semibold text-white hover:bg-black/85 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 disabled:cursor-not-allowed disabled:opacity-45"
              onClick={() => void startGeneration()}
              disabled={isRunning}
            >
              <Play className="size-3.5" aria-hidden="true" />
              Generate
            </button>

            <button
              type="button"
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-black/20 bg-white px-3 text-xs font-semibold text-black hover:bg-black/[0.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 disabled:cursor-not-allowed disabled:opacity-45"
              onClick={stopGeneration}
              disabled={!isRunning}
            >
              <Square className="size-3.5" aria-hidden="true" />
              Stop
            </button>
          </div>
        </header>

        <section className="grid gap-3 border-b border-black/15 py-3 lg:grid-cols-[minmax(360px,620px)_minmax(0,1fr)]">
          <div>
            <label htmlFor="descriptions" className="mb-1.5 block text-xs font-semibold text-black/70">
              descriptions
            </label>
            <textarea
              id="descriptions"
              className="min-h-32 w-full resize-y rounded-md border border-black/20 bg-white p-2 text-sm leading-relaxed text-black focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
              value={descriptions}
              onChange={(event) => setDescriptions(event.target.value)}
              aria-describedby={formError ? "generation-form-error" : undefined}
              aria-invalid={Boolean(formError)}
              disabled={isRunning}
            />
            {formError ? (
              <p id="generation-form-error" className="mt-1 text-xs text-red-700" role="alert">
                {formError}
              </p>
            ) : null}
          </div>

          <div className="grid content-start gap-2 text-xs text-black/70 sm:grid-cols-2 xl:grid-cols-4">
            <label className="inline-flex items-center gap-2 rounded-md border border-black/10 p-2">
              <input
                type="checkbox"
                className="size-4"
                checked={includeDebugPrompts}
                onChange={(event) => setIncludeDebugPrompts(event.target.checked)}
                disabled={isRunning}
              />
              include debug prompts
            </label>
            <label className="inline-flex items-center gap-2 rounded-md border border-black/10 p-2">
              <input
                type="checkbox"
                className="size-4"
                checked={includeSelfIntroBenchmark}
                onChange={(event) => setIncludeSelfIntroBenchmark(event.target.checked)}
                disabled={isRunning}
              />
              include self-intro benchmark
            </label>
            <label className="inline-flex items-center gap-2 rounded-md border border-black/10 p-2">
              <input
                type="checkbox"
                className="size-4"
                checked={includeConversationBenchmark}
                onChange={(event) => setIncludeConversationBenchmark(event.target.checked)}
                disabled={isRunning || !includeSelfIntroBenchmark}
              />
              include conversation benchmark
            </label>
            <label className="inline-flex items-center gap-2 rounded-md border border-black/10 p-2">
              <input
                type="checkbox"
                className="size-4"
                checked={showRawJson}
                onChange={(event) => setShowRawJson(event.target.checked)}
              />
              show raw JSON
            </label>
            <div className="rounded-md border border-black/10 p-2 font-mono tabular-nums text-black/65 sm:col-span-2 xl:col-span-4">
              results: {slots.length} / ready: {readyCount} / failed: {failedCount}
              {copyStatus ? <span className="ml-3 text-blue-700">{copyStatus}</span> : null}
            </div>
          </div>
        </section>

        <section
          className="grid gap-3 py-3 md:grid-cols-2 2xl:grid-cols-3"
          aria-busy={isRunning}
          aria-live="polite"
        >
          {slots.length === 0 ? (
            <div className="rounded-md border border-black/15 bg-white p-4 text-sm text-black/60">
              No slots yet.
            </div>
          ) : (
            slots.map((slot, index) => (
              <SlotView
                key={slot.slotId}
                index={index}
                slot={slot}
                showImages={false}
                showRawJson={showRawJson}
                onCopy={copyToClipboard}
              />
            ))
          )}
        </section>
      </div>
    </main>
  );
}
