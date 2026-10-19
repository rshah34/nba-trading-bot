"""Read models for the dashboard: plain dicts assembled from the database and the
in-memory box-score history. Nothing here writes, and nothing calls an outside API.
"""

from __future__ import annotations

import math
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from nba_bot.agents import betting_agent, data_agent, news_agent, props_agent
from nba_bot.agents.data_agent import _season_for_date
from nba_bot.data.nba_stats import is_preseason
from nba_bot.features.games import GAME_MODEL
from nba_bot.db.models import (
    REAL_GAMES,
    Bet,
    Game,
    Odds,
    PlayerGameStats,
    Prediction,
    PropBet,
    Team,
)
from nba_bot.features.props import (
    MARKET_LABELS,
    MARKETS,
    SHOOTING,
    GameLine,
    Projection,
    PropsData,
    game_context,
    load_props_data,
    normalize_name,
    project_team,
    shooting_means,
    stat_guide,
    team_form,
)

STATS = ("pts", "reb", "ast", "fg3m", "stl", "blk", "tov")
_BOX = {"pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "fg3m",
        "stl": "steals", "blk": "blocks", "tov": "turnovers"}
# The box-score history takes ~0.3s to load; pages share one copy for a couple of minutes.
_CACHE_SECONDS = 120
_cache: dict = {"at": 0.0, "data": None}
# A player counts as on a team's current roster if he has appeared this recently,
# measured from the newest game in the data (so the page still works in the offseason).
_ROSTER_DAYS = 45


def props_data(session: Session) -> PropsData:
    if _cache["data"] is None or time.monotonic() - _cache["at"] > _CACHE_SECONDS:
        _cache["data"], _cache["at"] = load_props_data(session), time.monotonic()
    return _cache["data"]


def team_map(session: Session) -> dict[int, dict]:
    return {t.team_id: {"id": t.team_id, "abbr": t.abbreviation, "name": t.full_name,
                        "conference": t.conference, "division": t.division}
            for t in session.execute(select(Team)).scalars()}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _f(v) -> float | None:
    return float(v) if v is not None else None


def averages(lines: list[GameLine], last: int | None = None) -> dict | None:
    """Per-game averages over a player's games (optionally just the most recent)."""
    rows = lines[-last:] if last else lines
    if not rows:
        return None
    n = len(rows)
    out = {"gp": n, "min": round(sum(g.minutes for g in rows) / n, 1)}
    for s in STATS + SHOOTING:
        out[s] = round(sum(getattr(g, s) for g in rows) / n, 1)
    return out


def _seasons(data: PropsData) -> list[str]:
    return sorted({g.season for games in data.team_games.values() for g in games}, reverse=True)


# --------------------------------------------------------------------------- #
# Players.
# --------------------------------------------------------------------------- #
def players_index(session: Session, season: str | None) -> dict:
    data, teams = props_data(session), team_map(session)
    seasons = _seasons(data)
    season = season if season in seasons else (seasons[0] if seasons else "")
    rows = []
    for pid, log in data.logs.items():
        games = [g for g in log if g.season == season]
        if not games:
            continue
        team = teams.get(games[-1].team_id)  # who he played for that season, not where he is now
        rows.append({"id": pid, "name": data.names[pid], "team": team["abbr"] if team else None,
                     "team_id": team["id"] if team else None, "season": averages(games),
                     "l10": averages(games, 10), "l5": averages(games, 5)})
    rows.sort(key=lambda r: r["season"]["pts"], reverse=True)
    return {"season": season, "seasons": seasons, "players": rows}


def directory(session: Session) -> dict:
    """Every team and every player with a box score on record, for search and
    filters. Unlike the season table, a player who missed the newest season is here,
    with the numbers from the last season he did play."""
    data, teams = props_data(session), team_map(session)
    players = []
    for pid, name in data.names.items():
        log = data.logs.get(pid, [])
        last = log[-1].season if log else None
        team = teams.get(data.current_team.get(pid))
        players.append({"id": pid, "name": name, "team": team["abbr"] if team else None,
                        "last_season": last,
                        "pts": averages([g for g in log if g.season == last])["pts"] if log else None})
    players.sort(key=lambda p: (p["last_season"] or "", p["pts"] or 0), reverse=True)
    return {"season": next(iter(_seasons(data)), None),
            "teams": sorted(teams.values(), key=lambda t: t["name"]), "players": players}


