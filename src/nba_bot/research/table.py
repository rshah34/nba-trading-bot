"""The research table: one row per projected player-game, with what the model said,
what happened, and everything known about the game before tip.

Built from the props backtest's own replay, so it is the model exactly as the code
stands, point-in-time, with the rotation that actually played known in advance (the
backtest's `oracle` mode — the live pipeline gets close to it from the injury report).
Each team is projected a second time without the minutes and stat budgets, which is
how much the budgets moved each player.
"""

from __future__ import annotations

import math
from collections import defaultdict

import pandas as pd

from nba_bot.backtest.props_backtest import replay
from nba_bot.features.props import ROTATION_MIN, PropsData, over_under_probs, project_team

STATS = ("pts", "reb", "ast", "fg3m", "stl", "blk", "tov")
# The market each stat is priced in; "pra" is points + rebounds + assists.
MARKET_OF = {"pts": "player_points", "reb": "player_rebounds", "ast": "player_assists",
             "fg3m": "player_threes", "stl": "player_steals", "blk": "player_blocks",
             "tov": "player_turnovers", "pra": "player_points_rebounds_assists"}
TARGETS = (*STATS, "pra")
SEASONS = ("2024-25", "2025-26")
MIN_MINUTES = 15.0                    # the same floor the backtest scores at
_ROLES = ((34.0, "star"), (28.0, "starter"), (20.0, "rotation"), (0.0, "bench"))
_ALTITUDE = {1610612743, 1610612762}  # Denver, Utah

# What each column means, for the agent. Outcome columns (proj_*, actual_*, phi_*,
# line_*, over_*) are described once by pattern.
FEATURES: dict[str, str] = {
    "season": "2024-25 or 2025-26",
    "month": "calendar month of the game (10 = October … 4 = April)",
    "team_game_number": "the team's game number this season (1 = opener)",
    "home": "true if the player's team is at home",
    "rest_days": "days off for the team before this game (0 = second night of a back-to-back)",
    "b2b": "true on the second night of a back-to-back",
    "three_in_four": "true when this is the team's third game in four nights",
    "road_trip_game": "consecutive road games including this one (0 at home)",
    "at_altitude": "true for a road game at Denver or Utah",
    "opp_rest_days": "the opponent's days off before this game",
    "opp_b2b": "true if the opponent is on the second night of a back-to-back",
    "expected_margin": "points the player's team is expected to win by (negative = underdog)",
    "players_out": "players from the team's previous game (12+ minutes lately) not playing tonight",
    "minutes_out": "recent minutes per game of those absent players, summed",
    "role": "projected minutes tier: star (34+), starter (28-34), rotation (20-28), bench (<20)",
    "proj_minutes": "projected minutes",
    "l5_minutes": "average minutes over his last 5 games",
    "l20_minutes": "average minutes over his last 20 games",
    "minutes_trend": "l5_minutes minus l20_minutes",
    "games_played": "his games this season before this one",
    "career_games": "his games on record before this one (two seasons at most)",
    "player_rest_days": "days since his own last game",
    "games_missed": "team games since he last played (a layoff when 1+)",
    "new_team": "true in his first game for a new team",
    "games_with_team": "his games for this team before this one",
    "budget_minutes_mult": "how much the minutes budget scaled his minutes (1 = untouched)",
    "budget_pts_mult": "how much the minutes and stat budgets together scaled his points",
    **{f"opp_allows_{s}": f"opponent's {s} allowed vs. league (1.0 = average, as applied)"
       for s in STATS},
}
OUTCOMES = ("proj_<stat>: projected mean; actual_<stat>: what happened; phi_<stat>: the "
            "projection's variance/mean; line_<stat>: a test line (his last-10 average, "
            "rounded to .5); over_<stat>: whether he went over it. <stat> is one of "
            + ", ".join(TARGETS) + ". Also proj_minutes / actual_minutes.")


def _role(minutes: float) -> str:
    return next(name for floor, name in _ROLES if minutes >= floor)


def _avg(log, attr: str, n: int) -> float:
    recent = log[-n:]
    return sum(getattr(g, attr) for g in recent) / len(recent) if recent else 0.0


