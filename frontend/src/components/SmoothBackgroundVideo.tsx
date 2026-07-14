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

function revealAfterDecodedFrame(video: HTMLVideoElement, reveal: () => void) {
  let revealed = false;
  const finish = () => {
    if (revealed) {
      return;
    }
    revealed = true;
    reveal();
  };

  if (typeof video.requestVideoFrameCallback === "function") {
    video.requestVideoFrameCallback(finish);
    window.setTimeout(finish, 250);
    return;
  }

  window.requestAnimationFrame(() => window.requestAnimationFrame(finish));
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

  useEffect(() => {
    latestSrcRef.current = src;
    setFailedSrc(null);
    setLayers((current) =>
      current.some((layer) => layer.src === src)
        ? current
        : [...current, { src, ready: false }],
    );
  }, [src]);

  const latestLayer = layers.find((layer) => layer.src === src);
  const hasVisibleLayer = layers.some(
    (layer) => layer.ready && (layer.src !== src || revealWhenReady),
  );

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
          autoPlay
          loop
          muted
          playsInline
          preload="auto"
          onLoadedData={(event) => {
            const video = event.currentTarget;
            revealAfterDecodedFrame(video, () => {
              if (latestSrcRef.current !== layer.src && !layer.ready) {
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
          }}
          onError={() => {
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
