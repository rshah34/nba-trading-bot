"""Daily live pipeline: sequence the existing agents into the game-day phases.

Each step is isolated — a flaky nba_api / odds / Claude call is logged and the
run continues, so an unattended scheduled job never dies on one bad step. Four
phases follow the game-day timeline:

* pregame  — morning: refresh data + news, capture game lines, predict, bet games.
* props    — mid-afternoon: re-read injuries and news, price player props, bet,
             and text the day's best bets. Later than the game bets on purpose:
             props are won on knowing who plays, and that firms up through the day.
* close    — near tip: re-capture game lines and the prop markets we hold, which
             become the closing prices CLV is measured against.
* postgame — next morning: results, settlement, and the results text.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nba_bot import digest, notify
from nba_bot.agents import (
    analysis_agent,
    betting_agent,
    data_agent,
    evaluation_agent,
    news_agent,
    props_agent,
)
from nba_bot.db.models import REAL_GAMES, Game
from nba_bot.rag import ingest as news_ingest

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


def _has_slate(session: Session, on: date) -> bool:
    """Any regular-season or playoff game on the date? (No texts on empty days.)"""
    return bool(session.execute(
        select(func.count()).select_from(Game).where(Game.game_date == on, REAL_GAMES)
    ).scalar())


def _send_best_bets(session: Session, on: date, window_hours: float | None = None) -> dict:
    if not _has_slate(session, on):
        return {"sent": False, "reason": "no games today"}
    if window_hours is not None:
        # The early run only speaks up when it actually covered a game.
        soon = datetime.now(timezone.utc) + timedelta(hours=window_hours)
        early = session.execute(
            select(func.count()).select_from(Game).where(
                Game.game_date == on, REAL_GAMES, Game.status == "scheduled",
                Game.tipoff_at <= soon)
        ).scalar()
        if not early:
            return {"sent": False, "reason": "no early tips"}
    return {"sent": True, "channels": notify.send(digest.best_bets(session, on), "NBA best bets")}


def _send_results(session: Session) -> dict:
    text = digest.results(session)
    if text is None:
        return {"sent": False, "reason": "nothing settled"}
    return {"sent": True, "channels": notify.send(text, "NBA results", kind="results")}


def run_pregame(session: Session, on: date | None = None) -> dict:
    """Morning: refresh data + injuries + results + news, capture odds, predict, bet games."""
    on = on or date.today()
    return {
        "ingest": _step("ingest", lambda: data_agent.run_nightly(session)),
        "news": _step("news", lambda: news_ingest.sync_news(session)),
        "odds": _step("ingest-odds", lambda: data_agent.sync_odds(session)),
        "predict": _step(
            "predict",
            lambda: [p.game_id for p in analysis_agent.run_predictions(session, on)],
        ),
        # Size paper bets where the calibrated prob disagrees with the current market.
        "bets": _step("record-bets", lambda: betting_agent.record_bets(session, on)),
    }


def run_props(session: Session, on: date | None = None,
              window_hours: float | None = None) -> dict:
    """Freshest availability → price player props → bet → text.

    `window_hours` limits the run to games tipping that soon: the early run that
    covers afternoon tips the main run would be too late for.
    """
    on = on or date.today()

    def bets() -> dict:
        result = props_agent.record_prop_bets(session, on, window_hours=window_hours)
        result["bets"] = [f"{b['player_name']} {b['decision'].side} {b['decision'].line:g} "
                          f"{b['market']}" for b in result["bets"]]
        return result

    return {
        "injuries": _step("injuries", lambda: data_agent.sync_injuries(session)),
        "news": _step("news", lambda: news_ingest.sync_news(session)),
        "signals": _step("news-signals", lambda: news_agent.extract_signals(session)),
        "prop_odds": _step("prop-odds", lambda: props_agent.sync_prop_odds(
            session, window_hours=window_hours or 20)),
        "prop_bets": _step("prop-bets", bets),
        "notify": _step("notify", lambda: _send_best_bets(session, on, window_hours)),
    }


def run_close(session: Session) -> dict:
    """Near tip: the snapshots that become the closing prices. Cheap — game lines
    are one request, and props re-price only the markets we hold bets in."""
    return {
        "odds": _step("ingest-odds", lambda: data_agent.sync_odds(session)),
        "prop_odds": _step(
            "prop-odds", lambda: props_agent.sync_prop_odds(session, only_open_bets=True)
        ),
    }


def run_postgame(session: Session) -> dict:
    """After games resolve: refresh final results + box/player stats, mark the
    closing line, score the newly-resolved predictions, settle, and text results.
    """
    return {
        "ingest": _step("ingest", lambda: data_agent.run_nightly(session)),
        "mark_closing": _step("mark-closing", lambda: data_agent.mark_closing_lines(session)),
        "evaluate": _step("evaluate", lambda: evaluation_agent.run_evaluation(session)),
        # Settle paper bets: CLV vs. the closing line + P&L on the outcome.
        "settle_bets": _step("settle-bets", lambda: betting_agent.settle_bets(session)),
        "settle_props": _step("settle-props", lambda: props_agent.settle_prop_bets(session)),
        "notify": _step("notify", lambda: _send_results(session)),
    }