def _ladder(proj: Projection, market: str) -> list[list[float]]:
    """[[line, P(over)], ...] at every half-point line the player could plausibly see."""
    mean = proj.mean(market)
    top = int(mean + 4 * math.sqrt(max(proj.phis[market] * mean, 1.0))) + 2
    return [[k + 0.5, round(proj.probs(market, k + 0.5)[0], 3)] for k in range(min(top, 90))]


def _projection_payload(proj: Projection) -> dict:
    return {
        "minutes": proj.minutes,
        "notes": proj.notes,
        "markets": {m: {"label": MARKET_LABELS[m], "mean": round(proj.mean(m), 1),
                        "ladder": _ladder(proj, m)} for m in MARKETS},
        "guide": stat_guide(proj, ""),
    }


def _next_game(session: Session, team_id: int, today: date) -> Game | None:
    return session.execute(
        select(Game)
        .where(Game.status == "scheduled", Game.game_date >= today,
               or_(Game.home_team_id == team_id, Game.away_team_id == team_id))
        .order_by(Game.game_date).limit(1)
    ).scalar_one_or_none()


# An absence expected to last longer than this is a standing fact about the team, not
# news about tonight: the page lists it in one line instead of in full.
_LONG_TERM = timedelta(days=21)


def _injury_rows(session: Session, team_id: int, now: datetime, game_date: date) -> list[dict]:
    """A team's injury report as it applies to one game: who is out of it, who is in
    doubt, and who is gone for the long haul."""
    data = props_data(session)
    return [
        {"player": i["player"], "player_id": props_agent._resolve_player(data, i["player"], {team_id}),
         "status": i["status"], "detail": i["reason"],
         "long_term": i["return_date"] is not None and i["return_date"] - game_date > _LONG_TERM}
        for i in data_agent.current_injuries(session, team_id, now, game_date)
    ]


# How fast a report fades from the top of a news list: half as prominent every two
# days. Display only — what the models read is ranked on how firmly it is sourced.
_NEWS_HALF_LIFE_DAYS = 2.0


def _newest_and_firmest(reports: list[news_agent.Report], now: datetime,
                        limit: int | None = None) -> list[news_agent.Report]:
    """News in reading order: how firm and widely reported it is, discounted by age,
    so this morning's solid report outranks last week's and a rumour stays below both."""
    def score(r: news_agent.Report) -> float:
        published = r.signal.published_at or r.signal.created_at
        age = max((now - published).total_seconds() / 86400, 0.0) if published else 0.0
        return r.weight * 0.5 ** (age / _NEWS_HALF_LIFE_DAYS)

    return sorted(reports, key=score, reverse=True)[:limit]


def _report(r: news_agent.Report, teams: dict[int, dict]) -> dict:
    s = r.signal
    team = teams.get(s.team_id)
    return {"player": s.player_name, "player_id": s.player_id, "team": team["abbr"] if team else None,
            "kind": s.kind, "sources": r.sources, "confidence": float(s.confidence),
            "summary": s.summary, "published_at": _iso(s.published_at)}


