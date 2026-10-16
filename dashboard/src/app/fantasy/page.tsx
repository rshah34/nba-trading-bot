"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { SlateHeader, useSlatePlayers } from "@/components/SlateControls";
import { SortableTable, type Column } from "@/components/SortableTable";
import { Card, Delta, doubtLabel, Empty, PageState, Segmented, Select, StatusBadge, TeamLink } from "@/components/ui";
import { fantasyPoints, SCORING } from "@/lib/fantasy";
import { num, STAT_LABELS } from "@/lib/format";
import type { SlatePlayer, StatKey } from "@/lib/types";

const STATS: StatKey[] = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov"];

interface Row extends SlatePlayer {
  fp: number;
  average: number | null;
  recent: number | null;
}

export default function FantasyPage() {
  const router = useRouter();
  const { data, error, loading, setDate } = useSlatePlayers();
  const [site, setSite] = useState("espn");
  const [team, setTeam] = useState("");

  const rows = useMemo(() => {
    const weights = SCORING[site].weights;
    return (data?.players ?? [])
      .filter((p) => !team || p.team === team)
      .map((p): Row => ({
        ...p,
        fp: fantasyPoints(p.proj, weights),
        average: p.season ? fantasyPoints(p.season, weights) : null,
        recent: p.l5 ? fantasyPoints(p.l5, weights) : null,
      }));
  }, [data, site, team]);

  if (!data) return <PageState loading={loading} error={error} />;
  const teams = [...new Set(data.players.map((p) => p.team))].sort();

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
    { key: "min", header: "MIN", align: "right", sort: (r) => r.proj.min, render: (r) => num(r.proj.min, 0) },
    { key: "fp", header: "Proj FP", align: "right", title: "Projected fantasy points tonight", sort: (r) => r.fp, render: (r) => <span className="font-semibold text-primary">{num(r.fp)}</span> },
    { key: "avg", header: "Season FP", align: "right", sort: (r) => r.average, render: (r) => num(r.average) },
    { key: "change", header: "vs season", align: "right", title: "Tonight's projection minus his season average", sort: (r) => (r.average == null ? null : r.fp - r.average), render: (r) => <Delta value={r.average == null ? null : r.fp - r.average} threshold={2} /> },
    { key: "recent", header: "Last 5 FP", align: "right", sort: (r) => r.recent, render: (r) => num(r.recent) },
    { key: "rate", header: "FP/min", align: "right", title: "Projected fantasy points per projected minute", sort: (r) => r.fp / Math.max(r.proj.min, 1), render: (r) => num(r.fp / Math.max(r.proj.min, 1), 2) },
    ...STATS.map((s): Column<Row> => ({ key: s, header: STAT_LABELS[s], align: "right", sort: (r) => r.proj[s], render: (r) => num(r.proj[s]) })),
  ];

  return (
    <div className={loading ? "opacity-60" : ""}>
      <SlateHeader title="Fantasy" data={data} setDate={setDate}>
        Projected fantasy points for the slate, with who is out already counted.
      </SlateHeader>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Segmented
          label="Scoring"
          value={site}
          onChange={setSite}
          options={Object.entries(SCORING).map(([value, s]) => ({ value, label: s.label }))}
        />
        <Select label="Team" value={team} onChange={setTeam} options={[{ value: "", label: "All teams" }, ...teams.map((t) => ({ value: t, label: t }))]} />
      </div>

      {rows.length === 0 ? (
        <Empty title="No upcoming games on the schedule" />
      ) : (
        <Card className="p-2">
          <SortableTable
            rows={rows}
            columns={columns}
            rowKey={(r) => r.id}
            initialSort={{ key: "fp", dir: "desc" }}
            pageSize={40}
            onRowClick={(r) => router.push(`/players/${r.id}`)}
          />
        </Card>
      )}
    </div>
  );
}
