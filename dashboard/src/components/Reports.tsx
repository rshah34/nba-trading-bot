import Link from "next/link";
import { ago, pct } from "@/lib/format";
import type { Injury, Report } from "@/lib/types";
import { StatusBadge, TeamMark } from "./ui";

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

const meta = (r: Report) =>
  `${r.sources > 1 ? `${r.sources} sources` : "1 source"} · ${pct(r.confidence)} sure${r.published_at ? ` · ${ago(r.published_at)}` : ""}`;

/** Consolidated news reports, strongest first (the API's order). `details: false`
 * moves the sources, confidence and age into a hover tooltip. */
export function ReportList({ reports, showPlayer = true, columns = false, details = true }: { reports: Report[]; showPlayer?: boolean; columns?: boolean; details?: boolean }) {
  return (
    <ul className={columns ? "grid gap-x-8 md:grid-cols-2 [&>li]:border-b [&>li]:border-[var(--border)]" : "divide-y divide-[var(--border)]"}>
      {reports.map((r, i) => (
        <li key={`${r.player}-${r.kind}-${i}`} className="py-2.5" title={details ? undefined : meta(r)}>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {showPlayer &&
              (r.player_id ? (
                <Link href={`/players/${r.player_id}`} className="text-sm font-medium text-primary hover:underline">
                  {r.player}
                </Link>
              ) : (
                <span className="text-sm font-medium text-primary">{r.player}</span>
              ))}
            {showPlayer && r.team && (
              <span className="inline-flex items-center gap-1 text-xs text-muted">
                <TeamMark abbr={r.team} />
                {r.team}
              </span>
            )}
            <StatusBadge status={kindLabel(r.kind)} />
            {details && <span className="tnum ml-auto text-xs text-muted">{meta(r)}</span>}
          </div>
          <p className="mt-1 text-sm text-secondary">{r.summary}</p>
        </li>
      ))}
    </ul>
  );
}

function PlayerName({ injury, plain = false }: { injury: Injury; plain?: boolean }) {
  const style = plain ? "hover:underline" : "font-medium text-primary hover:underline";
  return injury.player_id ? (
    <Link href={`/players/${injury.player_id}`} className={style}>
      {injury.player}
    </Link>
  ) : (
    <span className={plain ? "" : "font-medium text-primary"}>{injury.player}</span>
  );
}

/** The official injury report for a team. */
export function InjuryList({ injuries }: { injuries: Injury[] }) {
  const current = injuries.filter((i) => !i.long_term);
  const longTerm = injuries.filter((i) => i.long_term);
  return (
    <>
      <ul className="space-y-1.5">
        {current.map((i) => (
          <li key={i.player} title={i.detail ?? undefined} className="flex items-center gap-2 text-sm">
            <PlayerName injury={i} />
            <StatusBadge status={i.status} />
          </li>
        ))}
      </ul>
      {longTerm.length > 0 && (
        <p className={`text-xs text-secondary ${current.length > 0 ? "mt-2" : ""}`}>
          <span className="text-muted">Out long-term: </span>
          {longTerm.map((i, n) => (
            <span key={i.player} title={i.detail ?? undefined}>
              {n > 0 && ", "}
              <PlayerName injury={i} plain />
            </span>
          ))}
        </p>
      )}
    </>
  );
}
