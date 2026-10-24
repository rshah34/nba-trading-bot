"""Props agent: the DB-touching orchestration around the prop projection model.

Capture prop odds → project every player with a quote (sharing one minutes budget
per team) → bet where the blended probability beats the consensus price → settle
against the box score with CLV. Also logs every line it priced, bet or not, so the
model is scored against real books on hundreds of lines a night.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot import betting
from nba_bot.agents import data_agent, news_agent
from nba_bot.config import my_books, settings
from nba_bot.data import odds_api
from nba_bot.data.nba_stats import is_preseason
from nba_bot.data.team_lookup import resolve_team_id
from nba_bot.db.models import (
    REAL_GAMES,
    Game,
    PlayerGameStats,
    PropBet,
    PropOdds,
    PropProjection,
    Team,
)
from nba_bot.features.props import (
    BUDGET_UP_NOTE,
    GUIDE_PROB,
    MARKETS,
    ROTATION_MIN,
    Adjustment,
    GameLine,
    Projection,
    PropsData,
    TeamForm,
    game_context,
    load_props_data,
    normalize_name,
    over_under_probs,
    project_team,
    stat_guide,
    team_form,
)

log = logging.getLogger("nba_bot.props")

MODEL_VERSION = "props-v1"
# A player belongs to a team's current rotation if he has appeared for it this recently.
_ROSTER_WINDOW = timedelta(days=21)
# Rows this close to a game's newest snapshot are that snapshot.
_SNAPSHOT_WINDOW = timedelta(minutes=30)
# A game snapshotted this recently isn't bought again by the next props run.
_RESNAPSHOT_AFTER = timedelta(hours=6)
_MIN_PROJECTED_MINUTES = 15.0
_UNCERTAIN_STATUSES = {"day-to-day", "questionable", "doubtful", "gtd"}
# An uncertain player this big changes his teammates' minutes either way he goes.
_SWING_MINUTES = 20.0


def prop_markets() -> tuple[str, ...]:
    """Configured prop markets, limited to the ones the model can project."""
    wanted = [m.strip() for m in settings.prop_markets.split(",") if m.strip()]
    return tuple(m for m in wanted if m in MARKETS)


# --------------------------------------------------------------------------- #
# Odds capture.
# --------------------------------------------------------------------------- #
def _resolve_player(data: PropsData, name: str, team_ids: set[int]) -> int | None:
    """Match a sportsbook's player name to a player id. Two players can share a
    name, so prefer the one on a team in this game; otherwise require uniqueness."""
    ids = data.name_index().get(normalize_name(name), [])
    here = [pid for pid in ids if data.current_team.get(pid) in team_ids]
    if len(here) == 1:
        return here[0]
    return ids[0] if len(ids) == 1 else None


# Typical slate, for pacing the month's credits.
_GAMES_PER_DAY = 7.5
_GAME_LINE_SNAPSHOTS_PER_DAY = 3


def days_until_reset(today: date, reset_day: int) -> int:
    """Whole days after today until the quota refills."""
    if today.day < reset_day:
        return reset_day - today.day - 1
    next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    return (next_month - today).days + reset_day - 2


def credits_to_hold_back(days_left: int, n_prop_markets: int, n_game_markets: int) -> float:
    """Credits the rest of the quota month still needs for the essentials: the
    props snapshot that bets are made from, and the game lines. Closing snapshots
    are the one optional purchase, so they only spend what is left above this."""
    per_day = _GAMES_PER_DAY * n_prop_markets + _GAME_LINE_SNAPSHOTS_PER_DAY * n_game_markets
    return days_left * per_day


def sync_prop_odds(
    session: Session,
    *,
    data: PropsData | None = None,
    only_open_bets: bool = False,
    window_hours: float = 20,
) -> dict:
    """Snapshot player-prop odds for every game tipping within `window_hours`.

    Each game costs one credit per market, so nothing is bought twice: a game
    already snapshotted this afternoon is skipped.

    `only_open_bets` is the closing snapshot: it re-prices just the markets we hold
    bets in, which is all CLV needs. It is also the only optional purchase, so on a
    tight quota it is the first thing to go — it runs only while the month's
    essentials are still covered (`credits_to_hold_back`).
    """
    data = data or load_props_data(session)
    game_index = data_agent._build_game_index(session)
    markets = prop_markets()

    open_markets: dict[str, set[str]] = defaultdict(set)
    if only_open_bets:
        for gid, market in session.execute(
            select(PropBet.game_id, PropBet.market).where(PropBet.settled_at.is_(None))
        ):
            open_markets[gid].add(market)

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=window_hours)
    recent = set(session.execute(
        select(PropOdds.game_id).where(PropOdds.captured_at >= now - _RESNAPSHOT_AFTER).distinct()
    ).scalars())

    events, quota = odds_api.fetch_events(settings.odds_api_key)
    floor = float(settings.odds_quota_reserve)
    if only_open_bets:
        n_game_markets = len([m for m in settings.game_markets.split(",") if m.strip()])
        floor += credits_to_hold_back(
            days_until_reset(now.date(), settings.odds_quota_reset_day), len(markets), n_game_markets
        )
    out = {"events": 0, "rows": 0, "unmatched_players": 0, "skipped": 0, "quota": quota,
           "stopped_for_quota": False}

    for event in events:
        tip = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
        if not (now < tip <= horizon):
            continue  # already started (in-play prices would poison the close) or not today
        home_id, away_id = resolve_team_id(event["home_team"]), resolve_team_id(event["away_team"])
        game_id = (data_agent._match_game_id(game_index, tip.date(), home_id, away_id)
                   if home_id and away_id else None)
        if game_id is None or is_preseason(game_id):
            out["skipped"] += 1
            continue
        if not only_open_bets and game_id in recent:
            continue  # an earlier run today already bought this game
        wanted = tuple(m for m in markets if m in open_markets[game_id]) if only_open_bets else markets
        if not wanted:
            continue

        remaining = (out["quota"] or {}).get("remaining")
        if remaining is not None and float(remaining) - len(wanted) < floor:
            out["stopped_for_quota"] = True
            break

        raw, quota = odds_api.fetch_event_props(
            settings.odds_api_key, event["id"], wanted, settings.odds_bookmakers)
        out["quota"] = quota
        out["events"] += 1
        for offer in odds_api.parse_event_props(raw):
            pid = _resolve_player(data, offer.player, {home_id, away_id})
            if pid is None:
                out["unmatched_players"] += 1
            session.add(PropOdds(
                game_id=game_id, sportsbook=offer.sportsbook, market=offer.market,
                player_name=offer.player, player_id=pid, line=offer.line,
                over_price=offer.over_price, under_price=offer.under_price, captured_at=now,
            ))
            out["rows"] += 1
    session.commit()
    return out


# --------------------------------------------------------------------------- #
# Live projections.
# --------------------------------------------------------------------------- #
@dataclass
class Slate:
    """What every team's projections for one date share."""

    game_date: date
    as_of: datetime
    factors: dict[int, dict[str, float]]    # team -> what it allows vs. the league
    form: dict[int, TeamForm]
    signals: dict[int, list]                # player -> live news signals
    unplayed: dict[int, int]                # team -> its unplayed games before this date