def player_detail(session: Session, player_id: int) -> dict | None:
    data, teams = props_data(session), team_map(session)
    if player_id not in data.names:
        return None
    log = data.logs.get(player_id, [])
    now, today = datetime.now(timezone.utc), date.today()

    scores = {g.game_id: g for g in session.execute(
        select(Game).where(Game.game_id.in_([g.game_id for g in log]))).scalars()}
    games = []
    for g in reversed(log):
        row = {"game_id": g.game_id, "date": g.game_date.isoformat(), "season": g.season,
               "team": teams.get(g.team_id, {}).get("abbr"),
               "opp": teams.get(g.opp_id, {}).get("abbr"), "min": round(g.minutes, 1)}
        row.update({s: getattr(g, s) for s in STATS})
        game = scores.get(g.game_id)
        if game is not None:
            home = game.home_team_id == g.team_id
            mine, theirs = ((game.home_score, game.away_score) if home
                            else (game.away_score, game.home_score))
            row["home"] = home
            if mine is not None and theirs is not None:
                row["result"] = f"{'W' if mine > theirs else 'L'} {mine}-{theirs}"
        games.append(row)

    team_id = data.current_team.get(player_id)
    out = {"id": player_id, "name": data.names[player_id], "team": teams.get(team_id),
           # Every season on record, whether or not he played in it: the page says so
           # when he didn't, rather than hiding the season.
           "seasons": _seasons(data),
           "games": games, "next": None, "projection": None, "status": None,
           "lines": {}, "reports": []}
    nxt = _next_game(session, team_id, today) if team_id is not None else None
    slate = props_agent.slate_for(session, data, nxt.game_date if nxt else today, now)
    out["reports"] = [_report(r, teams)
                      for r in _newest_and_firmest(
                          news_agent.consolidate(slate.signals.get(player_id, [])), now)]
    if team_id is None:
        return out

    opp_id = None
    if nxt is not None:
        opp_id = nxt.away_team_id if nxt.home_team_id == team_id else nxt.home_team_id
        out["next"] = {"game_id": nxt.game_id, "date": nxt.game_date.isoformat(),
                       "tipoff_at": _iso(nxt.tipoff_at), "opp": teams.get(opp_id, {}).get("abbr"),
                       "home": nxt.home_team_id == team_id, "preseason": is_preseason(nxt.game_id)}
        for (pid, market), quotes in props_agent.latest_quotes(session, nxt.game_id).items():
            cons = props_agent.consensus(quotes) if pid == player_id else None
            if cons is not None:
                out["lines"][market] = {"line": cons[0], "books": cons[1], "over": round(cons[2], 3)}

    view = props_agent.team_view(session, data, slate, team_id, opp_id,
                                 nxt is not None and nxt.home_team_id == team_id,
                                 preseason=nxt is not None and is_preseason(nxt.game_id))
    proj = view.projections.get(player_id)
    if proj is not None:
        out["projection"] = _projection_payload(proj)
    name = normalize_name(data.names[player_id])
    listed = next((i for i in data_agent.current_injuries(session, team_id, now, slate.game_date)
                   if normalize_name(i["player"]) == name), None)
    if listed is not None:
        out["status"] = {"label": listed["status"], "detail": listed["reason"]}
    elif player_id in view.blocked:
        out["status"] = {"label": "Questionable", "detail": view.blocked[player_id]}
    return out


# --------------------------------------------------------------------------- #
# Slate + games.
# --------------------------------------------------------------------------- #
def _market_home_prob(session: Session, game_id: str) -> float | None:
    cons = betting_agent.consensus_decimal_odds(
        session.execute(select(Odds).where(Odds.game_id == game_id)).scalars().all())
    if cons is None:
        return None
    return round((1 / cons[0]) / (1 / cons[0] + 1 / cons[1]), 3)


def _pick_rows(session: Session, where, teams: dict[int, dict]) -> list[dict]:
    """Game-winner and prop bets matching a Game filter, as one list, newest first."""
    rows = []
    for bet, game in session.execute(
        select(Bet, Game).join(Game, Bet.game_id == Game.game_id).where(where)
    ).all():
        side, other = ((game.home_team_id, game.away_team_id) if bet.side == "home"
                       else (game.away_team_id, game.home_team_id))
        rows.append({
            "kind": "game", "game_id": game.game_id, "date": game.game_date.isoformat(),
            "decided_at": _iso(bet.decided_at),
            "matchup": f"{teams[game.away_team_id]['abbr']} @ {teams[game.home_team_id]['abbr']}",
            "label": f"{teams[side]['abbr']} to win {'vs' if bet.side == 'home' else 'at'} "
                     f"{teams[other]['abbr']}",
            "player_id": None, "book": bet.sportsbook, "odds": _f(bet.decimal_odds),
            "model": _f(bet.model_prob), "market": _f(bet.market_prob), "edge": _f(bet.edge),
            "stake": _f(bet.stake_fraction), "pnl": _f(bet.pnl), "clv": _f(bet.clv),
            "result": None if bet.settled_at is None else ("won" if bet.won else "lost"),
            "actual": None, "notes": bet.reason, "guide": None,
            "line": None, "playable_odds": _f(bet.playable_odds), "playable_line": None,
            "flag": bet.flag,
        })
    for bet, game in session.execute(
        select(PropBet, Game).join(Game, PropBet.game_id == Game.game_id).where(where)
    ).all():
        rows.append({
            "kind": "prop", "game_id": game.game_id, "date": game.game_date.isoformat(),
            "decided_at": _iso(bet.decided_at),
            "matchup": f"{teams[game.away_team_id]['abbr']} @ {teams[game.home_team_id]['abbr']}",
            "label": f"{bet.player_name} {bet.side} {float(bet.line):g} "
                     f"{MARKET_LABELS.get(bet.market, bet.market)}",
            "player_id": bet.player_id, "book": bet.sportsbook, "odds": _f(bet.decimal_odds),
            "model": _f(bet.model_prob), "market": _f(bet.market_prob), "edge": _f(bet.edge),
            "stake": _f(bet.stake_fraction), "pnl": _f(bet.pnl), "clv": _f(bet.clv),
            "result": bet.result, "actual": _f(bet.actual_value), "notes": bet.notes,
            "guide": bet.stat_guide, "line": _f(bet.line),
            "playable_odds": _f(bet.playable_odds), "playable_line": _f(bet.playable_line),
            "flag": bet.flag,
        })
    rows.sort(key=lambda r: r["decided_at"] or "", reverse=True)
    return rows


