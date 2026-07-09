"use client";

import { AlertCircle, Code2 } from "lucide-react";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { AdminSpeechFile } from "@/lib/adminSpeechApi";

type Drafts = Record<string, string>;
type ValidationState = Record<string, string | null>;
type JsonRecord = Record<string, unknown>;

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

export {
  AuxiliaryDataEditor,
  CharacterBibleTemplateEditor,
  SpeechRuntimeEditor,
  ToneRuntimeEditor,
};
