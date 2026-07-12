"use client";

import { presentError } from "@/lib/errorPresentation";

export default function GlobalError({ error, reset }: { error: Error; reset: () => void }) {
  const presented = presentError(error, "Приложение временно недоступно. Попробуйте ещё раз.");
  return (
    <html lang="ru">
      <body>
        <main style={{ minHeight: "100dvh", display: "grid", placeItems: "center", padding: 24, textAlign: "center" }}>
          <div>
            <h1>Приложение не открылось</h1>
            <p>{presented.message}</p>
            {presented.technicalDetails ? <pre>{presented.technicalDetails}</pre> : null}
            <button type="button" onClick={reset}>Попробовать снова</button>
          </div>
        </main>
      </body>
    </html>
  );
}
