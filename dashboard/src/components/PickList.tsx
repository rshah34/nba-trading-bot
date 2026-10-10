import Link from "next/link";
import { american, pct, signed } from "@/lib/format";
import type { Pick } from "@/lib/types";
import { Card } from "./ui";

const RESULT: Record<string, { label: string; tone: string }> = {
  won: { label: "Won", tone: "bg-good" },
  lost: { label: "Lost", tone: "bg-critical" },
  push: { label: "Push", tone: "bg-series-rest" },
  void: { label: "Void", tone: "bg-series-rest" },
};

export function ResultBadge({ result }: { result: Pick["result"] }) {
  const r = result ? RESULT[result] : { label: "Open", tone: "bg-series-1" };
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium text-secondary">
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${r.tone}`} aria-hidden />
      {r.label}
    </span>
  );
}

/** The day's picks as cards, biggest edge first. */
export function PickCards({ picks }: { picks: Pick[] }) {
  const ordered = [...picks].sort((a, b) => (b.edge ?? 0) - (a.edge ?? 0));
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {ordered.map((p, i) => (
        <Card key={`${p.game_id}-${p.label}`} className="p-4">
          <div className="flex items-center justify-between text-xs text-muted">
            <span>
              Pick {i + 1} · {p.kind === "game" ? "Game winner" : "Player prop"}
            </span>
            <ResultBadge result={p.result} />
          </div>
          <div className="mt-1.5 text-sm font-semibold text-primary">
            {p.player_id ? (
              <Link href={`/players/${p.player_id}`} className="hover:underline">
                {p.label}
              </Link>
            ) : (
              p.label
            )}
          </div>
          <div className="tnum mt-0.5 text-xs text-secondary">
            <Link href={`/games/${p.game_id}`} className="hover:underline">
              {p.matchup}
            </Link>
            {" · "}
            {american(p.odds)}
            {p.book ? ` at ${p.book}` : " (average price)"}
          </div>
          <dl className="tnum mt-3 grid grid-cols-4 gap-2 border-t border-[var(--border)] pt-3 text-xs">
            {[
              ["Model", pct(p.model)],
              ["Market", pct(p.market)],
              ["Edge", p.edge == null ? "—" : `${signed(p.edge * 100)} pts`],
              ["Stake", p.stake == null ? "—" : `${(p.stake * 100).toFixed(1)}u`],
            ].map(([k, v]) => (
              <div key={k}>
                <dt className="text-muted">{k}</dt>
                <dd className="font-medium text-primary">{v}</dd>
              </div>
            ))}
          </dl>
          {p.notes && <p className="mt-2 text-xs text-secondary">{p.notes}</p>}
          {p.guide && (
            <p className="tnum mt-2 text-xs text-secondary">
              <span className="text-muted">Single stats, over at or below / under at or above: </span>
              {(["pts", "reb", "ast"] as const)
                .filter((s) => p.guide![s])
                .map((s) => `${s.toUpperCase()} ${p.guide![s].over_at ?? "–"} / ${p.guide![s].under_at ?? "–"}`)
                .join(" · ")}
            </p>
          )}
        </Card>
      ))}
    </div>
  );
}
