"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { PickCards } from "@/components/PickList";
import { InjuryList, ReportList } from "@/components/Reports";
import { SortableTable, type Column } from "@/components/SortableTable";
import { Card, PageState, SectionTitle, StatusBadge, TeamLink } from "@/components/ui";
import { useApi } from "@/lib/api";
import { clock, longDate, num, pct, STAT_LABELS } from "@/lib/format";
import { pra } from "@/lib/markets";
import type { GameDetail, GamePlayer, GameSide, StatLine } from "@/lib/types";

const COLS = ["min", "pts", "reb", "ast", "fg3m", "stl", "blk", "tov", "pra"] as const;
// Side by side before the game there is room for fewer columns.
const PREGAME_COLS = COLS.filter((c) => !["stl", "blk", "tov"].includes(c));
const value = (line: StatLine | null, key: (typeof COLS)[number]) =>
  line == null ? null : key === "pra" ? pra(line) : line[key];

/** Projected box score; once the game is played, the actual number with the projection beside it. */
function BoxScore({ side, final }: { side: GameSide; final: boolean }) {
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
          <StatusBadge status={r.status ? "Questionable" : null} />
        </span>
      ),
    },
    ...(final ? COLS : PREGAME_COLS).map((key): Column<GamePlayer> => ({
      key,
      header: STAT_LABELS[key],
      align: "right",
      sort: (r) => value(final ? r.actual : r.proj, key),
      render: (r) => {
        const proj = value(r.proj, key);
        if (!final) return num(proj);
        return (
          <>
            <span className="text-primary">{num(value(r.actual, key), 0)}</span>
            <span className="ml-1.5 inline-block w-9 text-left text-xs text-muted">{proj == null ? "" : num(proj)}</span>
          </>
        );
      },
    })),
  ];
  return (
    <Card className="p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="text-sm font-semibold text-primary">
          <TeamLink id={side.team.id} abbr={side.team.name} />
        </h3>
        <span className="text-xs text-muted">{final ? "Actual, with the projection in grey" : "Projected"}</span>
      </div>
      <SortableTable
        rows={side.players}
        columns={columns}
        rowKey={(r) => r.id}
        initialSort={{ key: "min", dir: "desc" }}
      />
      {side.players.some((p) => p.notes.length > 0) && !final && (
        <p className="mt-3 text-xs text-secondary">
          {[...new Set(side.players.flatMap((p) => p.notes))].slice(0, 3).join(" · ")}
        </p>
      )}
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
  if (!game) return <PageState loading={loading} error={error} />;
  const final = game.status === "final";

  return (
    <div>
      <Link href={`/?date=${game.date}`} className="text-xs text-secondary hover:text-primary">
        ← {longDate(game.date)}
      </Link>
      <div className="mb-6 mt-2 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-primary">
            {game.away.name} <span className="font-normal text-muted">at</span> {game.home.name}
          </h1>
          <p className="mt-1 text-sm text-secondary">
            {final ? `Final: ${game.away.abbr} ${game.away.score}, ${game.home.abbr} ${game.home.score}` : `Tip ${clock(game.tipoff_at) || "time not set"}`}
            {game.preseason ? " · Preseason (exhibition minutes; projections are for regular-season roles)" : ""}
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
          <p className="mt-1.5 text-sm text-secondary">{game.reasoning}</p>
          {game.key_factors && game.key_factors.length > 0 && (
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm text-secondary">
              {game.key_factors.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          )}
        </Card>
      )}

      <SectionTitle
        hint={
          final
            ? "What each player did, next to what the model would have projected for the players who took the floor."
            : "Each team shares one minutes budget: when rotation players are out, the rest absorb their minutes."
        }
      >
        Box scores
      </SectionTitle>
      <div className={`grid gap-4 ${final ? "" : "lg:grid-cols-2"}`}>
        <BoxScore side={game.away_side} final={final} />
        <BoxScore side={game.home_side} final={final} />
      </div>
    </div>
  );
}