def slate_for(session: Session, data: PropsData, game_date: date, as_of: datetime) -> Slate:
    season = data_agent._season_for_date(game_date)
    unplayed: dict[int, int] = defaultdict(int)
    for home, away in session.execute(
        select(Game.home_team_id, Game.away_team_id).where(
            REAL_GAMES, Game.status == "scheduled",
            Game.game_date >= as_of.date(), Game.game_date < game_date)
    ):
        unplayed[home] += 1
        unplayed[away] += 1
    return Slate(game_date, as_of, data.opponent_factors(game_date, season),
                 team_form(data, game_date, season), news_agent.active_signals(session, as_of),
                 dict(unplayed))


@dataclass
class TeamView:
    """One team's projections under both readings of its uncertain players."""

    projections: dict[int, Projection]      # uncertain players assumed to play
    alt: dict[int, Projection] | None       # ...assumed to sit (None = no swing player)
    blocked: dict[int, str]                 # player -> why we won't bet him
    out: list[int]                          # ruled out, biggest role first


def _name_absences(projections: dict[int, Projection], names: list[str]) -> None:
    """Say who is out in the "teammates out" note."""
    if not names:
        return
    who = BUDGET_UP_NOTE.replace("teammates", " and ".join(names))
    for proj in projections.values():
        proj.notes = [who if n == BUDGET_UP_NOTE else n for n in proj.notes]


