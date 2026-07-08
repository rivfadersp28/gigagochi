"use client";

/* eslint-disable @next/next/no-img-element */
import { X } from "lucide-react";
import type { CSSProperties } from "react";

import type { GenerateTravelResponse } from "@/lib/types";

type TravelSceneCardStyle = CSSProperties & {
  "--travel-card-rotation": string;
  "--travel-card-offset-x": string;
};

type TravelStoryOverlayProps = {
  result: GenerateTravelResponse;
  onClose: () => void;
};

const TRAVEL_CARD_ROTATIONS = [5, -5, 4, -4, 3, -3, 5] as const;

export function TravelStoryOverlay({ result, onClose }: TravelStoryOverlayProps) {
  const imagesBySceneIndex = new Map(
    result.images.map((image) => [image.sceneIndex, image.imageUrl]),
  );
  const visibleScenes = result.story.scenes
    .map((scene) => ({
      scene,
      imageUrl: imagesBySceneIndex.get(scene.index),
    }))
    .filter((item): item is { scene: typeof item.scene; imageUrl: string } =>
      Boolean(item.imageUrl),
    );

  return (
    <section className="travel-story-overlay" aria-label={result.story.title}>
      <div className="travel-story-phone">
        <button
          type="button"
          className="travel-story-close"
          aria-label="Закрыть путешествие"
          onClick={onClose}
        >
          <X className="size-[22px]" aria-hidden="true" />
        </button>

        <div className="travel-story-stack">
          {visibleScenes.map(({ scene, imageUrl }, index) => {
            const slotStyle: TravelSceneCardStyle = {
              "--travel-card-rotation": `${
                TRAVEL_CARD_ROTATIONS[index % TRAVEL_CARD_ROTATIONS.length]
              }deg`,
              "--travel-card-offset-x": index % 2 === 0 ? "17.08px" : "28.02px",
            };

            return (
              <article
                key={`${result.travelId}-${scene.index}`}
                className="travel-scene-card-slot"
                style={slotStyle}
                aria-label={`${scene.title}. ${scene.text}`}
              >
                <div className="travel-scene-card">
                  <img src={imageUrl} alt={scene.title} draggable={false} />
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
