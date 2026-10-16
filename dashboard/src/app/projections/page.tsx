"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { SlateHeader, useSlatePlayers } from "@/components/SlateControls";
import { SortableTable, type Column } from "@/components/SortableTable";
import { Card, Delta, doubtLabel, Empty, PageState, Select, StatusBadge, TeamLink } from "@/components/ui";
import { num, pct } from "@/lib/format";
import { MARKETS, marketValue } from "@/lib/markets";
import type { SlateLine, SlatePlayer, SlateSide } from "@/lib/types";

interface Row extends SlatePlayer {
  projected: number;
  average: number | null;
  recent: number | null;
  /** Tonight's projection minus his season average, in the stat's own units. */
  change: number | null;
  /** How much more (or less) than average the opponent allows in this market's stats. */
  matchup: number | null;
  line: SlateLine | null;
}

export default function ProjectionsPage() {
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
          change: average != null ? projected - average : null,
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
  // The season most players' averages come from (last season until this one has games).
  const labels = rows.map((r) => r.season_label).filter(Boolean) as string[];
  const season = labels.sort((a, b) => labels.filter((s) => s === b).length - labels.filter((s) => s === a).length)[0] ?? "the season";
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
              <StatusBadge status={doubtLabel(r.status)} />
            </span>
          )}
        </span>
      ),
    },
    { key: "team", header: "Team", sort: (r) => r.team, render: (r) => <span onClick={(e) => e.stopPropagation()}><TeamLink id={r.team_id} abbr={r.team} /></span> },
    { key: "opp", header: "Opp", sort: (r) => r.opp, render: (r) => `${r.home ? "vs" : "@"} ${r.opp}` },
    { key: "proj", header: "Tonight", align: "right", title: `The model's projected ${label} for tonight`, sort: (r) => r.projected, render: (r) => <span className="font-semibold text-primary">{num(r.projected)}</span> },
    { key: "avg", header: "Season avg", align: "right", title: `His ${label} per game over ${season}`, sort: (r) => r.average, render: (r) => num(r.average) },
    { key: "recent", header: "Last 5 avg", align: "right", title: `His ${label} per game over his last five games`, sort: (r) => r.recent, render: (r) => num(r.recent) },
    {
      key: "change",
      header: "Tonight vs avg",
      align: "right",
      title: "Tonight's projection minus his season average. Green: expected to beat it; red: to fall short.",
      sort: (r) => r.change,
      // Coloured once the gap is a tenth of his average: a point of PTS is noise, a point of STL isn't.
      render: (r) => <Delta value={r.change} threshold={Math.max(0.1 * (r.average ?? 0), 0.2)} />,
    },
  ];
  // The opponent's numbers need a few games of the season before they mean anything.
  if (rows.some((r) => r.matchup != null)) {
    columns.push({ key: "matchup", header: "Opp allows", align: "right", title: `How much more ${label} than the league average this opponent gives up`, sort: (r) => r.matchup, render: (r) => <Delta value={r.matchup == null ? null : r.matchup * 100} threshold={3} digits={0} suffix="%" /> });
  }
  if (anyLine) {
    columns.push(
      { key: "line", header: "Book line", align: "right", title: "The sportsbooks' over/under line", sort: (r) => r.line?.line, render: (r) => (r.line ? r.line.line : "") },
      { key: "over", header: "Over chance", align: "right", title: "The model's chance he goes over the line", sort: (r) => r.line?.model_over, render: (r) => (r.line ? pct(r.line.model_over) : "") },
      { key: "edge", header: "Edge", align: "right", title: "The model's chance of the over minus the sportsbooks', in points. Positive leans over, negative leans under.", sort: edge, render: (r) => (r.line ? <Delta value={edge(r) == null ? null : edge(r)! * 100} threshold={4} digits={0} /> : "") },
      { key: "hits", header: "Over, last 10", align: "right", title: "Games he went over this line in his last ten", sort: (r) => (r.line ? r.line.hits / Math.max(r.line.games, 1) : null), render: (r) => (r.line ? `${r.line.hits} of ${r.line.games}` : "") },
    );
  }

  return (
    <div className={loading ? "opacity-60" : ""}>
      <SlateHeader title="Projections" data={data} setDate={setDate}>
        Pick a stat to see what the model projects for everyone playing.
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
    </div>
  );
}
