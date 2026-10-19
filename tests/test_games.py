"""Game-winner model. DB/network-free."""

from datetime import date, timedelta

from nba_bot.features import games
from nba_bot.features.props import GameLine, PropsData, TeamForm

TEAM, OTHER = 1, 2
DAY = date(2026, 1, 15)


def _player(team: int, minutes: float, pts: int, last: date) -> list[GameLine]:
    return [GameLine(f"g{i}", last - timedelta(days=2 * i), "2025-26", team, OTHER,
                     minutes=minutes, pts=pts) for i in range(10)][::-1]


def _data(**logs) -> PropsData:
    ids = {name: i for i, name in enumerate(logs, start=10)}
    return PropsData(logs={ids[n]: lg for n, lg in logs.items()},
                     names={i: n for n, i in ids.items()}, current_team={}, last_seen={},
                     team_games={}, team_box={})


def test_even_teams_leave_home_court():
    form = {TEAM: TeamForm(3.0, None, ()), OTHER: TeamForm(3.0, None, ())}
    p, margin = games.win_probability(games.expected_margin(form, TEAM, OTHER), 0, 0)
    assert margin == games.HOME_COURT and 0.55 < p < 0.6


def test_missing_scoring_moves_the_line_both_ways():
    base, _ = games.win_probability(0.0, 0, 0)
    assert base == 0.5
    home_short, m1 = games.win_probability(0.0, 25, 0)
    away_short, m2 = games.win_probability(0.0, 0, 25)
    assert home_short < 0.5 < away_short and m1 == -m2 == -25 * games.MISSING_PTS_WEIGHT


def test_only_recent_regulars_count_as_missing():
    data = _data(
        star=_player(TEAM, 35, 28, DAY - timedelta(days=2)),
        bench=_player(TEAM, 8, 4, DAY - timedelta(days=2)),              # under 12 minutes
        long_gone=_player(TEAM, 34, 25, DAY - timedelta(days=40)),       # already priced in
        traded=_player(OTHER, 30, 20, DAY - timedelta(days=2)),          # never played for us
    )
    ids = {n: i for i, n in data.names.items()}
    missing = games.regulars_missing(data, TEAM, DAY, set(data.names))
    assert [pid for pid, _ in missing] == [ids["star"]] and missing[0][1] == 28
