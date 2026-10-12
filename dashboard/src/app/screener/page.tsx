"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { SlateHeader, useSlatePlayers } from "@/components/SlateControls";
import { SortableTable, type Column } from "@/components/SortableTable";
import { BarCell, Card, Delta, Empty, PageState, Select, StatusBadge, TeamLink } from "@/components/ui";
import { num, pct } from "@/lib/format";
import { MARKETS, marketValue } from "@/lib/markets";
import type { SlateLine, SlatePlayer, SlateSide } from "@/lib/types";

interface Row extends SlatePlayer {
  projected: number;
  average: number | null;
  recent: number | null;
  /** Projection against his season average, as a fraction. */
  change: number | null;
  /** How much more (or less) than average the opponent allows in this market's stats. */
  matchup: number | null;
  line: SlateLine | null;
}

export default function ScreenerPage() {
  const router = useRouter();
  const { data, error, loading, setDate } = useSlatePlayers();
  const [market, setMarket] = useState("player_points");
  const [team, setTeam] = useState("");

  const rows = useMemo(() => {
    if (!data) return [];
    const stats = MARKETS.find((m) => m.key === market)!.stats;
    const sides = new Map<number, SlateSide>(data.games.flatMap((g) => [[g.away.id, g.away], [g.home.id, g.home]]));
    return data.players
      .filter((p) => !team || p.team === team)
      .map((p): Row => {
        const projected = marketValue(p.proj, stats);
        const average = p.season ? marketValue(p.season, stats) : null;
        const allows = sides.get(p.team_id)?.matchup ?? {};
        const known = stats.every((s) => allows[s] != null) && projected > 0;
        return {
          ...p,
          projected,
          average,
          recent: p.l5 ? marketValue(p.l5, stats) : null,
          change: average ? projected / average - 1 : null,
          // For a combined market, each stat counts by how much of the projection it is.
          matchup: known ? stats.reduce((sum, s) => sum + p.proj[s] * allows[s]!, 0) / projected - 1 : null,
          line: p.lines[market] ?? null,
        };
      });
  }, [data, market, team]);

  if (!data) return <PageState loading={loading} error={error} />;
  const label = MARKETS.find((m) => m.key === market)!.label;
  const teams = [...new Set(data.players.map((p) => p.team))].sort();
  const anyLine = rows.some((r) => r.line);
  const top = Math.max(...rows.map((r) => r.projected), 0);
  const edge = (r: Row) => (r.line?.model_over == null ? null : r.line.model_over - r.line.market_over);

  const columns: Column<Row>[] = [
    {
      key: "name",
      header: "Player",
      sort: (r) => r.name,
      render: (r) => (
        <span className="flex items-center gap-2">
          <Link href={`/players/${r.id}`} className="font-medium text-primary hover:underline" onClick={(e) => e.stopPropagation()}>
            {r.name}
          </Link>
          {r.status && (
            <span title={r.status}>
              <StatusBadge status="Questionable" />
            </span>
          )}
        </span>
      ),
    },
    { key: "team", header: "Team", sort: (r) => r.team, render: (r) => <span onClick={(e) => e.stopPropagation()}><TeamLink id={r.team_id} abbr={r.team} /></span> },
    { key: "opp", header: "Opp", sort: (r) => r.opp, render: (r) => `${r.home ? "vs" : "@"} ${r.opp}` },
    { key: "min", header: "MIN", align: "right", sort: (r) => r.proj.min, render: (r) => num(r.proj.min, 0) },
    { key: "proj", header: `Proj ${label}`, align: "right", sort: (r) => r.projected, render: (r) => <BarCell value={r.projected} max={top} /> },
    { key: "avg", header: "Season", align: "right", sort: (r) => r.average, render: (r) => num(r.average) },
    { key: "change", header: "vs season", align: "right", title: "Tonight's projection against his season average", sort: (r) => r.change, render: (r) => <Delta value={r.change == null ? null : r.change * 100} threshold={5} digits={0} suffix="%" /> },
    { key: "recent", header: "Last 5", align: "right", sort: (r) => r.recent, render: (r) => num(r.recent) },
  ];
  // The opponent's numbers need a few games of the season before they mean anything.
  if (rows.some((r) => r.matchup != null)) {
    columns.push({ key: "matchup", header: "Matchup", align: "right", title: "How much more or less than the league average this opponent allows", sort: (r) => r.matchup, render: (r) => <Delta value={r.matchup == null ? null : r.matchup * 100} threshold={3} digits={0} suffix="%" /> });
  }
  if (anyLine) {
    columns.push(
      { key: "line", header: "Line", align: "right", sort: (r) => r.line?.line, render: (r) => (r.line ? r.line.line : "") },
      { key: "over", header: "Model over", align: "right", title: "The model's chance he goes over the line", sort: (r) => r.line?.model_over, render: (r) => (r.line ? pct(r.line.model_over) : "") },
      { key: "edge", header: "vs market", align: "right", title: "Model chance of the over minus the sportsbooks' own, in points. Negative favours the under.", sort: edge, render: (r) => (r.line ? <Delta value={edge(r) == null ? null : edge(r)! * 100} threshold={4} digits={0} /> : "") },
      { key: "hits", header: "Last 10", align: "right", title: "Games over this line in his last ten", sort: (r) => (r.line ? r.line.hits / Math.max(r.line.games, 1) : null), render: (r) => (r.line ? `${r.line.hits}/${r.line.games}` : "") },
    );
  }

  return (
    <div className={loading ? "opacity-60" : ""}>
      <SlateHeader title="Screener" data={data} setDate={setDate}>
        Projections for every player on the slate, in any stat.
      </SlateHeader>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Select label="Stat" value={market} onChange={setMarket} options={MARKETS.map((m) => ({ value: m.key, label: m.label }))} />
        <Select label="Team" value={team} onChange={setTeam} options={[{ value: "", label: "All teams" }, ...teams.map((t) => ({ value: t, label: t }))]} />
        <span className="tnum ml-auto text-xs text-muted">{rows.length} players</span>
      </div>

      {rows.length === 0 ? (
        <Empty title="No players match" />
      ) : (
        <Card className="p-2">
          <SortableTable
            key={market}
            rows={rows}
            columns={columns}
            rowKey={(r) => r.id}
            initialSort={{ key: "proj", dir: "desc" }}
            pageSize={40}
            onRowClick={(r) => router.push(`/players/${r.id}`)}
          />
        </Card>
      )}
      {!anyLine && rows.length > 0 && (
        <p className="mt-3 max-w-3xl text-xs text-secondary">
          No sportsbook lines for {label} yet. When lines are in, this table adds the line, the model&apos;s chance of
          the over, and his recent hit rate.
        </p>
      )}
    </div>
  );
}