def team_view(
    session: Session, data: PropsData, slate: Slate, team_id: int, opp_id: int, home: bool,
    preseason: bool = False,
) -> TeamView:
    """`preseason` drops the game context: exhibition games are no one's debut or return,
    and their rest, venue and margin say nothing about a player's role."""
    game_date = slate.game_date
    season = data_agent._season_for_date(game_date)
    window = game_date - _ROSTER_WINDOW
    # The roster is the league's list. Once the team has played a game that counts,
    # only those who have played one lately are projected, so a healthy scratch takes
    # no one's minutes; before that, many regulars sit out preseason, so everyone on
    # the list is, and the budgets below wait for a real game.
    form = slate.form.get(team_id)
    settled = form is not None and bool(form.dates)

    def recent(pid: int) -> bool:
        if not settled:
            return True
        log = data.log_before(pid, game_date)
        return bool(log) and log[-1].game_date >= window

    roster = {pid for pid, team in data.current_team.items() if team == team_id and recent(pid)}
    # Someone on the injury report in the last few days is on his way back (or still
    # out, which the next block handles), however long ago he last played.
    for name in data_agent.recently_listed(session, team_id, slate.as_of):
        pid = _resolve_player(data, name, {team_id})
        if pid is not None and data.current_team.get(pid) == team_id:
            roster.add(pid)

    out_ids: set[int] = set()
    blocked: dict[int, str] = {}
    for inj in data_agent.current_injuries(session, team_id, slate.as_of, game_date):
        pid = _resolve_player(data, inj["player"], {team_id})
        if pid is None:
            continue
        status = (inj["status"] or "").lower()
        if status == "out":
            out_ids.add(pid)
        elif status in _UNCERTAIN_STATUSES:
            blocked[pid] = f"listed {inj['status']}"

    extra: dict[int, Adjustment] = {}
    for pid in roster:
        view = news_agent.player_view(slate.signals.get(pid, []))
        if view.out:
            out_ids.add(pid)
        elif view.uncertain:
            blocked.setdefault(pid, view.reason)
        if view.adjustment is not None:
            extra[pid] = view.adjustment

    def recent_minutes(pid: int) -> float:
        recent: list[GameLine] = data.log_before(pid, game_date)[-5:]
        return sum(g.minutes for g in recent) / len(recent) if recent else 0.0

    out = sorted((pid for pid in out_ids if pid in roster), key=recent_minutes, reverse=True)
    # Only a player who was in the rotation lately is the reason minutes are going
    # spare tonight; someone out for months has long since been absorbed.
    missing = [data.names[pid] for pid in out
               if recent_minutes(pid) >= ROTATION_MIN and data.last_seen[pid] >= window][:2]

    factors = slate.factors.get(opp_id)
    context = None if preseason else game_context(
        slate.form, team_id, opp_id, home, game_date, factors, slate.unplayed.get(team_id, 0))
    logs = {pid: data.log_before(pid, game_date) for pid in roster if pid not in out_ids}

    def project(source: dict[int, list[GameLine]]) -> dict[int, Projection]:
        projections = project_team(source, opp_factors=factors, extra=extra, context=context,
                                   use_budget=settled, season=season)
        _name_absences(projections, missing)
        return projections

    projections = project(logs)
    # Who else plays swings everyone's minutes, and a wrong guess is worse than no
    # guess (backtest: stale availability loses to no availability). So when a real
    # rotation player is a coin flip, price the team both ways and only bet what
    # holds up under each.
    swing = {pid for pid in blocked
             if pid in projections and projections[pid].minutes >= _SWING_MINUTES}
    alt = project({p: lg for p, lg in logs.items() if p not in swing}) if swing else None
    return TeamView(projections, alt, blocked, out)


