import type { BetRow, BetsSummary } from "@/lib/types";
import { MetricTile } from "./MetricTile";

const signedPts = (v: number | null, digits = 1) =>
  v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}`;
const pct = (v: number | null) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);

function SummaryTiles({ s }: { s: BetsSummary }) {
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <MetricTile
        label="Avg CLV"
        value={s.avg_clv == null ? "—" : `${signedPts(s.avg_clv)} pts`}
        hint={
          s.clv_positive_rate == null
            ? "beat the closing line — the north-star metric"
            : `beat the close ${pct(s.clv_positive_rate)} of the time`
        }
      />
      <MetricTile
        label="Bets"
        value={String(s.n_bets)}
        sub={s.n_pending > 0 ? `${s.n_pending} open` : undefined}
        hint="settled paper bets"
      />
      <MetricTile label="Win rate" value={pct(s.win_rate)} hint="of settled bets" />
      <MetricTile
        label="ROI"
        value={s.roi == null ? "—" : signedPts(s.roi) + "%"}
        sub={`bankroll ${s.final_bankroll.toFixed(3)}×`}
        hint="profit / total staked"
      />
    </div>
  );
}

function Cell({ v, digits = 1 }: { v: number | null; digits?: number }) {
  if (v == null) return <span className="text-muted">—</span>;
  const color = v > 0 ? "text-good" : v < 0 ? "text-critical" : "text-secondary";
  return (
    <span className={`tnum font-medium ${color}`}>
      {v >= 0 ? "+" : ""}
      {(v * 100).toFixed(digits)}
    </span>
  );
}

function Result({ b }: { b: BetRow }) {
  if (!b.settled) return <span className="text-muted">open</span>;
  const won = b.won === true;
  return (
    <span className={`tnum font-medium ${won ? "text-good" : "text-critical"}`}>
      {won ? "Won" : "Lost"} <CellInline v={b.pnl} />
    </span>
  );
}

function CellInline({ v }: { v: number | null }) {
  if (v == null) return null;
  return <span className="text-secondary">({v >= 0 ? "+" : ""}{(v * 100).toFixed(1)}%)</span>;
}

export function BetsPanel({ summary, rows }: { summary: BetsSummary; rows: BetRow[] }) {
  return (
    <div className="space-y-5">
      <SummaryTiles s={summary} />
      {rows.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-[var(--border)] bg-surface">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-muted">
                <th className="px-4 py-3 font-medium">Date</th>
                <th className="px-4 py-3 font-medium">Matchup</th>
                <th className="px-4 py-3 font-medium">Bet</th>
                <th className="px-4 py-3 text-right font-medium">Edge</th>
                <th className="px-4 py-3 text-right font-medium">Odds</th>
                <th className="px-4 py-3 text-right font-medium">Stake</th>
                <th className="px-4 py-3 text-right font-medium">CLV</th>
                <th className="px-4 py-3 font-medium">Result</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => {
                const bet = b.side === "home" ? b.home : b.away;
                return (
                  <tr key={`${b.game_id}-${b.model_version}`} className="border-b border-[var(--border)] last:border-0">
                    <td className="tnum whitespace-nowrap px-4 py-3 text-secondary">{b.game_date}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-primary">
                      <span className="text-secondary">{b.away.abbreviation}</span>
                      <span className="mx-1 text-muted">@</span>
                      <span className="font-medium">{b.home.abbreviation}</span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 font-medium text-primary">{bet.abbreviation}</td>
                    <td className="px-4 py-3 text-right"><CellInlineEdge v={b.edge} /></td>
                    <td className="tnum px-4 py-3 text-right text-secondary">{b.decimal_odds.toFixed(2)}</td>
                    <td className="tnum px-4 py-3 text-right text-secondary">{(b.stake_fraction * 100).toFixed(1)}%</td>
                    <td className="px-4 py-3 text-right"><Cell v={b.clv} /></td>
                    <td className="whitespace-nowrap px-4 py-3"><Result b={b} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CellInlineEdge({ v }: { v: number }) {
  return (
    <span className="tnum font-medium text-good">
      +{(v * 100).toFixed(1)}
    </span>
  );
}
