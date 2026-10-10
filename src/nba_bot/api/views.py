"""Read models for the dashboard: plain dicts assembled from the database and the
in-memory box-score history. Nothing here writes, and nothing calls an outside API.
"""

from __future__ import annotations

import math
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from nba_bot.agents import analysis_agent, betting_agent, news_agent, props_agent
from nba_bot.agents.data_agent import _season_for_date
from nba_bot.config import settings
from nba_bot.data.nba_stats import is_preseason
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
    GameLine,
    Projection,
    PropsData,
    load_props_data,
    normalize_name,
    project_team,
    stat_guide,
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
                        "conference": t.conference}
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
    for s in STATS:
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
        team = teams.get(data.current_team.get(pid))
        rows.append({"id": pid, "name": data.names[pid], "team": team["abbr"] if team else None,
                     "team_id": team["id"] if team else None, "season": averages(games),
                     "l10": averages(games, 10), "l5": averages(games, 5)})
    rows.sort(key=lambda r: r["season"]["pts"], reverse=True)
    return {"season": season, "seasons": seasons, "players": rows}


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
           "games": games, "next": None, "projection": None, "status": None,
           "lines": {}, "reports": []}
    signals = news_agent.active_signals(session, now)
    out["reports"] = [_report(r, teams) for r in news_agent.consolidate(signals.get(player_id, []))]
    if team_id is None:
        return out

    nxt = _next_game(session, team_id, today)
    game_date = nxt.game_date if nxt else today
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

    factors = data.opponent_factors(game_date, _season_for_date(game_date)).get(opp_id)
    view = props_agent.team_view(session, data, team_id, factors, game_date, now, signals)
    proj = view.projections.get(player_id)
    if proj is not None:
        out["projection"] = _projection_payload(proj)
    name = normalize_name(data.names[player_id])
    listed = next((i for i in analysis_agent.current_injuries(session, team_id, now)
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
            "player_id": None, "book": None, "odds": _f(bet.decimal_odds),
            "model": _f(bet.model_prob), "market": _f(bet.market_prob), "edge": _f(bet.edge),
            "stake": _f(bet.stake_fraction), "pnl": _f(bet.pnl), "clv": _f(bet.clv),
            "result": None if bet.settled_at is None else ("won" if bet.won else "lost"),
            "actual": None, "notes": None, "guide": None,
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
            "guide": bet.stat_guide,
        })
    rows.sort(key=lambda r: r["decided_at"] or "", reverse=True)
    return rows


def _records(session: Session, data: PropsData) -> dict[int, str]:
    """team -> "W-L" for the newest season with games played."""
    seasons = _seasons(data)
    if not seasons:
        return {}
    return {tid: f"{t['w']}-{t['gp'] - t['w']}"
            for tid, t in _team_totals(session, data, seasons[0]).items()}


def _game_card(session: Session, game: Game, teams: dict[int, dict],
               records: dict[int, str]) -> dict:
    pred = session.execute(
        select(Prediction)
        .where(Prediction.game_id == game.game_id,
               Prediction.model_version == settings.analysis_model)
        .order_by(Prediction.as_of.desc()).limit(1)
    ).scalar_one_or_none()
    return {
        "game_id": game.game_id, "date": game.game_date.isoformat(), "status": game.status,
        "preseason": is_preseason(game.game_id), "tipoff_at": _iso(game.tipoff_at),
        "home": {**teams[game.home_team_id], "score": game.home_score,
                 "record": records.get(game.home_team_id)},
        "away": {**teams[game.away_team_id], "score": game.away_score,
                 "record": records.get(game.away_team_id)},
        "model_home_prob": _f(pred.predicted_home_win_prob) if pred else None,
        "market_home_prob": _market_home_prob(session, game.game_id),
        "reasoning": pred.reasoning if pred else None,
        "key_factors": (pred.context_used or {}).get("key_factors") if pred else None,
    }


def slate(session: Session, on: date | None) -> dict:
    teams = team_map(session)
    records = _records(session, props_data(session))
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
        "games": [_game_card(session, g, teams, records) for g in games],
        "picks": _pick_rows(session, Game.game_date == on, teams),
    }


def _player_row(pid: int, name: str, proj: Projection | None, status: str | None,
                actual: PlayerGameStats | None) -> dict:
    row = {"id": pid, "name": name, "status": status, "proj": None, "actual": None, "notes": []}
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
    factors = data.opponent_factors(game.game_date, _season_for_date(game.game_date))
    final = game.status == "final"
    signals = {} if final else news_agent.active_signals(session, now)

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
                opp_factors=factors.get(opp_id))
            rows = [_player_row(pid, r.player_name, projections.get(pid), None, r)
                    for pid, r in played.items()]
            rows.sort(key=lambda r: r["actual"]["min"], reverse=True)
        else:
            view = props_agent.team_view(session, data, team_id, factors.get(opp_id),
                                         game.game_date, now, signals)
            rows = [_player_row(pid, data.names[pid], p, view.blocked.get(pid), None)
                    for pid, p in view.projections.items() if p.minutes >= 5]
            rows.sort(key=lambda r: r["proj"]["min"], reverse=True)
            out = [{"player": i["player"], "status": i["status"], "detail": i["reason"]}
                   for i in analysis_agent.current_injuries(session, team_id, now)]
            reports = [_report(r, teams) for r in news_agent.team_news(session, [team_id], now)]
        sides.append({"team": teams[team_id], "players": rows, "injuries": out, "reports": reports})

    card = _game_card(session, game, teams, _records(session, data))
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

    injuries = analysis_agent.current_injuries(session, team_id, now)
    listed = {normalize_name(i["player"]): i["status"] for i in injuries}
    roster = []
    for pid, team in data.current_team.items():
        if team != team_id or data.last_seen[pid] < newest - timedelta(days=_ROSTER_DAYS):
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
    nxt = _next_game(session, team_id, today)
    return {
        **_team_summary(teams[team_id], _team_totals(session, data, season).get(team_id)),
        "season": season, "roster": roster, "results": results,
        "next": nxt and {"game_id": nxt.game_id, "date": nxt.game_date.isoformat(),
                         "home": nxt.home_team_id == team_id,
                         "opp": teams[nxt.away_team_id if nxt.home_team_id == team_id
                                      else nxt.home_team_id]["abbr"]},
        "injuries": [{"player": i["player"], "status": i["status"], "detail": i["reason"]}
                     for i in injuries],
        "reports": [_report(r, teams) for r in news_agent.team_news(session, [team_id], now, 12)],
    }


# --------------------------------------------------------------------------- #
# News + picks.
# --------------------------------------------------------------------------- #
def news(session: Session, team_id: int | None) -> list[dict]:
    teams = team_map(session)
    ids = [team_id] if team_id else list(teams)
    reports = news_agent.team_news(session, ids, datetime.now(timezone.utc), limit=200)
    return [_report(r, teams) for r in reports]


def picks(session: Session) -> dict:
    teams = team_map(session)
    return {
        "picks": _pick_rows(session, Game.game_id.is_not(None), teams)[:300],
        "props": props_agent.track_record(session),
        "games": betting_agent.track_record(session),
    }
