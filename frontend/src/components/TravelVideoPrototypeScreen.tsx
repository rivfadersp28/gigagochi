"use client";

import { Check, Film, Image as ImageIcon, PenLine, Route } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { SmoothBackgroundVideo } from "@/components/SmoothBackgroundVideo";
import { ScreenAppBar } from "@/components/ScreenAppBar";
import { getTravelVideoPrototype, startTravelVideoPrototype } from "@/lib/api";
import { presentError } from "@/lib/errorPresentation";
import {
  clearTravelVideoPrototypeRequest,
  prepareTravelVideoPrototypeRequest,
} from "@/lib/pendingTravelVideoPrototype";
import { setTelegramBackgroundColor, useTelegramBackButton } from "@/lib/telegram";
import { APP_BACKGROUND_COLOR } from "@/lib/theme";
import type { TravelVideoPrototype, TravelVideoPrototypeStatus } from "@/lib/types";
import { useLocalPetState } from "@/lib/useLocalPetState";

import styles from "./TravelVideoPrototypeScreen.module.css";

const PROMPT_MAX_LENGTH = 1000;
const POLL_INTERVAL_MS = 3_000;
const ENTRY_BACKGROUND_VIDEO = "/figma/travel-entry-bg.mp4?ping_pong_v=20260714-2";
const JOB_ID_PATTERN = /^travel-video-prototype-[a-f0-9]{32}$/u;

const GENERATION_STEPS: Array<{
  status: TravelVideoPrototypeStatus;
  label: string;
  icon: typeof PenLine;
}> = [
  { status: "writing", label: "Пишем сценарий", icon: PenLine },
  { status: "illustrating", label: "Собираем три сцены", icon: ImageIcon },
  { status: "animating", label: "Оживляем", icon: Film },
];

type TravelVideoPrototypeScreenProps = {
  petId: string;
};

function storageKey(petId: string) {
  return `travel-video-prototype:${petId}:active-job`;
}

function activeStepIndex(status: TravelVideoPrototypeStatus) {
  if (status === "queued" || status === "writing") return 0;
  if (status === "illustrating") return 1;
  return 2;
}

