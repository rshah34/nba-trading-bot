"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { PickCards } from "@/components/PickList";
import { ReportList } from "@/components/Reports";
import { Card, Empty, PageState, SectionTitle, TeamMark } from "@/components/ui";
import { useApi } from "@/lib/api";
import { clock, longDate, pct, seriesLine, todayIso } from "@/lib/format";
import type { GameCard, Report, Slate } from "@/lib/types";

/** A probability as a thin meter with its number beside it. */
function ProbRow({ label, value, fill }: { label: string; value: number | null; fill: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-12 text-muted">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--gridline)]">
        {value != null && <div className={`h-full rounded-full ${fill}`} style={{ width: `${value * 100}%` }} />}
      </div>
      <span className="tnum w-9 text-right font-medium text-primary">{pct(value)}</span>
    </div>
  );
}

function GameTile({ game }: { game: GameCard }) {
  const final = game.status === "final";
  const homeWon = final && (game.home.score ?? 0) > (game.away.score ?? 0);
  return (
    <Link href={`/games/${game.game_id}`} className="block">
      <Card className="h-full p-4 transition-colors hover:border-[var(--text-muted)]">
        <div className="flex items-center justify-between text-xs text-muted">
          {final ? (
            <span className="rounded-full bg-[var(--gridline)] px-2 py-0.5 font-medium text-secondary">Final</span>
          ) : (
            <span className="rounded-full bg-accent/15 px-2 py-0.5 font-medium text-accent">
              {clock(game.tipoff_at) || "Scheduled"}
            </span>
          )}
          <span>
            {game.series ? seriesLine(game.home.abbr, game.away.abbr, game.series) : game.preseason ? "Preseason" : ""}
          </span>
        </div>
        <div className="mt-2 space-y-1">
          {([
            [game.away, final && !homeWon],
            [game.home, homeWon],
          ] as const).map(([team, won]) => (
            <div key={team.id} className="flex items-baseline justify-between gap-2">
              <span className={`flex items-center gap-2 text-sm ${!final || won ? "font-semibold text-primary" : "text-secondary"}`}>
                <TeamMark abbr={team.abbr} className="self-center" />
                {team.name}
                {team.record && <span className="tnum ml-1.5 text-xs font-normal text-muted">{team.record}</span>}
              </span>
              <span className={`tnum text-lg ${won ? "font-semibold text-primary" : "text-secondary"}`}>
                {team.score ?? ""}
              </span>
            </div>
          ))}
        </div>
        {(game.model_home_prob != null || game.market_home_prob != null) && (
          <div className="mt-3 space-y-1 border-t border-[var(--border)] pt-3">
            <div className="text-[11px] text-muted">{game.home.abbr} win probability</div>
            <ProbRow label="Model" value={game.model_home_prob} fill="bg-series-1" />
            <ProbRow label="Market" value={game.market_home_prob} fill="bg-series-rest" />
          </div>
        )}
      </Card>
    </Link>
  );
}

/** The strongest current reports, league-wide. */
function LatestNews() {
  const { data } = useApi<Report[]>("/news");
  if (!data || data.length === 0) return null;
  return (
    <section>
      <SectionTitle>
        <Link href="/news" className="hover:underline">
          Injury and role news →
        </Link>
      </SectionTitle>
      <Card className="px-4 py-1">
        <ReportList reports={data.slice(0, 6)} columns />
      </Card>
    </section>
  );
}

function Tonight() {
  const router = useRouter();
  const date = useSearchParams().get("date");
  const { data, error, loading } = useApi<Slate>(date ? `/slate?date=${date}` : "/slate");
  const go = (d: string | null) => router.push(d ? `/?date=${d}` : "/");

  if (!data) return <PageState loading={loading} error={error} />;
  const isToday = data.date === todayIso();
  const navButton = "rounded-lg border border-[var(--border)] bg-surface px-2.5 py-1 text-xs font-medium text-secondary enabled:hover:text-primary disabled:opacity-40";

  return (
    <div className={loading ? "opacity-60" : ""}>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-primary">{longDate(data.date)}</h1>
          <p className="mt-1 text-sm text-secondary">
            {data.games.length} game{data.games.length === 1 ? "" : "s"}
            {isToday ? " tonight" : ""}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <button type="button" className={navButton} disabled={!data.prev} onClick={() => go(data.prev)}>
            ← Previous
          </button>
          <button type="button" className={navButton} disabled={isToday} onClick={() => go(todayIso())}>
            Today
          </button>
          <button type="button" className={navButton} disabled={!data.next} onClick={() => go(data.next)}>
            Next →
          </button>
          <input
            type="date"
            aria-label="Jump to a date"
            value={data.date}
            onChange={(e) => e.target.value && go(e.target.value)}
            className="rounded-lg border border-[var(--border)] bg-surface px-2 py-1 text-xs text-primary"
          />
        </div>
      </div>

      <section className="mb-8">
        <SectionTitle>Picks</SectionTitle>
        {data.picks.length > 0 ? (
          <PickCards picks={data.picks} />
        ) : (
          <Card className="border-dashed px-4 py-3 text-sm text-secondary">
            No picks for this day.
          </Card>
        )}
      </section>

      <section>
        <SectionTitle>Games</SectionTitle>
        {data.games.length > 0 ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {data.games.map((g) => (
              <GameTile key={g.game_id} game={g} />
            ))}
          </div>
        ) : (
          <Empty title="No games on this date" />
        )}
      </section>

      <div className="mt-8">
        <LatestNews />
      </div>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<PageState loading error={null} />}>
      <Tonight />
    </Suspense>
  );
}
