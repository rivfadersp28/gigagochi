type StatBarProps = {
  label: string;
  value: number;
};

export function StatBar({ label, value }: StatBarProps) {
  const safeValue = Math.max(0, Math.min(100, value));

  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium text-[var(--ink)]">{label}</span>
        <span className="font-mono text-[13px] text-[var(--ink-muted)] tabular-nums">
          {safeValue}/100
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-[4px] border border-[var(--line-soft)] bg-white">
        <div
          className="h-full rounded-[3px] bg-[var(--leaf)] transition-[width] duration-200"
          style={{ width: `${safeValue}%` }}
        />
      </div>
    </div>
  );
}
