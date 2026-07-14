"use client";

/* eslint-disable @next/next/no-img-element */
import type {
  AnimationEvent as ReactAnimationEvent,
  CSSProperties,
  PointerEvent as ReactPointerEvent,
} from "react";
import { useRef, useState } from "react";

import type { FoodId } from "@/lib/localPetFood";

export type FoodAsset = {
  id: FoodId;
  label: string;
  src: string;
  rotation: number;
};

type FoodDragState = {
  pointerId: number | null;
  x: number;
  y: number;
  isDragging: boolean;
  animationPhase: "idle" | "consuming" | "reappearing";
};

type FeedFoodTokenStyle = CSSProperties & {
  "--feed-food-rotation": string;
  "--feed-food-translate-x": string;
  "--feed-food-translate-y": string;
};

type DraggableFoodTokenProps = {
  food: FoodAsset;
  disabled: boolean;
  onDrop: (clientX: number, clientY: number, foodId: FoodId) => boolean;
  onActivate: (foodId: FoodId) => boolean;
};

export function DraggableFoodToken({
  food,
  disabled,
  onDrop,
  onActivate,
}: DraggableFoodTokenProps) {
  const [dragState, setDragState] = useState<FoodDragState>({
    pointerId: null,
    x: 0,
    y: 0,
    isDragging: false,
    animationPhase: "idle",
  });
  const dragOriginRef = useRef({ x: 0, y: 0 });
  const suppressClickRef = useRef(false);

  function handlePointerDown(event: ReactPointerEvent<HTMLButtonElement>) {
    if (disabled) {
      return;
    }

    dragOriginRef.current = {
      x: event.clientX,
      y: event.clientY,
    };
    suppressClickRef.current = false;
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragState({
      pointerId: event.pointerId,
      x: 0,
      y: 0,
      isDragging: true,
      animationPhase: "idle",
    });
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLButtonElement>) {
    if (dragState.pointerId !== event.pointerId) {
      return;
    }

    const nextX = event.clientX - dragOriginRef.current.x;
    const nextY = event.clientY - dragOriginRef.current.y;
    if (Math.hypot(nextX, nextY) > 6) {
      suppressClickRef.current = true;
    }
    setDragState((current) => ({
      ...current,
      x: nextX,
      y: nextY,
    }));
  }

  function finishPointerDrag(event: ReactPointerEvent<HTMLButtonElement>) {
    if (dragState.pointerId !== event.pointerId) {
      return;
    }

    const didFeed = onDrop(event.clientX, event.clientY, food.id);
    suppressClickRef.current = suppressClickRef.current || didFeed;

    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }

    setDragState((current) => ({
      pointerId: null,
      x: didFeed ? current.x : 0,
      y: didFeed ? current.y : 0,
      isDragging: false,
      animationPhase: didFeed ? "consuming" : "idle",
    }));
  }

  function handleAnimationEnd(event: ReactAnimationEvent<HTMLButtonElement>) {
    if (event.target !== event.currentTarget) {
      return;
    }

    setDragState((current) => {
      if (current.animationPhase === "consuming") {
        return {
          ...current,
          x: 0,
          y: 0,
          animationPhase: "reappearing",
        };
      }
      if (current.animationPhase === "reappearing") {
        return {
          ...current,
          animationPhase: "idle",
        };
      }
      return current;
    });
  }

  const tokenStyle: FeedFoodTokenStyle = {
    "--feed-food-rotation": `${food.rotation}deg`,
    "--feed-food-translate-x": `${dragState.x}px`,
    "--feed-food-translate-y": `${dragState.y}px`,
  };

  const animationClass =
    dragState.animationPhase === "consuming"
      ? "feed-food-token--consuming"
      : dragState.animationPhase === "reappearing"
        ? "feed-food-token--reappearing"
        : "";

  return (
    <button
      type="button"
      className={`feed-food-token ${dragState.isDragging ? "feed-food-token--dragging" : ""} ${animationClass}`}
      style={tokenStyle}
      disabled={disabled}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={finishPointerDrag}
      onPointerCancel={finishPointerDrag}
      onAnimationEnd={handleAnimationEnd}
      onClick={(event) => {
        if (suppressClickRef.current) {
          suppressClickRef.current = false;
          event.preventDefault();
          return;
        }
        onActivate(food.id);
      }}
      aria-label={`Дать персонажу ${food.label}`}
    >
      <img src={food.src} alt="" draggable={false} />
    </button>
  );
}
