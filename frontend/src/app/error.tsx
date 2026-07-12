"use client";

import { ErrorNotice } from "@/components/ErrorNotice";
import { presentError } from "@/lib/errorPresentation";

export default function AppError({ error, reset }: { error: Error; reset: () => void }) {
  const presented = presentError(error, "Не получилось открыть экран. Попробуйте ещё раз.");
  return (
    <main className="tma-screen grid place-items-center bg-[var(--paper)] px-6 text-center text-[var(--ink)]">
      <div className="grid max-w-sm gap-4">
        <h1 className="text-balance text-2xl font-semibold">Экран не открылся</h1>
        <ErrorNotice error={presented} className="text-pretty text-sm text-[var(--ink-muted)]" />
        <button type="button" className="rounded-lg bg-[var(--ink)] px-4 py-3 text-[var(--paper)]" onClick={reset}>
          Попробовать снова
        </button>
      </div>
    </main>
  );
}
