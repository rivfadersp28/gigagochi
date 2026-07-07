"use client";

import {
  AlertCircle,
  Check,
  Database,
  FileJson,
  RefreshCw,
  Rocket,
  Save,
  Search,
  Settings2,
  SlidersHorizontal,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchAdminSpeechPublishJob,
  fetchAdminSpeechManifest,
  saveAdminSpeechFiles,
  startAdminSpeechPublish,
  type AdminDialogueInfluenceItem,
  type AdminSpeechDataSource,
  type AdminSpeechFile,
  type AdminSpeechManifest,
  type AdminSpeechPublishJob,
} from "@/lib/adminSpeechApi";
import { cn } from "@/lib/utils";

type Drafts = Record<string, string>;
type ValidationState = Record<string, string | null>;
const PUBLISH_POLL_INTERVAL_MS = 1500;
const SPEECH_RUNTIME_FILE_ID = "speech_runtime";

const SOURCE_OPTIONS: Array<{ id: AdminSpeechDataSource; label: string; description: string }> = [
  {
    id: "local",
    label: "Local",
    description: "Файлы backend/data в текущем worktree.",
  },
  {
    id: "production",
    label: "Production",
    description: "Файлы backend/data на Hetzner.",
  },
];

const QUICK_FILTERS = [
  { id: "all", label: "Все", fileIds: null },
  {
    id: "dialogue",
    label: "Диалог",
    fileIds: ["speech_runtime", "story_library", "age_speech_examples"],
  },
  {
    id: "personality",
    label: "Характер",
    fileIds: ["speech_runtime", "age_speech_examples"],
  },
  {
    id: "world",
    label: "Мир",
    fileIds: [
      "story_library",
      "story_constructor",
      "travel_story_templates",
      "world_descriptions",
    ],
  },
  { id: "sources", label: "Сиды", fileIds: ["external_character_sources"] },
] as const;

type QuickFilterId = (typeof QUICK_FILTERS)[number]["id"];
type EditorTab = "dialogue" | "runtime" | "editor" | "details";
type JsonRecord = Record<string, unknown>;

const STATE_SURFACES = [
  { id: "chat", label: "Chat" },
  { id: "proactive", label: "Proactive" },
  { id: "ambient", label: "Idle" },
] as const;

const STATE_FLAGS = [
  { id: "age", label: "Возраст" },
  { id: "mood", label: "Mood" },
  { id: "hunger", label: "Голод" },
  { id: "energy", label: "Энергия" },
] as const;

const AGE_STAGES = [
  { id: "baby", label: "Baby" },
  { id: "teen", label: "Teen" },
  { id: "adult", label: "Adult" },
] as const;

