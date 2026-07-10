"use client";

import { useEffect, useLayoutEffect, useState, type RefObject } from "react";

const FIGMA_SCREEN_HEIGHT = 874;
const FIGMA_KEYBOARD_VISIBLE_HEIGHT = 542;
const CONVERSATION_INPUT_REST_BOTTOM = 57;
const CONVERSATION_KEYBOARD_GAP = 17;
const CONVERSATION_FOCUS_PRIME_MS = 600;

function estimatedKeyboardOffset(sceneHeight: number) {
  const visibleHeight = Math.round(
    sceneHeight * (FIGMA_KEYBOARD_VISIBLE_HEIGHT / FIGMA_SCREEN_HEIGHT),
  );

  return Math.min(
    0,
    visibleHeight
      - sceneHeight
      + CONVERSATION_INPUT_REST_BOTTOM
      - CONVERSATION_KEYBOARD_GAP,
  );
}

export function useConversationKeyboardOffset(
  active: boolean,
  sceneRef: RefObject<HTMLElement | null>,
) {
  const [offsetY, setOffsetY] = useState(0);

  useLayoutEffect(() => {
    if (!active) {
      return;
    }

    const scene = sceneRef.current;
    if (!scene) {
      return;
    }

    const inputPanel = scene.querySelector<HTMLElement>(".conversation-input-panel");
    if (!inputPanel) {
      return;
    }

    const sceneHeight = scene.clientHeight || window.innerHeight || FIGMA_SCREEN_HEIGHT;
    inputPanel.style.setProperty("transition", "none");
    inputPanel.style.setProperty(
      "transform",
      `translate3d(0, ${estimatedKeyboardOffset(sceneHeight)}px, 0)`,
    );

    const releasePrimeTimeoutId = window.setTimeout(() => {
      inputPanel.style.removeProperty("transition");
      inputPanel.style.removeProperty("transform");
    }, CONVERSATION_FOCUS_PRIME_MS);

    return () => {
      window.clearTimeout(releasePrimeTimeoutId);
      inputPanel.style.removeProperty("transition");
      inputPanel.style.removeProperty("transform");
    };
  }, [active, sceneRef]);

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
