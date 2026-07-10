"use client";

/* eslint-disable @next/next/no-img-element */
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
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
};

type FeedFoodTokenStyle = CSSProperties & {
  "--feed-food-rotation": string;
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

    setDragState({
      pointerId: null,
      x: 0,
      y: 0,
      isDragging: false,
    });
  }

  const tokenStyle: FeedFoodTokenStyle = {
    "--feed-food-rotation": `${food.rotation}deg`,
    transform: `translate3d(${dragState.x}px, ${dragState.y}px, 0) rotate(var(--feed-food-rotation))`,
  };

  return (
    <button
      type="button"
      className={`feed-food-token ${dragState.isDragging ? "feed-food-token--dragging" : ""}`}
      style={tokenStyle}
      disabled={disabled}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={finishPointerDrag}
      onPointerCancel={finishPointerDrag}
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
