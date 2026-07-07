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
  SlidersHorizontal,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

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
  type AdminSpeechFile,
  type AdminSpeechManifest,
  type AdminSpeechPublishJob,
} from "@/lib/adminSpeechApi";
import { cn } from "@/lib/utils";

type Drafts = Record<string, string>;
type ValidationState = Record<string, string | null>;
const PUBLISH_POLL_INTERVAL_MS = 1500;

const QUICK_FILTERS = [
  { id: "all", label: "Все", fileIds: null },
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
  const [drafts, setDrafts] = useState<Drafts>({});
  const [activeId, setActiveId] = useState("");
  const [activeFilter, setActiveFilter] = useState<QuickFilterId>("all");
  const [query, setQuery] = useState("");
  const [validation, setValidation] = useState<ValidationState>({});
  const [showManifest, setShowManifest] = useState(false);
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
      setActiveId((current) => current || nextManifest.files[0]?.id || "");
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
        setActiveId(nextManifest.files[0]?.id || "");
        setNotice(null);
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

    setIsPublishing(true);
    setError(null);
    setNotice(null);
    setPublishJob(null);
    try {
      let current = await startAdminSpeechPublish(
        dirtyFiles.map((file) => ({
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
                Local character admin
              </div>
              <h1 className="mt-2 text-2xl font-semibold">Характеры персонажей</h1>
            </div>
            <Badge variant={dirtyIds.length ? "default" : "secondary"}>
              {dirtyIds.length ? `${dirtyIds.length} изменено` : "чисто"}
            </Badge>
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

          <ScrollArea className="mt-4 h-[calc(100vh-232px)] min-h-[320px]">
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
                disabled={!manifest || hasValidationError || isSaving || isPublishing}
              >
                {isPublishing ? (
                  <RefreshCw className="size-4 animate-spin" />
                ) : (
                  <Rocket className="size-4" />
                )}
                Опубликовать
              </Button>
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

          <Tabs defaultValue="editor" className="mt-4">
            <div className="flex items-center justify-between gap-3">
              <TabsList>
                <TabsTrigger value="editor">Редактор</TabsTrigger>
                <TabsTrigger value="details">Файл</TabsTrigger>
              </TabsList>
              <Label className="flex items-center gap-2 text-sm text-muted-foreground">
                <Switch checked={showManifest} onCheckedChange={setShowManifest} />
                Манифест
              </Label>
            </div>

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
