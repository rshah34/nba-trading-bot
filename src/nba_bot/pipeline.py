"""Daily live pipeline: sequence the existing agents into the game-day phases.

Each step is isolated — a flaky nba_api / odds / Claude call is logged and the
run continues, so an unattended scheduled job never dies on one bad step. Four
phases follow the game-day timeline:

* pregame  — morning: refresh data + news, predict today's games.
* props    — mid-afternoon: re-read injuries and news, take current game and prop
             prices, choose the day's few best bets across both, and text them.
             Late on purpose: props are won on knowing who plays, which firms up
             through the day, and the recorded price is the one on offer when the
             text goes out.
* close    — near tip: re-capture game lines and the prop markets we hold, which
             become the closing prices CLV is measured against.
* postgame — next morning: results and settlement.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
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
from nba_bot.db.models import REAL_GAMES, Game
from nba_bot.features.props import load_props_data

log = logging.getLogger("nba_bot.pipeline")


def summarize_run(result: dict) -> dict:
    """Collapse a phase result to {step: ok} for at-a-glance monitoring."""
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


def _slate_teams(session: Session, on: date, window_hours: float | None = None) -> set[int]:
    """Teams with a game still to be played on the date (tipping within the window,
    when one is given). Empty means there is nothing to price."""
    stmt = select(Game.home_team_id, Game.away_team_id).where(
        Game.game_date == on, REAL_GAMES, Game.status == "scheduled")
    if window_hours is not None:
        stmt = stmt.where(Game.tipoff_at <= datetime.now(timezone.utc) + timedelta(hours=window_hours))
    return {team for row in session.execute(stmt) for team in row}


def _send_best_bets(session: Session, on: date) -> dict:
    return {"text": notify.send(digest.best_bets(session, on))}


def run_pregame(session: Session, on: date | None = None) -> dict:
    """Morning: refresh data + injuries + results + news, and predict today's games.
    Nothing is bet here — the picks are made in the afternoon, at the prices then."""
    on = on or date.today()
    steps = {
        "ingest": _step("ingest", lambda: data_agent.run_nightly(session)),
        "news": _step("news", lambda: news_agent.sync_news(session)),
    }
    teams = _slate_teams(session, on)
    if teams:  # no games today: nothing to read the news for, nothing to predict
        steps["signals"] = _step(
            "news-signals", lambda: news_agent.extract_signals(session, team_ids=teams))
        steps["predict"] = _step(
            "predict", lambda: [p.game_id for p in analysis_agent.run_predictions(session, on)])
    return steps


def run_props(session: Session, on: date | None = None,
              window_hours: float | None = None) -> dict:
    """Freshest availability → current game + prop prices → the day's picks → text.

    `window_hours` limits the run to games tipping that soon: the early run that
    covers afternoon tips the main run would be too late for.
    """
    on = on or date.today()
    teams = _slate_teams(session, on, window_hours)
    if not teams:
        # Nothing to price: don't fetch odds or pay a model to read news nobody will use.
        return {"slate": {"ok": True, "result": "no games to price in this window"}}

    data = load_props_data(session)  # one read of the box-score history for the whole phase

    def choose() -> dict:
        result = picks.make_picks(session, on, data=data, window_hours=window_hours)
        result["picks"] = [digest.pick_label(c) for c in result["picks"]]
        return result

    return {
        "injuries": _step("injuries", lambda: data_agent.sync_injuries(session)),
        "news": _step("news", lambda: news_agent.sync_news(session)),
        "signals": _step(
            "news-signals", lambda: news_agent.extract_signals(session, team_ids=teams)),
        "odds": _step("ingest-odds", lambda: data_agent.sync_odds(session)),
        "prop_odds": _step("prop-odds", lambda: props_agent.sync_prop_odds(
            session, data=data, window_hours=window_hours or 20)),
        "picks": _step("picks", choose),
        "notify": _step("notify", lambda: _send_best_bets(session, on)),
    }


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


def run_postgame(session: Session) -> dict:
    """After games resolve: refresh final results + box/player stats, mark the
    closing line, score the newly-resolved predictions, and settle the bets.
    """
    return {
        "ingest": _step("ingest", lambda: data_agent.run_nightly(session)),
        "mark_closing": _step("mark-closing", lambda: data_agent.mark_closing_lines(session)),
        "evaluate": _step("evaluate", lambda: evaluation_agent.run_evaluation(session)),
        # Settle paper bets: CLV vs. the closing line + P&L on the outcome.
        "settle_bets": _step("settle-bets", lambda: betting_agent.settle_bets(session)),
        "settle_props": _step("settle-props", lambda: props_agent.settle_prop_bets(session)),
    }
