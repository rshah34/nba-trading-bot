"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { PickCards } from "@/components/PickList";
import { ReportList } from "@/components/Reports";
import { Card, Empty, PageState, SectionTitle } from "@/components/ui";
import { useApi } from "@/lib/api";
import { clock, longDate, num, pct, signed, todayIso } from "@/lib/format";
import type { GameCard, PlayersIndex, Report, Slate } from "@/lib/types";

/** A probability as a thin meter with its number beside it. */
function ProbRow({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-12 text-muted">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--gridline)]">
        {value != null && <div className="h-full rounded-full bg-series-1" style={{ width: `${value * 100}%` }} />}
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
          <span>{final ? "Final" : clock(game.tipoff_at) || "Scheduled"}</span>
          {game.preseason && <span>Preseason</span>}
        </div>
        <div className="mt-2 space-y-1">
          {([
            [game.away, final && !homeWon],
            [game.home, homeWon],
          ] as const).map(([team, won]) => (
            <div key={team.id} className="flex items-baseline justify-between gap-2">
              <span className={`text-sm ${!final || won ? "font-semibold text-primary" : "text-secondary"}`}>
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
            <ProbRow label="Model" value={game.model_home_prob} />
            <ProbRow label="Market" value={game.market_home_prob} />
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
      <SectionTitle hint="Firmest and most widely reported first.">
        <Link href="/news" className="hover:underline">
          Injury and role news →
        </Link>
      </SectionTitle>
      <Card className="px-4 py-1">
        <ReportList reports={data.slice(0, 5)} />
      </Card>
    </section>
  );
}

/** Whose scoring has moved most over his last five games. */
function Trending() {
  const { data } = useApi<PlayersIndex>("/players");
  if (!data) return null;
  const regulars = data.players
    .filter((p) => p.l5.gp === 5 && p.l5.min >= 20)
    .map((p) => ({ ...p, delta: p.l5.pts - p.season.pts }))
    .sort((a, b) => b.delta - a.delta);
  const rows = [...regulars.slice(0, 5), ...regulars.slice(-5).reverse()];
  if (rows.length === 0) return null;
  return (
    <section>
      <SectionTitle hint={`Points over the last five games against the ${data.season} average, players at 20+ minutes.`}>
        <Link href="/players" className="hover:underline">
          Heating up and cooling off →
        </Link>
      </SectionTitle>
      <Card className="px-4 py-1">
        <ul className="tnum divide-y divide-[var(--border)] text-sm">
          {rows.map((p) => (
            <li key={p.id} className="flex items-baseline justify-between gap-3 py-2">
              <Link href={`/players/${p.id}`} className="truncate font-medium text-primary hover:underline">
                {p.name} <span className="text-xs font-normal text-muted">{p.team}</span>
              </Link>
              <span className="shrink-0 text-secondary">
                {num(p.l5.pts)} <span className="text-xs text-muted">vs {num(p.season.pts)}</span>
                <span className="ml-2 inline-block w-11 text-right font-medium text-primary">{signed(p.delta)}</span>
              </span>
            </li>
          ))}
        </ul>
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
            {isToday ? " tonight" : ""}. Open one for both teams&apos; projected box scores, injuries and news.
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <button type="button" className={navButton} disabled={!data.prev} onClick={() => go(data.prev)}>
            ← Previous
          </button>
          <button type="button" className={navButton} onClick={() => go(null)}>
            Next games
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
        <SectionTitle hint="At most three a day across game winners and player props, chosen where the model disagrees most with current prices. Paper bets — the bot never places one.">
          Picks
        </SectionTitle>
        {data.picks.length > 0 ? (
          <PickCards picks={data.picks} />
        ) : (
          <Card className="border-dashed px-4 py-3 text-sm text-secondary">
            No picks for this day. They are made about 90 minutes before the first evening tip, once injury news has
            firmed up; preseason games are never bet.
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

      <div className="mt-8 grid gap-6 lg:grid-cols-2">
        <LatestNews />
        <Trending />
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
