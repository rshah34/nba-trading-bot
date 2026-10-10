"""Daily live pipeline: sequence the existing agents into the game-day runs.

Each step is isolated — a flaky nba_api / odds / Claude call is logged and the
run continues, so an unattended scheduled job never dies on one bad step. Two
runs make up the day:

* daily — mid-afternoon, everything in order: settle what finished, read the
          injuries and news, predict tonight's games, take current game and prop
          prices, choose the day's few best bets across both, and text them.
          Late on purpose: props are won on knowing who plays, which firms up
          through the day, and the recorded price is the one on offer when the
          text goes out.
* close — near tip: re-capture game lines and the prop markets we hold, which
          become the closing prices CLV is measured against.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from nba_bot import digest, notify, picks
from nba_bot.agents import (
    analysis_agent,
    betting_agent,
    data_agent,
    evaluation_agent,
    news_agent,
    props_agent,
)
from nba_bot.db.models import NOT_STARTED, REAL_GAMES, Game
from nba_bot.features.props import load_props_data

log = logging.getLogger("nba_bot.pipeline")


def summarize_run(result: dict) -> dict:
    """Collapse a run result to {step: ok} for at-a-glance monitoring."""
    return {k: bool(v.get("ok")) for k, v in result.items() if isinstance(v, dict)}


def append_run_log(phase: str, result: dict, log_dir: str | Path = "logs") -> dict:
    """Append a one-line JSON run summary to logs/pipeline-runs.jsonl.

    Gives an unattended scheduler a durable record of what ran and whether every
    step succeeded, without needing to parse full console logs.
    """
    steps = summarize_run(result)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "ok": all(steps.values()) if steps else False,
        "steps": steps,
    }
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "pipeline-runs.jsonl").open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def _step(name: str, fn) -> dict:
    try:
        result = fn()
        log.info("%s ✓ %s", name, result)
        return {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001 — isolate steps so one failure doesn't abort the run
        log.exception("%s FAILED", name)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _slate_teams(session: Session, on: date) -> set[int]:
    """Teams with a game still to be played on the date. Empty means there is
    nothing to price."""
    stmt = select(Game.home_team_id, Game.away_team_id).where(
        Game.game_date == on, REAL_GAMES, Game.status == "scheduled", NOT_STARTED)
    return {team for row in session.execute(stmt) for team in row}


def run_daily(session: Session, on: date | None = None) -> dict:
    """Settle what has finished, then price tonight: results → settlement → news →
    predictions → current game + prop prices → the day's picks → text."""
    on = on or date.today()
    steps = {
        # Finals and box scores for the games since the last run, tonight's schedule,
        # and the current injury report.
        "ingest": _step("ingest", lambda: data_agent.run_nightly(session)),
        # The rest of the season's fixtures: the league adds and moves games as it goes.
        "schedule": _step("schedule", lambda: data_agent.sync_schedule(session)),
        "mark_closing": _step("mark-closing", lambda: data_agent.mark_closing_lines(session)),
        "evaluate": _step("evaluate", lambda: evaluation_agent.run_evaluation(session)),
        # Settle paper bets: CLV vs. the closing line + P&L on the outcome.
        "settle_bets": _step("settle-bets", lambda: betting_agent.settle_bets(session)),
        "settle_props": _step("settle-props", lambda: props_agent.settle_prop_bets(session)),
        "news": _step("news", lambda: news_agent.sync_news(session)),
    }
    teams = _slate_teams(session, on)
    if not teams:
        # Nothing to price: don't fetch odds or pay a model to read news nobody will use.
        return steps

    data = load_props_data(session)  # one read of the box-score history, last night's included

    def choose() -> dict:
        result = picks.make_picks(session, on, data=data)
        result["picks"] = [digest.pick_label(c) for c in result["picks"]]
        return result

    steps |= {
        "signals": _step(
            "news-signals", lambda: news_agent.extract_signals(session, team_ids=teams)),
        "predict": _step(
            "predict", lambda: [p.game_id for p in analysis_agent.run_predictions(session, on)]),
        # Prices last, so they are as fresh as they can be when the picks are made.
        "odds": _step("ingest-odds", lambda: data_agent.sync_odds(session)),
        "prop_odds": _step("prop-odds", lambda: props_agent.sync_prop_odds(session, data=data)),
        "picks": _step("picks", choose),
        "notify": _step(
            "notify", lambda: {"text": notify.send(digest.best_bets(session, on))}),
    }
    return steps


def _game_odds(session: Session, on: date) -> dict:
    """Game-line snapshot — skipped on a day with nothing left to play, when it
    would spend credits pricing games we aren't betting."""
    if not _slate_teams(session, on):
        return {"skipped": "no games left today"}
    return data_agent.sync_odds(session)


# Each close run prices only the games tipping soon, so with runs three hours apart
# every game is bought once, by the run nearest its tip.
_CLOSE_WINDOW_HOURS = 3.25


def run_close(session: Session, on: date | None = None) -> dict:
    """Near tip: the snapshots that become the closing prices. Cheap — game lines
    are one request, and props re-price only the markets we hold bets in."""
    on = on or date.today()
    return {
        "odds": _step("ingest-odds", lambda: _game_odds(session, on)),
        "prop_odds": _step(
            "prop-odds", lambda: props_agent.sync_prop_odds(
                session, only_open_bets=True, window_hours=_CLOSE_WINDOW_HOURS)
        ),
    }
