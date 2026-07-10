/* eslint-disable @next/next/no-img-element */

import styles from "./PetThinkingIndicator.module.css";

export const PET_THINKING_MIN_VISIBLE_MS = 1_000;

export const PET_THINKING_FRAME_SOURCES = [
  "/figma/thinking-frame-1.svg",
  "/figma/thinking-frame-2.svg",
  "/figma/thinking-frame-3.svg",
] as const;

export function PetThinkingIndicator() {
  const frameStyles = [styles.image1, styles.image2, styles.image3];

  return (
    <div
      className={styles.indicator}
      data-pet-thinking-indicator="true"
      role="status"
      aria-label="Персонаж думает"
    >
      {PET_THINKING_FRAME_SOURCES.map((source, index) => (
        <img
          key={source}
          src={source}
          alt=""
          className={`${styles.image} ${frameStyles[index]}`}
          data-pet-thinking-frame={index + 1}
          aria-hidden="true"
          draggable={false}
        />
      ))}
    </div>
  );
}
