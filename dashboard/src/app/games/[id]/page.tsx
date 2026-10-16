"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { PickCards } from "@/components/PickList";
import { InjuryList, ReportList } from "@/components/Reports";
import { SortableTable, type Column } from "@/components/SortableTable";
import { Card, doubtLabel, PageState, SectionTitle, Segmented, StatusBadge, TeamLink, TeamMark } from "@/components/ui";
import { useApi } from "@/lib/api";
import { clock, longDate, num, pct, seriesLine, STAT_LABELS } from "@/lib/format";
import { pra } from "@/lib/markets";
import type { GameDetail, GamePlayer, GameSide, StatLine } from "@/lib/types";

type View = "actual" | "avg" | "proj";
type Col = "min" | "pts" | "reb" | "ast" | "fg3m" | "stl" | "blk" | "tov" | "pra";
// A played game reads like a box score; a projection adds the combined line the picks are made in.
const BOX_COLS: Col[] = ["min", "pts", "reb", "ast", "fg3m", "stl", "blk", "tov"];
const PROJ_COLS: Col[] = ["min", "pts", "reb", "ast", "fg3m", "pra"];
const value = (line: StatLine | null, key: Col) => (line == null ? null : key === "pra" ? pra(line) : line[key]);

/** What is moving a team's projections, each note with the players it applies to. */
function Adjustments({ players }: { players: GamePlayer[] }) {
  const byNote = new Map<string, string[]>();
  for (const p of players) for (const n of p.notes) byNote.set(n, [...(byNote.get(n) ?? []), p.name]);
  if (byNote.size === 0) return null;
  return (
    <div className="mt-4 border-t border-[var(--border)] pt-3">
      <div className="mb-2 text-xs font-medium text-muted">Adjustments to these projections</div>
      <ul className="space-y-1.5 text-xs text-secondary">
        {[...byNote].slice(0, 6).map(([note, names]) => (
          <li key={note} className="flex gap-2">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" aria-hidden />
            <span>
              <span className="font-medium text-primary">
                {names.length > 3 ? `${names.length} players` : names.join(", ")}
              </span>
              {": "}
              {note.replace(/^news: /, "")}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** One team's table: what each player did, or what the model projects for him. */
function BoxScore({ side, view }: { side: GameSide; view: View }) {
  const line = (r: GamePlayer) => (view === "actual" ? r.actual : view === "avg" ? r.avg : r.proj);
  const players = side.players.filter((r) => line(r) != null);
  const columns: Column<GamePlayer>[] = [
    {
      key: "name",
      header: "Player",
      sort: (r) => r.name,
      render: (r) => (
        <span className="flex items-center gap-2">
          <Link href={`/players/${r.id}`} className="font-medium text-primary hover:underline">
            {r.name}
          </Link>
          <span title={r.status ?? undefined}>
            <StatusBadge status={r.status ? doubtLabel(r.status) : null} />
          </span>
        </span>
      ),
    },
    ...(view === "actual" ? BOX_COLS : PROJ_COLS).map((key): Column<GamePlayer> => ({
      key,
      header: STAT_LABELS[key],
      align: "right",
      sort: (r) => value(line(r), key),
      render: (r) => num(value(line(r), key), view === "actual" || (view === "proj" && key === "min") ? 0 : 1),
    })),
  ];
  return (
    <Card className="p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-primary">
          <TeamLink id={side.team.id} abbr={side.team.name} mark={side.team.abbr} />
        </h3>
      </div>
      {players.length > 0 ? (
        <SortableTable
          key={view}
          dense
          rows={players}
          columns={columns}
          rowKey={(r) => r.id}
          initialSort={{ key: "pts", dir: "desc" }}
        />
      ) : (
        <p className="py-6 text-center text-sm text-muted">
          {view === "actual" ? "The box score isn't in yet. It arrives with the next daily run." : "No players to show for this team yet."}
        </p>
      )}
      {view === "proj" && <Adjustments players={players} />}
      {side.injuries.length > 0 && (
        <div className="mt-4 border-t border-[var(--border)] pt-3">
          <div className="mb-2 text-xs font-medium text-muted">Injury report</div>
          <InjuryList injuries={side.injuries} />
        </div>
      )}
      {side.reports.length > 0 && (
        <div className="mt-4 border-t border-[var(--border)] pt-3">
          <div className="text-xs font-medium text-muted">News</div>
          <ReportList reports={side.reports} />
        </div>
      )}
    </Card>
  );
}

export default function GamePage() {
  const { id } = useParams<{ id: string }>();
  const { data: game, error, loading } = useApi<GameDetail>(`/games/${id}`);
  const [chosen, setChosen] = useState<View | null>(null);
  if (!game) return <PageState loading={loading} error={error} />;
  const final = game.status === "final";
  const view: View = chosen ?? (final ? "actual" : "avg");
  // Whose averages these are: the season most of the listed players last played in.
  const seasons = [...game.away_side.players, ...game.home_side.players].map((p) => p.avg_season).filter(Boolean);
  const avgSeason = seasons.sort((a, b) => seasons.filter((s) => s === b).length - seasons.filter((s) => s === a).length)[0];

  return (
    <div>
      <Link href={`/?date=${game.date}`} className="text-xs text-secondary hover:text-primary">
        ← {longDate(game.date)}
      </Link>
      <div className="mb-6 mt-2 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex flex-wrap items-center gap-x-2.5 text-2xl font-semibold tracking-tight text-primary">
            <TeamMark abbr={game.away.abbr} className="h-3.5 w-3.5" />
            {game.away.name} <span className="font-normal text-muted">at</span>
            <TeamMark abbr={game.home.abbr} className="h-3.5 w-3.5" />
            {game.home.name}
          </h1>
          <p className="mt-1 text-sm text-secondary">
            {final ? `Final: ${game.away.abbr} ${game.away.score}, ${game.home.abbr} ${game.home.score}` : `Tip ${clock(game.tipoff_at) || "time not set"}`}
            {game.preseason ? " · Preseason" : ""}
            {game.series ? ` · ${seriesLine(game.home.abbr, game.away.abbr, game.series)}` : ""}
          </p>
        </div>
        {(game.model_home_prob != null || game.market_home_prob != null) && (
          <dl className="flex gap-6 text-sm">
            <div>
              <dt className="text-xs text-muted">Model: {game.home.abbr} wins</dt>
              <dd className="text-xl font-semibold text-primary">{pct(game.model_home_prob)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Market</dt>
              <dd className="text-xl font-semibold text-primary">{pct(game.market_home_prob)}</dd>
            </div>
          </dl>
        )}
      </div>

      {game.picks.length > 0 && (
        <section className="mb-8">
          <SectionTitle>Picks in this game</SectionTitle>
          <PickCards picks={game.picks} />
        </section>
      )}

      {game.reasoning && (
        <Card className="mb-8 p-4">
          <div className="text-xs font-medium text-muted">Why the model sees it this way</div>
          {game.key_factors && game.key_factors.length > 0 && (
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm text-secondary marker:text-accent">
              {game.key_factors.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          )}
          <details className="mt-2 text-sm text-secondary">
            <summary className="cursor-pointer text-xs font-medium text-accent">Full reasoning</summary>
            <p className="mt-1.5 whitespace-pre-line">{game.reasoning}</p>
          </details>
        </Card>
      )}

      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-primary">
            <span className="h-3.5 w-1 rounded-full bg-accent" aria-hidden />
            {view === "actual" ? "Box score" : view === "avg" ? "Season averages" : "Projected box score"}
          </h2>
          {view !== "actual" && (
            <p className="mt-0.5 pl-3 text-xs text-muted">
              {view === "avg"
                ? `Per game${avgSeason ? ` in ${avgSeason}` : ""}, for everyone expected to play. PRA is points + rebounds + assists.`
                : "The model's expected line for this game. PRA is points + rebounds + assists."}
            </p>
          )}
        </div>
        <Segmented
          label="Numbers shown"
          value={view}
          onChange={setChosen}
          options={[
            final ? { value: "actual", label: "What happened" } : { value: "avg", label: "Season averages" },
            { value: "proj", label: final ? "What the model projected" : "Model projection" },
          ]}
        />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <BoxScore side={game.away_side} view={view} />
        <BoxScore side={game.home_side} view={view} />
      </div>
    </div>
  );
}
