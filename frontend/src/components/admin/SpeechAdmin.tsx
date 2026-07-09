"use client";

import {
  AlertCircle,
  Check,
  Code2,
  Database,
  FileJson,
  RefreshCw,
  Rocket,
  Save,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchAdminSpeechManifest,
  fetchAdminSpeechPublishJob,
  saveAdminSpeechFiles,
  startAdminSpeechPublish,
  type AdminSpeechFile,
  type AdminSpeechManifest,
  type AdminSpeechPublishJob,
} from "@/lib/adminSpeechApi";
import { cn } from "@/lib/utils";

type Drafts = Record<string, string>;
type ValidationState = Record<string, string | null>;
type JsonRecord = Record<string, unknown>;

const PUBLISH_POLL_INTERVAL_MS = 1500;
const SPEECH_RUNTIME_FILE_ID = "speech_runtime";
const TONE_RUNTIME_FILE_ID = "tone_runtime";
const CHARACTER_BIBLE_TEMPLATE_FILE_ID = "character_bible_template";

const AGE_STAGES = [
  { id: "baby", label: "Baby" },
  { id: "teen", label: "Teen" },
  { id: "adult", label: "Adult" },
] as const;

const STATE_MODIFIERS = [
  { id: "hungry", label: "Голодный" },
  { id: "happy", label: "Радостный" },
  { id: "happyLowEnergy", label: "Радостный + здоровье просело" },
  { id: "sad", label: "Грустный" },
  { id: "lowEnergy", label: "Здоровье просело" },
] as const;

const STATE_PARAM_BANDS = [
  {
    id: "hunger",
    label: "Голод",
    lowMax: "hungerLowMax",
    highMin: "hungerHighMin",
  },
  {
    id: "happiness",
    label: "Счастье",
    lowMax: "happinessLowMax",
    highMin: "happinessHighMin",
  },
  {
    id: "energy",
    label: "Здоровье",
    lowMax: "energyLowMax",
    highMin: "energyHighMin",
  },
] as const;

const STATE_PARAM_LABELS = [
  { id: "low", label: "низко" },
  { id: "normal", label: "норма" },
  { id: "high", label: "высоко" },
] as const;

const CONTEXT_ROUTING_SOURCES = [
  { id: "worldContext", label: "World context" },
  { id: "characterProfile", label: "Character profile" },
  { id: "userMemory", label: "User memory" },
  { id: "chatHistory", label: "Chat history" },
  { id: "recentReplies", label: "Recent replies" },
] as const;

const CONTEXT_SOURCE_ROWS = [
  { id: "characterProfile", label: "Профиль" },
  { id: "stateParams", label: "Параметры" },
  { id: "liteOverlay", label: "Развитие" },
  { id: "storyLibrary", label: "Мир" },
  { id: "userMemory", label: "Память" },
  { id: "chatHistory", label: "История" },
  { id: "recentReplies", label: "Антиповтор" },
] as const;

const CONTEXT_SOURCE_SURFACES = [
  { id: "chat", label: "Чат" },
  { id: "ambient", label: "Idle" },
  { id: "proactive", label: "Pro" },
  { id: "push", label: "Push" },
  { id: "backgroundStory", label: "Story" },
] as const;

const CONTEXT_SOURCE_MODES = [
  { id: "disabled", label: "выкл" },
  { id: "auto", label: "авто" },
  { id: "always", label: "вкл" },
] as const;

function unsupportedContextSourceCell(sourceId: string, surfaceId: string): string | null {
  if (sourceId === "chatHistory" && !["chat", "ambient", "backgroundStory"].includes(surfaceId)) {
    return "История используется только в Chat, Idle и Story.";
  }
  if (sourceId === "recentReplies" && !["ambient", "backgroundStory"].includes(surfaceId)) {
    return "Антиповтор используется только в Idle и Story.";
  }
  return null;
}

function contextSourceModeOptions(sourceId: string) {
  if (sourceId === "stateParams") {
    return CONTEXT_SOURCE_MODES.filter((mode) => mode.id !== "auto");
  }
  return CONTEXT_SOURCE_MODES;
}

const CHARACTER_BIBLE_LEGACY_DEFAULTS = [
  { id: "identityRole", label: "identity.role default" },
  { id: "voiceRhythm", label: "voice rhythm default" },
  { id: "addressingUser", label: "addressing_user" },
  { id: "humorStyle", label: "humor_style" },
  { id: "uncertaintyStyle", label: "uncertainty_style" },
  { id: "initiativeStyle", label: "initiative_style" },
  { id: "attitudeToUser", label: "attitude_to_user" },
  { id: "provenanceLicenseNotes", label: "provenance license_notes" },
] as const;

const AUXILIARY_FILE_IDS = [
  "story_library",
  "age_speech_examples",
  "story_constructor",
  "travel_story_templates",
  "world_descriptions",
] as const;

