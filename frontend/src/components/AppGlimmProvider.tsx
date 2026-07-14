"use client";

import { GlimmProvider } from "glimm/react";

export function AppGlimmProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <GlimmProvider
      palette={{
        a: [0, 0.95, 0.75],
        b: [0, 0.04, 0.08],
        c: [0.5, 0.5, 0.5],
        d: [0.24, 0.98, 0.55],
      }}
      direction="ttb"
      brightness={1.2}
      sweepMs={800}
      outroMs={350}
      midpoint={0.5}
      reducedMotion="instant"
    >
      {children}
    </GlimmProvider>
  );
}
