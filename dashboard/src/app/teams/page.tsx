"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { SortableTable, type Column } from "@/components/SortableTable";
import { Card, PageState, Segmented, Select } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, signed, STAT_LABELS } from "@/lib/format";
import type { StatKey, TeamRow, TeamsIndex } from "@/lib/types";

type View = "allowed" | "for";
const STATS: StatKey[] = ["pts", "reb", "ast", "fg3m", "stl", "blk", "tov"];

export default function TeamsPage() {
  const router = useRouter();
  const [season, setSeason] = useState("");
  const { data, error, loading } = useApi<TeamsIndex>(season ? `/teams?season=${season}` : "/teams");
  const [view, setView] = useState<View>("allowed");

  // Shade each stat column from its lowest to its highest value, so the softest
  // (or most productive) teams stand out without reading 30 numbers.
  const shade = useMemo(() => {
    const ranges = Object.fromEntries(
      STATS.map((s) => {
        const vals = (data?.teams ?? []).map((t) => t[view][s]);
        return [s, [Math.min(...vals), Math.max(...vals)] as const];
      }),
    );
    return (s: StatKey, v: number) => {
      const [lo, hi] = ranges[s];
      const t = hi > lo ? (v - lo) / (hi - lo) : 0;
      return `color-mix(in srgb, var(--series-1) ${Math.round(t * 38)}%, transparent)`;
    };
  }, [data, view]);

  if (!data) return <PageState loading={loading} error={error} />;

  const columns: Column<TeamRow>[] = [
    {
      key: "name",
      header: "Team",
      sort: (t) => t.name,
      render: (t) => (
        <Link href={`/teams/${t.id}`} className="font-medium text-primary hover:underline" onClick={(e) => e.stopPropagation()}>
          {t.name}
        </Link>
      ),
    },
    { key: "record", header: "W-L", align: "right", sort: (t) => t.w / t.gp, render: (t) => `${t.w}-${t.l}` },
    { key: "margin", header: "Margin", align: "right", title: "Average point margin", sort: (t) => t.margin, render: (t) => signed(t.margin) },
    ...STATS.map((s): Column<TeamRow> => ({
      key: s,
      header: STAT_LABELS[s],
      align: "right",
      sort: (t) => t[view][s],
      render: (t) => (
        <span className="-my-1 inline-block min-w-12 rounded px-1.5 py-1 text-primary" style={{ background: shade(s, t[view][s]) }}>
          {num(t[view][s])}
        </span>
      ),
    })),
  ];

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-primary">Teams</h1>
      <p className="mt-1 max-w-2xl text-sm text-secondary">
        Per-game numbers for {data.season}. &ldquo;Allowed&rdquo; is what opponents put up against a team — the place to
        look for a soft matchup. The more strongly shaded a cell, the higher the value in its column.
      </p>
      <div className="my-4 flex flex-wrap items-center gap-2">
        <Segmented
          label="Stats shown"
          value={view}
          onChange={setView}
          options={[
            { value: "allowed", label: "Allowed to opponents" },
            { value: "for", label: "Team's own" },
          ]}
        />
        {data.seasons.length > 1 && (
          <Select label="Season" value={data.season} onChange={setSeason} options={data.seasons.map((s) => ({ value: s, label: s }))} />
        )}
      </div>
      <Card className={`p-2 ${loading ? "opacity-60" : ""}`}>
        <SortableTable
          key={view}
          rows={data.teams}
          columns={columns}
          rowKey={(t) => t.id}
          initialSort={{ key: "pts", dir: "desc" }}
          onRowClick={(t) => router.push(`/teams/${t.id}`)}
        />
      </Card>
    </div>
  );
}