export function TravelVideoPrototypeScreen({ petId }: TravelVideoPrototypeScreenProps) {
  const router = useRouter();
  const localPet = useLocalPetState();
  const [prompt, setPrompt] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [prototype, setPrototype] = useState<TravelVideoPrototype | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pet = localPet.pet?.petId === petId ? localPet.pet : null;

  const goBack = useCallback(() => router.push(`/pet/${petId}`), [petId, router]);
  useTelegramBackButton(goBack);

  useEffect(() => {
    setTelegramBackgroundColor("#101713");
    return () => setTelegramBackgroundColor(APP_BACKGROUND_COLOR);
  }, []);

  useEffect(() => {
    const storedJobId = window.localStorage.getItem(storageKey(petId));
    let cancelled = false;
    window.queueMicrotask(() => {
      if (!cancelled && storedJobId && JOB_ID_PATTERN.test(storedJobId)) {
        setJobId(storedJobId);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [petId]);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const next = await getTravelVideoPrototype(jobId);
        if (cancelled) return;
        setPrototype(next);
        setError(null);
        if (next.status === "ready" || next.status === "failed") {
          return;
        }
        timer = window.setTimeout(poll, POLL_INTERVAL_MS);
      } catch (caught) {
        if (cancelled) return;
        setError(presentError(caught, "Не удалось проверить готовность путешествия.").message);
        timer = window.setTimeout(poll, POLL_INTERVAL_MS * 2);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId]);

  const isGenerating = Boolean(
    prototype && prototype.status !== "ready" && prototype.status !== "failed",
  ) || isStarting;
  const stepIndex = activeStepIndex(prototype?.status ?? "queued");
  const progress = useMemo(() => `${Math.max(8, (stepIndex + 1) * 33)}%`, [stepIndex]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!pet || !prompt.trim() || isStarting) return;
    setIsStarting(true);
    setError(null);
    setPrototype(null);
    try {
      const requestKey = prepareTravelVideoPrototypeRequest(petId, prompt);
      const started = await startTravelVideoPrototype(prompt, pet, requestKey);
      clearTravelVideoPrototypeRequest(petId, requestKey);
      window.localStorage.setItem(storageKey(petId), started.jobId);
      setPrototype(started);
      setJobId(started.jobId);
    } catch (caught) {
      setError(presentError(caught, "Не удалось отправить персонажа в путешествие.").message);
    } finally {
      setIsStarting(false);
    }
  }

  function startAnother() {
    window.localStorage.removeItem(storageKey(petId));
    setJobId(null);
    setPrototype(null);
    setPrompt("");
    setError(null);
  }

  const ready = prototype?.status === "ready" ? prototype : null;
  const failed = prototype?.status === "failed" ? prototype : null;

  return (
    <main className={styles.viewport} aria-busy={isGenerating}>
      <SmoothBackgroundVideo src={ENTRY_BACKGROUND_VIDEO} className={styles.background} />
      <div className={styles.colorWash} aria-hidden="true" />
      <div className={styles.grain} aria-hidden="true" />

      <ScreenAppBar
        className={styles.header}
        onBack={goBack}
        title={(
          <div className={styles.eyebrow}>
            <Route aria-hidden="true" />
            <span>Путешествие</span>
          </div>
        )}
      />

      <section className={styles.content}>
        {localPet.status === "loading" ? (
          <div className={styles.stateCard}>Ищем персонажа…</div>
        ) : !pet ? (
          <div className={styles.stateCard}>
            <h1>Персонаж не найден</h1>
            <button type="button" onClick={goBack}>Вернуться</button>
          </div>
        ) : ready ? (
          <article className={styles.result}>
            <div className={styles.videoFrame}>
              <video
                key={ready.videoUrl}
                src={ready.videoUrl}
                poster={ready.imageUrl}
                autoPlay
                loop
                muted
                playsInline
                controls
              />
            </div>
            <div className={styles.storyCard}>
              <div className={styles.readyLabel}><Check aria-hidden="true" /> Маршрут пройден</div>
              <h1>{ready.title}</h1>
              <p>{ready.scenario}</p>
            </div>
            <button type="button" className={styles.secondaryButton} onClick={startAnother}>
              Новое путешествие
            </button>
          </article>
        ) : isGenerating || (prototype && !failed) ? (
          <div className={styles.generatingCard}>
            <div className={styles.routeMark}><Route aria-hidden="true" /></div>
            <h1>Готовим путешествие</h1>
            <p>{prototype?.prompt || prompt}</p>
            <div className={styles.progressTrack} aria-hidden="true">
              <span style={{ width: progress }} />
            </div>
            <ol className={styles.steps}>
              {GENERATION_STEPS.map((step, index) => {
                const Icon = step.icon;
                const complete = index < stepIndex;
                const active = index === stepIndex;
                return (
                  <li key={step.status} data-active={active} data-complete={complete}>
                    <span>{complete ? <Check aria-hidden="true" /> : <Icon aria-hidden="true" />}</span>
                    {step.label}
                  </li>
                );
              })}
            </ol>
          </div>
        ) : (
          <form className={styles.form} onSubmit={handleSubmit}>
            <div className={styles.intro}>
              <span>Новый маршрут для {pet.name?.trim() || "персонажа"}</span>
              <h1>Куда отправимся?</h1>
              <p>Опиши место, событие или целую идею — мы превратим её в короткую сцену.</p>
            </div>
            <label htmlFor="travel-video-prompt" className="sr-only">Идея путешествия</label>
            <textarea
              id="travel-video-prompt"
              value={prompt}
              onChange={(event) => {
                setPrompt(event.target.value);
                setError(null);
              }}
              className={styles.prompt}
              maxLength={PROMPT_MAX_LENGTH}
              placeholder="Например: на ночной рынок духов, где нужно найти потерянный фонарь…"
              autoFocus
            />
            <div className={styles.formMeta}>
              <span>{prompt.length}/{PROMPT_MAX_LENGTH}</span>
              <span>Вертикальный 9:16 · до 15 секунд</span>
            </div>
            <button
              type="submit"
              className={styles.primaryButton}
              disabled={!prompt.trim() || isStarting}
            >
              <Route aria-hidden="true" />
              Отправить в путешествие
            </button>
          </form>
        )}

        {failed ? (
          <div className={styles.errorCard} role="alert">
            <strong>Путешествие сорвалось</strong>
            <span>{failed.error || "Попробуй другой маршрут."}</span>
            <button type="button" onClick={startAnother}>Попробовать ещё раз</button>
          </div>
        ) : error ? (
          <div className={styles.errorCard} role="alert">{error}</div>
        ) : null}
      </section>
    </main>
  );
}
