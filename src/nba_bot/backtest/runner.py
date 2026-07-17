"""Drive a backtest: load the season, predict a chronological slice of games with
the Analysis Agent (point-in-time), score them with the Evaluation Agent, and
report. Predictions are tagged with a distinct model_version so backtest data is
cleanly separable from live predictions.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time, timezone

import anthropic
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nba_bot.agents import analysis_agent, evaluation_agent
from nba_bot.backtest import loader, report
from nba_bot.config import settings
from nba_bot.db.engine import SessionLocal
from nba_bot.db.models import Game

log = logging.getLogger("nba_bot.backtest")

# Rough output-token budget per prediction (thinking + structured payload) for
# the cost estimate; $/1M input,output.
_EST_OUTPUT_TOKENS = 900
_PRICING = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-4-8": (5.0, 25.0),
}


def _backtest_games(
    session: Session, season: str, limit: int, season_slice: str = "start"
) -> list[Game]:
    """Select `limit` games from a season, always returned in chronological order.

    season_slice: 'start' (opening games), 'mid' (around midseason — richest signal
    without late-season resting), or 'end' (final regular-season games, max form history).
    """
    where = (Game.season == season, Game.status == "final", Game.home_score.is_not(None))
    total = session.execute(select(func.count()).select_from(Game).where(*where)).scalar_one()
    if season_slice == "end":
        offset = max(0, total - limit)
    elif season_slice == "mid":
        offset = max(0, total // 2 - limit // 2)
    else:
        offset = 0
    return list(
        session.execute(
            select(Game).where(*where).order_by(Game.game_date, Game.game_id).offset(offset).limit(limit)
        ).scalars().all()
    )


def estimate_cost(session: Session, games: list[Game], model: str, client: anthropic.Anthropic) -> dict:
    """Estimate spend by token-counting one representative prompt × the game count."""
    if not games:
        return {"games": 0, "est_usd": 0.0}
    g = games[len(games) // 2]
    home = session.get(Game, g.game_id)
    # Reuse the agent's prompt shape with placeholder context for the count.
    sample_user = analysis_agent._build_user_prompt({
        "home_team": "Home", "away_team": "Away", "game_date": str(home.game_date),
        "home_rest_days": home.home_rest_days, "away_rest_days": home.away_rest_days,
        "home_b2b": home.is_back_to_back_home, "away_b2b": home.is_back_to_back_away,
        "home_form": {"games": 10, "wins": 6, "losses": 4, "avg_margin": 2.0},
        "away_form": {"games": 10, "wins": 5, "losses": 5, "avg_margin": -1.0},
        "home_ff": {"games": 10, "pace": 99.5, "efg": 0.55, "tov_pct": 0.13, "oreb_pct": 0.28,
                    "ft_rate": 0.25, "def_efg": 0.53, "def_tov_pct": 0.14, "dreb_pct": 0.73,
                    "def_ft_rate": 0.24},
        "away_ff": {"games": 10, "pace": 98.0, "efg": 0.53, "tov_pct": 0.14, "oreb_pct": 0.26,
                    "ft_rate": 0.24, "def_efg": 0.55, "def_tov_pct": 0.12, "dreb_pct": 0.71,
                    "def_ft_rate": 0.26},
        "home_players": [{"name": "Player One", "ppg": 26.0, "rpg": 7.0, "apg": 6.0}] * 4,
        "away_players": [{"name": "Player Two", "ppg": 24.0, "rpg": 8.0, "apg": 5.0}] * 4,
        "home_injuries": [], "away_injuries": [], "news": "",
    })
    in_tokens = client.messages.count_tokens(
        model=model, system=analysis_agent._SYSTEM,
        messages=[{"role": "user", "content": sample_user}],
    ).input_tokens
    in_price, out_price = _PRICING.get(model, (2.0, 10.0))
    per_game = (in_tokens * in_price + _EST_OUTPUT_TOKENS * out_price) / 1_000_000
    return {"games": len(games), "input_tokens_each": in_tokens,
            "est_usd": round(per_game * len(games), 3)}


def model_version_for(season: str, model: str, season_slice: str = "start", tag: str = "") -> str:
    base = f"backtest-{season}-{model}-{season_slice}"
    return f"{base}-{tag}" if tag else base


def predict_and_evaluate(
    session: Session,
    games: list[Game],
    model: str,
    model_version: str,
    client: anthropic.Anthropic | None = None,
    oracle_injuries: bool = False,
    use_matchup_features: bool = True,
    concurrency: int = 8,
) -> dict:
    """Predict each game point-in-time, score them, and return the report. Spends API credits.

    Predictions run concurrently (`concurrency` workers), each with its own DB session
    since a Session isn't thread-safe; the Anthropic client is shared (it is). Per-game
    failures are isolated so one bad game doesn't abort the run — the report's `failed`
    count reflects them. Concurrency turns a ~1hr sequential 300-game run into minutes.

    oracle_injuries=True measures the on-off feature's CEILING by deriving availability
    from each game's own box score — optimistic and mildly look-ahead, never a real
    track record. Tag such runs distinctly.

    use_matchup_features=False runs the champion prompt (no Four Factors / on-off) for a
    clean A/B baseline on the same slice.
    """
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)
    jobs = [(g.game_id, g.game_date) for g in games]  # detach from the outer session

    def _predict(job: tuple[str, object]) -> None:
        game_id, game_date = job
        as_of = datetime.combine(game_date, time(18, 0), tzinfo=timezone.utc)
        with SessionLocal() as s:  # per-thread session
            analysis_agent.predict_game(
                s, game_id, as_of=as_of, model_version=model_version,
                client=client, model=model, use_news=False, oracle_injuries=oracle_injuries,
                use_matchup_features=use_matchup_features,
            )

    failed = 0
    if concurrency and concurrency > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_predict, j): j for j in jobs}
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception:  # noqa: BLE001 — isolate one flaky game from the run
                    log.exception("prediction failed for %s", futures[fut][0])
                    failed += 1
    else:
        for j in jobs:
            try:
                _predict(j)
            except Exception:  # noqa: BLE001
                log.exception("prediction failed for %s", j[0])
                failed += 1

    evaluation_agent.run_evaluation(session)
    rep = report.build_report(session, model_version)
    rep["failed"] = failed
    return rep


def run_backtest(
    session: Session,
    season: str = "2025-26",
    limit: int = 30,
    model: str = "claude-haiku-4-5",
    season_slice: str = "start",
) -> dict:
    """Load the season, predict `limit` games, evaluate, and return the report."""
    load_result = loader.load_season(session, season)
    games = _backtest_games(session, season, limit, season_slice)
    rep = predict_and_evaluate(
        session, games, model, model_version_for(season, model, season_slice)
    )
    return {"loaded": load_result, "report": rep}
