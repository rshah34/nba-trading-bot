import type { PredictionSummary } from "@/lib/types";

const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function EdgeCell({ edge }: { edge: number | null }) {
  if (edge == null) return <span className="text-muted">—</span>;
  const pts = (edge * 100).toFixed(1);
  const positive = edge > 0;
  const color = positive ? "text-good" : "text-critical";
  return (
    <span className={`tnum font-medium ${color}`}>
      {positive ? "+" : ""}
      {pts}
    </span>
  );
}

function ResultCell({ p }: { p: PredictionSummary }) {
  if (p.status !== "final" || p.home_score == null || p.away_score == null) {
    return <span className="text-muted">scheduled</span>;
  }
  const homeWon = p.home_score > p.away_score;
  const pickedHome = p.predicted_home_win_prob >= 0.5;
  const correct = homeWon === pickedHome;
  return (
    <span className="inline-flex items-center gap-2">
      <span className="tnum text-secondary">
        {p.away_score}–{p.home_score}
      </span>
      <span
        className={correct ? "text-good" : "text-critical"}
        title={correct ? "Model picked the winner" : "Model missed"}
      >
        {correct ? "✓" : "✗"}
      </span>
    </span>
  );
}

export function PredictionsTable({ rows }: { rows: PredictionSummary[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border)] bg-surface">
      <table className="w-full min-w-[640px] text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-muted">
            <th className="px-4 py-3 font-medium">Date</th>
            <th className="px-4 py-3 font-medium">Matchup</th>
            <th className="px-4 py-3 text-right font-medium">Model (home)</th>
            <th className="px-4 py-3 text-right font-medium">Market</th>
            <th className="px-4 py-3 text-right font-medium">Edge (pts)</th>
            <th className="px-4 py-3 font-medium">Result</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={`${p.game_id}-${p.model_version}-${p.as_of}`} className="border-b border-[var(--border)] last:border-0">
              <td className="tnum whitespace-nowrap px-4 py-3 text-secondary">{p.game_date}</td>
              <td className="whitespace-nowrap px-4 py-3 text-primary">
                <span className="text-secondary">{p.away.abbreviation}</span>
                <span className="mx-1 text-muted">@</span>
                <span className="font-medium">{p.home.abbreviation}</span>
              </td>
              <td className="tnum px-4 py-3 text-right text-primary">{pct(p.predicted_home_win_prob)}</td>
              <td className="tnum px-4 py-3 text-right text-secondary">{pct(p.market_home_win_prob)}</td>
              <td className="px-4 py-3 text-right">
                <EdgeCell edge={p.edge} />
              </td>
              <td className="whitespace-nowrap px-4 py-3">
                <ResultCell p={p} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
