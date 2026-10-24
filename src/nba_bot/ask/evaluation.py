"""How often Ask gets it right: questions with answers computed straight from the data.

Each case's expected answer is worked out when the check runs, so it stays right as
new games arrive. An answer passes when it contains every expected item: a record
("64-18"), a number to one decimal, a name. An item can be a set of acceptable forms
("Thunder" or "OKC"; "3" or "three"). One question asks for data that isn't there, and
passes only if the answer says so instead of making something up.

`nba-bot ask-eval` runs them (about a cent each) and saves the results to logs/.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from nba_bot.ask import agent, sql

Item = str | tuple[str, ...]
_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
          "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
          "seventeen", "eighteen", "nineteen", "twenty")
_NOT_THERE = ("not in the data", "isn't in", "is not in", "doesn't", "does not", "don't",
              "do not", "no playoff", "not available", "only covers", "only has",
              "starts with", "begins with", "not included")


@dataclass
class Case:
    question: str
    expect: Callable[[], list[Item]]


# --------------------------------------------------------------------------- #
# Grading.
# --------------------------------------------------------------------------- #
def normalize(s: str) -> str:
    s = re.sub("[‐-―−]", "-", s)   # en and em dashes, minus signs, first:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()   # this drops them
    return re.sub(r"\s+", " ", s.lower())


def grade(answer: str | None, expected: list[Item]) -> list[Item]:
    """The expected items missing from the answer; empty means it passed."""
    text = normalize(answer or "")
    missing = []
    for item in expected:
        forms = item if isinstance(item, tuple) else (item,)
        if not any(normalize(f) in text for f in forms):
            missing.append(item)
    return missing


# --------------------------------------------------------------------------- #
# Ground truth, straight from the ask schema.
# --------------------------------------------------------------------------- #
def _rows(query: str) -> list[list]:
    return sql.run(query)["rows"]


def _one(query: str):
    return _rows(query)[0]


def _team(abbr: str) -> tuple[str, ...]:
    name = _one(f"SELECT name FROM teams WHERE team = '{abbr}'")[0]
    return (abbr, name.split()[-1], name)


def _rec(w, l) -> tuple[str, ...]:  # noqa: E741
    return (f"{w}-{l}",)


def _f1(x) -> str:
    return f"{float(x):.1f}"


def _count(n) -> tuple[str, ...]:
    n = int(n)
    return (str(n), _WORDS[n]) if n < len(_WORDS) else (str(n),)


def _record(where: str, team: str) -> list[Item]:
    w, l = _one(f"SELECT sum(won::int), sum((NOT won)::int) FROM team_games "  # noqa: E741
                f"WHERE team = '{team}' AND {where}")
    return [_rec(w, l)]


def _top(query: str) -> list[Item]:
    """Names (any of, for ties) and the value from a query ordered best first."""
    rows = _rows(query)
    best = rows[0][1]
    names = tuple(n for r in rows if r[1] == best for n in (r[0],))
    return [names, _f1(best) if isinstance(best, float) else _count(best)]


def _top_team(query: str) -> list[Item]:
    rows = _rows(query)
    best = rows[0][1]
    return [tuple(f for r in rows if r[1] == best for f in _team(r[0]))]


REG = "season_type = 'regular'"
S, P = "season = '2025-26'", "season = '2024-25'"
_LEADER = ("SELECT player, round(avg({stat})::numeric, 1)::float FROM player_games "
           "WHERE {season} AND " + REG + " AND minutes > 0 GROUP BY player "
           "HAVING count(*) >= 58 ORDER BY 2 DESC LIMIT 5")


def _triple_doubles(player: str, season: str) -> list[Item]:
    n = _one(f"SELECT count(*) FROM player_games WHERE player = '{player}' AND {season} AND {REG} "
             "AND (points >= 10)::int + (rebounds >= 10)::int + (assists >= 10)::int "
             "+ (steals >= 10)::int + (blocks >= 10)::int >= 3")[0]
    return [_count(n)]


CASES: list[Case] = [
    Case("Which team had the best regular-season record in 2025-26?", lambda: [
        *_top_team(f"SELECT team, sum(won::int) FROM team_games WHERE {S} AND {REG} "
                   "GROUP BY 1 ORDER BY 2 DESC"),
        _rec(*_one(f"SELECT max(w), min(l) FROM (SELECT sum(won::int) w, sum((NOT won)::int) l "
                   f"FROM team_games WHERE {S} AND {REG} GROUP BY team) x"))]),
    Case("Which team had the worst record in 2024-25?", lambda: _top_team(
        f"SELECT team, -sum(won::int) FROM team_games WHERE {P} AND {REG} GROUP BY 1 ORDER BY 2 DESC")),
    Case("What was the Celtics' home record in 2025-26?",
         lambda: _record(f"{S} AND {REG} AND home", "BOS")),
    Case("What was the Lakers' road record in 2024-25?",
         lambda: _record(f"{P} AND {REG} AND NOT home", "LAL")),
    Case("How did the Knicks do on the second night of back-to-backs in 2025-26?",
         lambda: _record(f"{S} AND {REG} AND back_to_back", "NYK")),
    Case("How many points per game did the Thunder score in 2025-26?", lambda: [_f1(_one(
        f"SELECT avg(team_score) FROM team_games WHERE team = 'OKC' AND {S} AND {REG}")[0])]),
    Case("Which team allowed the fewest points per game in 2024-25?", lambda: [
        *_top_team(f"SELECT team, -round(avg(opp_score), 1) FROM team_games WHERE {P} AND {REG} "
                   "GROUP BY 1 ORDER BY 2 DESC"),
        _f1(_one(f"SELECT min(a) FROM (SELECT avg(opp_score) a FROM team_games WHERE {P} AND {REG} "
                 "GROUP BY team) x")[0])]),
    Case("What was the head-to-head record between the Celtics and the Knicks in 2025-26?",
         lambda: [(lambda w, l: (f"{w}-{l}", f"{l}-{w}"))(*_one(  # noqa: E741
             f"SELECT sum(won::int), sum((NOT won)::int) FROM team_games WHERE team = 'BOS' "
             f"AND opponent = 'NYK' AND {S} AND {REG}"))]),
    Case("Who led the league in scoring in 2025-26?",
         lambda: _top(_LEADER.format(stat="points", season=S))),
    Case("Who led the league in rebounding in 2024-25?",
         lambda: _top(_LEADER.format(stat="rebounds", season=P))),
    Case("Who averaged the most assists in 2025-26?",
         lambda: _top(_LEADER.format(stat="assists", season=S))),
    Case("Who made the most threes per game in 2025-26?",
         lambda: _top(_LEADER.format(stat="fg3m", season=S))[:1]),
    Case("Who had the most 40-point games in 2025-26?", lambda: _top(
        f"SELECT player, count(*) FROM player_games WHERE {S} AND {REG} AND points >= 40 "
        "GROUP BY 1 ORDER BY 2 DESC")),
    Case("How many points per game did Jayson Tatum average in 2024-25?", lambda: [_f1(_one(
        f"SELECT avg(points) FROM player_games WHERE player = 'Jayson Tatum' AND {P} AND {REG} "
        "AND minutes > 0")[0])]),
    Case("What did Jalen Brunson average in Knicks losses in 2025-26?", lambda: [_f1(_one(
        f"SELECT avg(points) FROM player_games WHERE player = 'Jalen Brunson' AND {S} AND {REG} "
        "AND minutes > 0 AND NOT won")[0])]),
    Case("How many triple-doubles did Nikola Jokić have in 2025-26?",
         lambda: _triple_doubles("Nikola Jokić", S)),
    Case("How many games did Shai Gilgeous-Alexander play in 2025-26?", lambda: [_count(_one(
        f"SELECT count(*) FROM player_games WHERE player = 'Shai Gilgeous-Alexander' AND {S} "
        f"AND {REG} AND minutes > 0")[0])]),
    Case("What did Luka Dončić average in points against top-10 teams in 2025-26?", lambda: [
        _f1(_one(f"SELECT avg(points) FROM player_games WHERE player = 'Luka Dončić' AND {S} "
                 f"AND {REG} AND minutes > 0 AND opp_rank_before <= 10")[0])]),
    Case("What was Anthony Edwards' highest-scoring game in 2024-25?", lambda: [_count(_one(
        f"SELECT max(points) FROM player_games WHERE player = 'Anthony Edwards' AND {P} "
        f"AND {REG}")[0])]),
    Case("What was the Celtics' record after the All-Star break in 2025-26?",
         lambda: _record(f"{S} AND {REG} AND after_all_star_break", "BOS")),
    Case("Which team won the most games after the All-Star break in 2024-25?", lambda: _top_team(
        f"SELECT team, sum(won::int) FROM team_games WHERE {P} AND {REG} AND after_all_star_break "
        "GROUP BY 1 ORDER BY 2 DESC")),
    Case("What was the Pistons' record against top-10 teams in 2025-26?",
         lambda: _record(f"{S} AND {REG} AND opp_rank_before <= 10", "DET")),
    Case("How many games did the Nuggets win by 20 or more points in 2025-26?", lambda: [_count(
        _one(f"SELECT count(*) FROM team_games WHERE team = 'DEN' AND {S} AND {REG} "
             "AND margin >= 20")[0])]),
    Case("What is the Celtics' preseason record this season?",
         lambda: _record("season = '2026-27' AND season_type = 'preseason'", "BOS")),
    Case("What was the Celtics' record in games decided by five points or fewer in 2025-26?",
         lambda: _record(f"{S} AND {REG} AND abs(margin) <= 5", "BOS")),
    Case("What was the most points a team scored in a game in 2025-26?", lambda: [_count(_one(
        f"SELECT max(team_score) FROM team_games WHERE {S} AND {REG}")[0])]),
    Case("How many games are on October 20, 2026?", lambda: [_count(_one(
        "SELECT count(*) FROM schedule WHERE game_date = '2026-10-20'")[0])]),
    Case("Who is listed as out for the Warriors on the latest injury report?", lambda: [
        tuple(r[0] for r in _rows("SELECT player FROM injuries WHERE team = 'GSW' "
                                  "AND status ILIKE 'out'"))]),
    Case("What was the Celtics' playoff record in 2024-25?",
         lambda: _record(f"{P} AND season_type = 'playoffs'", "BOS")),
    Case("Which team won the most playoff games in 2025-26?", lambda: _top_team(
        f"SELECT team, sum(won::int) FROM team_games WHERE {S} AND season_type = 'playoffs' "
        "GROUP BY 1 ORDER BY 2 DESC")),
    Case("Who led the league in scoring in 2020-21?", lambda: [("2022-23", *_NOT_THERE)]),
]


# --------------------------------------------------------------------------- #
# Running it.
# --------------------------------------------------------------------------- #
def run_eval(session: Session, cases: list[Case] = CASES, *, budget: float = 1.0,
             say: Callable[[str], None] = lambda _: None) -> dict:
    results, spent = [], 0.0
    for case in cases:
        if spent >= budget:
            say(f"budget of ${budget:g} reached; stopping")
            break
        try:
            expected = case.expect()
        except (sql.QueryError, IndexError) as exc:   # the data can't support this case now
            say(f"skip  {case.question} ({exc})")
            continue
        record = agent.ask(session, case.question, source="eval")
        spent += float(record.cost_usd)
        missing = grade(record.answer, expected)
        results.append({"question": case.question, "passed": not missing,
                        "expected": expected, "missing": missing, "answer": record.answer,
                        "status": record.status, "cost_usd": float(record.cost_usd),
                        "queries": len(record.queries)})
        say(f"{'PASS' if not missing else 'FAIL'}  {case.question}"
            + ("" if not missing else f"  (missing {missing})"))
    passed = sum(r["passed"] for r in results)
    report = {"date": date.today().isoformat(), "model": agent.MODEL, "passed": passed,
              "total": len(results), "cost_usd": round(spent, 4), "results": results}
    path = Path("logs") / f"ask-eval-{date.today():%Y-%m-%d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str))
    report["path"] = str(path)
    return report
