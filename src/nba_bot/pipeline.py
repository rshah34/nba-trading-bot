"""Daily live pipeline: sequence the existing agents into the game-day phases.

Each step is isolated — a flaky nba_api / odds / Claude call is logged and the
run continues, so an unattended cron job never dies on one bad step. The phases
map to the game-day timeline (see the as_of/cutoff model): pregame refreshes data
and predicts with the freshest injuries; postgame captures the closing line and
scores resolved games.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from nba_bot.agents import analysis_agent, betting_agent, data_agent, evaluation_agent

log = logging.getLogger("nba_bot.pipeline")


def _step(name: str, fn) -> dict:
    try:
        result = fn()
        log.info("%s ✓ %s", name, result)
        return {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001 — isolate steps so one failure doesn't abort the run
        log.exception("%s FAILED", name)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def run_pregame(session: Session, on: date | None = None) -> dict:
    """Morning / pre-tip: refresh data + injuries + results, capture odds, predict today."""
    on = on or date.today()
    return {
        "ingest": _step("ingest", lambda: data_agent.run_nightly(session)),
        "odds": _step("ingest-odds", lambda: data_agent.sync_odds(session)),
        "predict": _step(
            "predict",
            lambda: [p.game_id for p in analysis_agent.run_predictions(session, on)],
        ),
        # Size paper bets where the calibrated prob disagrees with the current market.
        "bets": _step("record-bets", lambda: betting_agent.record_bets(session, on)),
    }


def run_postgame(session: Session) -> dict:
    """After games resolve: refresh final results + box/player stats, mark the
    closing line, and score the newly-resolved predictions.
    """
    return {
        "ingest": _step("ingest", lambda: data_agent.run_nightly(session)),
        "mark_closing": _step("mark-closing", lambda: data_agent.mark_closing_lines(session)),
        "evaluate": _step("evaluate", lambda: evaluation_agent.run_evaluation(session)),
        # Settle paper bets: CLV vs. the closing line + P&L on the outcome.
        "settle_bets": _step("settle-bets", lambda: betting_agent.settle_bets(session)),
    }
