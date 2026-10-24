"""Ask: answer a plain-English question about the data by writing SQL.

Claude gets the schema and one tool, `run_sql`, which runs a single read-only SELECT as
the nba_ask login (see `sql.py`). It looks things up, checks what it found, and writes
a short answer in which every number comes from a query it ran. Every question, its
answer and the queries behind it are saved, so the page can show the work.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

import anthropic
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from nba_bot import usage
from nba_bot.ask import data, sql
from nba_bot.config import settings
from nba_bot.db.models import AskQuestion

log = logging.getLogger("nba_bot.ask")

MODEL = "claude-sonnet-5-5"
EFFORT = "medium"
MAX_TURNS = 10
QUESTION_BUDGET = 0.30       # dollars, one question
DAILY_CAP = 2.0              # dollars, every question asked today together
_SHOWN_ROWS = 25             # rows of each query kept for the page's "show the work"

SYSTEM = """You answer questions about NBA games and player statistics by querying a \
PostgreSQL database with the run_sql tool. Every number in your answer must come from \
a query you ran in this conversation; never from memory.

Tables (schema `ask`, read-only):

teams(team, name, conference, division)
  team is the abbreviation used everywhere else (BOS, LAL, ...); name is the full name \
("Boston Celtics"). Map nicknames and cities to the abbreviation through this table.

team_games(season, season_type, game_id, game_date, team, opponent, home, team_score, \
opp_score, margin, won, rest_days, back_to_back, after_all_star_break, \
team_wins_before, team_losses_before, opp_wins_before, opp_losses_before, \
team_rank_before, opp_rank_before)
  One row per team per final game, so each game appears twice, once from each side. \
