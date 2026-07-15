"use client";

/* eslint-disable @next/next/no-img-element */
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { CSSProperties } from "react";
import { useLayoutEffect, useRef, useState } from "react";

import type { StoryHistoryItem } from "./storyHistory";

type StoryCardStyle = CSSProperties & {
  "--story-card-rotation": string;
  "--story-card-y": string;
};

type TravelStoryOverlayProps = {
  stories: StoryHistoryItem[];
  onClose: () => void;
};

const STORY_CARD_ROTATIONS = [1.7, -4.64, 4.1, -3.2, 2.8, -2.4] as const;

export function TravelStoryOverlay({ stories, onClose }: TravelStoryOverlayProps) {
  const [phoneNode, setPhoneNode] = useState<HTMLDivElement | null>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const cardRefs = useRef<Array<HTMLElement | null>>([]);
  const scrollStepRef = useRef(1);

  useLayoutEffect(() => {
    const phone = phoneNode;
    const track = trackRef.current;
    if (!phone || !track || !stories.length) {
      return;
    }

    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)") ?? null;
    let animationFrameId = 0;
    let wheelSnapTimeoutId = 0;
    let cardMetrics = {
      activeTop: 0,
      stackTop: 0,
      stackGap: 0,
      exitTop: 0,
    };
    let pointerStart: {
      pointerId: number;
      scrollTop: number;
      y: number;
    } | null = null;

    const maxStoryIndex = Math.max(stories.length - 1, 0);

    const scrollToStory = (index: number) => {
      const targetIndex = Math.max(0, Math.min(maxStoryIndex, index));
      phone.scrollTo({
        top: targetIndex * scrollStepRef.current,
        behavior: reducedMotion?.matches ? "auto" : "smooth",
      });
    };

    const updateCardPositions = () => {
      animationFrameId = 0;
      const rawPosition = phone.scrollTop / scrollStepRef.current;
      const position = reducedMotion?.matches ? Math.round(rawPosition) : rawPosition;
      const { activeTop, stackTop, stackGap, exitTop } = cardMetrics;

      cardRefs.current.forEach((card, index) => {
        if (!card) {
          return;
        }

        const delta = index - position;
        const y = delta < 0
          ? activeTop + (exitTop - activeTop) * Math.min(-delta, 1)
          : activeTop
            + (stackTop - activeTop) * Math.min(delta, 1)
            + stackGap * Math.max(0, Math.min(delta - 1, 2));
        card.style.setProperty("--story-card-y", `${y}px`);
        card.style.opacity = delta < -1 || delta > 3 ? "0" : "1";
        card.style.zIndex = String(100 - Math.round(Math.abs(delta) * 10));
      });
    };

    const scheduleCardUpdate = () => {
      if (!animationFrameId) {
        animationFrameId = window.requestAnimationFrame(updateCardPositions);
      }
    };

    const updateMetrics = () => {
      // Batch layout reads here. Scroll frames below only read scrollTop and
      // write compositor-friendly transform/opacity values for each card.
      const width = phone.clientWidth;
      const height = phone.clientHeight;
      const firstCard = cardRefs.current[0];
      const cardHeight = firstCard?.offsetHeight ?? Math.min((width * 526) / 402, 526);
      const activeTop = Math.min((width * 142) / 402, 142);
      const stackPeek = Math.min((width * 141) / 402, 141);
      const step = Math.max(Math.round(height * 0.58), 1);
      cardMetrics = {
        activeTop,
        stackTop: Math.max(activeTop + cardHeight * 0.7, height - stackPeek),
        stackGap: Math.min((width * 69) / 402, 69),
        exitTop: -cardHeight - 64,
      };
      scrollStepRef.current = step;
      track.style.height = `${height + step * Math.max(stories.length - 1, 0)}px`;
      updateCardPositions();
    };

    const handlePointerDown = (event: PointerEvent) => {
      if (event.pointerType === "mouse" && event.button !== 0) {
        return;
      }

      pointerStart = {
        pointerId: event.pointerId,
        scrollTop: phone.scrollTop,
        y: event.clientY,
      };
      phone.setPointerCapture?.(event.pointerId);
      event.preventDefault();
    };

    const handlePointerMove = (event: PointerEvent) => {
      if (!pointerStart || pointerStart.pointerId !== event.pointerId) {
        return;
      }

      phone.scrollTop = pointerStart.scrollTop + pointerStart.y - event.clientY;
      event.preventDefault();
    };

    const finishPointerGesture = (event: PointerEvent) => {
      if (!pointerStart || pointerStart.pointerId !== event.pointerId) {
        return;
      }

      const step = scrollStepRef.current;
      const distance = pointerStart.y - event.clientY;
      const startIndex = Math.round(pointerStart.scrollTop / step);
      const targetIndex = Math.abs(distance) >= 36
        ? startIndex + Math.sign(distance)
        : Math.round(phone.scrollTop / step);
      phone.releasePointerCapture?.(event.pointerId);
      pointerStart = null;
      scrollToStory(targetIndex);
      event.preventDefault();
    };

    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      phone.scrollTop += event.deltaY;
      window.clearTimeout(wheelSnapTimeoutId);
      wheelSnapTimeoutId = window.setTimeout(() => {
        scrollToStory(Math.round(phone.scrollTop / scrollStepRef.current));
      }, 120);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      const currentIndex = Math.round(phone.scrollTop / scrollStepRef.current);
      const direction = event.key === "ArrowDown" || event.key === "PageDown"
        ? 1
        : event.key === "ArrowUp" || event.key === "PageUp"
          ? -1
          : 0;

      if (direction) {
        event.preventDefault();
        scrollToStory(currentIndex + direction);
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        scrollToStory(event.key === "Home" ? 0 : maxStoryIndex);
      }
    };

    const resizeObserver = new ResizeObserver(updateMetrics);
    resizeObserver.observe(phone);
    phone.addEventListener("scroll", scheduleCardUpdate, { passive: true });
    phone.addEventListener("pointerdown", handlePointerDown);
    phone.addEventListener("pointermove", handlePointerMove);
    phone.addEventListener("pointerup", finishPointerGesture);
    phone.addEventListener("pointercancel", finishPointerGesture);
    phone.addEventListener("wheel", handleWheel, { passive: false });
    phone.addEventListener("keydown", handleKeyDown);
    reducedMotion?.addEventListener("change", scheduleCardUpdate);
    updateMetrics();

    return () => {
      phone.removeEventListener("scroll", scheduleCardUpdate);
      phone.removeEventListener("pointerdown", handlePointerDown);
      phone.removeEventListener("pointermove", handlePointerMove);
      phone.removeEventListener("pointerup", finishPointerGesture);
      phone.removeEventListener("pointercancel", finishPointerGesture);
      phone.removeEventListener("wheel", handleWheel);
      phone.removeEventListener("keydown", handleKeyDown);
      reducedMotion?.removeEventListener("change", scheduleCardUpdate);
      resizeObserver.disconnect();
      window.clearTimeout(wheelSnapTimeoutId);
      if (animationFrameId) {
        window.cancelAnimationFrame(animationFrameId);
      }
    };
  }, [phoneNode, stories.length]);

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Content id="travel-story-overlay" className="travel-story-overlay">
          <Dialog.Description className="sr-only">
            Архив сгенерированных событий питомца
          </Dialog.Description>
          <div className="travel-story-backdrop" aria-hidden="true" />

          <Dialog.Close asChild>
            <button type="button" className="travel-story-close" aria-label="Закрыть истории">
              <X className="size-[22px]" aria-hidden="true" />
            </button>
          </Dialog.Close>

          <div
            ref={setPhoneNode}
            className="travel-story-phone"
            tabIndex={0}
            aria-label="Истории: листайте вверх или вниз"
          >
            <div ref={trackRef} className="travel-story-scroll-track">
              <div className="travel-story-stage">
                <Dialog.Title asChild>
                  <h1 className="travel-story-title text-balance">
                    Истории
                  </h1>
                </Dialog.Title>

                {stories.length ? (
                  <div className="travel-story-deck" aria-live="polite">
                    {stories.map((story, index) => {
                      const cardStyle: StoryCardStyle = {
                        "--story-card-rotation": `${
                          STORY_CARD_ROTATIONS[index % STORY_CARD_ROTATIONS.length]
                        }deg`,
                        "--story-card-y": index === 0
                          ? "min(35.323vw, 142px)"
                          : `calc(var(--tma-viewport-height) - min(35.075vw, 141px) + ${
                            Math.min(index - 1, 2) * 69
                          }px)`,
                      };

                      return (
                        <article
                          key={story.id}
                          ref={(node) => {
                            cardRefs.current[index] = node;
                          }}
                          className="travel-story-card-shell"
                          style={cardStyle}
                          aria-label={`${story.title}. ${story.text}`}
                        >
                          <div
                            className={`travel-story-card ${
                              story.imageUrl ? "" : "travel-story-card--text-only"
                            }`}
                          >
                            <img
                              src="/figma/story-card-mask.svg"
                              alt=""
                              className="travel-story-card__paper-texture"
                              aria-hidden="true"
                              draggable={false}
                            />
                            {story.imageUrl ? (
                              <div className="travel-story-card__image-mask">
                                <img src={story.imageUrl} alt="" draggable={false} />
                              </div>
                            ) : null}
                            <p className="travel-story-card__text text-pretty">{story.text}</p>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                ) : (
                  <div className="travel-story-empty">
                    <img
                      src="/figma/story-card-mask.svg"
                      alt=""
                      className="travel-story-empty__paper-texture"
                      aria-hidden="true"
                      draggable={false}
                    />
                    <p className="travel-story-empty__title text-balance">Историй пока нет</p>
                    <p className="travel-story-empty__text text-pretty">
                      Они появятся после новых событий питомца.
                    </p>
                    <Dialog.Close asChild>
                      <button type="button" className="travel-story-empty__action">
                        Вернуться
                      </button>
                    </Dialog.Close>
                  </div>
                )}
              </div>

            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
