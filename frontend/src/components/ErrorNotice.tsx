import type { PresentedError } from "@/lib/errorPresentation";

type ErrorNoticeProps = {
  error: PresentedError;
  id?: string;
  className?: string;
};

export function ErrorNotice({ error, id, className }: ErrorNoticeProps) {
  return (
    <div id={id} className={className} role="alert">
      <p>{error.message}</p>
      {error.technicalDetails ? (
        <details className="mt-2 text-left">
          <summary className="cursor-pointer">Технические детали</summary>
          <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-xs">
            {error.technicalDetails}
          </pre>
        </details>
      ) : null}
    </div>
  );
}