margin = team_score - opp_score. Records and ranks are the regular season going into \
that game; rank is by point margin per game, 1 = best (noisy in the first weeks, null \
before a team's first game and in preseason). "Top teams" usually means \
opp_rank_before <= 10; say which definition you used.

player_games(season, season_type, game_date, game_id, player_id, player, team, \
opponent, home, minutes, points, rebounds, assists, steals, blocks, turnovers, fgm, \
fga, fg3m, fg3a, ftm, fta, plus_minus, pra, won, after_all_star_break, opp_rank_before)
  One row per player per final game. minutes = 0 or null means he did not play: \
filter minutes > 0 for averages and games played. pra = points + rebounds + assists. \
Find players with ILIKE ('%tatum%'), then use the exact name.

schedule(season, season_type, game_date, tipoff_at, home, away, status, home_score, \
away_score)
  Every game, played or not; status is 'scheduled' or 'final'.

injuries(team, player, status, reason, return_date, reported_at)
  The latest injury report. A player whose return_date is after a game's date is out \
for it, whatever the status word says.

news(player, team, kind, summary, confidence, published_at, expires_at)
  Recent news read into player notes (kind: out, doubtful, questionable, returning, \
minutes_limit, role_change, ...), while they still apply.

Rules:
- season is a string like '2025-26'. season_type is 'regular', 'preseason', \
'playoffs' or 'play-in'. Use the regular season unless the question says otherwise, \
and say which season(s) you used.
- Playoff and play-in games are in both tables (season_type 'playoffs' / \
'play-in'); their records and ranks are the final regular-season ones. Count a \
series by grouping a team's playoff games against one opponent.
- Not in the data: seasons before 2022-23, play-by-play, shot locations, lineups, \
contracts. If the question needs them, say so plainly.
- League leaders: per-game leaders need at least 70% of games played (58 of 82) \
unless the question says otherwise; say so. A traded player's season is all his \
teams together: group by player, not by player and team.
- Keep queries small: aggregate in SQL, return at most a few dozen rows.
- If a query fails, read the error and fix it.

Answer in plain sentences: no markdown at all (no bullets, bold, headings or \
tables); the page shows your queries and their rows separately. The direct answer \
first, with its key numbers, then a sentence or two of context or caveat (sample \
size, definition used). Under 120 words. Records as W-L (12-4), averages to one decimal, percentages to one \
decimal with a % sign."""

TOOLS = [{
    "name": "run_sql",
    "description": "Run one read-only SELECT (or WITH ... SELECT) against the ask schema "
                   "and get back the columns and up to 200 rows. Errors come back as text "
                   "to fix.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "One PostgreSQL SELECT statement."},
            "purpose": {"type": "string", "description": "What this query finds out, in a "
                                                         "short phrase, for the person asking."},
        },
        "required": ["query", "purpose"],
        "additionalProperties": False,
    },
}]


def _coverage(session: Session) -> str:
    rows = session.execute(text(
        "SELECT season, season_type, count(*) / 2, max(game_date) FROM ask.team_games "
        "GROUP BY 1, 2 ORDER BY 1, 2")).all()
    return "; ".join(f"{s} {t}: {n} games through {d}" for s, t, n, d in rows)


def _opening(question: str, today: date, coverage: str) -> str:
    return (f"Today is {today:%A, %B %-d, %Y}. Final games in the data: {coverage}.\n\n"
            f"Question: {question.strip()}")


# --------------------------------------------------------------------------- #
# Limits.
# --------------------------------------------------------------------------- #
def why_not_ask(session: Session, question: str, now: datetime | None = None) -> str | None:
    """The reason a question can't be asked now, or None if it can."""
    if not question.strip():
        return "Type a question first."
    if len(question) > 500:
        return "Keep the question under 500 characters."
    if not settings.ask_db_password:
        return "Ask is not set up: set ASK_DB_PASSWORD in .env and run `nba-bot init-db`."
    now = now or datetime.now(timezone.utc)
    today = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    spent = float(session.execute(
        select(func.coalesce(func.sum(AskQuestion.cost_usd), 0))
        .where(AskQuestion.asked_at >= today, AskQuestion.source == "page")).scalar_one())
    if spent + QUESTION_BUDGET > DAILY_CAP:
        return f"Today's questions have used ${spent:.2f} of the ${DAILY_CAP:g} daily limit."
    return None


# --------------------------------------------------------------------------- #
# The loop.
# --------------------------------------------------------------------------- #
def ask(session: Session, question: str, *, client: anthropic.Anthropic | None = None,
        today: date | None = None, source: str = "page") -> AskQuestion:
    """Answer one question; the saved record carries the answer and the queries."""
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    record = AskQuestion(question=question.strip(), status="running", cost_usd=0, turns=0,
                         queries=[], source=source)
    session.add(record)
    session.commit()
    data.refresh_if_stale(session)
    messages: list[dict] = [{"role": "user", "content": _opening(
        question, today or date.today(), _coverage(session))}]
    spent = 0.0

    def end(status: str, answer: str | None) -> AskQuestion:
        record.status, record.answer, record.cost_usd = status, answer, round(spent, 4)
        session.commit()
        return record

    for turn in range(1, MAX_TURNS + 1):
        try:
            response = client.beta.messages.create(
                model=MODEL, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
                output_config={"effort": EFFORT}, cache_control={"type": "ephemeral"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.APIError as exc:
            log.exception("ask call failed")
            return end("error", f"The model couldn't be reached: {exc}")
        spent += usage.record("ask", response.model, response) or 0.0
        record.turns = turn

        if response.stop_reason == "refusal":
            return end("refused", "The model declined to answer this one.")
        messages.append({"role": "assistant", "content": response.content})
        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            answer = "\n".join(b.text for b in response.content if b.type == "text").strip()
            return end("done", answer or None)

        results = []
        for call in calls:
            args = call.input or {}
            entry = {"purpose": args.get("purpose", ""), "sql": args.get("query", "")}
            try:
                result = sql.run(args.get("query", ""))
                entry |= {"columns": result["columns"], "rows": result["rows"][:_SHOWN_ROWS],
                          "row_count": result["row_count"], "truncated": result["truncated"]}
                content, is_error = json.dumps(result, default=str), False
            except sql.QueryError as exc:
                entry["error"] = str(exc)
                content, is_error = f"Error: {exc}", True
            record.queries = [*record.queries, entry]
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": content,
                            "is_error": is_error})
        session.commit()
        messages.append({"role": "user", "content": results})
        if spent >= QUESTION_BUDGET:
            return end("too_long", "This question took more work than one question is "
                                   "allowed; try asking it more narrowly.")
    return end("too_long", "This question took more steps than one question is allowed; "
                           "try asking it more narrowly.")
