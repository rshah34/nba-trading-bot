"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { InjuryList, ReportList } from "@/components/Reports";
import { SortableTable, type Column } from "@/components/SortableTable";
import { Card, PageState, SectionTitle, StatusBadge, TeamMark, Tile } from "@/components/ui";
import { useApi } from "@/lib/api";
import { num, shortDate, signed, STAT_LABELS } from "@/lib/format";
import { pra } from "@/lib/markets";
import type { StatKey, TeamDetail } from "@/lib/types";

type RosterRow = TeamDetail["roster"][number];
const STATS: StatKey[] = ["pts", "reb", "ast", "fg3m"];

export default function TeamPage() {
  const { id } = useParams<{ id: string }>();
  const { data: team, error, loading } = useApi<TeamDetail>(`/teams/${id}`);
  if (!team || String(team.id) !== id) return <PageState loading={loading} error={error} />;

  const columns: Column<RosterRow>[] = [
    {
      key: "name",
      header: "Player",
      sort: (r) => r.name,
      render: (r) => (
        <span className="flex items-center gap-2">
          <Link href={`/players/${r.id}`} className="font-medium text-primary hover:underline">
            {r.name}
          </Link>
          <StatusBadge status={r.status} />
        </span>
      ),
    },
    { key: "gp", header: "GP", align: "right", sort: (r) => r.season?.gp, render: (r) => r.season?.gp ?? "—" },
    { key: "min", header: "MIN", align: "right", sort: (r) => r.season?.min, render: (r) => num(r.season?.min) },
    { key: "l5min", header: "L5 MIN", align: "right", title: "Minutes over his last 5 games", sort: (r) => r.l5?.min, render: (r) => num(r.l5?.min) },
    ...STATS.map((s): Column<RosterRow> => ({
      key: s,
      header: STAT_LABELS[s],
      align: "right",
      sort: (r) => r.season?.[s],
      render: (r) => num(r.season?.[s]),
    })),
    { key: "pra", header: "PRA", align: "right", sort: (r) => (r.season ? pra(r.season) : null), render: (r) => num(r.season ? pra(r.season) : null) },
  ];
  const carried = team.roster.some((r) => r.stats_season !== team.season);

  return (
    <div>
      <Link href="/teams" className="text-xs text-secondary hover:text-primary">
        ← Teams
      </Link>
      <div className="mb-6 mt-2">
        <h1 className="flex items-center gap-2.5 text-2xl font-semibold tracking-tight text-primary">
          <TeamMark abbr={team.abbr} className="h-3.5 w-3.5" />
          {team.name}
        </h1>
        <p className="mt-1 text-sm text-secondary">
          {team.conference ?? ""}
          {team.next && (
            <>
              {" · next: "}
              <Link href={`/games/${team.next.game_id}`} className="hover:underline">
                {team.next.home ? "vs" : "at"} {team.next.opp}, {shortDate(team.next.date)}
              </Link>
            </>
          )}
        </p>
      </div>

      {team.gp > 0 && (
        <div className="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Tile label={`Record · ${team.season}`} value={`${team.w}-${team.l}`} />
          <Tile label="Average margin" value={signed(team.margin)} />
          <Tile label="Points scored" value={num(team.pf)} hint="per game" />
          <Tile label="Points allowed" value={num(team.pa)} hint="per game" />
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <section>
          <SectionTitle hint={carried ? "Averages are from each player's most recent season, which may have been with another team." : undefined}>
            Roster
          </SectionTitle>
          <Card className="p-2">
            <SortableTable rows={team.roster} columns={columns} rowKey={(r) => r.id} initialSort={{ key: "min", dir: "desc" }} />
          </Card>
        </section>

        <div className="space-y-6">
          <section>
            <SectionTitle>Recent results</SectionTitle>
            <Card className="px-4 py-2">
              <ul className="tnum divide-y divide-[var(--border)] text-sm">
                {team.results.map((g) => (
                  <li key={g.game_id}>
                    <Link href={`/games/${g.game_id}`} className="flex items-baseline justify-between py-1.5 hover:underline">
                      <span className="text-secondary">
                        {shortDate(g.date)} {g.home ? "vs" : "at"} {g.opp}
                        {g.preseason && <span className="ml-1 text-xs text-muted">pre</span>}
                      </span>
                      <span className="font-medium text-primary">{g.result}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>
          </section>
          {team.injuries.length > 0 && (
            <section>
              <SectionTitle>Injury report</SectionTitle>
              <Card className="p-4">
                <InjuryList injuries={team.injuries} />
              </Card>
            </section>
          )}
          {team.reports.length > 0 && (
            <section>
              <SectionTitle>News</SectionTitle>
              <Card className="px-4 py-1">
                <ReportList reports={team.reports} />
              </Card>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