_PLAYOFFS = "004"


def _standing(session: Session, game: Game, cache: dict) -> dict:
    """What to show beside each team in a game: its record in that kind of game
    (preseason or regular season) through the game's date, or — in the playoffs —
    where the series stands. `cache` is shared across one page's games."""
    kind = game.game_id[:3]
    series = kind == _PLAYOFFS
    # A playoff game id ends in round, series, game number: all but the last digit name the series.
    scope = game.game_id[:-1] if series else (kind if kind == "001" else "002")
    key = (game.season, scope, game.game_date)
    if key not in cache:
        wins: dict[int, list[int]] = {}
        for home, away, hs, as_ in session.execute(
            select(Game.home_team_id, Game.away_team_id, Game.home_score, Game.away_score)
            .where(Game.season == game.season, Game.game_id.like(f"{scope}%"),
                   Game.status == "final", Game.home_score.is_not(None),
                   Game.game_date <= game.game_date)
        ):
            for team, won in ((home, hs > as_), (away, as_ > hs)):
                tally = wins.setdefault(team, [0, 0])
                tally[0 if won else 1] += 1
        cache[key] = wins
    wins = cache[key]
    if not series:
        return {"records": {t: f"{won}-{lost}" for t, (won, lost) in wins.items()}, "series": None}
    home, away = wins.get(game.home_team_id, [0, 0])[0], wins.get(game.away_team_id, [0, 0])[0]
    return {"records": {}, "series": {"home": home, "away": away}}


def _game_card(session: Session, game: Game, teams: dict[int, dict], cache: dict) -> dict:
    pred = session.execute(
        select(Prediction)
        .where(Prediction.game_id == game.game_id,
               Prediction.model_version == GAME_MODEL)
        .order_by(Prediction.as_of.desc()).limit(1)
    ).scalar_one_or_none()
    standing = _standing(session, game, cache)

    def record(team_id: int) -> str | None:
        return None if standing["series"] else standing["records"].get(team_id, "0-0")

    return {
        "game_id": game.game_id, "date": game.game_date.isoformat(), "status": game.status,
        "preseason": is_preseason(game.game_id), "tipoff_at": _iso(game.tipoff_at),
        "series": standing["series"],
        "home": {**teams[game.home_team_id], "score": game.home_score,
                 "record": record(game.home_team_id)},
        "away": {**teams[game.away_team_id], "score": game.away_score,
                 "record": record(game.away_team_id)},
        "model_home_prob": _f(pred.predicted_home_win_prob) if pred else None,
        "market_home_prob": _market_home_prob(session, game.game_id),
        "key_factors": (pred.context_used or {}).get("key_factors") if pred else None,
    }


