"use client";

import { GlimmProvider } from "glimm/react";

export function AppGlimmProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <GlimmProvider
      palette={{
        a: [0.43, 0.69, 0.47],
        b: [0.61, 0.34, 0.48],
        c: [0.5, 0.5, 0.5],
        d: [0.93, 0.67, 0.42],
      }}
      sweepMs={800}
      outroMs={350}
      midpoint={0.5}
      reducedMotion="instant"
    >
      {children}
    </GlimmProvider>
  );
}
