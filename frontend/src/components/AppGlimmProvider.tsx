"use client";

import { GlimmProvider } from "glimm/react";

export function AppGlimmProvider({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <GlimmProvider
      palette="prism"
      sweepMs={800}
      outroMs={350}
      midpoint={0.5}
      reducedMotion="instant"
    >
      {children}
    </GlimmProvider>
  );
}
