// A stat tile: a label and one hero number. Not a chart — the number IS the mark.
export function MetricTile({
  label,
  value,
  sub,
  hint,
}: {
  label: string;
  value: string;
  sub?: string;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-surface p-5">
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className="tnum mt-2 text-3xl font-semibold text-primary">{value}</div>
      {sub && <div className="mt-1 text-sm text-secondary">{sub}</div>}
      {hint && <div className="mt-2 text-xs text-muted">{hint}</div>}
    </div>
  );
}
