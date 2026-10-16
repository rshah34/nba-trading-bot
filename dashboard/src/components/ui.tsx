import Link from "next/link";
import type { ReactNode } from "react";
import { TEAM_COLORS } from "@/lib/teams";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-[var(--border)] bg-surface ${className}`}>{children}</div>
  );
}

export function SectionTitle({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-primary">
        <span className="h-3.5 w-1 rounded-full bg-accent" aria-hidden />
        {children}
      </h2>
      {hint && <p className="mt-0.5 max-w-3xl pl-3 text-xs text-muted">{hint}</p>}
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
            o.value === value ? "bg-accent/15 text-accent" : "text-secondary hover:text-primary"
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

/** A team's colour as a small chip. Decoration beside the name, never the only cue. */
export function TeamMark({ abbr, className = "" }: { abbr: string | null | undefined; className?: string }) {
  const color = abbr ? TEAM_COLORS[abbr] : undefined;
  if (!color) return null;
  return (
    <span
      aria-hidden
      className={`inline-block h-2.5 w-2.5 shrink-0 rounded-[3px] ring-1 ring-[var(--border)] ${className}`}
      style={{ background: color }}
    />
  );
}

/** Team abbreviation, with its colour, as a link to the team page. `mark` names the
 * team whose colour to show when the label isn't the abbreviation. */
export function TeamLink({ id, abbr, mark, className = "" }: { id: number | null; abbr: string | null; mark?: string | null; className?: string }) {
  if (!abbr) return <span className="text-muted">—</span>;
  const label = (
    <span className="inline-flex items-center gap-1.5">
      <TeamMark abbr={mark ?? abbr} />
      {abbr}
    </span>
  );
  if (id == null) return <span className={className}>{label}</span>;
  return (
    <Link href={`/teams/${id}`} className={`hover:underline ${className}`}>
      {label}
    </Link>
  );
}

const TONES = {
  critical: { dot: "bg-critical", pill: "border-critical/40 bg-critical/10" },
  warning: { dot: "bg-warning", pill: "border-warning/50 bg-warning/15" },
  good: { dot: "bg-good", pill: "border-good/40 bg-good/10" },
  neutral: { dot: "bg-series-1", pill: "border-series-1/40 bg-series-1/10" },
};
const STATUS_TONE: Record<string, keyof typeof TONES> = {
  out: "critical",
  doubtful: "critical",
  "smaller role": "critical",
  questionable: "warning",
  "day-to-day": "warning",
  gtd: "warning",
  "minutes limit": "warning",
  returning: "good",
  "bigger role": "good",
  note: "neutral",
};

/** Availability or role status: a tinted pill with a dot and the word, never colour alone. */
export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  const tone = TONES[STATUS_TONE[status.toLowerCase()] ?? "warning"];
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-1.5 py-0.5 text-[11px] font-medium text-primary ${tone.pill}`}>
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${tone.dot}`} aria-hidden />
      {status}
    </span>
  );
}

/** The status word for a player in doubt: the injury report's own ("Day-To-Day",
 * "Doubtful") when that is the reason, otherwise "Questionable". */
export const doubtLabel = (reason: string) =>
  reason.startsWith("listed ") ? reason.slice("listed ".length) : "Questionable";

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

/** A signed change, green or red once it is big enough to matter, grey otherwise. */
export function Delta({ value, threshold, digits = 1, suffix = "" }: { value: number | null; threshold: number; digits?: number; suffix?: string }) {
  if (value == null) return <span className="text-muted">—</span>;
  const tone = value >= threshold ? "text-good" : value <= -threshold ? "text-critical" : "text-muted";
  return (
    <span className={tone}>
      {value > 0 ? "+" : ""}
      {value.toFixed(digits)}
      {suffix}
    </span>
  );
}
