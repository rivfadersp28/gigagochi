"use client";

/* eslint-disable react-hooks/set-state-in-effect -- source changes append an incoming media layer while the decoded outgoing layer remains mounted */

import { useEffect, useRef, useState } from "react";

import styles from "./SmoothBackgroundVideo.module.css";

const VIDEO_FADE_MS = 220;

type VideoLayer = {
  src: string;
  ready: boolean;
};

type SmoothBackgroundVideoProps = {
  src: string;
  className?: string;
  onActiveVideoChange?: (video: HTMLVideoElement) => void;
  onError?: (src: string) => void;
  onReady?: (src: string) => void;
  revealWhenReady?: boolean;
};

function revealAfterDecodedFrame(video: HTMLVideoElement, reveal: () => void): () => void {
  let active = true;
  let videoFrameId: number | null = null;
  let timeoutId: number | null = null;
  const animationFrameIds: number[] = [];
  const cancelScheduledWork = () => {
    if (videoFrameId !== null && typeof video.cancelVideoFrameCallback === "function") {
      video.cancelVideoFrameCallback(videoFrameId);
      videoFrameId = null;
    }
    if (timeoutId !== null) {
      window.clearTimeout(timeoutId);
      timeoutId = null;
    }
    animationFrameIds.splice(0).forEach((id) => window.cancelAnimationFrame(id));
  };
  const finish = () => {
    if (!active) {
      return;
    }
    active = false;
    cancelScheduledWork();
    reveal();
  };

  if (typeof video.requestVideoFrameCallback === "function") {
    videoFrameId = video.requestVideoFrameCallback(finish);
    timeoutId = window.setTimeout(finish, 250);
  } else {
    animationFrameIds.push(window.requestAnimationFrame(() => {
      animationFrameIds.push(window.requestAnimationFrame(finish));
    }));
  }

  return () => {
    active = false;
    cancelScheduledWork();
  };
}

function usePrefersReducedMotion() {
  const [reducedMotion, setReducedMotion] = useState(() => (
    typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches
  ));

  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handleChange = () => setReducedMotion(query.matches);
    handleChange();
    query.addEventListener("change", handleChange);
    return () => query.removeEventListener("change", handleChange);
  }, []);

  return reducedMotion;
}

export function SmoothBackgroundVideo({
  src,
  className = "",
  onActiveVideoChange,
  onError,
  onReady,
  revealWhenReady = true,
}: SmoothBackgroundVideoProps) {
  const [layers, setLayers] = useState<VideoLayer[]>(() => [{ src, ready: false }]);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const latestSrcRef = useRef(src);
  const rootRef = useRef<HTMLDivElement>(null);
  const revealCleanupBySrcRef = useRef(new Map<string, () => void>());
  const reducedMotion = usePrefersReducedMotion();
  const previousReducedMotionRef = useRef(reducedMotion);

  useEffect(() => {
    const videos = rootRef.current?.querySelectorAll("video") ?? [];
    if (reducedMotion) {
      videos.forEach((video) => {
        if (!video.paused) {
          video.pause();
        }
      });
    } else if (previousReducedMotionRef.current) {
      videos.forEach((video) => {
        if (video.paused) {
          void video.play().catch(() => undefined);
        }
      });
    }
    previousReducedMotionRef.current = reducedMotion;
  }, [reducedMotion]);

  useEffect(() => () => {
    revealCleanupBySrcRef.current.forEach((cleanup) => cleanup());
    revealCleanupBySrcRef.current.clear();
  }, []);

  useEffect(() => {
    latestSrcRef.current = src;
    setFailedSrc(null);
    for (const layer of layers) {
      if (layer.ready || layer.src === src) {
        continue;
      }
      const video = [...(rootRef.current?.querySelectorAll("video") ?? [])].find(
        (candidate) => candidate.getAttribute("src") === layer.src,
      );
      if (video) {
        video.pause();
        video.removeAttribute("src");
        video.load();
      }
    }
    setLayers((current) => {
      const retained = current.filter((layer) => layer.ready || layer.src === src);
      return retained.some((layer) => layer.src === src)
        ? retained
        : [...retained, { src, ready: false }];
    });
  // `layers` is intentionally read only at the source-change boundary. Adding it
  // would rerun cleanup after readiness updates and stop the outgoing fade layer.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src]);

  const latestLayer = layers.find((layer) => layer.src === src);
  const hasVisibleLayer = layers.some(
    (layer) => layer.ready && (layer.src !== src || revealWhenReady),
  );

  useEffect(() => {
    const activeSources = new Set(layers.map((layer) => layer.src));
    revealCleanupBySrcRef.current.forEach((cleanup, layerSrc) => {
      if (!activeSources.has(layerSrc)) {
        cleanup();
        revealCleanupBySrcRef.current.delete(layerSrc);
      }
    });
  }, [layers]);

  useEffect(() => {
    if (!latestLayer?.ready || !revealWhenReady || layers.length === 1) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setLayers((current) => current.filter((layer) => layer.src === src));
    }, VIDEO_FADE_MS);
    return () => window.clearTimeout(timeoutId);
  }, [latestLayer?.ready, layers.length, revealWhenReady, src]);

  return (
    <div
      ref={rootRef}
      className={`${styles.root} ${hasVisibleLayer ? styles.rootReady : ""} ${
        failedSrc === src ? styles.rootFailed : ""
      } ${className}`}
      aria-hidden="true"
    >
      {layers.map((layer, index) => (
        <video
          key={layer.src}
          src={layer.src}
          className={`${styles.video} ${
            layer.ready && (layer.src !== src || revealWhenReady) ? styles.videoReady : ""
          }`}
          style={{ zIndex: index + 1 }}
          autoPlay={!reducedMotion}
          loop={!reducedMotion}
          muted
          playsInline
          preload="auto"
          onLoadedData={(event) => {
            const video = event.currentTarget;
            revealCleanupBySrcRef.current.get(layer.src)?.();
            const cleanup = revealAfterDecodedFrame(video, () => {
              revealCleanupBySrcRef.current.delete(layer.src);
              if (latestSrcRef.current !== layer.src && !layer.ready) {
                video.pause();
                setLayers((current) =>
                  current.filter((currentLayer) => currentLayer.src !== layer.src),
                );
                return;
              }
              setLayers((current) =>
                current.map((currentLayer) =>
                  currentLayer.src === layer.src
                    ? { ...currentLayer, ready: true }
                    : currentLayer,
                ),
              );
              onActiveVideoChange?.(video);
              onReady?.(layer.src);
            });
            revealCleanupBySrcRef.current.set(layer.src, cleanup);
          }}
          onError={() => {
            revealCleanupBySrcRef.current.get(layer.src)?.();
            revealCleanupBySrcRef.current.delete(layer.src);
            setLayers((current) => current.filter((currentLayer) => currentLayer.src !== layer.src));
            if (latestSrcRef.current === layer.src) {
              setFailedSrc(layer.src);
            }
            onError?.(layer.src);
          }}
        />
      ))}
      <div
        className={`${styles.shimmer} ${
          hasVisibleLayer || failedSrc === src ? styles.shimmerHidden : ""
        }`}
      />
    </div>
  );
}
