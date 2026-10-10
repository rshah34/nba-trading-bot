"""Data Agent: nightly ingestion of teams, today's games, injuries, and box scores
for completed games. Run via `nba-bot ingest` (see cli/main.py).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

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


def _to_int(v) -> int | None:
    # A frame mixing final and unplayed games carries NaN, not None, for the latter.
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_datetime(v) -> datetime | None:
    if v is None or v != v:  # NaT/NaN compare unequal to themselves
        return None
    return v.to_pydatetime() if hasattr(v, "to_pydatetime") else v


def _last_game_date_before(session: Session, team_id: int, before: date) -> date | None:
    stmt = (
        select(Game.game_date)
        .where(
            # Played or not: last night's game counts for a back-to-back even while
            # it is still waiting on its final score.
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
                home_score=_to_int(g["home_score"]),
                away_score=_to_int(g["away_score"]),
                status=g["status"],
                home_rest_days=home_rest,
                away_rest_days=away_rest,
                is_back_to_back_home=home_rest == 0,
                is_back_to_back_away=away_rest == 0,
                tipoff_at=_to_datetime(g["tipoff_at"]),
            )
            .on_conflict_do_update(
                index_elements=[Game.game_id],
                set_={
                    "home_score": _to_int(g["home_score"]),
                    "away_score": _to_int(g["away_score"]),
                    "status": g["status"],
                    "tipoff_at": _to_datetime(g["tipoff_at"]),
                    "home_rest_days": home_rest,
                    "away_rest_days": away_rest,
                    "is_back_to_back_home": home_rest == 0,
                    "is_back_to_back_away": away_rest == 0,
                },
            )
        )
        session.execute(stmt)
        count += 1
    session.commit()
    return count


def sync_schedule(session: Session) -> int:
    """Store the rest of the season's schedule, so any date can be looked up ahead of
    time and rest days are known before the day itself.

    Only games still to be played are written. Results come from the daily
    scoreboard sync, so a game that is already final is never touched here. Re-run
    daily: the league adds games (NBA Cup rounds, playoffs) and moves postponed ones.
    """
    schedule = nba_stats.get_schedule(_season_for_date(date.today()))
    known = set(session.execute(select(Team.team_id)).scalars())

    last_played: dict[int, date] = {}
    rows = []
    for g in schedule.sort_values(["game_date", "game_id"]).itertuples():
        if g.home_team_id not in known or g.away_team_id not in known:
            continue  # a playoff slot whose teams aren't decided, or an exhibition opponent
        rest = {t: (g.game_date - last_played[t]).days - 1 if t in last_played else None
                for t in (g.home_team_id, g.away_team_id)}
        last_played[g.home_team_id] = last_played[g.away_team_id] = g.game_date
        if g.final:
            continue
        rows.append({
            "game_id": g.game_id, "season": _season_for_date(g.game_date),
            "game_date": g.game_date, "home_team_id": g.home_team_id,
            "away_team_id": g.away_team_id, "status": "scheduled",
            "tipoff_at": _to_datetime(g.tipoff_at),
            "home_rest_days": rest[g.home_team_id], "away_rest_days": rest[g.away_team_id],
            "is_back_to_back_home": rest[g.home_team_id] == 0,
            "is_back_to_back_away": rest[g.away_team_id] == 0,
        })
    if not rows:
        return 0
    stmt = pg_insert(Game).values(rows)
    moved = ("game_date", "tipoff_at", "home_rest_days", "away_rest_days",
             "is_back_to_back_home", "is_back_to_back_away")
    session.execute(stmt.on_conflict_do_update(
        index_elements=[Game.game_id], set_={c: stmt.excluded[c] for c in moved},
        where=Game.status != "final",
    ))
    session.commit()
    return len(rows)


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


def _pct(made, attempted) -> float | None:
    return round(made / attempted, 3) if attempted else None


def store_box_score(session: Session, game_id: str) -> dict:
    """Fetch a game's V3 box score (one API call) and upsert BOTH player_game_stats
    and the authoritative team_game_stats box columns.

    The team box gives the offensive/defensive rebound split (needed for the Four
    Factors) that summed player rows can't. Only box columns are written to
    team_game_stats — points and plus_minus stay owned by the final-score upsert
    (their single source of truth), so ordering between the two never matters.
    """
    player_df, team_df = nba_stats.get_box_score_v3(game_id)

    player_rows = 0
    for _, r in player_df.iterrows():
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
            .returning(PlayerGameStats.player_id)
        )
        # RETURNING yields a row only on a real insert; psycopg's rowcount for
        # ON CONFLICT DO NOTHING is -1 either way, which counted every insert as 0.
        player_rows += session.execute(stmt).first() is not None

    team_rows = 0
    for _, r in team_df.iterrows():
        fgm, fga = _to_int(r["fieldGoalsMade"]), _to_int(r["fieldGoalsAttempted"])
        fg3m, fg3a = _to_int(r["threePointersMade"]), _to_int(r["threePointersAttempted"])
        ftm, fta = _to_int(r["freeThrowsMade"]), _to_int(r["freeThrowsAttempted"])
        box = {
            "fgm": fgm, "fga": fga, "fg3m": fg3m, "fg3a": fg3a, "ftm": ftm, "fta": fta,
            "oreb": _to_int(r["reboundsOffensive"]),
            "dreb": _to_int(r["reboundsDefensive"]),
            "rebounds": _to_int(r["reboundsTotal"]),
            "assists": _to_int(r["assists"]),
            "steals": _to_int(r["steals"]),
            "blocks": _to_int(r["blocks"]),
            "turnovers": _to_int(r["turnovers"]),
            "fg_pct": _pct(fgm, fga),
            "fg3_pct": _pct(fg3m, fg3a),
            "ft_pct": _pct(ftm, fta),
        }
        session.execute(
            pg_insert(TeamGameStats)
            .values(game_id=game_id, team_id=int(r["teamId"]), **box)
            .on_conflict_do_update(
                index_elements=[TeamGameStats.game_id, TeamGameStats.team_id], set_=box
            )
        )
        team_rows += 1

    session.commit()
    return {"player_rows": player_rows, "team_rows": team_rows}


def backfill_recent_stats(session: Session, lookback_days: int = 3) -> dict:
    """For recently completed games, fill team margins (from final scores, no box
    call) and per-player box scores (V3). Idempotent — skips games already stored.
    """
    cutoff = date.today() - timedelta(days=lookback_days)
    games = session.execute(
        select(Game.game_id, Game.home_team_id, Game.away_team_id, Game.home_score, Game.away_score)
        .where(Game.status == "final", Game.home_score.is_not(None), Game.game_date >= cutoff)
    ).all()
    have_players = set(session.execute(
        select(PlayerGameStats.game_id)
        .where(PlayerGameStats.game_id.in_([g.game_id for g in games])).distinct()
    ).scalars())

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
            # One V3 fetch fills player rows AND the authoritative team box
            # (raw counts + OREB/DREB); points/plus_minus above are preserved.
            player_rows += store_box_score(session, gid)["player_rows"]
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


def sync_odds(session: Session, markets: tuple[str, ...] | None = None) -> dict:
    """Capture current NBA odds for all books and attach them to our games.

    Each call inserts a fresh snapshot row per (game, sportsbook); repeated runs
    over a day build the line-movement history that mark_closing_lines() reads.
    Costs one credit per market; `GAME_MARKETS` sets which.
    """
    if markets is None:
        markets = tuple(m.strip() for m in settings.game_markets.split(",") if m.strip())
    events, quota = odds_api.fetch_nba_odds(settings.odds_api_key, markets)
    index = _build_game_index(session)

    inserted, unmatched, live = 0, 0, 0
    now = datetime.now(timezone.utc)
    for raw in events:
        ev = odds_api.parse_event(raw)
        if ev.commence_time <= now:
            # The feed keeps listing games in progress with in-play prices; storing
            # one would make it the "closing line" and corrupt every CLV number.
            live += 1
            continue
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
    return {"events": len(events), "odds_rows": inserted, "unmatched_events": unmatched,
            "skipped_live": live, "quota": quota}


# How far back a run reaches for games it hasn't seen finish: long enough to catch
# up after the machine has been off for a while.
_CATCH_UP_DAYS = 14


def mark_closing_lines(session: Session, lookback_days: int = _CATCH_UP_DAYS) -> int:
    """Flag the latest pre-tipoff snapshot per (game, sportsbook) as the closing line.

    Covers recently finished games only: older ones were marked on earlier runs and
    never change, and no snapshot is ever stored after tip-off, so the newest row
    per book is the close.
    """
    cutoff = date.today() - timedelta(days=lookback_days)
    rows = session.execute(
        select(Odds)
        .join(Game, Odds.game_id == Game.game_id)
        .where(Game.status == "final", Game.game_date >= cutoff)
        .order_by(Odds.game_id, Odds.captured_at.desc())
    ).scalars().all()

    marked = 0
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.game_id, row.sportsbook)
        row.is_closing_line = key not in seen
        if key not in seen:
            seen.add(key)
            marked += 1
    session.commit()
    return marked


def run_nightly(session: Session) -> dict:
    """Full ingestion: teams, recent + today's + tomorrow's games, injuries, box scores.

    The previous days are re-synced on purpose: a game ends after the day's run, so
    its final score only arrives on a later one. The re-sync reaches back to the
    oldest game still waiting on a result, so days the machine was off are caught up
    instead of staying `scheduled` forever and never settling.
    """
    teams = sync_teams(session)
    today = date.today()
    oldest = session.execute(
        select(func.min(Game.game_date)).where(
            Game.status == "scheduled", Game.game_date < today,
            Game.game_date >= today - timedelta(days=_CATCH_UP_DAYS))
    ).scalar()
    lookback_days = max(2, (today - oldest).days) if oldest else 2
    games_recent = sum(
        sync_games_for_date(session, today - timedelta(days=d))
        for d in range(lookback_days, 0, -1)
    )
    games_today = sync_games_for_date(session, today)
    games_tomorrow = sync_games_for_date(session, today + timedelta(days=1))
    injuries = sync_injuries(session)
    recent = backfill_recent_stats(session, lookback_days=lookback_days + 1)
    return {
        "teams": teams,
        "games_recent": games_recent,
        "games_today": games_today,
        "games_tomorrow": games_tomorrow,
        "injuries": injuries,
        "recent_stats": recent,
    }