def latest_quotes(session: Session, game_id: str) -> dict[tuple[int, str], list[betting.PropQuote]]:
    """(player, market) -> two-way quotes from the newest snapshot of a game."""
    newest = session.execute(
        select(func.max(PropOdds.captured_at)).where(PropOdds.game_id == game_id)
    ).scalar_one_or_none()
    if newest is None:
        return {}
    rows = session.execute(
        select(PropOdds).where(
            PropOdds.game_id == game_id,
            PropOdds.captured_at >= newest - _SNAPSHOT_WINDOW,
            PropOdds.player_id.is_not(None),
            PropOdds.over_price.is_not(None),
            PropOdds.under_price.is_not(None),
        )
    ).scalars().all()
    quotes: dict[tuple[int, str], list[betting.PropQuote]] = defaultdict(list)
    for r in rows:
        quotes[(r.player_id, r.market)].append(betting.PropQuote(
            sportsbook=r.sportsbook, line=float(r.line),
            over_decimal=betting.american_to_decimal(r.over_price),
            under_decimal=betting.american_to_decimal(r.under_price),
        ))
    return quotes


def consensus(quotes: list[betting.PropQuote]) -> tuple[float, int, float] | None:
    """(most-quoted line, books on it, de-vigged P(over)) for one player-market."""
    by_line: dict[float, list[betting.PropQuote]] = defaultdict(list)
    for q in quotes:
        by_line[q.line].append(q)
    if not by_line:
        return None
    line, group = max(by_line.items(), key=lambda kv: len(kv[1]))
    fair = [betting.devig_two_way(1 / q.over_decimal, 1 / q.under_decimal)[0] for q in group]
    return line, len(group), sum(fair) / len(fair)


