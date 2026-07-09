"use client";

import {
  AlertCircle,
  Check,
  Database,
  FileJson,
  RefreshCw,
  Rocket,
  Save,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
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

import {
  AuxiliaryDataEditor,
  CharacterBibleTemplateEditor,
  SpeechRuntimeEditor,
  ToneRuntimeEditor,
} from "./SpeechAdminEditors";

type Drafts = Record<string, string>;
type ValidationState = Record<string, string | null>;

const PUBLISH_POLL_INTERVAL_MS = 1500;
const SPEECH_RUNTIME_FILE_ID = "speech_runtime";
const TONE_RUNTIME_FILE_ID = "tone_runtime";
const CHARACTER_BIBLE_TEMPLATE_FILE_ID = "character_bible_template";

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