def build(data: PropsData, seasons: tuple[str, ...] = SEASONS) -> pd.DataFrame:
    """Every projected player-game of 15+ projected minutes in the given seasons."""
    rows: list[dict] = []
    for season in seasons:
        # (game, team) -> who played, to find tonight's absences from last game.
        played: dict[tuple[str, int], set[int]] = defaultdict(set)
        for pid, log in data.logs.items():
            for g in log:
                if g.season == season:
                    played[(g.game_id, g.team_id)].add(pid)
        dates = {t: [g.game_date for g in gs if g.season == season]
                 for t, gs in data.team_games.items()}
        homes = {t: [g.home_id == t for g in gs if g.season == season]
                 for t, gs in data.team_games.items()}
        ids = {t: [g.game_id for g in gs if g.season == season]
               for t, gs in data.team_games.items()}

        for tg in replay(data, season):
            if not tg.projections:
                continue
            gd = tg.game.game_date
            team_dates, opp_dates = dates[tg.team_id], dates[tg.opp_id]
            idx = team_dates.index(gd)
            rest = (gd - team_dates[idx - 1]).days - 1 if idx else None
            opp_idx = opp_dates.index(gd)
            opp_rest = (gd - opp_dates[opp_idx - 1]).days - 1 if opp_idx else None
            trip = 0
            for h in reversed(homes[tg.team_id][:idx + 1]):
                if h:
                    break
                trip += 1
            previous = played.get((ids[tg.team_id][idx - 1], tg.team_id), set()) if idx else set()
            absent = [p for p in previous if p not in tg.actual
                      and _avg(data.log_before(p, gd), "minutes", 5) >= ROTATION_MIN]
            minutes_out = sum(_avg(data.log_before(p, gd), "minutes", 5) for p in absent)
            unbudgeted = project_team(tg.logs, opp_factors=tg.opp_factors, context=tg.context,
                                      use_budget=False)
            factors = tg.opp_factors or {}

            for pid, proj in tg.projections.items():
                if pid not in tg.actual or proj.minutes < MIN_MINUTES:
                    continue
                log, actual = tg.logs[pid], tg.actual[pid]
                last = log[-1]
                plain = unbudgeted.get(pid)
                row = {
                    "season": season, "game_date": gd, "month": gd.month,
                    "game_id": tg.game.game_id, "player_id": pid,
                    "player": data.names.get(pid, str(pid)),
                    "team_id": tg.team_id, "opp_id": tg.opp_id,
                    "team_game_number": idx + 1, "home": tg.context.home,
                    "rest_days": rest, "b2b": rest == 0,
                    "three_in_four": idx >= 2 and (gd - team_dates[idx - 2]).days <= 3,
                    "road_trip_game": trip,
                    "at_altitude": not tg.context.home and tg.opp_id in _ALTITUDE,
                    "opp_rest_days": opp_rest, "opp_b2b": opp_rest == 0,
                    "expected_margin": round(tg.context.expected_margin, 2),
                    "players_out": len(absent), "minutes_out": round(minutes_out, 1),
                    "role": _role(proj.minutes), "proj_minutes": round(proj.minutes, 2),
                    "actual_minutes": actual.minutes,
                    "l5_minutes": round(_avg(log, "minutes", 5), 2),
                    "l20_minutes": round(_avg(log, "minutes", 20), 2),
                    "games_played": sum(g.season == season for g in log),
                    "career_games": len(log),
                    "player_rest_days": (gd - last.game_date).days - 1,
                    "games_missed": sum(last.game_date < d < gd for d in team_dates),
                    "new_team": last.team_id != tg.team_id,
                    "games_with_team": sum(g.team_id == tg.team_id for g in log),
                    "budget_minutes_mult": round(proj.minutes / plain.minutes, 4)
                    if plain and plain.minutes else 1.0,
                    "budget_pts_mult": round(proj.means["pts"] / plain.means["pts"], 4)
                    if plain and plain.means["pts"] else 1.0,
                }
                row["minutes_trend"] = round(row["l5_minutes"] - row["l20_minutes"], 2)
                for s in STATS:
                    row[f"opp_allows_{s}"] = round(factors.get(s, 1.0), 4)
                last10 = log[-10:]
                for t in TARGETS:
                    parts = ("pts", "reb", "ast") if t == "pra" else (t,)
                    mean = sum(proj.means[s] for s in parts)
                    l10 = sum(sum(getattr(g, s) for s in parts) for g in last10) / len(last10)
                    line = math.floor(l10) + 0.5
                    value = sum(getattr(actual, s) for s in parts)
                    row[f"proj_{t}"] = mean
                    row[f"actual_{t}"] = value
                    row[f"phi_{t}"] = proj.phis[MARKET_OF[t]]
                    row[f"line_{t}"] = line
                    row[f"over_{t}"] = value > line
                rows.append(row)
    return pd.DataFrame(rows)


def over_probability(mean: pd.Series, phi: pd.Series, line: pd.Series) -> pd.Series:
    """P(over) for each row, from the projection's distribution."""
    return pd.Series([over_under_probs(m, f, ln)[0] for m, f, ln in zip(mean, phi, line)],
                     index=mean.index)