def slate(session: Session, on: date | None) -> dict:
    teams = team_map(session)
    cache: dict = {}
    if on is None:
        today = date.today()
        on = (session.execute(select(func.min(Game.game_date)).where(Game.game_date >= today))
              .scalar_one_or_none()
              or session.execute(select(func.max(Game.game_date))).scalar_one_or_none() or today)
    games = session.execute(
        select(Game).where(Game.game_date == on).order_by(Game.tipoff_at, Game.game_id)
    ).scalars().all()
    before = session.execute(
        select(func.max(Game.game_date)).where(Game.game_date < on)).scalar_one_or_none()
    after = session.execute(
        select(func.min(Game.game_date)).where(Game.game_date > on)).scalar_one_or_none()
    return {
        "date": on.isoformat(),
        "prev": before.isoformat() if before else None,
        "next": after.isoformat() if after else None,
        "games": [_game_card(session, g, teams, cache) for g in games],
        "picks": _pick_rows(session, Game.game_date == on, teams),
    }


def slate_players(session: Session, on: date | None) -> dict:
    """Every projected player on one upcoming date: who is playing, for how long,
    and what he is projected for. One payload feeds the projections and fantasy pages."""
    data, teams = props_data(session), team_map(session)
    now, today = datetime.now(timezone.utc), date.today()
    upcoming = Game.status == "scheduled", Game.game_date >= today
    dates = list(session.execute(
        select(Game.game_date).where(*upcoming).distinct().order_by(Game.game_date).limit(7)
    ).scalars())
    if on not in dates:
        on = dates[0] if dates else None
    out = {"date": on and on.isoformat(), "dates": [d.isoformat() for d in dates],
           "games": [], "players": []}
    if on is None:
        return out

    slate = props_agent.slate_for(session, data, on, now)
    games = session.execute(
        select(Game).where(Game.game_date == on, Game.status == "scheduled")
        .order_by(Game.tipoff_at, Game.game_id)
    ).scalars().all()
    for game in games:
        quotes = props_agent.latest_quotes(session, game.game_id)
        sides = []
        for team_id, opp_id in ((game.away_team_id, game.home_team_id),
                                (game.home_team_id, game.away_team_id)):
            home = team_id == game.home_team_id
            view = props_agent.team_view(session, data, slate, team_id, opp_id, home,
                                         preseason=is_preseason(game.game_id))
            sides.append({
                **teams[team_id],
                # What the opponent gives up in each stat against the league (1.0 = average).
                "matchup": {s: round(f, 3) for s, f in (slate.factors.get(opp_id) or {}).items()},
            })
            for pid, proj in view.projections.items():
                if proj.minutes < 5:
                    continue
                log = data.log_before(pid, on)
                lines = {}
                for market in MARKETS:
                    cons = props_agent.consensus(quotes.get((pid, market), []))
                    if cons is None:
                        continue
                    line, books, market_over = cons
                    over, _under, push = proj.probs(market, line)
                    last = log[-10:]
                    lines[market] = {
                        "line": line, "books": books, "market_over": round(market_over, 3),
                        "model_over": round(over / (1 - push), 3) if push < 1 else None,
                        "hits": sum(g.value(market) > line for g in last), "games": len(last),
                    }
                shots = shooting_means(log, proj.means["pts"])
                out["players"].append({
                    "id": pid, "name": data.names[pid], "team": teams[team_id]["abbr"],
                    "team_id": team_id, "opp": teams[opp_id]["abbr"], "home": home,
                    "game_id": game.game_id, "status": view.blocked.get(pid), "notes": proj.notes,
                    "proj": {"min": proj.minutes,
                             **{s: round(proj.means[s], 2) for s in STATS},
                             **{s: round(v, 2) for s, v in shots.items()}},
                    "season": averages([g for g in log if g.season == log[-1].season]),
                    "season_label": log[-1].season if log else None,
                    "l5": averages(log, 5), "lines": lines,
                })
        out["games"].append({"game_id": game.game_id, "tipoff_at": _iso(game.tipoff_at),
                             "preseason": is_preseason(game.game_id),
                             "away": sides[0], "home": sides[1]})
    return out


def _player_row(pid: int, name: str, proj: Projection | None, status: str | None,
                actual: PlayerGameStats | None, log: list[GameLine] | None = None) -> dict:
    """One player in a game: the model's line, what he did, and — before the game —
    his per-game averages from his most recent season with games."""
    row = {"id": pid, "name": name, "status": status, "proj": None, "actual": None,
           "avg": None, "avg_season": None, "notes": []}
    if log:
        season = log[-1].season
        row["avg"], row["avg_season"] = averages([g for g in log if g.season == season]), season
    if proj is not None:
        row["proj"] = {"min": proj.minutes, **{s: round(proj.means[s], 1) for s in STATS}}
        row["notes"] = proj.notes
    if actual is not None:
        row["actual"] = {"min": round(float(actual.minutes or 0), 1),
                         **{s: getattr(actual, col) or 0 for s, col in _BOX.items()}}
    return row


