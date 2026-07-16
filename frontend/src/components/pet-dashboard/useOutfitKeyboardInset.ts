"use client";

import { useEffect, useState, type RefObject } from "react";

const REST_INSET = 28;
const KEYBOARD_GAP = 27;

export function useOutfitKeyboardInset(
  active: boolean,
  sceneRef: RefObject<HTMLElement | null>,
) {
  const [inset, setInset] = useState(REST_INSET);

  useEffect(() => {
    if (!active) return;
    let animationFrameId: number | null = null;

    function update() {
      animationFrameId = null;
      const sceneHeight = sceneRef.current?.clientHeight || window.innerHeight;
      const viewport = window.visualViewport;
      const visibleBottom = viewport
        ? viewport.offsetTop + viewport.height
        : window.innerHeight;
      const nextInset = Math.max(
        REST_INSET,
        Math.round(sceneHeight - visibleBottom + KEYBOARD_GAP),
      );
      setInset((current) => current === nextInset ? current : nextInset);
    }

    function scheduleUpdate() {
      if (animationFrameId === null) {
        animationFrameId = window.requestAnimationFrame(update);
      }
    }

    scheduleUpdate();
    window.addEventListener("resize", scheduleUpdate, { passive: true });
    window.visualViewport?.addEventListener("resize", scheduleUpdate, { passive: true });
    window.visualViewport?.addEventListener("scroll", scheduleUpdate, { passive: true });
    return () => {
      if (animationFrameId !== null) window.cancelAnimationFrame(animationFrameId);
      window.removeEventListener("resize", scheduleUpdate);
      window.visualViewport?.removeEventListener("resize", scheduleUpdate);
      window.visualViewport?.removeEventListener("scroll", scheduleUpdate);
    };
  }, [active, sceneRef]);

  return active ? inset : REST_INSET;
}