# --------------------------------------------------------------------------- #
# Betting.
# --------------------------------------------------------------------------- #
def price_props(
    session: Session,
    game_date: date,
    *,
    data: PropsData | None = None,
    as_of: datetime | None = None,
    log_lines: bool = True,
    min_team_games: int = 5,
    **decide_kwargs,
) -> list[dict]:
    """Price every quoted player line for a date and return the bets worth making,
    best edge first — at most one per player (his markets are heavily correlated),
    and none for a player already bet. Nothing is bet here.

    `log_lines` also stores every line it priced (bet or not) so the model can be
    scored against the market later.
    Bets are only made at the books in `MY_BOOKS`; every book quoted still counts
    toward the consensus.
    `min_team_games`: no bet on a player until he has that many games this season
    for his current team. Opening-week projections rest on last season's role, often
    on another team; the lines are still priced and logged, just not bet.
    """
    data = data or load_props_data(session)
    as_of = as_of or datetime.now(timezone.utc)
    season = data_agent._season_for_date(game_date)
    slate = slate_for(session, data, game_date, as_of)
    names = {t.team_id: t.abbreviation for t in session.execute(select(Team)).scalars()}
    decide_kwargs.setdefault("books", my_books())

    games = session.execute(
        select(Game).where(Game.game_date == game_date, Game.status == "scheduled")
    ).scalars().all()

    candidates: list[dict] = []
    for game in games:
        if is_preseason(game.game_id):
            continue
        if game.tipoff_at is not None and game.tipoff_at <= as_of:
            continue
        quotes = latest_quotes(session, game.game_id)
        if not quotes:
            continue
        views = {
            team: team_view(session, data, slate, team, opp, team == game.home_team_id)
            for team, opp in ((game.home_team_id, game.away_team_id),
                              (game.away_team_id, game.home_team_id))
        }
        matchup = f"{names.get(game.away_team_id, '?')} @ {names.get(game.home_team_id, '?')}"

        per_player: dict[int, dict] = {}
        for (pid, market), qs in quotes.items():
            team = next((t for t, v in views.items() if pid in v.projections), None)
            if team is None or market not in MARKETS:
                continue
            view = views[team]
            proj = view.projections[pid]
            if proj.minutes < _MIN_PROJECTED_MINUTES:
                continue

            cons = consensus(qs)
            if cons is not None and log_lines:
                line, n_books, market_over = cons
                over, under, push = proj.probs(market, line)
                session.execute(
                    pg_insert(PropProjection)
                    .values(game_id=game.game_id, player_id=pid, player_name=data.names[pid],
                            market=market, model_version=MODEL_VERSION, as_of=as_of, line=line,
                            n_books=n_books, market_over=round(market_over, 4),
                            model_over=round(over / (1 - push), 4) if push < 1 else 0.5,
                            proj_mean=round(proj.mean(market), 3), proj_minutes=proj.minutes)
                    .on_conflict_do_nothing(constraint="prop_projections_key")
                )

            settled_in = sum(g.season == season and g.team_id == team
                             for g in data.logs.get(pid, []))
            if pid in view.blocked or settled_in < min_team_games:
                continue
            d = betting.decide_prop(qs, lambda ln, p=proj, m=market: p.probs(m, ln), **decide_kwargs)
            if d is None:
                continue
            notes = list(proj.notes)
            if view.alt is not None:
                other = view.alt.get(pid)
                d2 = other and betting.decide_prop(
                    qs, lambda ln, p=other, m=market: p.probs(m, ln), **decide_kwargs
                )
                if not d2 or (d2.side, d2.line) != (d.side, d.line):
                    continue  # the edge depends on a teammate's coin-flip status
                if d2.expected_value < d.expected_value:
                    d, proj = d2, other
                notes.append("holds whether or not the questionable teammate plays")
            cand = {
                "kind": "prop", "edge": d.edge, "game_id": game.game_id, "matchup": matchup, "tipoff_at": game.tipoff_at,
                "player_id": pid, "player_name": data.names[pid], "team_id": team,
                "team": names.get(team, "?"), "market": market, "decision": d,
                "proj_mean": proj.mean(market), "proj_phi": proj.phis[market],
                "proj_minutes": proj.minutes,
                "reason": _reason(proj.mean(market), d, market, data.log_before(pid, game_date), notes),
                "stat_guide": stat_guide(proj, market),
            }
            best = per_player.get(pid)
            if best is None or d.edge > best["edge"]:
                per_player[pid] = cand
        candidates.extend(per_player.values())

    already = set(session.execute(
        select(PropBet.game_id, PropBet.player_id).where(PropBet.model_version == MODEL_VERSION)
    ).all())
    candidates = [c for c in candidates if (c["game_id"], c["player_id"]) not in already]
    candidates.sort(key=lambda c: c["edge"], reverse=True)
    if log_lines:
        session.commit()
    return candidates


def _reason(mean: float, d: betting.PropDecision, market: str, log: list[GameLine],
            notes: list[str]) -> str:
    """The one line sent with a pick: the projection against the line, how the bet
    would have done lately, and whatever is moving the projection tonight."""
    last = log[-10:]
    hits = sum(g.value(market) > d.line if d.side == "over" else g.value(market) < d.line
               for g in last)
    return "; ".join([f"projects {mean:.1f} against a line of {d.line:g}",
                      f"{d.side} in {hits} of his last {len(last)}", *notes])