const STATE_MODIFIERS = [
  { id: "hungry", label: "Hungry" },
  { id: "happy", label: "Happy" },
  { id: "happyLowEnergy", label: "Happy + low energy" },
  { id: "sad", label: "Sad" },
  { id: "lowEnergy", label: "Low energy" },
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

function booleanAt(config: JsonRecord, path: string[], fallback = false) {
  const value = readPath(config, path);
  return typeof value === "boolean" ? value : fallback;
}

function numberAt(config: JsonRecord, path: string[], fallback = 0) {
  const value = readPath(config, path);
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
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

function RuntimeNumberField({
  label,
  description,
  value,
  onChange,
}: {
  label: string;
  description?: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <div className="space-y-2">
      <div>
        <Label className="text-sm font-medium">{label}</Label>
        {description ? (
          <p className="mt-1 text-xs leading-4 text-muted-foreground">{description}</p>
        ) : null}
      </div>
      <Input
        type="number"
        value={String(value)}
        onChange={(event) => onChange(parseIntegerInput(event.target.value, value))}
      />
    </div>
  );
}

function RuntimeField({
  label,
  description,
  value,
  rows = 3,
  onChange,
}: {
  label: string;
  description?: string;
  value: string;
  rows?: number;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-2">
      <div>
        <Label className="text-sm font-medium">{label}</Label>
        {description ? (
          <p className="mt-1 text-xs leading-4 text-muted-foreground">{description}</p>
        ) : null}
      </div>
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
  description,
  values,
  rows = 4,
  onChange,
}: {
  label: string;
  description?: string;
  values: string[];
  rows?: number;
  onChange: (values: string[]) => void;
}) {
  return (
    <RuntimeField
      label={label}
      description={description ?? "Одна строка — одно правило."}
      value={values.join("\n")}
      rows={rows}
      onChange={(value) => onChange(textToLines(value))}
    />
  );
}

function RuntimeSection({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-border/70 p-4">
      <div className="mb-4">
        <h3 className="text-sm font-semibold">{title}</h3>
        <p className="mt-1 text-xs leading-4 text-muted-foreground">{description}</p>
      </div>
      <div className="grid gap-4">{children}</div>
    </section>
  );
}

function RuntimeConfigEditor({
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
        <AlertDescription>Исправь файл во вкладке «Редактор».</AlertDescription>
      </Alert>
    );
  }

  const updatePath = (path: string[], value: unknown) => {
    onChange(formatJson(writePath(config, path, value)));
  };

  return (
    <div className="grid gap-4">
      <RuntimeSection
        title="Голос и контракт"
        description="Базовые правила, которые подмешиваются в chat, proactive и idle."
      >
        <RuntimeField
          label="Persona contract"
          value={stringAt(config, ["personaContract"])}
          onChange={(value) => updatePath(["personaContract"], value)}
        />
        <RuntimeField
          label="Memory usage rule"
          value={stringAt(config, ["memoryUsageRule"])}
          rows={2}
          onChange={(value) => updatePath(["memoryUsageRule"], value)}
        />
        <RuntimeLineList
          label="Общие правила видимых реплик"
          values={stringListAt(config, ["visibleReply", "globalRules"])}
          onChange={(values) => updatePath(["visibleReply", "globalRules"], values)}
        />
        <RuntimeLineList
          label="Правила обычного чата"
          values={stringListAt(config, ["visibleReply", "chatRules"])}
          onChange={(values) => updatePath(["visibleReply", "chatRules"], values)}
        />
        <RuntimeField
          label="Intro для baby examples"
          value={stringAt(config, ["visibleReply", "babyExamplesIntro"])}
          rows={2}
          onChange={(value) => updatePath(["visibleReply", "babyExamplesIntro"], value)}
        />
      </RuntimeSection>

      <RuntimeSection
        title="Возраст и состояние"
        description="Какие state-модификаторы попадают в разные поверхности реплики."
      >
        <div className="overflow-x-auto rounded-md border border-border/70">
          <div className="grid min-w-[560px] grid-cols-[120px_repeat(4,minmax(0,1fr))] items-center border-b border-border/70 bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground">
            <span>Surface</span>
            {STATE_FLAGS.map((flag) => (
              <span key={flag.id}>{flag.label}</span>
            ))}
          </div>
          {STATE_SURFACES.map((surface) => (
            <div
              key={surface.id}
              className="grid min-w-[560px] grid-cols-[120px_repeat(4,minmax(0,1fr))] items-center gap-2 border-b border-border/50 px-3 py-3 last:border-b-0"
            >
              <span className="text-sm font-medium">{surface.label}</span>
              {STATE_FLAGS.map((flag) => (
                <Switch
                  key={flag.id}
                  checked={booleanAt(config, ["stateLayer", "surfaces", surface.id, flag.id])}
                  onCheckedChange={(checked) =>
                    updatePath(["stateLayer", "surfaces", surface.id, flag.id], checked)
                  }
                  aria-label={`${surface.label}: ${flag.label}`}
                />
              ))}
            </div>
          ))}
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          {AGE_STAGES.map((stage) => (
            <RuntimeField
              key={stage.id}
              label={`Age hint: ${stage.label}`}
              value={stringAt(config, ["stateLayer", "ageRoleHints", stage.id])}
              rows={2}
              onChange={(value) => updatePath(["stateLayer", "ageRoleHints", stage.id], value)}
            />
          ))}
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <RuntimeNumberField
            label="Hunger low max"
            description="При hunger <= этому значению можно подмешивать голод."
            value={numberAt(config, ["stateLayer", "thresholds", "hungerLowMax"], 29)}
            onChange={(value) => updatePath(["stateLayer", "thresholds", "hungerLowMax"], value)}
          />
          <RuntimeNumberField
            label="Energy low max"
            description="При energy <= этому значению можно подмешивать усталость."
            value={numberAt(config, ["stateLayer", "thresholds", "energyLowMax"], 30)}
            onChange={(value) => updatePath(["stateLayer", "thresholds", "energyLowMax"], value)}
          />
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
      </RuntimeSection>

      <RuntimeSection
        title="Idle и proactive"
        description="Шаблоны главного экрана и самостоятельных реплик персонажа."
      >
        <RuntimeLineList
          label="Правила proactive"
          values={stringListAt(config, ["visibleReply", "proactiveRules"])}
          onChange={(values) => updatePath(["visibleReply", "proactiveRules"], values)}
        />
        <RuntimeLineList
          label="Правила idle"
          values={stringListAt(config, ["visibleReply", "ambientRules"])}
          onChange={(values) => updatePath(["visibleReply", "ambientRules"], values)}
        />
        <RuntimeField
          label="Idle self-prompt"
          description="Открытая инструкция для самостоятельной idle-реплики без фиксированных ходов."
          value={stringAt(config, ["ambientSelfPrompt"])}
          rows={7}
          onChange={(value) => updatePath(["ambientSelfPrompt"], value)}
        />
        <RuntimeField
          label="Recent idle anti-repeat"
          value={stringAt(config, ["recentAmbientRepliesRule"])}
          rows={2}
          onChange={(value) => updatePath(["recentAmbientRepliesRule"], value)}
        />
      </RuntimeSection>

      <RuntimeSection
        title="Память персонажа"
        description="Фоновые extractors, которые превращают выдуманные детали в lite_overlay и story_library_overlay."
      >
        <RuntimeField
          label="World seed prompt"
          value={stringAt(config, ["characterMemory", "worldSeedSystem"])}
          rows={5}
          onChange={(value) => updatePath(["characterMemory", "worldSeedSystem"], value)}
        />
        <RuntimeField
          label="Lite facts extraction"
          value={stringAt(config, ["characterMemory", "factExtractionSystem"])}
          rows={7}
          onChange={(value) => updatePath(["characterMemory", "factExtractionSystem"], value)}
        />
        <RuntimeField
          label="Story bricks extraction"
          value={stringAt(config, ["characterMemory", "storyExtractionSystem"])}
          rows={6}
          onChange={(value) => updatePath(["characterMemory", "storyExtractionSystem"], value)}
        />
      </RuntimeSection>

      <RuntimeSection
        title="Память пользователя"
        description="Что сохранять о владельце и как консолидировать наблюдения."
      >
        <RuntimeField
          label="User memory extraction"
          value={stringAt(config, ["userMemory", "extractionSystem"])}
          rows={6}
          onChange={(value) => updatePath(["userMemory", "extractionSystem"], value)}
        />
        <RuntimeField
          label="User memory consolidation"
          value={stringAt(config, ["userMemory", "consolidationSystem"])}
          rows={5}
          onChange={(value) => updatePath(["userMemory", "consolidationSystem"], value)}
        />
      </RuntimeSection>

      <RuntimeSection
        title="WORLD_CONTEXT"
        description="Как выбранные story bricks вставляются в prompt."
      >
        <RuntimeField
          label="World context template"
          description="Обязательные placeholders: {mode_rule}, {lines}."
          value={stringAt(config, ["worldContext", "blockTemplate"])}
          rows={5}
          onChange={(value) => updatePath(["worldContext", "blockTemplate"], value)}
        />
        <RuntimeField
          label="Chat mode rule"
          value={stringAt(config, ["worldContext", "chatModeRule"])}
          rows={2}
          onChange={(value) => updatePath(["worldContext", "chatModeRule"], value)}
        />
        <RuntimeField
          label="Ambient/proactive mode rule"
          value={stringAt(config, ["worldContext", "ambientProactiveModeRule"])}
          rows={2}
          onChange={(value) =>
            updatePath(["worldContext", "ambientProactiveModeRule"], value)
          }
        />
      </RuntimeSection>
    </div>
  );
}

function matchesQuickFilter(file: AdminSpeechFile, filterId: QuickFilterId) {
  const filter = QUICK_FILTERS.find((item) => item.id === filterId);
  return !filter?.fileIds || filter.fileIds.some((fileId) => fileId === file.id);
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

function summaryText(file: AdminSpeechFile) {
  if (file.format === "jsonl") {
    return `${Number(file.summary.lines ?? 0)} строк`;
  }
  const keys = Array.isArray(file.summary.keys) ? file.summary.keys.join(", ") : "";
  return keys || `${Number(file.summary.topLevelKeys ?? 0)} ключей`;
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

function surfaceLabel(value: string) {
  if (value === "chat") {
    return "chat";
  }
  if (value === "proactive") {
    return "proactive";
  }
  if (value === "ambient") {
    return "idle";
  }
  return value;
}

function InfluenceItem({
  item,
  file,
  onOpenFile,
}: {
  item: AdminDialogueInfluenceItem;
  file?: AdminSpeechFile;
  onOpenFile: (fileId: string) => void;
}) {
  const canOpen = Boolean(item.fileId && file);
  const content = (
    <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-sm font-semibold">{item.label}</h4>
            {item.role ? <Badge variant="secondary">{item.role}</Badge> : null}
            <Badge variant={item.editable ? "default" : "outline"}>
              {item.editable ? "editable" : "runtime"}
            </Badge>
          </div>
          <p className="mt-2 text-xs leading-5 text-muted-foreground">{item.summary}</p>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-1">
          {item.surfaces.length ? (
            item.surfaces.map((surface) => (
              <Badge key={surface} variant="outline">
                {surfaceLabel(surface)}
              </Badge>
            ))
          ) : (
            <Badge variant="secondary">не dialog</Badge>
          )}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{item.source}</span>
        {item.configPath ? <span className="font-mono">{item.configPath}</span> : null}
        {file ? <span className="font-mono">{file.path}</span> : null}
      </div>
    </>
  );

  if (!canOpen || !item.fileId) {
    return <div className="rounded-lg border border-border/70 bg-card p-3">{content}</div>;
  }

  return (
    <button
      type="button"
      className="w-full rounded-lg border border-border/70 bg-card p-3 text-left transition-colors hover:bg-muted/60"
      onClick={() => onOpenFile(item.fileId as string)}
    >
      {content}
    </button>
  );
}

function InfluenceSection({
  title,
  description,
  items,
  filesById,
  onOpenFile,
}: {
  title: string;
  description: string;
  items: AdminDialogueInfluenceItem[];
  filesById: Map<string, AdminSpeechFile>;
  onOpenFile: (fileId: string) => void;
}) {
  return (
    <section className="rounded-lg border border-border/70 p-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="mt-1 text-xs leading-4 text-muted-foreground">{description}</p>
        </div>
        <Badge variant="outline">{items.length}</Badge>
      </div>
      <div className="grid gap-2">
        {items.map((item) => (
          <InfluenceItem
            key={item.id}
            item={item}
            file={item.fileId ? filesById.get(item.fileId) : undefined}
            onOpenFile={onOpenFile}
          />
        ))}
      </div>
    </section>
  );
}

function DialogueInfluencePanel({
  manifest,
  files,
  onOpenFile,
}: {
  manifest: AdminSpeechManifest | null;
  files: AdminSpeechFile[];
  onOpenFile: (fileId: string) => void;
}) {
  const filesById = new Map(files.map((file) => [file.id, file]));
  const modifiers = manifest?.dialogue?.modifiers ?? [];
  const collections = manifest?.dialogue?.collections ?? [];

  return (
    <div className="grid gap-4">
      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-lg border border-border/70 p-4">
          <div className="text-xs text-muted-foreground">Prompt modifiers</div>
          <div className="mt-2 text-2xl font-semibold">{modifiers.length}</div>
        </div>
        <div className="rounded-lg border border-border/70 p-4">
          <div className="text-xs text-muted-foreground">RAG / memory collections</div>
          <div className="mt-2 text-2xl font-semibold">{collections.length}</div>
        </div>
        <div className="rounded-lg border border-border/70 p-4">
          <div className="text-xs text-muted-foreground">Source</div>
          <div className="mt-2 text-2xl font-semibold">
            {manifest?.mode === "production" ? "Production" : "Local"}
          </div>
        </div>
      </div>

      <InfluenceSection
        title="Модификаторы prompt"
        description="Слои, которые напрямую меняют system prompt видимой реплики."
        items={modifiers}
        filesById={filesById}
        onOpenFile={onOpenFile}
      />
      <InfluenceSection
        title="RAG, память и датасеты"
        description="Коллекции и динамическая память, которые могут попасть в WORLD_CONTEXT или memory block."
        items={collections}
        filesById={filesById}
        onOpenFile={onOpenFile}
      />
    </div>
  );
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
  return status;
}

function wait(ms: number) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export function SpeechAdmin() {
  const [manifest, setManifest] = useState<AdminSpeechManifest | null>(null);
  const [dataSource, setDataSource] = useState<AdminSpeechDataSource>("local");
  const [drafts, setDrafts] = useState<Drafts>({});
  const [activeId, setActiveId] = useState("");
  const [activeTab, setActiveTab] = useState<EditorTab>("dialogue");
  const [activeFilter, setActiveFilter] = useState<QuickFilterId>("dialogue");
  const [query, setQuery] = useState("");
  const [validation, setValidation] = useState<ValidationState>({});
  const [showManifest, setShowManifest] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const [publishJob, setPublishJob] = useState<AdminSpeechPublishJob | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadManifest(
    options: { clearNotice?: boolean; source?: AdminSpeechDataSource } = {},
  ) {
    setIsLoading(true);
    setError(null);
    const clearNotice = options.clearNotice ?? true;
    const source = options.source ?? dataSource;
    try {
      const nextManifest = await fetchAdminSpeechManifest(source);
      setManifest(nextManifest);
      setDrafts(Object.fromEntries(nextManifest.files.map((file) => [file.id, file.content])));
      setValidation({});
      setActiveId((current) => {
        if (current && nextManifest.files.some((file) => file.id === current)) {
          return current;
        }
        return nextManifest.files[0]?.id || "";
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

    fetchAdminSpeechManifest(dataSource)
      .then((nextManifest) => {
        if (ignore) {
          return;
        }
        setManifest(nextManifest);
        setDrafts(
          Object.fromEntries(nextManifest.files.map((file) => [file.id, file.content])),
        );
        setValidation({});
        setActiveId(nextManifest.files[0]?.id || "");
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
  }, [dataSource]);

  const files = useMemo(() => manifest?.files ?? [], [manifest]);
  const activeFile = files.find((file) => file.id === activeId) ?? files[0];
  const filteredFiles = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return files.filter((file) => matchesQuickFilter(file, activeFilter));
    }
    return files.filter(
      (file) =>
        matchesQuickFilter(file, activeFilter) &&
        [file.label, file.path, file.description, file.id].some((value) =>
          value.toLowerCase().includes(needle),
        ),
    );
  }, [activeFilter, files, query]);

  const dirtyIds = files
    .filter((file) => (drafts[file.id] ?? file.content) !== file.content)
    .map((file) => file.id);
  const hasValidationError = Object.values(validation).some(Boolean);
  const activeDraft = activeFile ? drafts[activeFile.id] ?? activeFile.content : "";
  const hasRuntimeEditor = activeFile?.id === SPEECH_RUNTIME_FILE_ID;
  const selectedTab = activeTab === "runtime" && !hasRuntimeEditor ? "editor" : activeTab;

  function selectDataSource(source: AdminSpeechDataSource) {
    if (source === dataSource) {
      return;
    }
    if (dirtyIds.length) {
      setError("Сначала сохрани или сбрось изменения перед сменой Local/Production.");
      return;
    }
    setDataSource(source);
    setActiveId("");
    setActiveTab("dialogue");
    setIsLoading(true);
    setNotice(null);
    setError(null);
  }

  function openInfluenceFile(fileId: string) {
    setActiveId(fileId);
    setActiveFilter("all");
    setActiveTab(fileId === SPEECH_RUNTIME_FILE_ID ? "runtime" : "editor");
  }

  function updateDraft(file: AdminSpeechFile, content: string) {
    setDrafts((current) => ({ ...current, [file.id]: content }));
    setValidation((current) => ({
      ...current,
      [file.id]: validateContent(file, content),
    }));
    setNotice(null);
  }

  function formatActiveFile() {
    if (!activeFile || activeFile.format !== "json") {
      return;
    }
    const parsed = JSON.parse(activeDraft || "{}");
    updateDraft(activeFile, `${JSON.stringify(parsed, null, 2)}\n`);
  }

  function activateFilter(filterId: QuickFilterId) {
    setActiveFilter(filterId);
    const nextActiveFile = files.find((file) => matchesQuickFilter(file, filterId));
    if (nextActiveFile && (!activeFile || !matchesQuickFilter(activeFile, filterId))) {
      setActiveId(nextActiveFile.id);
    }
  }

  function validateDirtyFiles() {
    const nextValidation: ValidationState = {};
    const dirtyFiles = files.filter((file) => dirtyIds.includes(file.id));
    for (const file of dirtyFiles) {
      nextValidation[file.id] = validateContent(file, drafts[file.id] ?? file.content);
    }
    setValidation((current) => ({ ...current, ...nextValidation }));
    if (Object.values(nextValidation).some(Boolean)) {
      setError("Сначала исправь JSON/JSONL.");
      return null;
    }
    return dirtyFiles;
  }

  async function saveAll() {
    if (!manifest || !dirtyIds.length) {
      return;
    }
    if (dataSource === "production") {
      await publishAll();
      return;
    }
    const dirtyFiles = validateDirtyFiles();
    if (!dirtyFiles) {
      return;
    }

    setIsSaving(true);
    setError(null);
    try {
      const result = await saveAdminSpeechFiles(
        dirtyFiles.map((file) => ({
          id: file.id,
          content: drafts[file.id] ?? file.content,
        })),
        dataSource,
      );
      await loadManifest({ clearNotice: false });
      setNotice(`Сохранено: ${result.files.map((file) => file.path).join(", ")}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setIsSaving(false);
    }
  }

  async function publishAll() {
    if (!manifest) {
      return;
    }
    if (!manifest.deploy.enabled) {
      setError(manifest.deploy.message);
      return;
    }
    const dirtyFiles = validateDirtyFiles();
    if (!dirtyFiles) {
      return;
    }
    const filesToPublish = dataSource === "production" ? files : dirtyFiles;

    setIsPublishing(true);
    setError(null);
    setNotice(null);
    setPublishJob(null);
    try {
      let current = await startAdminSpeechPublish(
        filesToPublish.map((file) => ({
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
            ? `Опубликовано: commit ${current.commitSha}, Hetzner health OK.`
            : "Опубликовано: Hetzner health OK.",
        );
      } else {
        setError(current.error ?? "Публикация завершилась ошибкой.");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setIsPublishing(false);
    }
  }

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto grid min-h-screen w-full max-w-[1440px] grid-cols-1 lg:grid-cols-[360px_minmax(0,1fr)]">
        <aside className="border-b border-border/70 p-4 lg:border-r lg:border-b-0">
	          <div className="flex items-center justify-between gap-3">
	            <div>
	              <div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
	                <Database className="size-4" />
	                Dialogue admin
	              </div>
	              <h1 className="mt-2 text-2xl font-semibold">Характеры персонажей</h1>
	            </div>
	            <Badge variant={dirtyIds.length ? "default" : "secondary"}>
	              {dirtyIds.length ? `${dirtyIds.length} изменено` : "чисто"}
	            </Badge>
	          </div>

	          <div className="mt-4 grid gap-2 rounded-lg border border-border/70 p-2">
	            {SOURCE_OPTIONS.map((source) => (
	              <button
	                key={source.id}
	                type="button"
	                className={cn(
	                  "rounded-md px-3 py-2 text-left transition-colors",
	                  dataSource === source.id ? "bg-primary text-primary-foreground" : "hover:bg-muted",
	                )}
	                onClick={() => selectDataSource(source.id)}
	                disabled={isLoading || isSaving || isPublishing}
	              >
	                <div className="text-sm font-medium">{source.label}</div>
	                <div
	                  className={cn(
	                    "mt-1 text-xs",
	                    dataSource === source.id ? "text-primary-foreground/80" : "text-muted-foreground",
	                  )}
	                >
	                  {source.description}
	                </div>
	              </button>
	            ))}
	          </div>
	          {manifest?.sync ? (
	            <div className="mt-3 flex items-center justify-between gap-3 rounded-md border border-border/70 px-3 py-2 text-xs text-muted-foreground">
              <span className="min-w-0 truncate">{manifest.sync.message}</span>
              <Badge variant="outline" className="shrink-0">
                {manifest.sync.serverCommit ?? syncStatusLabel(manifest.sync.status)}
              </Badge>
            </div>
          ) : null}

          <Separator className="my-4" />

          <div className="flex gap-2">
            <div className="relative min-w-0 flex-1">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                aria-label="Фильтр файлов"
                className="pl-8"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Слой характера"
              />
            </div>
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

          <div className="mt-3 flex flex-wrap gap-2" aria-label="Быстрые фильтры">
            {QUICK_FILTERS.map((filter) => (
              <Button
                key={filter.id}
                type="button"
                variant={activeFilter === filter.id ? "default" : "outline"}
                size="sm"
                onClick={() => activateFilter(filter.id)}
              >
                {filter.id === "personality" ? (
                  <SlidersHorizontal className="size-3.5" />
                ) : null}
                {filter.label}
              </Button>
            ))}
          </div>

	          <ScrollArea className="mt-4 h-[calc(100vh-336px)] min-h-[320px]">
            <div className="space-y-2 pr-3">
              {filteredFiles.map((file) => {
                const isActive = activeFile?.id === file.id;
                const isDirty = dirtyIds.includes(file.id);
                const validationError = validation[file.id];
                return (
                  <button
                    key={file.id}
                    type="button"
                    className={cn(
                      "w-full rounded-lg border p-3 text-left transition-colors",
                      isActive
                        ? "border-primary/50 bg-muted"
                        : "border-border/70 bg-card hover:bg-muted/60",
                    )}
                    onClick={() => setActiveId(file.id)}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">{file.label}</div>
                        <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
                          {file.path}
                        </div>
                      </div>
                      <Badge
                        variant={
                          validationError ? "destructive" : isDirty ? "default" : "secondary"
                        }
                      >
                        {validationError ? "ошибка" : isDirty ? "изменено" : file.format}
                      </Badge>
                    </div>
                    <div className="mt-2 text-xs leading-4 text-muted-foreground">
                      {file.description}
                    </div>
                    <div className="mt-3 flex items-center justify-between gap-3 text-xs text-muted-foreground">
                      <span>{summaryText(file)}</span>
                      <span>{formatBytes(file.sizeBytes)}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          </ScrollArea>
        </aside>

        <section className="min-w-0 p-4 lg:p-6">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <FileJson className="size-4" />
                {activeFile?.path ?? "нет файла"}
              </div>
              <h2 className="mt-2 truncate text-xl font-semibold">
                {activeFile?.label ?? "Нет файла"}
              </h2>
              {activeFile ? (
                <p className="mt-1 max-w-3xl text-sm leading-5 text-muted-foreground">
                  {activeFile.description}
                </p>
              ) : null}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Select value={activeId} onValueChange={setActiveId}>
                <SelectTrigger className="w-[240px]" aria-label="Выбрать файл">
                  <SelectValue placeholder="Выбрать файл" />
                </SelectTrigger>
                <SelectContent>
                  {files.map((file) => (
                    <SelectItem key={file.id} value={file.id}>
                      {file.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                type="button"
                variant="outline"
                onClick={formatActiveFile}
                disabled={
                  !activeFile || activeFile.format !== "json" || Boolean(validation[activeFile.id])
                }
              >
                Формат JSON
              </Button>
	              {dataSource === "local" ? (
	                <>
	                  <Button
	                    type="button"
	                    onClick={() => void saveAll()}
	                    disabled={!dirtyIds.length || hasValidationError || isSaving || isPublishing}
	                  >
	                    {isSaving ? (
	                      <RefreshCw className="size-4 animate-spin" />
	                    ) : (
	                      <Save className="size-4" />
	                    )}
	                    Сохранить
	                  </Button>
	                  <Button
	                    type="button"
	                    variant="outline"
	                    onClick={() => void publishAll()}
	                    disabled={
	                      !manifest?.deploy.enabled || hasValidationError || isSaving || isPublishing
	                    }
	                  >
	                    {isPublishing ? (
	                      <RefreshCw className="size-4 animate-spin" />
	                    ) : (
	                      <Rocket className="size-4" />
	                    )}
	                    Опубликовать
	                  </Button>
	                </>
	              ) : (
	                <Button
	                  type="button"
	                  onClick={() => void saveAll()}
	                  disabled={
	                    !dirtyIds.length ||
	                    !manifest?.deploy.enabled ||
	                    hasValidationError ||
	                    isSaving ||
	                    isPublishing
	                  }
	                >
	                  {isPublishing ? (
	                    <RefreshCw className="size-4 animate-spin" />
	                  ) : (
	                    <Rocket className="size-4" />
	                  )}
	                  Применить в production
	                </Button>
	              )}
            </div>
          </div>

          {error ? (
            <Alert variant="destructive" className="mt-4">
              <AlertCircle className="size-4" />
              <AlertTitle>Ошибка</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          {notice ? (
            <Alert className="mt-4">
              <Check className="size-4" />
              <AlertTitle>Сохранено</AlertTitle>
              <AlertDescription>{notice}</AlertDescription>
            </Alert>
          ) : null}

          {publishJob ? (
            <Card className="mt-4">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between gap-3">
                  <CardTitle className="text-base">Публикация</CardTitle>
                  <Badge
                    variant={publishJob.status === "failed" ? "destructive" : "secondary"}
                  >
                    {publishStatusLabel(publishJob.status)}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent>
                <pre className="max-h-72 overflow-auto rounded-lg border border-border/70 bg-muted/50 p-4 font-mono text-xs leading-relaxed text-muted-foreground">
                  {publishJob.logs.length
                    ? publishJob.logs
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
              </CardContent>
            </Card>
          ) : null}

          <Tabs
            value={selectedTab}
            onValueChange={(value) => setActiveTab(value as EditorTab)}
            className="mt-4"
          >
	            <div className="flex items-center justify-between gap-3">
	              <TabsList>
	                <TabsTrigger value="dialogue">
	                  <SlidersHorizontal className="size-3.5" />
	                  Влияние
	                </TabsTrigger>
	                <TabsTrigger value="runtime" disabled={!hasRuntimeEditor}>
	                  <Settings2 className="size-3.5" />
	                  Настройка
                </TabsTrigger>
                <TabsTrigger value="editor">Редактор</TabsTrigger>
                <TabsTrigger value="details">Файл</TabsTrigger>
              </TabsList>
              <Label className="flex items-center gap-2 text-sm text-muted-foreground">
                <Switch checked={showManifest} onCheckedChange={setShowManifest} />
                Манифест
	              </Label>
	            </div>

	            <TabsContent value="dialogue" className="mt-4">
	              <DialogueInfluencePanel
	                manifest={manifest}
	                files={files}
	                onOpenFile={openInfluenceFile}
	              />
	            </TabsContent>

	            <TabsContent value="runtime" className="mt-4">
              <RuntimeConfigEditor
                content={activeDraft}
                onChange={(content) => {
                  if (activeFile) {
                    updateDraft(activeFile, content);
                  }
                }}
              />
            </TabsContent>

            <TabsContent value="editor" className="mt-4">
              <Card>
                <CardHeader className="pb-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <CardTitle className="text-base">
                      {activeFile?.format.toUpperCase() ?? "EDITOR"}
                    </CardTitle>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <span>{activeFile ? formatDate(activeFile.updatedAt) : ""}</span>
                      {activeFile ? (
                        <Badge variant="outline">{summaryText(activeFile)}</Badge>
                      ) : null}
                    </div>
                  </div>
                </CardHeader>
                <CardContent>
                  {activeFile ? (
                    <div className="space-y-2">
                      <Label htmlFor="speech-admin-editor" className="sr-only">
                        {activeFile.label}
                      </Label>
                      <Textarea
                        id="speech-admin-editor"
                        value={activeDraft}
                        onChange={(event) => updateDraft(activeFile, event.target.value)}
                        spellCheck={false}
                        className="min-h-[calc(100vh-330px)] resize-none font-mono text-xs leading-relaxed"
                      />
                      {validation[activeFile.id] ? (
                        <p className="text-sm text-destructive">{validation[activeFile.id]}</p>
                      ) : null}
                    </div>
                  ) : (
                    <div className="min-h-[360px]" />
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="details" className="mt-4">
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Метаданные</CardTitle>
                </CardHeader>
                <CardContent>
                  <pre className="overflow-auto rounded-lg border border-border/70 bg-muted/50 p-4 font-mono text-xs leading-relaxed text-muted-foreground">
                    {JSON.stringify(showManifest ? manifest : activeFile, null, 2)}
                  </pre>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </section>
      </div>
    </main>
  );
}
