"use client";

import { useEffect, useState, type RefObject } from "react";

const FIGMA_SCREEN_HEIGHT = 874;
const CONVERSATION_INPUT_REST_BOTTOM = 57;
const CONVERSATION_KEYBOARD_GAP = 17;

export function useConversationKeyboardOffset(
  active: boolean,
  sceneRef: RefObject<HTMLElement | null>,
) {
  const [offsetY, setOffsetY] = useState(0);

  useEffect(() => {
    if (!active) {
      return;
    }

    let animationFrameId: number | null = null;

    function updateOffset() {
      animationFrameId = null;
      const sceneHeight =
        sceneRef.current?.clientHeight || window.innerHeight || FIGMA_SCREEN_HEIGHT;
      const visualViewport = window.visualViewport;
      const visibleBottom = visualViewport
        ? visualViewport.offsetTop + visualViewport.height
        : window.innerHeight;
      const nextOffset = Math.min(
        0,
        Math.round(
          visibleBottom -
            sceneHeight +
            CONVERSATION_INPUT_REST_BOTTOM -
            CONVERSATION_KEYBOARD_GAP,
        ),
      );

      setOffsetY((currentOffset) =>
        currentOffset === nextOffset ? currentOffset : nextOffset,
      );
    }

    function scheduleUpdate() {
      if (animationFrameId === null) {
        animationFrameId = window.requestAnimationFrame(updateOffset);
      }
    }

    scheduleUpdate();
    window.addEventListener("resize", scheduleUpdate, { passive: true });
    window.visualViewport?.addEventListener("resize", scheduleUpdate, { passive: true });
    window.visualViewport?.addEventListener("scroll", scheduleUpdate, { passive: true });

    return () => {
      if (animationFrameId !== null) {
        window.cancelAnimationFrame(animationFrameId);
      }
      window.removeEventListener("resize", scheduleUpdate);
      window.visualViewport?.removeEventListener("resize", scheduleUpdate);
      window.visualViewport?.removeEventListener("scroll", scheduleUpdate);
    };
  }, [active, sceneRef]);

  return active ? offsetY : 0;
}
