import Link from "next/link";
import { ago, pct } from "@/lib/format";
import type { Injury, Report } from "@/lib/types";
import { StatusBadge } from "./ui";

const KIND_LABEL: Record<string, string> = {
  out: "Out",
  doubtful: "Doubtful",
  questionable: "Questionable",
  minutes_limit: "Minutes limit",
  role_up: "Bigger role",
  role_down: "Smaller role",
  returning: "Returning",
  other: "Note",
};

export const kindLabel = (kind: string) => KIND_LABEL[kind] ?? kind;

/** Consolidated news reports, strongest first (the API's order). */
export function ReportList({ reports, showPlayer = true }: { reports: Report[]; showPlayer?: boolean }) {
  return (
    <ul className="divide-y divide-[var(--border)]">
      {reports.map((r, i) => (
        <li key={`${r.player}-${r.kind}-${i}`} className="py-2.5">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {showPlayer &&
              (r.player_id ? (
                <Link href={`/players/${r.player_id}`} className="text-sm font-medium text-primary hover:underline">
                  {r.player}
                </Link>
              ) : (
                <span className="text-sm font-medium text-primary">{r.player}</span>
              ))}
            {showPlayer && r.team && <span className="text-xs text-muted">{r.team}</span>}
            <StatusBadge status={kindLabel(r.kind)} />
          </div>
          <p className="mt-1 text-sm text-secondary">{r.summary}</p>
          <p className="tnum mt-0.5 text-xs text-muted">
            {r.sources > 1 ? `${r.sources} sources` : "1 source"} · confidence {pct(r.confidence)}
            {r.published_at ? ` · ${ago(r.published_at)}` : ""}
          </p>
        </li>
      ))}
    </ul>
  );
}

/** The official injury report for a team. */
export function InjuryList({ injuries }: { injuries: Injury[] }) {
  return (
    <ul className="space-y-1.5">
      {injuries.map((i) => (
        <li key={i.player} className="flex flex-wrap items-baseline gap-x-2 text-sm">
          <span className="font-medium text-primary">{i.player}</span>
          <StatusBadge status={i.status} />
          {i.detail && <span className="basis-full text-xs text-secondary">{i.detail}</span>}
        </li>
      ))}
    </ul>
  );
}
