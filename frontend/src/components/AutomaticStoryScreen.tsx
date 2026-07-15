"use client";

import { useEffect, useState } from "react";

import {
  getAutomaticInteractiveStory,
  type AutomaticInteractiveStory,
} from "@/lib/api";

import styles from "./AutomaticStoryScreen.module.css";

export function AutomaticStoryScreen({ token }: { token: string }) {
  const [story, setStory] = useState<AutomaticInteractiveStory | null>(null);
  const [choice, setChoice] = useState<0 | 1 | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getAutomaticInteractiveStory(token)
      .then((value) => active && setStory(value))
      .catch(() => active && setError("Не удалось открыть эту историю."));
    return () => { active = false; };
  }, [token]);

  if (error) {
    return <main className={styles.state}>{error}</main>;
  }
  if (!story) {
    return <main className={styles.state}>Открываем историю…</main>;
  }

  const videoUrl = choice === null
    ? story.situationVideoUrl
    : story.outcomeVideoUrls[choice];

  return (
    <main className={styles.viewport}>
      <section className={styles.card}>
        <video
          key={videoUrl}
          className={styles.video}
          src={videoUrl}
          autoPlay
          loop
          muted
          playsInline
        />
        <div className={styles.content}>
          <h1>{story.title}</h1>
          <p>{choice === null ? story.storyText : story.outcomes[choice]}</p>
          {choice === null ? (
            <>
              <h2>{story.question}</h2>
              <div className={styles.actions}>
                {story.choices.map((label, index) => (
                  <button key={label} type="button" onClick={() => setChoice(index as 0 | 1)}>
                    {label}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <button className={styles.secondary} type="button" onClick={() => setChoice(null)}>
              Выбрать другой вариант
            </button>
          )}
        </div>
      </section>
    </main>
  );
}
