"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { SortableTable, type Column } from "@/components/SortableTable";
import { Card, PageState, Segmented, Select, TeamLink } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, signed, STAT_LABELS } from "@/lib/format";
import { pra } from "@/lib/markets";
import type { PlayerRow, PlayersIndex, StatKey } from "@/lib/types";

type Window = "season" | "l10" | "l5";
const STATS: StatKey[] = ["pts", "reb", "ast", "fg3m", "stl", "blk", "tov"];

export default function PlayersPage() {
  const router = useRouter();
  const [season, setSeason] = useState("");
  const { data, error, loading } = useApi<PlayersIndex>(season ? `/players?season=${season}` : "/players");
  const [window, setWindow] = useState<Window>("season");
  const [team, setTeam] = useState("");
  const [query, setQuery] = useState("");

  const teams = useMemo(
    () => [...new Set((data?.players ?? []).map((p) => p.team).filter(Boolean) as string[])].sort(),
    [data],
  );
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data?.players ?? []).filter(
      (p) =>
        (!team || p.team === team) &&
        (!q || p.name.toLowerCase().includes(q)),
    );
  }, [data, team, query]);

  if (!data) return <PageState loading={loading} error={error} />;

  const columns: Column<PlayerRow>[] = [
    {
      key: "name",
      header: "Player",
      sort: (r) => r.name,
      render: (r) => (
        <Link href={`/players/${r.id}?season=${data.season}`} className="font-medium text-primary hover:underline" onClick={(e) => e.stopPropagation()}>
          {r.name}
        </Link>
      ),
    },
    { key: "team", header: "Team", sort: (r) => r.team, render: (r) => <span onClick={(e) => e.stopPropagation()}><TeamLink id={r.team_id} abbr={r.team} /></span> },
    { key: "gp", header: "GP", align: "right", sort: (r) => r[window].gp, render: (r) => r[window].gp },
    { key: "min", header: "MIN", align: "right", sort: (r) => r[window].min, render: (r) => num(r[window].min) },
    ...STATS.map((s): Column<PlayerRow> => ({
      key: s,
      header: STAT_LABELS[s],
      align: "right",
      sort: (r) => r[window][s],
      render: (r) => num(r[window][s]),
    })),
    { key: "pra", header: "PRA", align: "right", title: "Points + rebounds + assists", sort: (r) => pra(r[window]), render: (r) => num(pra(r[window])) },
  ];
  if (window !== "season") {
    // Who is trending: the recent window against the season, in the two numbers that move lines.
    columns.push(
      { key: "dmin", header: "MIN Δ", align: "right", title: "Recent minutes minus season minutes", sort: (r) => r[window].min - r.season.min, render: (r) => signed(r[window].min - r.season.min) },
      { key: "dpts", header: "PTS Δ", align: "right", title: "Recent points minus season points", sort: (r) => r[window].pts - r.season.pts, render: (r) => signed(r[window].pts - r.season.pts) },
    );
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-primary">Players</h1>
      <p className="mt-1 max-w-2xl text-sm text-secondary">
        Per-game averages. Click a column to sort; switch to the last 5 or 10 games to see who is trending, then open a
        player to test a line against his game log.
      </p>

      <div className="my-4 flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter by name"
          aria-label="Filter by name"
          className="w-44 rounded-lg border border-[var(--border)] bg-surface px-2.5 py-1.5 text-xs text-primary placeholder:text-muted"
        />
        <Segmented
          label="Games included"
          value={window}
          onChange={setWindow}
          options={[
            { value: "season", label: "Season" },
            { value: "l10", label: "Last 10" },
            { value: "l5", label: "Last 5" },
          ]}
        />
        <Select label="Team" value={team} onChange={setTeam} options={[{ value: "", label: "All teams" }, ...teams.map((t) => ({ value: t, label: t }))]} />
        {data.seasons.length > 1 && (
          <Select label="Season" value={data.season} onChange={setSeason} options={data.seasons.map((s) => ({ value: s, label: s }))} />
        )}
        <span className="tnum ml-auto text-xs text-muted">
          {rows.length} players · {data.season}
        </span>
      </div>

      <Card className={`p-2 ${loading ? "opacity-60" : ""}`}>
        <SortableTable
          key={window}
          rows={rows}
          columns={columns}
          rowKey={(r) => r.id}
          initialSort={{ key: "pts", dir: "desc" }}
          pageSize={50}
          onRowClick={(r) => router.push(`/players/${r.id}?season=${data.season}`)}
        />
      </Card>
    </div>
  );
}
