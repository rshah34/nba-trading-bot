"""Backtest the game-winner model over a completed season. Free — no API calls.

Point-in-time: each game is predicted from the season's games before it. Who is
missing is read from who actually played (the injury report isn't kept
historically), so like the props backtest's `oracle` mode this is the model with
availability known; live, it comes from the injury report and the news.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

from nba_bot.backtest.report import calibration_table
from nba_bot.data.nba_stats import is_preseason
from nba_bot.features import games as model
from nba_bot.features.props import PropsData, team_form


def predictions(data: PropsData, season: str) -> list[tuple[float, bool, bool]]:
    """(P(home wins), home won, both teams past MIN_GAMES) for every game of the season."""
    played: dict[tuple[str, int], set[int]] = defaultdict(set)
    seen: dict[int, set[int]] = defaultdict(set)    # team -> everyone who played for it
    for pid, log in data.logs.items():
        for g in log:
            if g.season == season:
                played[(g.game_id, g.team_id)].add(pid)
                seen[g.team_id].add(pid)

    games = sorted({g.game_id: g for gs in data.team_games.values() for g in gs
                    if g.season == season and not is_preseason(g.game_id)}.values(),
                   key=lambda g: (g.game_date, g.game_id))
    out = []
    form, form_date = {}, None
    for g in games:
        home, away = data.team_box.get((g.game_id, g.home_id)), data.team_box.get((g.game_id, g.away_id))
        if home is None or away is None:
            continue
        if g.game_date != form_date:
            form, form_date = team_form(data, g.game_date, season), g.game_date

        def missing(team: int, gd: date = g.game_date, gid: str = g.game_id) -> float:
            absent = seen[team] - played[(gid, team)]
            return sum(pts for _, pts in model.regulars_missing(data, team, gd, absent))

        p, _ = model.win_probability(model.expected_margin(form, g.home_id, g.away_id),
                                     missing(g.home_id), missing(g.away_id))
        ready = all(len(form[t].dates) >= model.MIN_GAMES for t in (g.home_id, g.away_id)
                    if t in form)
        out.append((p, home["pts"] > away["pts"], ready))
    return out


def score(rows: list[tuple[float, bool, bool]]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    clip = [(min(max(p, 1e-4), 1 - 1e-4), y) for p, y, _ in rows]
    return {
        "n": n,
        "brier": round(sum((p - y) ** 2 for p, y in clip) / n, 4),
        "log_loss": round(-sum(math.log(p if y else 1 - p) for p, y in clip) / n, 4),
        "accuracy": round(sum((p > 0.5) == y for p, y in clip) / n, 3),
        "calibration": calibration_table([(p, y) for p, y in clip]),
    }