def game_detail(session: Session, game_id: str) -> dict | None:
    game = session.get(Game, game_id)
    if game is None:
        return None
    data, teams = props_data(session), team_map(session)
    now = datetime.now(timezone.utc)
    final = game.status == "final"
    if final:
        season = _season_for_date(game.game_date)
        factors = data.opponent_factors(game.game_date, season)
        form = team_form(data, game.game_date, season)
    else:
        slate = props_agent.slate_for(session, data, game.game_date, now)

    sides = []
    for team_id, opp_id in ((game.away_team_id, game.home_team_id),
                            (game.home_team_id, game.away_team_id)):
        rows, out, reports = [], [], []
        if final:
            # What the model would have projected for the players who took the floor.
            played = {r.player_id: r for r in session.execute(
                select(PlayerGameStats).where(PlayerGameStats.game_id == game_id,
                                              PlayerGameStats.team_id == team_id,
                                              PlayerGameStats.minutes > 0)).scalars()}
            projections = project_team(
                {pid: data.log_before(pid, game.game_date) for pid in played},
                opp_factors=factors.get(opp_id),
                context=None if is_preseason(game.game_id) else game_context(
                    form, team_id, opp_id, team_id == game.home_team_id,
                    game.game_date, factors.get(opp_id)))
            rows = [_player_row(pid, r.player_name, projections.get(pid), None, r)
                    for pid, r in played.items()]
            rows.sort(key=lambda r: r["actual"]["min"], reverse=True)
        else:
            view = props_agent.team_view(session, data, slate, team_id, opp_id,
                                         team_id == game.home_team_id,
                                         preseason=is_preseason(game.game_id))
            rows = [_player_row(pid, data.names[pid], p, view.blocked.get(pid), None,
                                data.log_before(pid, game.game_date))
                    for pid, p in view.projections.items() if p.minutes >= 5]
            rows.sort(key=lambda r: r["proj"]["min"], reverse=True)
            out = _injury_rows(session, team_id, now, game.game_date)
            reports = [_report(r, teams) for r in _newest_and_firmest(
                news_agent.team_news(session, [team_id], now, limit=200), now, 8)]
        sides.append({"team": teams[team_id], "players": rows, "injuries": out, "reports": reports})

    card = _game_card(session, game, teams, {})
    card.update({"away_side": sides[0], "home_side": sides[1],
                 "picks": _pick_rows(session, Game.game_id == game_id, teams)})
    return card


# --------------------------------------------------------------------------- #
# Teams.
# --------------------------------------------------------------------------- #
def _team_totals(session: Session, data: PropsData, season: str) -> dict[int, dict]:
    """Per team for a season: record, points for/against, and what it produces and
    allows per game in each stat."""
    games = session.execute(
        select(Game).where(Game.season == season, Game.status == "final", REAL_GAMES,
                           Game.home_score.is_not(None))
    ).scalars().all()
    blank = lambda: {"gp": 0, "w": 0, "pf": 0, "pa": 0,  # noqa: E731
                     "for": dict.fromkeys(STATS, 0.0), "allowed": dict.fromkeys(STATS, 0.0), "n": 0}
    totals: dict[int, dict] = {}
    for g in games:
        for team, opp, mine, theirs in ((g.home_team_id, g.away_team_id, g.home_score, g.away_score),
                                        (g.away_team_id, g.home_team_id, g.away_score, g.home_score)):
            t = totals.setdefault(team, blank())
            t["gp"] += 1
            t["w"] += mine > theirs
            t["pf"] += mine
            t["pa"] += theirs
            own, other = data.team_box.get((g.game_id, team)), data.team_box.get((g.game_id, opp))
            if own and other:
                t["n"] += 1
                for s in STATS:
                    t["for"][s] += own[s]
                    t["allowed"][s] += other[s]
    return totals


