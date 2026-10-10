"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { GameLogChart, type LogPoint } from "@/components/GameLogChart";
import { ReportList } from "@/components/Reports";
import { SortableTable, type Column } from "@/components/SortableTable";
import { Card, Empty, PageState, SectionTitle, Segmented, StatusBadge, TeamLink, Tile } from "@/components/ui";
import { useApi } from "@/lib/api";
import { clock, fairOdds, num, pct, shortDate, signed, STAT_LABELS } from "@/lib/format";
import { MARKETS, marketValue } from "@/lib/markets";
import type { PlayerDetail, PlayerGame, StatKey } from "@/lib/types";

type Venue = "all" | "home" | "away";
const WINDOWS = [5, 10, 20] as const;
const LOG_STATS: StatKey[] = ["pts", "reb", "ast", "fg3m", "stl", "blk", "tov"];
const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const halfPoint = (x: number) => Math.max(0.5, Math.floor(x) + 0.5);

/** Test any line for any market against the player's games and the model's projection. */
function PropExplorer({ player, season }: { player: PlayerDetail; season: string }) {
  const seasonGames = useMemo(() => player.games.filter((g) => g.season === season), [player, season]);

  const [marketKey, setMarketKey] = useState(
    () => MARKETS.find((m) => player.lines[m.key])?.key ?? "player_points",
  );
  const [span, setSpan] = useState<number | "season">(10);
  const [venue, setVenue] = useState<Venue>("all");
  const market = MARKETS.find((m) => m.key === marketKey)!;
  const projected = player.projection?.markets[marketKey];
  const book = player.lines[marketKey];

  // The line starts at the book's number if there is one, else just under the projection or average.
  const defaultLine = useMemo(() => {
    if (book) return book.line;
    if (projected) return halfPoint(projected.mean - 0.01);
    return halfPoint((avg(seasonGames.map((g) => marketValue(g, market.stats))) ?? 0.5) - 0.01);
  }, [book, projected, seasonGames, market]);
  const [custom, setCustom] = useState<{ market: string; line: number } | null>(null);
  const line = custom?.market === marketKey ? custom.line : defaultLine;
  const setLine = (v: number) => setCustom({ market: marketKey, line: Math.max(0.5, v) });

  const eligible = seasonGames.filter((g) => venue === "all" || (venue === "home") === (g.home ?? true));
  const shown = (span === "season" ? eligible : eligible.slice(0, span)).slice().reverse();
  const points: LogPoint[] = shown.map((g) => ({
    key: g.game_id,
    date: g.date,
    opp: g.opp ?? "",
    home: g.home,
    value: marketValue(g, market.stats),
    minutes: g.min,
    result: g.result,
  }));

  const hitRate = (games: PlayerGame[]) => {
    const vals = games.map((g) => marketValue(g, market.stats));
    return { over: vals.filter((v) => v > line).length, n: vals.length };
  };
  const pOver = projected?.ladder.find(([l]) => l === line)?.[1] ?? (projected ? 0 : null);
  const guide = market.stats.length === 1 ? player.projection?.guide[market.stats[0]] : undefined;
  const step = "rounded-md border border-[var(--border)] px-2 py-1 text-sm font-medium text-secondary hover:text-primary";

  return (
    <Card className="p-4">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1" role="group" aria-label="Market">
          {MARKETS.map((m) => (
            <button
              key={m.key}
              type="button"
              aria-pressed={m.key === marketKey}
              onClick={() => setMarketKey(m.key)}
              className={`rounded-md px-2 py-1 text-xs font-medium ${
                m.key === marketKey ? "bg-primary text-plane" : "border border-[var(--border)] text-secondary hover:text-primary"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Segmented
          label="Games shown"
          value={String(span)}
          onChange={(v) => setSpan(v === "season" ? "season" : Number(v))}
          options={[...WINDOWS.map((w) => ({ value: String(w), label: `Last ${w}` })), { value: "season", label: `All of ${season}` }]}
        />
        <Segmented
          label="Venue"
          value={venue}
          onChange={setVenue}
          options={[
            { value: "all", label: "All" },
            { value: "home", label: "Home" },
            { value: "away", label: "Away" },
          ]}
        />
        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-xs text-muted">Line</span>
          <button type="button" className={step} aria-label="Lower the line" onClick={() => setLine(line - 1)}>
            −
          </button>
          <input
            type="number"
            step={1}
            min={0.5}
            value={line}
            aria-label="Line"
            onChange={(e) => Number.isFinite(e.target.valueAsNumber) && setLine(halfPoint(e.target.valueAsNumber - 0.01))}
            className="tnum w-16 rounded-md border border-[var(--border)] bg-surface px-2 py-1 text-center text-sm font-semibold text-primary"
          />
          <button type="button" className={step} aria-label="Raise the line" onClick={() => setLine(line + 1)}>
            +
          </button>
          {book && line !== book.line && (
            <button type="button" className={`${step} text-xs`} onClick={() => setCustom(null)}>
              Book line {book.line}
            </button>
          )}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_260px]">
        {points.length > 0 ? (
          <GameLogChart points={points} line={line} unit={market.label} />
        ) : (
          <p className="py-16 text-center text-sm text-muted">No games match these filters.</p>
        )}
        <div className="space-y-4">
          <div>
            <div className="text-xs font-medium text-muted">Went over {line} {market.label}</div>
            <dl className="tnum mt-1.5 space-y-1 text-sm">
              {[...WINDOWS.map((w) => [`Last ${w}`, eligible.slice(0, w)] as const), [season, eligible] as const].map(
                ([label, games]) => {
                  const r = hitRate(games);
                  return (
                    <div key={label} className="flex items-baseline justify-between">
                      <dt className="text-secondary">{label}</dt>
                      <dd className="font-semibold text-primary">
                        {r.n ? pct(r.over / r.n) : "—"}
                        <span className="ml-1 text-xs font-normal text-muted">
                          {r.over}/{r.n}
                        </span>
                      </dd>
                    </div>
                  );
                },
              )}
            </dl>
          </div>
          <div className="border-t border-[var(--border)] pt-3">
            <div className="text-xs font-medium text-muted">Model, next game</div>
            {projected && pOver != null ? (
              <>
                <div className="mt-1 text-sm text-secondary">
                  Projects <span className="font-semibold text-primary">{num(projected.mean)}</span> {market.label} in{" "}
                  {num(player.projection!.minutes, 0)} min
                </div>
                <dl className="tnum mt-2 grid grid-cols-2 gap-2 text-sm">
                  {([
                    ["Over", pOver],
                    ["Under", 1 - pOver],
                  ] as const).map(([side, p]) => (
                    <div key={side} className="rounded-lg border border-[var(--border)] px-2.5 py-1.5">
                      <dt className="text-xs text-muted">{side} {line}</dt>
                      <dd className="font-semibold text-primary">{pct(p)}</dd>
                      <dd className="text-xs text-secondary">fair {fairOdds(p)}</dd>
                    </div>
                  ))}
                </dl>
                {book && (
                  <p className="tnum mt-2 text-xs text-secondary">
                    Books: {book.line} at {book.books} book{book.books === 1 ? "" : "s"}, {pct(book.over)} over.
                  </p>
                )}
                {guide && (
                  <p className="tnum mt-2 text-xs text-secondary">
                    Model alone would take the over at {guide.over_at ?? "no line"} or lower, the under at{" "}
                    {guide.under_at ?? "no line"} or higher.
                  </p>
                )}
              </>
            ) : (
              <p className="mt-1 text-sm text-secondary">No projection: he isn&apos;t in his team&apos;s current rotation, or has too few games.</p>
            )}
          </div>
        </div>
      </div>
      {player.projection && player.projection.notes.length > 0 && (
        <p className="mt-3 border-t border-[var(--border)] pt-3 text-xs text-secondary">{player.projection.notes.join(" · ")}</p>
      )}
    </Card>
  );
}

export default function PlayerPage() {
  const { id } = useParams<{ id: string }>();
  const asked = useSearchParams().get("season");
  const { data: player, error, loading } = useApi<PlayerDetail>(`/players/${id}`);
  const [picked, setPicked] = useState<string | null>(null);
  if (!player || String(player.id) !== id) return <PageState loading={loading} error={error} />;

  // The newest season unless one was asked for, even when he has no games in it:
  // an empty season is shown as empty, not skipped.
  const wanted = picked ?? asked;
  const season = wanted && player.seasons.includes(wanted) ? wanted : player.seasons[0];
  const played = (s: string) => player.games.filter((g) => g.season === s);
  const games = played(season);
  const otherSeasons = player.seasons.filter((s) => s !== season && played(s).length > 0);
  const teamsThatSeason = [...new Set(games.map((g) => g.team).filter(Boolean))].reverse();
  const mean = (list: PlayerGame[], k: StatKey | "min") => avg(list.map((g) => g[k]));
  const columns: Column<PlayerGame>[] = [
    { key: "date", header: "Date", sort: (g) => g.date, render: (g) => <Link href={`/games/${g.game_id}`} className="hover:underline">{shortDate(g.date)}</Link> },
    { key: "opp", header: "Opp", sort: (g) => g.opp, render: (g) => `${g.home === false ? "@" : "vs"} ${g.opp ?? ""}` },
    { key: "result", header: "Result", render: (g) => g.result ?? "" },
    ...(["min", ...LOG_STATS] as const).map((k): Column<PlayerGame> => ({
      key: k,
      header: STAT_LABELS[k],
      align: "right",
      sort: (g) => g[k],
      render: (g) => num(g[k], 0),
    })),
  ];

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight text-primary">{player.name}</h1>
            <StatusBadge status={player.status?.label} />
          </div>
          <p className="mt-1 text-sm text-secondary">
            {player.team ? <TeamLink id={player.team.id} abbr={player.team.name} /> : "No current team"}
            {player.next && (
              <>
                {" · next: "}
                <Link href={`/games/${player.next.game_id}`} className="hover:underline">
                  {player.next.home ? "vs" : "at"} {player.next.opp}, {shortDate(player.next.date)} {clock(player.next.tipoff_at)}
                </Link>
                {player.next.preseason ? " (preseason)" : ""}
              </>
            )}
          </p>
          {player.status?.detail && <p className="mt-1 max-w-2xl text-xs text-secondary">{player.status.detail}</p>}
        </div>
        {player.seasons.length > 0 && (
          <div className="text-right">
            <Segmented
              label="Season"
              value={season}
              onChange={setPicked}
              options={player.seasons.map((s) => ({ value: s, label: s }))}
            />
            <p className="tnum mt-1.5 text-xs text-muted">
              {games.length > 0
                ? `${games.length} game${games.length === 1 ? "" : "s"} in ${season} · ${teamsThatSeason.join(", ")}`
                : `No games in ${season}`}
            </p>
          </div>
        )}
      </div>

      {games.length === 0 ? (
        <Empty title={season ? `${player.name} has no games in ${season}` : `No regular-season games on record for ${player.name} yet`}>
          {otherSeasons.length > 0 ? (
            <>
              He didn&apos;t play a regular-season game that season. Games on record:{" "}
              {otherSeasons.map((s, i) => (
                <span key={s}>
                  {i > 0 && ", "}
                  <button type="button" className="font-medium text-primary underline" onClick={() => setPicked(s)}>
                    {s} ({played(s).length})
                  </button>
                </span>
              ))}
              .
            </>
          ) : (
            "Stats appear here after his first regular-season game."
          )}
        </Empty>
      ) : (
        <>
          <div className="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-5">
            {(["pts", "reb", "ast", "fg3m", "min"] as const).map((k) => {
              const s = mean(games, k)!;
              const recent = mean(games.slice(0, 5), k)!;
              return (
                <Tile
                  key={k}
                  label={`${STAT_LABELS[k]} · ${season}`}
                  value={num(s)}
                  hint={<span className="tnum">Last 5 of {season}: {num(recent)} ({signed(recent - s)})</span>}
                />
              );
            })}
          </div>

          <section className="mb-8">
            <SectionTitle hint={`Pick a market, set a line, and see how often he cleared it in ${season} and what the model gives it for his next game.`}>
              Prop explorer
            </SectionTitle>
            <PropExplorer key={`${player.id}-${season}`} player={player} season={season} />
          </section>
        </>
      )}

      {player.reports.length > 0 && (
        <section className="my-8">
          <SectionTitle>News</SectionTitle>
          <Card className="px-4 py-1">
            <ReportList reports={player.reports} showPlayer={false} />
          </Card>
        </section>
      )}

      {games.length > 0 && (
        <section>
          <SectionTitle>Game log · {season}</SectionTitle>
          <Card className="p-2">
            <SortableTable key={season} rows={games} columns={columns} rowKey={(g) => g.game_id} pageSize={15} />
          </Card>
        </section>
      )}
    </div>
  );
}
