"""Data Agent: nightly ingestion of teams, today's games, injuries, and box scores
for completed games. Run via `nba-bot ingest` (see cli/main.py).
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot.config import settings
from nba_bot.data import injuries as injuries_client
from nba_bot.data import nba_stats
from nba_bot.data import odds_api
from nba_bot.data.team_lookup import resolve_team_id
from nba_bot.db.models import Game, Injury, Odds, PlayerGameStats, Team, TeamGameStats

# Conference + division per team abbreviation. Static (realignment is rare) and not
# provided by nba_api's static team list, so it's maintained here.
_TEAM_ALIGNMENT: dict[str, tuple[str, str]] = {
    # Eastern Conference
    "BOS": ("East", "Atlantic"), "BKN": ("East", "Atlantic"), "NYK": ("East", "Atlantic"),
    "PHI": ("East", "Atlantic"), "TOR": ("East", "Atlantic"),
    "CHI": ("East", "Central"), "CLE": ("East", "Central"), "DET": ("East", "Central"),
    "IND": ("East", "Central"), "MIL": ("East", "Central"),
    "ATL": ("East", "Southeast"), "CHA": ("East", "Southeast"), "MIA": ("East", "Southeast"),
    "ORL": ("East", "Southeast"), "WAS": ("East", "Southeast"),
    # Western Conference
    "DEN": ("West", "Northwest"), "MIN": ("West", "Northwest"), "OKC": ("West", "Northwest"),
    "POR": ("West", "Northwest"), "UTA": ("West", "Northwest"),
    "GSW": ("West", "Pacific"), "LAC": ("West", "Pacific"), "LAL": ("West", "Pacific"),
    "PHX": ("West", "Pacific"), "SAC": ("West", "Pacific"),
    "DAL": ("West", "Southwest"), "HOU": ("West", "Southwest"), "MEM": ("West", "Southwest"),
    "NOP": ("West", "Southwest"), "SAS": ("West", "Southwest"),
}


def sync_teams(session: Session) -> int:
    rows = nba_stats.get_all_teams()
    count = 0
    for t in rows:
        conference, division = _TEAM_ALIGNMENT.get(t["abbreviation"], (None, None))
        stmt = (
            pg_insert(Team)
            .values(
                team_id=t["id"],
                abbreviation=t["abbreviation"],
                full_name=t["full_name"],
                conference=conference,
                division=division,
            )
            .on_conflict_do_update(
                index_elements=[Team.team_id],
                set_={
                    "abbreviation": t["abbreviation"],
                    "full_name": t["full_name"],
                    "conference": conference,
                    "division": division,
                },
            )
        )
        session.execute(stmt)
        count += 1
    session.commit()
    return count


def _last_game_date_before(session: Session, team_id: int, before: date) -> date | None:
    stmt = (
        select(Game.game_date)
        .where(
            Game.status == "final",
            Game.game_date < before,
            (Game.home_team_id == team_id) | (Game.away_team_id == team_id),
        )
        .order_by(Game.game_date.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def sync_games_for_date(session: Session, game_date: date) -> int:
    df = nba_stats.get_scoreboard(game_date)
    count = 0
    for _, g in df.iterrows():
        home_last = _last_game_date_before(session, int(g["home_team_id"]), game_date)
        away_last = _last_game_date_before(session, int(g["away_team_id"]), game_date)
        home_rest = (game_date - home_last).days - 1 if home_last else None
        away_rest = (game_date - away_last).days - 1 if away_last else None

        stmt = (
            pg_insert(Game)
            .values(
                game_id=g["game_id"],
                season=_season_for_date(game_date),
                game_date=g["game_date"],
                home_team_id=int(g["home_team_id"]),
                away_team_id=int(g["away_team_id"]),
                home_score=int(g["home_score"]) if g["home_score"] is not None else None,
                away_score=int(g["away_score"]) if g["away_score"] is not None else None,
                status=g["status"],
                home_rest_days=home_rest,
                away_rest_days=away_rest,
                is_back_to_back_home=home_rest == 0,
                is_back_to_back_away=away_rest == 0,
            )
            .on_conflict_do_update(
                index_elements=[Game.game_id],
                set_={
                    "home_score": int(g["home_score"]) if g["home_score"] is not None else None,
                    "away_score": int(g["away_score"]) if g["away_score"] is not None else None,
                    "status": g["status"],
                },
            )
        )
        session.execute(stmt)
        count += 1
    session.commit()
    return count


def _season_for_date(d: date) -> str:
    # NBA season labeled by its starting year; season flips in October.
    start_year = d.year if d.month >= 10 else d.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def sync_injuries(session: Session) -> int:
    teams_by_name = {t.full_name: t.team_id for t in session.execute(select(Team)).scalars()}
    count = 0
    for team in injuries_client.get_injuries():
        team_id = teams_by_name.get(team["team_name"])
        if team_id is None:
            continue
        for inj in team["injuries"]:
            if not inj["player_name"]:
                continue
            session.add(
                Injury(
                    team_id=team_id,
                    player_name=inj["player_name"],
                    status=inj["status"] or "unknown",
                    reason=inj["reason"],
                )
            )
            count += 1
    session.commit()
    return count


def _to_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def store_player_box_score(session: Session, game_id: str) -> int:
    """Fetch a game's player box score (V3) and upsert player_game_stats rows.

    Uses BoxScoreTraditionalV3 — V2 stopped publishing as of 2025-26. Shared by
    the live nightly ingest and the historical backfill.
    """
    df = nba_stats.get_player_box_score(game_id)
    count = 0
    for _, r in df.iterrows():
        minutes = nba_stats.parse_minutes(r["minutes"])
        if minutes is None:  # DNP
            continue
        stmt = (
            pg_insert(PlayerGameStats)
            .values(
                game_id=game_id,
                player_id=int(r["personId"]),
                player_name=f'{r["firstName"]} {r["familyName"]}'.strip(),
                team_id=int(r["teamId"]),
                minutes=minutes,
                points=_to_int(r["points"]),
                rebounds=_to_int(r["reboundsTotal"]),
                assists=_to_int(r["assists"]),
                steals=_to_int(r["steals"]),
                blocks=_to_int(r["blocks"]),
                turnovers=_to_int(r["turnovers"]),
                fgm=_to_int(r["fieldGoalsMade"]),
                fga=_to_int(r["fieldGoalsAttempted"]),
                fg3m=_to_int(r["threePointersMade"]),
                fg3a=_to_int(r["threePointersAttempted"]),
                ftm=_to_int(r["freeThrowsMade"]),
                fta=_to_int(r["freeThrowsAttempted"]),
                plus_minus=float(r["plusMinusPoints"]) if r["plusMinusPoints"] is not None else None,
            )
            .on_conflict_do_nothing(index_elements=[PlayerGameStats.game_id, PlayerGameStats.player_id])
        )
        count += session.execute(stmt).rowcount or 0
    session.commit()
    return count


def store_team_box_from_players(session: Session, game_id: str) -> int:
    """Populate team_game_stats box columns by aggregating the game's already-stored
    player rows — no extra API call. Percentages and assists are exact; rebounds and
    turnovers can slightly undercount the box score's own "team rebound/turnover"
    lines (deadball rebounds etc.). Upserts only the box columns, so points and
    plus_minus (from final scores) are preserved. The Four Factors work (see ROADMAP)
    will supersede this with an authoritative team box + opponent line.
    """
    rows = session.execute(
        select(
            PlayerGameStats.team_id,
            func.sum(PlayerGameStats.rebounds),
            func.sum(PlayerGameStats.assists),
            func.sum(PlayerGameStats.turnovers),
            func.sum(PlayerGameStats.fgm),
            func.sum(PlayerGameStats.fga),
            func.sum(PlayerGameStats.fg3m),
            func.sum(PlayerGameStats.fg3a),
            func.sum(PlayerGameStats.ftm),
            func.sum(PlayerGameStats.fta),
        )
        .where(PlayerGameStats.game_id == game_id)
        .group_by(PlayerGameStats.team_id)
    ).all()

    def pct(made, attempted) -> float | None:
        return round(made / attempted, 3) if attempted else None

    n = 0
    for team_id, reb, ast, tov, fgm, fga, fg3m, fg3a, ftm, fta in rows:
        box = {
            "fg_pct": pct(fgm, fga),
            "fg3_pct": pct(fg3m, fg3a),
            "ft_pct": pct(ftm, fta),
            "rebounds": reb,
            "assists": ast,
            "turnovers": tov,
        }
        session.execute(
            pg_insert(TeamGameStats)
            .values(game_id=game_id, team_id=team_id, **box)
            .on_conflict_do_update(
                index_elements=[TeamGameStats.game_id, TeamGameStats.team_id], set_=box
            )
        )
        n += 1
    return n


def backfill_recent_stats(session: Session, lookback_days: int = 3) -> dict:
    """For recently completed games, fill team margins (from final scores, no box
    call) and per-player box scores (V3). Idempotent — skips games already stored.
    """
    cutoff = date.today() - timedelta(days=lookback_days)
    games = session.execute(
        select(Game.game_id, Game.home_team_id, Game.away_team_id, Game.home_score, Game.away_score)
        .where(Game.status == "final", Game.home_score.is_not(None), Game.game_date >= cutoff)
    ).all()
    have_players = set(session.execute(select(PlayerGameStats.game_id).distinct()).scalars().all())

    team_rows, player_rows = 0, 0
    for gid, home_id, away_id, hs, aws in games:
        for team_id, pts, pm in ((home_id, hs, hs - aws), (away_id, aws, aws - hs)):
            session.execute(
                pg_insert(TeamGameStats)
                .values(game_id=gid, team_id=team_id, points=pts, plus_minus=pm)
                .on_conflict_do_update(
                    index_elements=[TeamGameStats.game_id, TeamGameStats.team_id],
                    set_={"points": pts, "plus_minus": pm},
                )
            )
            team_rows += 1
        if gid not in have_players:
            player_rows += store_player_box_score(session, gid)
        # Fill the team box columns (fg%, rebounds, assists, turnovers) from the
        # player rows now that they're stored; points/plus_minus above are kept.
        store_team_box_from_players(session, gid)
    session.commit()
    return {"team_stat_rows": team_rows, "player_rows": player_rows}


def _build_game_index(session: Session) -> dict[tuple[date, int, int], str]:
    """Map (game_date, home_team_id, away_team_id) -> game_id for odds matching."""
    games = session.execute(select(Game.game_id, Game.game_date, Game.home_team_id, Game.away_team_id))
    return {(g.game_date, g.home_team_id, g.away_team_id): g.game_id for g in games}


def _match_game_id(index, game_date: date, home_id: int, away_id: int) -> str | None:
    """Look up a game_id, tolerating a ±1 day skew between our stored game_date
    and the odds commence date (UTC vs the game's local calendar date)."""
    for delta in (0, 1, -1):
        gid = index.get((game_date + timedelta(days=delta), home_id, away_id))
        if gid:
            return gid
    return None


def sync_odds(session: Session, markets: tuple[str, ...] = odds_api.DEFAULT_MARKETS) -> dict:
    """Capture current NBA odds for all books and attach them to our games.

    Each call inserts a fresh snapshot row per (game, sportsbook); repeated runs
    over a day build the line-movement history that mark_closing_lines() reads.
    """
    events, quota = odds_api.fetch_nba_odds(settings.odds_api_key, markets)
    index = _build_game_index(session)

    inserted, unmatched = 0, 0
    for raw in events:
        ev = odds_api.parse_event(raw)
        home_id = resolve_team_id(ev.home_team)
        away_id = resolve_team_id(ev.away_team)
        if home_id is None or away_id is None:
            unmatched += 1
            continue
        game_id = _match_game_id(index, ev.commence_time.date(), home_id, away_id)
        if game_id is None:
            unmatched += 1
            continue
        for book in ev.books:
            session.add(
                Odds(
                    game_id=game_id,
                    sportsbook=book.sportsbook,
                    home_moneyline=book.home_moneyline,
                    away_moneyline=book.away_moneyline,
                    spread_home=book.spread_home,
                    spread_home_price=book.spread_home_price,
                    spread_away_price=book.spread_away_price,
                    total_points=book.total_points,
                    over_price=book.over_price,
                    under_price=book.under_price,
                )
            )
            inserted += 1
    session.commit()
    return {"events": len(events), "odds_rows": inserted, "unmatched_events": unmatched, "quota": quota}


def mark_closing_lines(session: Session) -> int:
    """Flag the latest pre-tipoff snapshot per (game, sportsbook) as the closing line.

    Runs over games that have already started (tipoff in the past); the most
    recently captured row for each book is the closing line, the rest are not.
    """
    started = session.execute(
        select(Game.game_id).where(Game.status == "final")
    ).scalars().all()

    marked = 0
    for game_id in started:
        rows = session.execute(
            select(Odds).where(Odds.game_id == game_id).order_by(Odds.captured_at.desc())
        ).scalars().all()
        seen_books: set[str] = set()
        for row in rows:
            is_close = row.sportsbook not in seen_books
            row.is_closing_line = is_close
            if is_close:
                seen_books.add(row.sportsbook)
                marked += 1
    session.commit()
    return marked


def run_nightly(session: Session) -> dict:
    """Full nightly ingestion: teams, today's + tomorrow's games, injuries, recent box scores."""
    teams = sync_teams(session)
    games_today = sync_games_for_date(session, date.today())
    games_tomorrow = sync_games_for_date(session, date.today() + timedelta(days=1))
    injuries = sync_injuries(session)
    recent = backfill_recent_stats(session)
    return {
        "teams": teams,
        "games_today": games_today,
        "games_tomorrow": games_tomorrow,
        "injuries": injuries,
        "recent_stats": recent,
    }
