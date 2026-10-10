import Link from "next/link";
import type { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-[var(--border)] bg-surface ${className}`}>{children}</div>
  );
}

export function SectionTitle({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-3">
      <h2 className="text-sm font-semibold text-primary">{children}</h2>
      {hint && <p className="mt-0.5 max-w-3xl text-xs text-secondary">{hint}</p>}
    </div>
  );
}

/** A row of mutually exclusive options. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-lg border border-[var(--border)] bg-surface p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
            o.value === value ? "bg-primary text-plane" : "text-secondary hover:text-primary"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Select({
  value,
  onChange,
  options,
  label,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  label: string;
}) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-lg border border-[var(--border)] bg-surface px-2.5 py-1.5 text-xs font-medium text-primary"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

/** Team abbreviation as a link to the team page. */
export function TeamLink({ id, abbr, className = "" }: { id: number | null; abbr: string | null; className?: string }) {
  if (!abbr) return <span className="text-muted">—</span>;
  if (id == null) return <span className={className}>{abbr}</span>;
  return (
    <Link href={`/teams/${id}`} className={`hover:underline ${className}`}>
      {abbr}
    </Link>
  );
}

const STATUS_TONE: Record<string, string> = {
  out: "bg-critical",
  doubtful: "bg-critical",
  questionable: "bg-warning",
  "day-to-day": "bg-warning",
  gtd: "bg-warning",
};

/** Availability status: a colored dot plus the word, never color alone. */
export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  const tone = STATUS_TONE[status.toLowerCase()] ?? "bg-warning";
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-[var(--border)] px-1.5 py-0.5 text-[11px] font-medium text-secondary">
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${tone}`} aria-hidden />
      {status}
    </span>
  );
}

/** Loading and error states shared by every page. */
export function PageState({ loading, error }: { loading: boolean; error: string | null }) {
  if (error)
    return (
      <Card className="border-dashed px-6 py-14 text-center">
        <div className="text-base font-medium text-primary">Couldn&apos;t load this</div>
        <p className="mx-auto mt-1 max-w-md text-sm text-secondary">{error}</p>
      </Card>
    );
  if (loading) return <div className="py-20 text-center text-sm text-muted">Loading…</div>;
  return null;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Card className="border-dashed px-6 py-10 text-center">
      <div className="text-sm font-medium text-primary">{title}</div>
      {children && <p className="mx-auto mt-1 max-w-lg text-sm text-secondary">{children}</p>}
    </Card>
  );
}

/** A label and one headline number. */
export function Tile({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <Card className="p-4">
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-primary">{value}</div>
      {hint && <div className="mt-1 text-xs text-secondary">{hint}</div>}
    </Card>
  );
}