function isAuxiliaryFileId(id: string): id is (typeof AUXILIARY_FILE_IDS)[number] {
  return (AUXILIARY_FILE_IDS as readonly string[]).includes(id);
}

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function parseJsonObject(content: string): JsonRecord | null {
  try {
    const parsed = JSON.parse(content || "{}");
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function cloneRecord(value: JsonRecord): JsonRecord {
  return JSON.parse(JSON.stringify(value)) as JsonRecord;
}

function formatJson(value: JsonRecord) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function readPath(root: JsonRecord, path: string[]): unknown {
  let current: unknown = root;
  for (const key of path) {
    if (!isRecord(current)) {
      return undefined;
    }
    current = current[key];
  }
  return current;
}

function writePath(root: JsonRecord, path: string[], value: unknown): JsonRecord {
  const next = cloneRecord(root);
  let cursor: JsonRecord = next;
  path.slice(0, -1).forEach((key) => {
    const child = isRecord(cursor[key]) ? { ...cursor[key] } : {};
    cursor[key] = child;
    cursor = child;
  });
  cursor[path[path.length - 1]] = value;
  return next;
}

function stringAt(config: JsonRecord, path: string[]) {
  const value = readPath(config, path);
  return typeof value === "string" ? value : "";
}

function stringListAt(config: JsonRecord, path: string[]) {
  const value = readPath(config, path);
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string");
}

function numberAt(config: JsonRecord, path: string[]) {
  const value = readPath(config, path);
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function contextModeAt(config: JsonRecord, path: string[]) {
  const value = stringAt(config, path);
  return CONTEXT_SOURCE_MODES.some((mode) => mode.id === value) ? value : "disabled";
}

function parseIntegerInput(value: string, fallback = 0) {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function textToLines(value: string) {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) {
    return "0 B";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  return `${(value / 1024).toFixed(1)} KB`;
}

function formatDate(value: string | null) {
  if (!value) {
    return "нет даты";
  }
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function validateContent(file: AdminSpeechFile, content: string): string | null {
  if (file.format === "json") {
    try {
      JSON.parse(content || "{}");
      return null;
    } catch (error) {
      return error instanceof Error ? error.message : "Invalid JSON";
    }
  }

  const lines = content.split(/\r?\n/);
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line) {
      continue;
    }
    try {
      JSON.parse(line);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Invalid JSONL";
      return `line ${index + 1}: ${message}`;
    }
  }
  return null;
}

function publishStatusLabel(status: AdminSpeechPublishJob["status"]) {
  if (status === "succeeded") {
    return "готово";
  }
  if (status === "failed") {
    return "ошибка";
  }
  if (status === "running") {
    return "в работе";
  }
  return "ожидание";
}

function isPublishFinished(status: AdminSpeechPublishJob["status"]) {
  return status === "succeeded" || status === "failed";
}

function syncStatusLabel(status: string) {
  if (status === "synced") {
    return "с сервера";
  }
  if (status === "already_current") {
    return "актуально";
  }
  if (status === "disabled") {
    return "выкл.";
  }
  if (status === "local_dirty") {
    return "локальные изменения";
  }
  return status;
}

function wait(ms: number) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function Section({
  title,
  meta,
  children,
}: {
  title: string;
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border/70 bg-background p-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-base font-semibold">{title}</h2>
        {meta}
      </div>
      <div className="grid gap-4">{children}</div>
    </section>
  );
}

function RuntimeField({
  label,
  value,
  rows = 3,
  onChange,
}: {
  label: string;
  value: string;
  rows?: number;
  onChange: (value: string) => void;
}) {
  return (
    <div className="grid gap-2">
      <Label className="text-sm font-medium">{label}</Label>
      <Textarea
        value={value}
        rows={rows}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-0 resize-y text-sm leading-relaxed"
      />
    </div>
  );
}

function RuntimeLineList({
  label,
  values,
  rows = 4,
  onChange,
}: {
  label: string;
  values: string[];
  rows?: number;
  onChange: (values: string[]) => void;
}) {
  return (
    <RuntimeField
      label={label}
      value={values.join("\n")}
      rows={rows}
      onChange={(value) => onChange(textToLines(value))}
    />
  );
}

function RuntimeNumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <div className="grid gap-2">
      <Label className="text-sm font-medium">{label}</Label>
      <Input
        type="number"
        value={String(value)}
        onChange={(event) => onChange(parseIntegerInput(event.target.value, value))}
      />
    </div>
  );
}

function StateParamBandEditor({
  config,
  band,
  onChange,
}: {
  config: JsonRecord;
  band: (typeof STATE_PARAM_BANDS)[number];
  onChange: (path: string[], value: unknown) => void;
}) {
  return (
    <div className="grid gap-3 rounded-md border border-border/60 p-3">
      <div className="text-xs font-medium uppercase text-muted-foreground">{band.label}</div>
      <div className="grid gap-3 sm:grid-cols-2">
        <RuntimeNumberField
          label="Low max"
          value={numberAt(config, ["stateLayer", "thresholds", band.lowMax])}
          onChange={(value) => onChange(["stateLayer", "thresholds", band.lowMax], value)}
        />
        <RuntimeNumberField
          label="High min"
          value={numberAt(config, ["stateLayer", "thresholds", band.highMin])}
          onChange={(value) => onChange(["stateLayer", "thresholds", band.highMin], value)}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {STATE_PARAM_LABELS.map((label) => (
          <RuntimeField
            key={label.id}
            label={label.label}
            value={stringAt(config, ["stateLayer", "stateParamLabels", band.id, label.id])}
            rows={2}
            onChange={(value) =>
              onChange(["stateLayer", "stateParamLabels", band.id, label.id], value)
            }
          />
        ))}
      </div>
    </div>
  );
}

function ContextSourcesMatrix({
  config,
  onChange,
}: {
  config: JsonRecord;
  onChange: (path: string[], value: unknown) => void;
}) {
  return (
    <Section title="Копилки">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[620px] border-separate border-spacing-0 text-sm">
          <thead>
            <tr>
              <th className="w-24 px-2 py-1 text-left text-xs font-medium text-muted-foreground" />
              {CONTEXT_SOURCE_SURFACES.map((surface) => (
                <th
                  key={surface.id}
                  className="px-2 py-1 text-left text-xs font-medium text-muted-foreground"
                >
                  {surface.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {CONTEXT_SOURCE_ROWS.map((source) => (
              <tr key={source.id}>
                <th className="px-2 py-1 text-left text-xs font-medium text-muted-foreground">
                  {source.label}
                </th>
                {CONTEXT_SOURCE_SURFACES.map((surface) => {
                  const path = ["contextSources", "surfaces", surface.id, source.id];
                  const unsupportedReason = unsupportedContextSourceCell(source.id, surface.id);
                  return (
                    <td key={surface.id} className="px-2 py-1">
                      {unsupportedReason ? (
                        <div
                          className="flex h-8 w-full items-center rounded-md border border-dashed border-input px-2 text-xs text-muted-foreground"
                          title={unsupportedReason}
                          aria-label={`${surface.label}: ${source.label} не используется`}
                        >
                          не исп.
                        </div>
                      ) : (
                        <select
                          value={contextModeAt(config, path)}
                          onChange={(event) => onChange(path, event.target.value)}
                          className="h-8 w-full rounded-md border border-input bg-background px-2 text-xs"
                          aria-label={`${surface.label}: ${source.label}`}
                        >
                          {contextSourceModeOptions(source.id).map((mode) => (
                            <option key={mode.id} value={mode.id}>
                              {mode.label}
                            </option>
                          ))}
                        </select>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function SpeechRuntimeEditor({
  content,
  onChange,
}: {
  content: string;
  onChange: (content: string) => void;
}) {
  const config = parseJsonObject(content);
  if (!config) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="size-4" />
        <AlertTitle>JSON не разобран</AlertTitle>
        <AlertDescription>Исправь speech_runtime.json в raw-редакторе.</AlertDescription>
      </Alert>
    );
  }

  const updatePath = (path: string[], value: unknown) => {
    onChange(formatJson(writePath(config, path, value)));
  };

  return (
    <div className="grid gap-4">
      <ContextSourcesMatrix config={config} onChange={updatePath} />

      <Section title="Idle">
        <RuntimeField
          label="Idle prompt"
          value={stringAt(config, ["surfacePrompts", "idle"])}
          rows={8}
          onChange={(value) => updatePath(["surfacePrompts", "idle"], value)}
        />
      </Section>

      <Section title="Обычный чат">
        <RuntimeField
          label="Chat prompt"
          value={stringAt(config, ["surfacePrompts", "chat"])}
          rows={10}
          onChange={(value) => updatePath(["surfacePrompts", "chat"], value)}
        />
        <RuntimeField
          label="Memory usage rule"
          value={stringAt(config, ["memoryUsageRule"])}
          rows={2}
          onChange={(value) => updatePath(["memoryUsageRule"], value)}
        />
        <RuntimeField
          label="Identity template"
          value={stringAt(config, ["identityTemplate"])}
          rows={4}
          onChange={(value) => updatePath(["identityTemplate"], value)}
        />
      </Section>

      <Section title="Proactive">
        <RuntimeField
          label="Proactive prompt"
          value={stringAt(config, ["surfacePrompts", "proactive"])}
          rows={6}
          onChange={(value) => updatePath(["surfacePrompts", "proactive"], value)}
        />
      </Section>

      <Section title="Telegram push">
        <RuntimeField
          label="Push prompt"
          value={stringAt(config, ["surfacePrompts", "push"])}
          rows={7}
          onChange={(value) => updatePath(["surfacePrompts", "push"], value)}
        />
      </Section>

      <Section title="Фоновые истории" meta={<Badge variant="outline">/story</Badge>}>
        <RuntimeField
          label="System prompt"
          value={stringAt(config, ["backgroundStory", "systemPrompt"])}
          rows={6}
          onChange={(value) => updatePath(["backgroundStory", "systemPrompt"], value)}
        />
        <RuntimeField
          label="User template"
          value={stringAt(config, ["backgroundStory", "userTemplate"])}
          rows={5}
          onChange={(value) => updatePath(["backgroundStory", "userTemplate"], value)}
        />
        <RuntimeField
          label="Aftermath extraction system"
          value={stringAt(config, ["backgroundStory", "aftermathExtractionSystem"])}
          rows={6}
          onChange={(value) =>
            updatePath(["backgroundStory", "aftermathExtractionSystem"], value)
          }
        />
        <RuntimeField
          label="Aftermath extraction user template"
          value={stringAt(config, ["backgroundStory", "aftermathExtractionUserTemplate"])}
          rows={4}
          onChange={(value) =>
            updatePath(["backgroundStory", "aftermathExtractionUserTemplate"], value)
          }
        />
        <div className="grid gap-4 md:grid-cols-3">
          <RuntimeField
            label="Default event type"
            value={stringAt(config, ["backgroundStory", "defaultEventType"])}
            rows={2}
            onChange={(value) => updatePath(["backgroundStory", "defaultEventType"], value)}
          />
          <RuntimeNumberField
            label="Max story chars"
            value={numberAt(config, ["backgroundStory", "maxStoryChars"])}
            onChange={(value) => updatePath(["backgroundStory", "maxStoryChars"], value)}
          />
          <RuntimeNumberField
            label="Max saved chars"
            value={numberAt(config, ["backgroundStory", "maxRagChars"])}
            onChange={(value) => updatePath(["backgroundStory", "maxRagChars"], value)}
          />
        </div>
      </Section>

      <Section title="Context routing" meta={<Badge variant="outline">единый gate</Badge>}>
        <RuntimeField
          label="AI router prompt"
          value={stringAt(config, ["contextRouting", "systemPrompt"])}
          rows={7}
          onChange={(value) => updatePath(["contextRouting", "systemPrompt"], value)}
        />
        <div className="grid gap-4 lg:grid-cols-2">
          {CONTEXT_ROUTING_SOURCES.map((source) => (
            <div key={source.id} className="grid gap-3 rounded-md border border-border/60 p-3">
              <div className="text-xs font-medium uppercase text-muted-foreground">
                {source.label}
              </div>
              <RuntimeField
                label="Описание"
                value={stringAt(config, ["contextRouting", "sources", source.id, "description"])}
                rows={2}
                onChange={(value) =>
                  updatePath(["contextRouting", "sources", source.id, "description"], value)
                }
              />
              <RuntimeField
                label="Когда подключать"
                value={stringAt(config, ["contextRouting", "sources", source.id, "criteria"])}
                rows={4}
                onChange={(value) =>
                  updatePath(["contextRouting", "sources", source.id, "criteria"], value)
                }
              />
            </div>
          ))}
        </div>
      </Section>

      <Section title="Возраст">
        <div className="grid gap-4 lg:grid-cols-3">
          {AGE_STAGES.map((stage) => (
            <RuntimeField
              key={stage.id}
              label={`Age hint: ${stage.label}`}
              value={stringAt(config, ["stateLayer", "ageRoleHints", stage.id])}
              rows={2}
              onChange={(value) =>
                updatePath(["stateLayer", "ageRoleHints", stage.id], value)
              }
            />
          ))}
        </div>
      </Section>

      <Section title="Настроение, голод, здоровье">
        <RuntimeField
          label="Story usage rule"
          value={stringAt(config, ["stateLayer", "stateParamUsageRule"])}
          rows={3}
          onChange={(value) => updatePath(["stateLayer", "stateParamUsageRule"], value)}
        />
        <div className="grid gap-4">
          {STATE_PARAM_BANDS.map((band) => (
            <StateParamBandEditor
              key={band.id}
              config={config}
              band={band}
              onChange={updatePath}
            />
          ))}
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {STATE_MODIFIERS.map((modifier) => (
            <RuntimeField
              key={modifier.id}
              label={modifier.label}
              value={stringAt(config, ["stateLayer", "stateModifiers", modifier.id])}
              rows={2}
              onChange={(value) =>
                updatePath(["stateLayer", "stateModifiers", modifier.id], value)
              }
            />
          ))}
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {(["food", "fear", "secondPerson", "ability", "petName"] as const).map((key) => (
            <RuntimeField
              key={key}
              label={`Placeholder: ${key}`}
              value={stringAt(config, ["ageExamplePlaceholders", key])}
              rows={2}
              onChange={(value) => updatePath(["ageExamplePlaceholders", key], value)}
            />
          ))}
        </div>
      </Section>

      <Section title="WORLD_CONTEXT">
        <RuntimeField
          label="World context template"
          value={stringAt(config, ["worldContext", "template"])}
          rows={5}
          onChange={(value) => updatePath(["worldContext", "template"], value)}
        />
        <RuntimeField
          label="Default query"
          value={stringAt(config, ["storyContext", "defaultQuery"])}
          rows={2}
          onChange={(value) => updatePath(["storyContext", "defaultQuery"], value)}
        />
      </Section>

      <Section title="Память и extractors">
        <RuntimeField
          label="Character fact extraction"
          value={stringAt(config, ["characterMemory", "factExtractionSystem"])}
          rows={6}
          onChange={(value) => updatePath(["characterMemory", "factExtractionSystem"], value)}
        />
        <RuntimeField
          label="World seed system"
          value={stringAt(config, ["characterMemory", "worldSeedSystem"])}
          rows={5}
          onChange={(value) => updatePath(["characterMemory", "worldSeedSystem"], value)}
        />
        <RuntimeField
          label="User memory extraction"
          value={stringAt(config, ["userMemory", "extractionSystem"])}
          rows={5}
          onChange={(value) => updatePath(["userMemory", "extractionSystem"], value)}
        />
        <RuntimeField
          label="User memory consolidation"
          value={stringAt(config, ["userMemory", "consolidationSystem"])}
          rows={5}
          onChange={(value) => updatePath(["userMemory", "consolidationSystem"], value)}
        />
      </Section>
    </div>
  );
}

function ToneRuntimeEditor({
  content,
  onChange,
}: {
  content: string;
  onChange: (content: string) => void;
}) {
  const config = parseJsonObject(content);
  if (!config) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="size-4" />
        <AlertTitle>JSON не разобран</AlertTitle>
        <AlertDescription>Исправь tone_runtime.json.</AlertDescription>
      </Alert>
    );
  }

  const activePreset = stringAt(config, ["activePreset"]);
  const presets = readPath(config, ["presets"]);
  const activeConfig =
    isRecord(presets) && isRecord(presets[activePreset]) ? presets[activePreset] : null;

  const updatePath = (path: string[], value: unknown) => {
    onChange(formatJson(writePath(config, path, value)));
  };

  const updatePresetPath = (path: string[], value: unknown) => {
    updatePath(["presets", activePreset, ...path], value);
  };

  const presetIds = isRecord(presets) ? Object.keys(presets).sort() : [];

  return (
    <Section
      title="Generation profile"
      meta={<Badge variant="outline">{activePreset || "нет activePreset"}</Badge>}
    >
      <div className="grid gap-2">
        <Label>Active preset</Label>
        <div className="flex flex-wrap gap-2">
          {presetIds.map((presetId) => (
            <Button
              key={presetId}
              type="button"
              size="sm"
              variant={presetId === activePreset ? "default" : "outline"}
              onClick={() => updatePath(["activePreset"], presetId)}
            >
              {presetId}
            </Button>
          ))}
        </div>
        <Input
          value={activePreset}
          onChange={(event) => updatePath(["activePreset"], event.target.value.trim())}
          placeholder="custom preset id"
        />
      </div>

      {!activeConfig ? (
        <Alert variant="destructive">
          <AlertCircle className="size-4" />
          <AlertTitle>Пресет не найден</AlertTitle>
          <AlertDescription>Добавь activePreset в presets.</AlertDescription>
        </Alert>
      ) : (
        <>
          <RuntimeField
            label="Label"
            value={stringAt(config, ["presets", activePreset, "label"])}
            rows={2}
            onChange={(value) => updatePresetPath(["label"], value)}
          />
          <RuntimeField
            label="Setting"
            value={stringAt(config, ["presets", activePreset, "setting"])}
            rows={5}
            onChange={(value) => updatePresetPath(["setting"], value)}
          />
          <RuntimeField
            label="Tone of voice"
            value={stringAt(config, ["presets", activePreset, "toneOfVoice"])}
            rows={4}
            onChange={(value) => updatePresetPath(["toneOfVoice"], value)}
          />
        </>
      )}
    </Section>
  );
}
function CharacterBibleTemplateEditor({
  content,
  onChange,
}: {
  content: string;
  onChange: (content: string) => void;
}) {
  const config = parseJsonObject(content);
  if (!config) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="size-4" />
        <AlertTitle>JSON не разобран</AlertTitle>
        <AlertDescription>Исправь character_bible_template.json.</AlertDescription>
      </Alert>
    );
  }

  const updatePath = (path: string[], value: unknown) => {
    onChange(formatJson(writePath(config, path, value)));
  };

  return (
    <Section
      title="Шаблон библии персонажа"
      meta={<Badge variant="outline">voice.catchphrases {"->"} lore.voice.favorite_phrases</Badge>}
    >
      <RuntimeField
        label="System prompt"
        value={stringAt(config, ["systemPrompt"])}
        rows={3}
        onChange={(value) => updatePath(["systemPrompt"], value)}
      />
      <RuntimeLineList
        label="Persona shape"
        values={stringListAt(config, ["prompt", "personaShape"])}
        rows={5}
        onChange={(values) => updatePath(["prompt", "personaShape"], values)}
      />
      <RuntimeLineList
        label="Generation rules"
        values={stringListAt(config, ["prompt", "rules"])}
        rows={8}
        onChange={(values) => updatePath(["prompt", "rules"], values)}
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <RuntimeField
          label="voice.catchphrases description"
          value={stringAt(config, [
            "schema",
            "properties",
            "voice",
            "properties",
            "catchphrases",
            "description",
          ])}
          rows={3}
          onChange={(value) =>
            updatePath(
              ["schema", "properties", "voice", "properties", "catchphrases", "description"],
              value,
            )
          }
        />
        <RuntimeField
          label="voice.sample_replies description"
          value={stringAt(config, [
            "schema",
            "properties",
            "voice",
            "properties",
            "sample_replies",
            "description",
          ])}
          rows={3}
          onChange={(value) =>
            updatePath(
              ["schema", "properties", "voice", "properties", "sample_replies", "description"],
              value,
            )
          }
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {CHARACTER_BIBLE_LEGACY_DEFAULTS.map((item) => (
          <RuntimeField
            key={item.id}
            label={item.label}
            value={stringAt(config, ["legacyDefaults", item.id])}
            rows={2}
            onChange={(value) => updatePath(["legacyDefaults", item.id], value)}
          />
        ))}
      </div>
      <RuntimeField
        label="Model generation rule"
        value={stringAt(config, ["prompt", "generationRule"])}
        rows={3}
        onChange={(value) => updatePath(["prompt", "generationRule"], value)}
      />
    </Section>
  );
}

function AuxiliaryDataEditor({
  files,
  drafts,
  selectedId,
  validation,
  onSelect,
  onChange,
}: {
  files: AdminSpeechFile[];
  drafts: Drafts;
  selectedId: string;
  validation: ValidationState;
  onSelect: (id: string) => void;
  onChange: (file: AdminSpeechFile, content: string) => void;
}) {
  const selectedFile = files.find((file) => file.id === selectedId) ?? files[0];
  if (!selectedFile) {
    return null;
  }

  const activeDraft = drafts[selectedFile.id] ?? selectedFile.content;

  function formatSelectedFile() {
    if (selectedFile.format !== "json") {
      return;
    }
    const parsed = JSON.parse(activeDraft || "{}");
    onChange(selectedFile, `${JSON.stringify(parsed, null, 2)}\n`);
  }

  return (
    <Section title="Дополнительные данные">
      <div className="flex flex-wrap gap-2">
        {files.map((file) => (
          <Button
            key={file.id}
            type="button"
            variant={selectedFile.id === file.id ? "default" : "outline"}
            size="sm"
            onClick={() => onSelect(file.id)}
          >
            {file.label}
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-mono text-xs text-muted-foreground">{selectedFile.path}</div>
          <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
            <span>{formatBytes(selectedFile.sizeBytes)}</span>
            <span>{formatDate(selectedFile.updatedAt)}</span>
            <Badge variant="outline">{selectedFile.format}</Badge>
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={formatSelectedFile}
          disabled={selectedFile.format !== "json" || Boolean(validation[selectedFile.id])}
        >
          <Code2 className="size-3.5" />
          Формат JSON
        </Button>
      </div>
      <Textarea
        value={activeDraft}
        onChange={(event) => onChange(selectedFile, event.target.value)}
        spellCheck={false}
        className="min-h-[380px] resize-y font-mono text-xs leading-relaxed"
      />
      {validation[selectedFile.id] ? (
        <p className="text-sm text-destructive">{validation[selectedFile.id]}</p>
      ) : null}
    </Section>
  );
}

function PublishLog({ job }: { job: AdminSpeechPublishJob }) {
  return (
    <section className="rounded-lg border border-border/70 p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">Deploy</h2>
        <Badge variant={job.status === "failed" ? "destructive" : "secondary"}>
          {publishStatusLabel(job.status)}
        </Badge>
      </div>
      <pre className="max-h-72 overflow-auto rounded-md border border-border/70 bg-muted/40 p-3 font-mono text-xs leading-relaxed text-muted-foreground">
        {job.logs.length
          ? job.logs
              .map((line) => {
                const time = new Intl.DateTimeFormat("ru-RU", {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                }).format(new Date(line.at));
                return `${time} ${line.message}`;
              })
              .join("\n")
          : "Ожидание запуска..."}
      </pre>
    </section>
  );
}

export function SpeechAdmin() {
  const [manifest, setManifest] = useState<AdminSpeechManifest | null>(null);
  const [drafts, setDrafts] = useState<Drafts>({});
  const [validation, setValidation] = useState<ValidationState>({});
  const [selectedAuxId, setSelectedAuxId] = useState<string>("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const [publishJob, setPublishJob] = useState<AdminSpeechPublishJob | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadManifest(options: { clearNotice?: boolean } = {}) {
    setIsLoading(true);
    setError(null);
    const clearNotice = options.clearNotice ?? true;
    try {
      const nextManifest = await fetchAdminSpeechManifest();
      setManifest(nextManifest);
      setDrafts(Object.fromEntries(nextManifest.files.map((file) => [file.id, file.content])));
      setValidation({});
      setSelectedAuxId((current) => {
        if (current && nextManifest.files.some((file) => file.id === current)) {
          return current;
        }
        return (
          nextManifest.files.find((file) => isAuxiliaryFileId(file.id))?.id ||
          nextManifest.files[0]?.id ||
          ""
        );
      });
      if (clearNotice) {
        setNotice(null);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    let ignore = false;

    fetchAdminSpeechManifest()
      .then((nextManifest) => {
        if (ignore) {
          return;
        }
        setManifest(nextManifest);
        setDrafts(
          Object.fromEntries(nextManifest.files.map((file) => [file.id, file.content])),
        );
        setValidation({});
        setSelectedAuxId(
          nextManifest.files.find((file) => isAuxiliaryFileId(file.id))?.id ||
            nextManifest.files[0]?.id ||
            "",
        );
        setNotice(null);
        setPublishJob(null);
      })
      .catch((caught) => {
        if (!ignore) {
          setError(caught instanceof Error ? caught.message : String(caught));
        }
      })
      .finally(() => {
        if (!ignore) {
          setIsLoading(false);
        }
      });

    return () => {
      ignore = true;
    };
  }, []);

  const files = useMemo(() => manifest?.files ?? [], [manifest]);
  const runtimeFile = files.find((file) => file.id === SPEECH_RUNTIME_FILE_ID);
  const toneFile = files.find((file) => file.id === TONE_RUNTIME_FILE_ID);
  const characterTemplateFile = files.find(
    (file) => file.id === CHARACTER_BIBLE_TEMPLATE_FILE_ID,
  );
  const auxiliaryFiles = files.filter((file) => isAuxiliaryFileId(file.id));
  const dirtyIds = files
    .filter((file) => (drafts[file.id] ?? file.content) !== file.content)
    .map((file) => file.id);
  const hasValidationError = Object.values(validation).some(Boolean);
  const hasUndeployedChanges = manifest?.sync.status === "local_dirty";

  function updateDraft(file: AdminSpeechFile, content: string) {
    setDrafts((current) => ({ ...current, [file.id]: content }));
    setValidation((current) => ({
      ...current,
      [file.id]: validateContent(file, content),
    }));
    setNotice(null);
  }

  function validateFiles(targetFiles: AdminSpeechFile[]) {
    const nextValidation: ValidationState = {};
    for (const file of targetFiles) {
      nextValidation[file.id] = validateContent(file, drafts[file.id] ?? file.content);
    }
    setValidation((current) => ({ ...current, ...nextValidation }));
    if (Object.values(nextValidation).some(Boolean)) {
      setError("Сначала исправь JSON/JSONL.");
      return null;
    }
    return targetFiles;
  }

  function dirtyFiles() {
    return files.filter((file) => dirtyIds.includes(file.id));
  }

  async function saveDirtyDraftsForAction() {
    if (!manifest || !dirtyIds.length) {
      return true;
    }

    const targetFiles = validateFiles(dirtyFiles());
    if (!targetFiles) {
      return false;
    }

    setIsSaving(true);
    setError(null);
    try {
      const result = await saveAdminSpeechFiles(
        targetFiles.map((file) => ({
          id: file.id,
          content: drafts[file.id] ?? file.content,
        })),
      );
      await loadManifest({ clearNotice: false });
      setNotice(`Сохранено: ${result.files.map((file) => file.path).join(", ")}`);
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      return false;
    } finally {
      setIsSaving(false);
    }
  }

  async function saveAll() {
    await saveDirtyDraftsForAction();
  }

  async function deployAll() {
    if (!manifest) {
      return;
    }
    if (!manifest.deploy.enabled) {
      setError(manifest.deploy.message);
      return;
    }
    const targetFiles = dirtyIds.length ? validateFiles(dirtyFiles()) : [];
    if (!targetFiles) {
      return;
    }

    setIsPublishing(true);
    setError(null);
    setNotice(null);
    setPublishJob(null);
    try {
      let current = await startAdminSpeechPublish(
        targetFiles.map((file) => ({
          id: file.id,
          content: drafts[file.id] ?? file.content,
        })),
      );
      setPublishJob(current);
      while (!isPublishFinished(current.status)) {
        await wait(PUBLISH_POLL_INTERVAL_MS);
        current = await fetchAdminSpeechPublishJob(current.id);
        setPublishJob(current);
      }
      if (current.status === "succeeded") {
        await loadManifest({ clearNotice: false });
        setNotice(
          current.commitSha
            ? `Deploy готов: commit ${current.commitSha}, Hetzner health OK.`
            : "Deploy готов: Hetzner health OK.",
        );
      } else {
        setError(current.error ?? "Deploy завершился ошибкой.");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setIsPublishing(false);
    }
  }

  const isBusy = isSaving || isPublishing || isLoading;
  const saveDisabled = !dirtyIds.length || hasValidationError || isBusy;
  const deployDisabled =
    !manifest?.deploy.enabled ||
    (!dirtyIds.length && !hasUndeployedChanges) ||
    hasValidationError ||
    isBusy;
  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto w-full max-w-[1320px] px-4 py-4 lg:px-6">
        <header className="mb-4 rounded-lg border border-border/70 bg-background p-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <Database className="size-4" />
                Admin phrases
              </div>
              <h1 className="mt-1 text-2xl font-semibold">Фразы персонажей</h1>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Badge
                variant={
                  dirtyIds.length ? "default" : hasUndeployedChanges ? "outline" : "secondary"
                }
              >
                {dirtyIds.length
                  ? `${dirtyIds.length} не сохранено`
                  : hasUndeployedChanges
                    ? "не задеплоено"
                    : "чисто"}
              </Badge>
              <Button
                type="button"
                onClick={() => void saveAll()}
                disabled={saveDisabled}
              >
                {isSaving ? (
                  <RefreshCw className="size-4 animate-spin" />
                ) : (
                  <Save className="size-4" />
                )}
                Save
              </Button>
              <Button
                type="button"
                onClick={() => void deployAll()}
                disabled={deployDisabled}
              >
                {isPublishing ? (
                  <RefreshCw className="size-4 animate-spin" />
                ) : (
                  <Rocket className="size-4" />
                )}
                Deploy
              </Button>
              <Button
                type="button"
                variant="outline"
                size="icon"
                onClick={() => void loadManifest()}
                disabled={isLoading || isSaving || isPublishing}
                aria-label="Обновить"
              >
                <RefreshCw className={cn("size-4", isLoading && "animate-spin")} />
              </Button>
            </div>
          </div>

          {manifest?.sync ? (
            <>
              <Separator className="my-3" />
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
                <span>{manifest.sync.message}</span>
                <Badge variant="outline">
                  {manifest.sync.serverCommit ?? syncStatusLabel(manifest.sync.status)}
                </Badge>
              </div>
            </>
          ) : null}
        </header>

        {error ? (
          <Alert variant="destructive" className="mb-4">
            <AlertCircle className="size-4" />
            <AlertTitle>Ошибка</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        {notice ? (
          <Alert className="mb-4">
            <Check className="size-4" />
            <AlertTitle>Готово</AlertTitle>
            <AlertDescription>{notice}</AlertDescription>
          </Alert>
        ) : null}

        {publishJob ? <PublishLog job={publishJob} /> : null}

        {isLoading ? (
          <div className="rounded-lg border border-border/70 p-6 text-sm text-muted-foreground">
            Загрузка...
          </div>
        ) : (
          <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
            <div className="grid gap-4">
              {runtimeFile ? (
                <SpeechRuntimeEditor
                  content={drafts[runtimeFile.id] ?? runtimeFile.content}
                  onChange={(content) => updateDraft(runtimeFile, content)}
                />
              ) : null}
              {toneFile ? (
                <ToneRuntimeEditor
                  content={drafts[toneFile.id] ?? toneFile.content}
                  onChange={(content) => updateDraft(toneFile, content)}
                />
              ) : null}
              {characterTemplateFile ? (
                <CharacterBibleTemplateEditor
                  content={drafts[characterTemplateFile.id] ?? characterTemplateFile.content}
                  onChange={(content) => updateDraft(characterTemplateFile, content)}
                />
              ) : null}
              <AuxiliaryDataEditor
                files={auxiliaryFiles}
                drafts={drafts}
                selectedId={selectedAuxId}
                validation={validation}
                onSelect={setSelectedAuxId}
                onChange={updateDraft}
              />
            </div>

            <aside className="grid h-fit gap-4 xl:sticky xl:top-4">
              <section className="rounded-lg border border-border/70 p-4">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
                  <FileJson className="size-4" />
                  Файлы
                </div>
                <div className="grid gap-2 text-sm">
                  {files.map((file) => {
                    const isDirty = dirtyIds.includes(file.id);
                    const hasError = Boolean(validation[file.id]);
                    return (
                      <div
                        key={file.id}
                        className="flex items-center justify-between gap-2 rounded-md border border-border/50 px-3 py-2"
                      >
                        <span className="min-w-0 truncate">{file.label}</span>
                        <Badge
                          variant={hasError ? "destructive" : isDirty ? "default" : "outline"}
                        >
                          {hasError ? "ошибка" : isDirty ? "изменено" : file.format}
                        </Badge>
                      </div>
                    );
                  })}
                </div>
              </section>

              <section className="rounded-lg border border-border/70 p-4 text-sm">
                <div className="font-semibold">Deploy</div>
                <p className="mt-2 leading-5 text-muted-foreground">
                  {manifest?.deploy.message ?? "Нет данных deploy."}
                </p>
              </section>
            </aside>
          </div>
        )}
      </div>
    </main>
  );
}
