import type { ModelInfo } from "@/lib/types";

export function ModelSelector({
  models,
  value,
  onChange,
}: {
  models: ModelInfo[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-secondary">
      <span className="text-muted">Model</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border border-[var(--border)] bg-surface px-3 py-1.5 text-primary outline-none focus:border-series-1"
      >
        {models.map((m) => (
          <option key={m.model_version} value={m.model_version}>
            {m.model_version} ({m.n_predictions})
          </option>
        ))}
      </select>
    </label>
  );
}
