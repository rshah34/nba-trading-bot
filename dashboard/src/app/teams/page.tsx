"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { SortableTable, type Column } from "@/components/SortableTable";
import {
  Card,
  PageState,
  SectionTitle,
  Segmented,
  Select,
  TeamMark,
} from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, signed, STAT_LABELS } from "@/lib/format";
import type { StatKey, TeamRow, TeamsIndex } from "@/lib/types";

type Stats = "for" | "allowed";
type Grouping = "league" | "conference" | "division";
const STATS: StatKey[] = ["pts", "reb", "ast", "fg3m", "stl", "blk", "tov"];
// Seeds 1-6 go straight to the playoffs; 7-10 play in for the last two spots.
const PLAYOFFS = 6;
const PLAY_IN = 10;

interface Standing extends TeamRow {
  pct: number;
  /** Games behind the leader of the group it's shown in. */
  gb: number;
  /** Place in its conference, which is what decides the playoffs. */
  seed: number;
}

/** Best record first, then fewest games behind (2-0 is ahead of 1-0); point margin
 * breaks a tie (the league's real tiebreakers need head-to-head results). */
const byRecord = (a: TeamRow, b: TeamRow) =>
  b.w / b.gp - a.w / a.gp || b.w - b.l - (a.w - a.l) || b.margin - a.margin;

function Seed({
  seed,
  conference,
}: {
  seed: number;
  conference: string | null;
}) {
  const tone =
    seed <= PLAYOFFS ? "bg-good" : seed <= PLAY_IN ? "bg-warning" : "";
  const label =
    seed <= PLAYOFFS
      ? "Playoff spot"
      : seed <= PLAY_IN
        ? "Play-in spot"
        : "Out of the playoff picture";
  return (
    <span
      className="inline-flex items-center gap-1.5"
      title={`${label} in the ${conference ?? ""}`}
    >
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full ${tone}`}
        aria-hidden
      />
      {seed}
    </span>
  );
}

export default function TeamsPage() {
  const router = useRouter();
  const [season, setSeason] = useState("");
  const { data, error, loading } = useApi<TeamsIndex>(
    season ? `/teams?season=${season}` : "/teams",
  );
  const [stats, setStats] = useState<Stats>("for");
  const [grouping, setGrouping] = useState<Grouping>("conference");

  const groups = useMemo(() => {
    const teams = data?.teams ?? [];
    const seeds = new Map<number, number>();
    for (const conf of new Set(teams.map((t) => t.conference))) {
      teams
        .filter((t) => t.conference === conf)
        .sort(byRecord)
        .forEach((t, i) => seeds.set(t.id, i + 1));
    }
    const key = (t: TeamRow) =>
      (grouping === "league"
        ? "League"
        : grouping === "conference"
          ? t.conference
          : t.division) ?? "";
    const names = [...new Set(teams.map(key))].sort();
    return names.map((name) => {
      const rows = teams.filter((t) => key(t) === name).sort(byRecord);
      const lead = rows[0];
      return {
        name,
        rows: rows.map((t): Standing => ({
          ...t,
          pct: t.w / t.gp,
          gb: lead ? (lead.w - t.w + (t.l - lead.l)) / 2 : 0,
          seed: seeds.get(t.id) ?? 0,
        })),
      };
    });
  }, [data, grouping]);

  if (!data) return <PageState loading={loading} error={error} />;

  // Seeds only mean something next to the rest of the conference.
  const seeded = grouping === "conference";
  const seedColumn: Column<Standing> = {
    key: "seed",
    header: "Seed",
    title: "Place in its conference: 1-6 make the playoffs, 7-10 the play-in",
    sort: (t) => t.seed,
    render: (t) => <Seed seed={t.seed} conference={t.conference} />,
  };
  const columns: Column<Standing>[] = [
    ...(seeded ? [seedColumn] : []),
    {
      key: "name",
      header: "Team",
      sort: (t) => t.name,
      render: (t) => (
        <Link
          href={`/teams/${t.id}`}
          className="inline-flex items-center gap-2 font-medium text-primary hover:underline"
          onClick={(e) => e.stopPropagation()}
        >
          <TeamMark abbr={t.abbr} />
          {t.name}
        </Link>
      ),
    },
    {
      key: "w",
      header: "W",
      align: "right",
      sort: (t) => t.w,
      render: (t) => t.w,
    },
    {
      key: "l",
      header: "L",
      align: "right",
      sort: (t) => t.l,
      render: (t) => t.l,
    },
    {
      key: "pct",
      header: "Pct",
      align: "right",
      sort: (t) => t.pct,
      render: (t) => t.pct.toFixed(3).replace(/^0/, ""),
    },
    {
      key: "gb",
      header: "GB",
      align: "right",
      title: "Games behind the leader",
      sort: (t) => t.gb,
      render: (t) => (t.gb === 0 ? "—" : t.gb.toFixed(1)),
    },
    {
      key: "margin",
      header: "Margin",
      align: "right",
      title: "Average point margin",
      sort: (t) => t.margin,
      render: (t) => signed(t.margin),
    },
    ...STATS.map((s): Column<Standing> => ({
      key: s,
      header: STAT_LABELS[s],
      align: "right",
      sort: (t) => t[stats][s],
      render: (t) => num(t[stats][s]),
    })),
  ];

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-primary">
        Teams
      </h1>
      <div className="my-4 flex flex-wrap items-center gap-2">
        <Segmented
          label="Standings by"
          value={grouping}
          onChange={setGrouping}
          options={[
            { value: "league", label: "League" },
            { value: "conference", label: "Conference" },
            { value: "division", label: "Division" },
          ]}
        />
        <Segmented
          label="Stats shown"
          value={stats}
          onChange={setStats}
          options={[
            { value: "for", label: "Team's own" },
            { value: "allowed", label: "Allowed to opponents" },
          ]}
        />
        {data.seasons.length > 1 && (
          <Select
            label="Season"
            value={data.season}
            onChange={setSeason}
            options={data.seasons.map((s) => ({ value: s, label: s }))}
          />
        )}
        {seeded && (
          <span className="ml-auto flex items-center gap-3 text-xs text-secondary">
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-good" aria-hidden />
              Playoffs
            </span>
            <span className="flex items-center gap-1.5">
              <span
                className="h-1.5 w-1.5 rounded-full bg-warning"
                aria-hidden
              />
              Play-in
            </span>
          </span>
        )}
      </div>
      <div className={`space-y-6 ${loading ? "opacity-60" : ""}`}>
        {groups.map((g) => (
          <section key={`${grouping}-${g.name}`}>
            {grouping !== "league" && (
              <SectionTitle>
                {grouping === "conference"
                  ? `${g.name}ern Conference`
                  : `${g.name} Division`}
              </SectionTitle>
            )}
            <Card className="p-2">
              <SortableTable
                key={`${grouping}-${stats}`}
                rows={g.rows}
                columns={columns}
                rowKey={(t) => t.id}
                initialSort={{ key: "pct", dir: "desc" }}
                onRowClick={(t) => router.push(`/teams/${t.id}`)}
              />
            </Card>
          </section>
        ))}
      </div>
    </div>
  );
}
