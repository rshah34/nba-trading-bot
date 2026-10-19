"""Ask: the query guard and the accuracy check's grading. DB/network-free."""

import pytest

from nba_bot.ask import sql
from nba_bot.ask.data import build_rows, season_type
from nba_bot.ask.evaluation import grade


@pytest.mark.parametrize("query", [
    "delete from team_games",
    "update team_games set won = true",
    "drop table teams",
    "select 1; drop table teams",
    "select 1; select 2",
    "set role nba_app",
    "-- select\ninsert into teams values (1)",
])
def test_only_one_select_gets_through(query):
    with pytest.raises(sql.QueryError):
        sql.check(query)


def test_selects_and_ctes_pass_tidied():
    assert sql.check("  select 1 ;  ") == "select 1"
    assert sql.check("-- note\nWITH x AS (SELECT 1) SELECT * FROM x;").startswith("-- note")


def test_a_semicolon_inside_a_string_is_still_one_statement_to_the_database():
    # The check is cautious and refuses it; the read-only login is the real guard.
    with pytest.raises(sql.QueryError):
        sql.check("select ';'; select 2")


def test_grading_ignores_accents_dashes_and_case():
    assert grade("Nikola Jokic averaged 10.7 assists", [("Nikola Jokić",), "10.7"]) == []
    assert grade("They went 64–18.", [("64-18",)]) == []
    assert grade("The THUNDER led", [("OKC", "Thunder")]) == []
    assert grade("They won three of them", [("3", "three")]) == []
    assert grade("Luka averaged 31.8", ["33.5", ("Luka",)]) == ["33.5"]


def test_team_games_carry_records_and_ranks_going_into_each_game():
    from datetime import date
    from types import SimpleNamespace as G

    games = [  # A beats B twice by 10; C beats A by 1; preseason rows carry no record
        G(game_id="0012400001", season="2024-25", game_date=date(2024, 10, 10), home_team_id=1,
          away_team_id=2, home_score=90, away_score=80, home_rest_days=None, away_rest_days=None),
        G(game_id="0022400001", season="2024-25", game_date=date(2024, 10, 22), home_team_id=1,
          away_team_id=2, home_score=100, away_score=90, home_rest_days=None, away_rest_days=None),
        G(game_id="0022400002", season="2024-25", game_date=date(2024, 10, 23), home_team_id=2,
          away_team_id=1, home_score=90, away_score=100, home_rest_days=0, away_rest_days=0),
        G(game_id="0022400003", season="2024-25", game_date=date(2024, 10, 25), home_team_id=3,
          away_team_id=1, home_score=101, away_score=100, home_rest_days=1, away_rest_days=1),
    ]
    rows = {(r["game_id"], r["team"]): r for r in build_rows(games, {1: "AAA", 2: "BBB", 3: "CCC"})}
    pre = rows[("0012400001", "AAA")]
    assert pre["season_type"] == "preseason" and pre["team_wins_before"] is None
    second = rows[("0022400002", "AAA")]
    assert (second["team_wins_before"], second["team_rank_before"], second["opp_rank_before"]) == (1, 1, 2)
    assert second["back_to_back"] is True and second["margin"] == 10 and second["won"]
    last = rows[("0022400003", "CCC")]
    assert last["opp_wins_before"] == 2 and last["team_rank_before"] is None   # C's first game
    assert season_type("0042400101") == "playoffs"