def record_prop_bets(session: Session, chosen: list[dict], as_of: datetime) -> int:
    """Record the chosen prop bets. One bet per player per game; returns how many
    were new."""
    placed = 0
    for c in chosen:
        d = c["decision"]
        res = session.execute(
            pg_insert(PropBet)
            .values(
                game_id=c["game_id"], player_id=c["player_id"], player_name=c["player_name"],
                team_id=c["team_id"], model_version=MODEL_VERSION, decided_at=as_of,
                market=c["market"], side=d.side, line=d.line, sportsbook=d.sportsbook,
                n_books=d.n_books, decimal_odds=d.decimal_odds, model_prob=d.model_prob,
                market_prob=d.market_prob, edge=d.edge, ev=d.expected_value,
                stake_fraction=d.stake_fraction, proj_mean=round(c["proj_mean"], 3),
                proj_phi=round(c["proj_phi"], 3), proj_minutes=c["proj_minutes"],
                notes=c["reason"], stat_guide=c["stat_guide"],
                playable_odds=d.playable_odds, playable_line=d.playable_line,
            )
            .on_conflict_do_nothing(constraint="prop_bets_game_player_model_key")
            .returning(PropBet.id)
        )
        placed += res.first() is not None
    session.commit()
    return placed


# --------------------------------------------------------------------------- #
# Settlement + track record.
# --------------------------------------------------------------------------- #
_BOX_COLUMN = {"pts": "points", "reb": "rebounds", "ast": "assists", "stl": "steals",
               "blk": "blocks", "fg3m": "fg3m", "tov": "turnovers"}


def _stat_value(row: PlayerGameStats, market: str) -> int:
    return sum(getattr(row, _BOX_COLUMN[s]) or 0 for s in MARKETS[market])


def _no_push(mean: float, phi: float, line: float, side: str) -> float:
    over, under, push = over_under_probs(mean, phi, line)
    live = 1 - push
    return (over if side == "over" else under) / live if live > 0 else 0.5


def _closing_prob(session: Session, bet: PropBet) -> float | None:
    """De-vigged closing probability of our side, expressed at the line we took.

    Books often close on a different number than we bet. The move from their line
    to ours is translated with the projection's own distribution, so a close of
    25.5 against our over 24.5 correctly reads as the market moving toward us.
    """
    rows = session.execute(
        select(PropOdds).where(
            PropOdds.game_id == bet.game_id, PropOdds.player_id == bet.player_id,
            PropOdds.market == bet.market, PropOdds.captured_at > bet.decided_at,
            PropOdds.over_price.is_not(None), PropOdds.under_price.is_not(None),
        ).order_by(PropOdds.captured_at.desc())
    ).scalars().all()
    latest: dict[str, PropOdds] = {}
    for r in rows:
        latest.setdefault(r.sportsbook, r)
    if not latest:
        return None  # nothing captured after the bet: no close to compare to

    mean, phi, line = float(bet.proj_mean), float(bet.proj_phi), float(bet.line)
    ours = _no_push(mean, phi, line, bet.side)
    probs = []
    for r in latest.values():
        fair_over = betting.devig_two_way(
            1 / betting.american_to_decimal(r.over_price),
            1 / betting.american_to_decimal(r.under_price),
        )[0]
        fair = fair_over if bet.side == "over" else 1 - fair_over
        probs.append(fair + ours - _no_push(mean, phi, float(r.line), bet.side))
    return round(min(max(sum(probs) / len(probs), 0.01), 0.99), 4)


