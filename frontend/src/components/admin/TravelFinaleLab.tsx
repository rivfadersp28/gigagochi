"use client";

import { Film, LoaderCircle, RefreshCw, Sparkles, Upload } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  compileTravelFinalePrompt,
  fetchTravelFinale,
  fetchTravelFinales,
  finaleAssetUrl,
  generateTravelFinaleVideo,
  importTravelFinale,
  type TravelFinaleAttempt,
  type TravelFinaleDetail,
  type TravelFinaleSummary,
} from "@/lib/travelFinaleApi";
import type { InteractiveTravelState } from "@/lib/types";

const DEFAULT_REFERENCE_BASE_URL = "https://gigagochi.serega.works";

function errorText(value: unknown) {
  return value instanceof Error ? value.message : String(value);
}

export function TravelFinaleLab() {
  const [items, setItems] = useState<TravelFinaleSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<TravelFinaleDetail | null>(null);
  const [direction, setDirection] = useState("");
  const [videoPrompt, setVideoPrompt] = useState("");
  const [referenceBaseUrl, setReferenceBaseUrl] = useState(DEFAULT_REFERENCE_BASE_URL);
  const [importValue, setImportValue] = useState("");
  const [busy, setBusy] = useState<"load" | "import" | "prompt" | "video" | null>("load");
  const [error, setError] = useState<string | null>(null);

  async function loadList(preferredId?: string) {
    setBusy("load");
    setError(null);
    try {
      const nextItems = await fetchTravelFinales();
      setItems(nextItems);
      const nextId = preferredId || selectedId || nextItems[0]?.travelId || "";
      setSelectedId(nextId);
      if (nextId) {
        const nextDetail = await fetchTravelFinale(nextId);
        setDetail(nextDetail);
        setDirection(nextDetail.defaultDirection);
        setVideoPrompt(nextDetail.attempts[0]?.prompt ?? "");
      } else {
        setDetail(null);
      }
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    // Initial remote state is intentionally loaded after hydration.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadList();
    // The lab loads once; later refreshes are explicit and preserve textarea drafts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function selectTravel(travelId: string) {
    setSelectedId(travelId);
    setBusy("load");
    setError(null);
    try {
      const nextDetail = await fetchTravelFinale(travelId);
      setDetail(nextDetail);
      setDirection(nextDetail.defaultDirection);
      setVideoPrompt(nextDetail.attempts[0]?.prompt ?? "");
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(null);
    }
  }

  async function handleImport() {
    setBusy("import");
    setError(null);
    try {
      const parsed = JSON.parse(importValue) as {
        travel?: InteractiveTravelState;
      } & Partial<InteractiveTravelState>;
      const travel = (parsed.travel ?? parsed) as InteractiveTravelState;
      await importTravelFinale(travel);
      setImportValue("");
      await loadList(travel.travelId);
    } catch (caught) {
      setError(errorText(caught));
      setBusy(null);
    }
  }

  async function handleCompilePrompt() {
    if (!selectedId) return;
    setBusy("prompt");
    setError(null);
    try {
      setVideoPrompt(await compileTravelFinalePrompt(selectedId, direction));
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(null);
    }
  }

  async function handleGenerateVideo() {
    if (!selectedId) return;
    setBusy("video");
    setError(null);
    try {
      const nextAttempt = await generateTravelFinaleVideo(
        selectedId,
        videoPrompt,
        referenceBaseUrl,
      );
      setDetail((current) =>
        current
          ? {
              ...current,
              attempts: [
                nextAttempt,
                ...current.attempts.filter((item) => item.id !== nextAttempt.id),
              ],
            }
          : current,
      );
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-5 py-8 text-foreground">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-sm text-muted-foreground">Local lab · Seedance 2.0 · 15 сек · 9:16</p>
          <h1 className="text-balance text-3xl font-semibold">Финальный ролик путешествия</h1>
        </div>
        <Button variant="outline" onClick={() => void loadList()} disabled={busy !== null}>
          <RefreshCw className={busy === "load" ? "animate-spin" : ""} /> Обновить
        </Button>
      </header>

      {error ? <div role="alert" className="rounded-xl border border-destructive/40 bg-destructive/10 p-4 text-sm">{error}</div> : null}

      <section className="grid gap-4 rounded-2xl border border-border bg-card p-5 lg:grid-cols-[1fr_1.4fr]">
        <div className="space-y-3">
          <label className="text-sm font-medium" htmlFor="travel-finale-select">Путешествие</label>
          <select
            id="travel-finale-select"
            className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            value={selectedId}
            onChange={(event) => void selectTravel(event.target.value)}
            disabled={busy !== null}
          >
            <option value="">Выбери snapshot</option>
            {items.map((item) => (
              <option key={item.travelId} value={item.travelId}>
                {item.owner.firstName || item.owner.username || item.owner.telegramId || "Без имени"} · {item.title} · {item.videoCount} видео
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-3">
          <label className="text-sm font-medium" htmlFor="travel-finale-import">Импорт JSON из localStorage</label>
          <div className="flex gap-2">
            <input
              id="travel-finale-import"
              className="h-10 min-w-0 flex-1 rounded-md border border-input bg-background px-3 text-sm"
              value={importValue}
              onChange={(event) => setImportValue(event.target.value)}
              placeholder='{"travel": {...}}'
            />
            <Button onClick={() => void handleImport()} disabled={!importValue.trim() || busy !== null}>
              <Upload /> Импорт
            </Button>
          </div>
        </div>
      </section>

      {detail ? (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(340px,0.75fr)]">
          <div className="space-y-6">
            <section className="space-y-3 rounded-2xl border border-border bg-card p-5">
              <h2 className="text-balance text-lg font-semibold">Собранный сюжет</h2>
              <pre className="max-h-[36rem] overflow-auto whitespace-pre-wrap rounded-xl bg-muted/40 p-4 text-sm leading-6 text-muted-foreground">{detail.story}</pre>
            </section>

            <section className="space-y-4 rounded-2xl border border-border bg-card p-5">
              <h2 className="text-balance text-lg font-semibold">Режиссёрский промпт</h2>
              <label className="sr-only" htmlFor="travel-finale-direction">
                Режиссёрский промпт
              </label>
              <textarea id="travel-finale-direction" className="min-h-48 w-full rounded-xl border border-input bg-background p-4 text-sm leading-6" value={direction} onChange={(event) => setDirection(event.target.value)} />
              <Button onClick={() => void handleCompilePrompt()} disabled={!direction.trim() || busy !== null}>
                {busy === "prompt" ? <LoaderCircle className="animate-spin" /> : <Sparkles />} Собрать video prompt
              </Button>
            </section>

            <section className="space-y-4 rounded-2xl border border-border bg-card p-5">
              <h2 className="text-balance text-lg font-semibold">Video prompt</h2>
              <label className="sr-only" htmlFor="travel-finale-video-prompt">
                Video prompt
              </label>
              <textarea id="travel-finale-video-prompt" className="min-h-72 w-full rounded-xl border border-input bg-background p-4 font-mono text-sm leading-6" value={videoPrompt} onChange={(event) => setVideoPrompt(event.target.value)} />
              <label className="block text-sm font-medium" htmlFor="reference-base-url">Публичная база исходных видео</label>
              <input id="reference-base-url" className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" value={referenceBaseUrl} onChange={(event) => setReferenceBaseUrl(event.target.value)} />
              <Button onClick={() => void handleGenerateVideo()} disabled={!videoPrompt.trim() || busy !== null}>
                {busy === "video" ? <LoaderCircle className="animate-spin" /> : <Film />} Сгенерировать 15 секунд
              </Button>
            </section>
          </div>

          <aside className="space-y-4">
            <h2 className="text-balance text-lg font-semibold">Попытки</h2>
            {detail.attempts.length ? detail.attempts.map((item: TravelFinaleAttempt) => (
              <article key={item.id} className="space-y-3 rounded-2xl border border-border bg-card p-4">
                <video className="aspect-[9/16] w-full rounded-xl bg-black object-cover" src={finaleAssetUrl(item.videoUrl)} controls playsInline />
                <div className="text-xs text-muted-foreground">{item.model} · {item.durationSeconds} сек · {new Date(item.createdAt).toLocaleString("ru-RU")}</div>
                <details><summary className="cursor-pointer text-sm">Промпт</summary><pre className="mt-3 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">{item.prompt}</pre></details>
              </article>
            )) : <p className="rounded-2xl border border-dashed border-border p-5 text-sm text-muted-foreground">Здесь появятся сгенерированные ролики.</p>}
          </aside>
        </div>
      ) : busy === null ? (
        <p className="rounded-2xl border border-dashed border-border p-8 text-center text-muted-foreground">Завершённых snapshot пока нет.</p>
      ) : null}
    </main>
  );
}