def _team_summary(team: dict, t: dict | None) -> dict:
    if not t or not t["gp"]:
        return {**team, "gp": 0}
    n = t["n"] or 1
    return {**team, "gp": t["gp"], "w": t["w"], "l": t["gp"] - t["w"],
            "pf": round(t["pf"] / t["gp"], 1), "pa": round(t["pa"] / t["gp"], 1),
            "margin": round((t["pf"] - t["pa"]) / t["gp"], 1),
            "for": {s: round(v / n, 1) for s, v in t["for"].items()},
            "allowed": {s: round(v / n, 1) for s, v in t["allowed"].items()}}


def teams_index(session: Session, season: str | None) -> dict:
    data, teams = props_data(session), team_map(session)
    seasons = _seasons(data)
    season = season if season in seasons else (seasons[0] if seasons else "")
    totals = _team_totals(session, data, season)
    rows = [_team_summary(t, totals.get(tid)) for tid, t in teams.items()]
    return {"season": season, "seasons": seasons, "teams": [r for r in rows if r["gp"]]}


def team_detail(session: Session, team_id: int) -> dict | None:
    data, teams = props_data(session), team_map(session)
    if team_id not in teams:
        return None
    now, today = datetime.now(timezone.utc), date.today()
    seasons = _seasons(data)
    season = seasons[0] if seasons else ""
    newest = max(data.last_seen.values(), default=today)

    nxt = _next_game(session, team_id, today)
    injuries = _injury_rows(session, team_id, now, nxt.game_date if nxt else today)
    listed = {normalize_name(i["player"]): i["status"] for i in injuries}
    # A player out since last season hasn't appeared lately, but he's still on the team.
    hurt = {i["player_id"] for i in injuries if i["player_id"] is not None}
    roster = []
    for pid, team in data.current_team.items():
        if team != team_id or (data.last_seen[pid] < newest - timedelta(days=_ROSTER_DAYS)
                               and pid not in hurt):
            continue
        log = data.logs.get(pid, [])
        recent_season = log[-1].season if log else season
        games = [g for g in log if g.season == recent_season]
        roster.append({"id": pid, "name": data.names[pid], "stats_season": recent_season,
                       "season": averages(games), "l5": averages(games, 5),
                       "status": listed.get(normalize_name(data.names[pid]))})
    roster.sort(key=lambda r: (r["season"] or {}).get("min", 0), reverse=True)

    recent = session.execute(
        select(Game).where(Game.status == "final", Game.home_score.is_not(None),
                           or_(Game.home_team_id == team_id, Game.away_team_id == team_id))
        .order_by(Game.game_date.desc()).limit(10)
    ).scalars().all()
    results = []
    for g in recent:
        home = g.home_team_id == team_id
        mine, theirs = (g.home_score, g.away_score) if home else (g.away_score, g.home_score)
        results.append({"game_id": g.game_id, "date": g.game_date.isoformat(), "home": home,
                        "opp": teams[g.away_team_id if home else g.home_team_id]["abbr"],
                        "result": f"{'W' if mine > theirs else 'L'} {mine}-{theirs}",
                        "preseason": is_preseason(g.game_id)})
    return {
        **_team_summary(teams[team_id], _team_totals(session, data, season).get(team_id)),
        "season": season, "roster": roster, "results": results,
        "next": nxt and {"game_id": nxt.game_id, "date": nxt.game_date.isoformat(),
                         "home": nxt.home_team_id == team_id,
                         "opp": teams[nxt.away_team_id if nxt.home_team_id == team_id
                                      else nxt.home_team_id]["abbr"]},
        "injuries": injuries,
        "reports": [_report(r, teams) for r in _newest_and_firmest(
            news_agent.team_news(session, [team_id], now, limit=200), now, 12)],
    }


# --------------------------------------------------------------------------- #
# News + picks.
# --------------------------------------------------------------------------- #
def news(session: Session, team_id: int | None) -> list[dict]:
    teams = team_map(session)
    ids = [team_id] if team_id else list(teams)
    now = datetime.now(timezone.utc)
    reports = news_agent.team_news(session, ids, now, limit=200)
    return [_report(r, teams) for r in _newest_and_firmest(reports, now)]


def picks(session: Session) -> dict:
    teams = team_map(session)
    return {
        "picks": _pick_rows(session, Game.game_id.is_not(None), teams)[:300],
        "props": props_agent.track_record(session),
        "games": betting_agent.track_record(session),
    }