def settle_prop_bets(session: Session) -> dict:
    """Score prop bets (and every priced line) for games whose box score is in."""
    rows = session.execute(
        select(PropBet, Game).join(Game, PropBet.game_id == Game.game_id)
        .where(PropBet.settled_at.is_(None), Game.status == "final")
    ).all()
    pending = session.execute(
        select(PropProjection, Game).join(Game, PropProjection.game_id == Game.game_id)
        .where(PropProjection.actual_value.is_(None), Game.status == "final")
    ).all()
    candidates = {g.game_id for _, g in rows} | {g.game_id for _, g in pending}
    have_box = set(session.execute(
        select(PlayerGameStats.game_id).where(PlayerGameStats.game_id.in_(candidates)).distinct()
    ).scalars()) if candidates else set()

    settled = 0
    for bet, game in rows:
        if game.game_id not in have_box:
            continue  # final score is in but the box score isn't yet
        box = session.get(PlayerGameStats, (bet.game_id, bet.player_id))
        if box is None or float(box.minutes or 0) <= 0:
            bet.result, bet.pnl = "void", 0.0  # books refund a prop when he doesn't play
        else:
            bet.actual_value = _stat_value(box, bet.market)
            if bet.stat_guide:
                # A new dict, so the JSON column is seen as changed.
                bet.stat_guide = {
                    stat: {**g, "actual": getattr(box, _BOX_COLUMN[stat]) or 0}
                    for stat, g in bet.stat_guide.items()
                }
            bet.result, bet.pnl = betting.settle_prop(
                bet.side, float(bet.line), bet.actual_value,
                float(bet.decimal_odds), float(bet.stake_fraction),
            )
            bet.closing_prob = _closing_prob(session, bet)
            if bet.closing_prob is not None:
                bet.clv = round(float(bet.closing_prob) - float(bet.market_prob), 4)
        bet.settled_at = datetime.now(timezone.utc)
        settled += 1

    scored = 0
    for proj, game in pending:
        if game.game_id not in have_box:
            continue
        box = session.get(PlayerGameStats, (proj.game_id, proj.player_id))
        if box is not None and float(box.minutes or 0) > 0:
            proj.actual_value = _stat_value(box, proj.market)
            scored += 1
    session.commit()
    return {"settled": settled, "lines_scored": scored}


def track_record(session: Session) -> dict:
    """Paper record on props: bets (CLV first), plus the model vs. the market on
    every line it priced."""
    bets = session.execute(
        select(PropBet).where(PropBet.settled_at.is_not(None), PropBet.result != "void")
    ).scalars().all()
    decided = [b for b in bets if b.result in ("won", "lost")]
    staked = sum(float(b.stake_fraction) for b in bets)
    clvs = [float(b.clv) for b in bets if b.clv is not None]
    record = {
        "n_bets": len(bets),
        "wins": sum(b.result == "won" for b in bets),
        "losses": sum(b.result == "lost" for b in bets),
        "pushes": sum(b.result == "push" for b in bets),
        "win_rate": round(sum(b.result == "won" for b in decided) / len(decided), 3) if decided else None,
        "roi": round(sum(float(b.pnl or 0) for b in bets) / staked, 4) if staked else None,
        "units": round(sum(float(b.pnl or 0) for b in bets) * 100, 2),  # 1 unit = 1% of bankroll
        "avg_clv": round(sum(clvs) / len(clvs), 4) if clvs else None,
        "clv_positive_rate": round(sum(c > 0 for c in clvs) / len(clvs), 3) if clvs else None,
    }

    # Single-stat guidance sent with the picks: each "over at X" / "under at Y" call
    # should come in about GUIDE_PROB of the time.
    calls = hits = 0
    for b in bets:
        for g in (b.stat_guide or {}).values():
            if "actual" not in g:
                continue
            if g["over_at"] is not None:
                calls += 1
                hits += g["actual"] > g["over_at"]
            if g["under_at"] is not None:
                calls += 1
                hits += g["actual"] < g["under_at"]
    if calls:
        record["guide"] = {"calls": calls, "hits": hits, "target": GUIDE_PROB}

    lines = session.execute(
        select(PropProjection).where(PropProjection.actual_value.is_not(None))
    ).scalars().all()
    scored = [(float(p.model_over), float(p.market_over), float(p.actual_value) > float(p.line))
              for p in lines if float(p.actual_value) != float(p.line)]
    if scored:
        n = len(scored)
        record["lines"] = {
            "n": n,
            "brier_model": round(sum((m - o) ** 2 for m, _, o in scored) / n, 4),
            "brier_market": round(sum((k - o) ** 2 for _, k, o in scored) / n, 4),
            "brier_blend": round(sum(((m + k) / 2 - o) ** 2 for m, k, o in scored) / n, 4),
        }
    return record


def todays_bets(session: Session, game_date: date) -> list[PropBet]:
    return session.execute(
        select(PropBet).join(Game, PropBet.game_id == Game.game_id)
        .where(Game.game_date == game_date, PropBet.model_version == MODEL_VERSION)
        .order_by(PropBet.ev.desc())
    ).scalars().all()
