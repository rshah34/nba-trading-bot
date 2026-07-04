"""Analysis Agent: for a single game, assemble point-in-time context (recent form,
injuries, and RAG-retrieved news — all filtered to an `as_of` cutoff), ask Claude
for an independent win-probability estimate, then compare it to the market to
compute edge.

The model is deliberately BLIND to the betting line so its estimate is
independent; the de-vigged market probability is computed separately and only
used to score edge. Every prediction records its `as_of` time and a snapshot of
exactly what context it saw (context_used), so backtests replay honestly.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

import anthropic
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot.config import settings
from nba_bot.db.models import Game, Injury, Odds, Prediction, Team, TeamGameStats
from nba_bot.markets import MarketLine, market_line_from_odds
from nba_bot.rag import retrieval

# Injury statuses worth surfacing to the model (skip healthy/available noise).
_RELEVANT_INJURY_STATUSES = {"out", "doubtful", "questionable", "day-to-day", "gtd"}


# --------------------------------------------------------------------------- #
# Point-in-time context assembly (DB reads, filtered to as_of).
# --------------------------------------------------------------------------- #
@dataclass
class TeamForm:
    games: int
    wins: int
    losses: int
    avg_margin: float | None  # average plus/minus over the window


def recent_form(session: Session, team_id: int, as_of: date, n: int = 10) -> TeamForm:
    """Win/loss and average margin over the team's last n completed games before as_of."""
    rows = session.execute(
        select(TeamGameStats.plus_minus)
        .join(Game, TeamGameStats.game_id == Game.game_id)
        .where(
            TeamGameStats.team_id == team_id,
            Game.status == "final",
            Game.game_date < as_of,
            TeamGameStats.plus_minus.is_not(None),
        )
        .order_by(Game.game_date.desc())
        .limit(n)
    ).scalars().all()

    margins = [float(m) for m in rows]
    wins = sum(1 for m in margins if m > 0)
    return TeamForm(
        games=len(margins),
        wins=wins,
        losses=len(margins) - wins,
        avg_margin=round(sum(margins) / len(margins), 1) if margins else None,
    )


def current_injuries(session: Session, team_id: int, as_of: datetime) -> list[dict]:
    """Latest known injury status per player for a team, as of the cutoff."""
    rows = session.execute(
        select(Injury)
        .where(Injury.team_id == team_id, Injury.reported_at <= as_of)
        .order_by(Injury.reported_at.desc())
    ).scalars().all()

    seen: set[str] = set()
    out: list[dict] = []
    for inj in rows:
        if inj.player_name in seen:
            continue
        seen.add(inj.player_name)
        if (inj.status or "").lower() in _RELEVANT_INJURY_STATUSES:
            out.append({"player": inj.player_name, "status": inj.status, "reason": inj.reason})
    return out


def market_line(session: Session, game_id: str, as_of: datetime) -> MarketLine:
    """Consensus market line for a game from odds captured at or before as_of."""
    rows = session.execute(
        select(Odds).where(Odds.game_id == game_id, Odds.captured_at <= as_of)
    ).scalars().all()
    return market_line_from_odds(list(rows))


# --------------------------------------------------------------------------- #
# LLM prediction (structured output).
# --------------------------------------------------------------------------- #
class GamePrediction(BaseModel):
    # Fields are generated in order — reasoning FIRST so the model reasons before
    # committing to a number (chain-of-thought; matters most for non-thinking models).
    reasoning: str = Field(description="Step-by-step rationale, written BEFORE the probability")
    key_factors: list[str] = Field(description="The 2-5 factors that most drove this prediction")
    home_win_probability: float = Field(description="Probability the home team wins, 0.0-1.0")
    predicted_home_margin: float = Field(
        description="Predicted home margin in points; positive = home wins by that many"
    )


_SYSTEM = (
    "You are an expert NBA analyst producing calibrated pre-game win probabilities. "
    "Reason step by step from the team form, rest, injuries, and news provided, then "
    "commit to a probability. Anchor on the league baseline — NBA home teams win about "
    "58% of games — and move up or down from there for the margin edge, rest advantage, "
    "and injuries. Be decisive: when one team is clearly stronger, commit to a confident "
    "probability (0.65-0.80+); only stay near 0.50 when the matchup is genuinely even. "
    "Weigh injuries to star players heavily. You are NOT given the betting line — this is "
    "your own independent estimate."
)


def _form_line(team: str, f: dict) -> str:
    if not f.get("games"):
        return f"  {team}: no games played yet"
    return f"  {team}: {f['wins']}-{f['losses']}, avg margin {f['avg_margin']:+.1f}"


def _build_user_prompt(context: dict) -> str:
    home, away = context["home_team"], context["away_team"]
    hf, af = context["home_form"], context["away_form"]

    edges = []
    if hf.get("avg_margin") is not None and af.get("avg_margin") is not None:
        d = hf["avg_margin"] - af["avg_margin"]
        edges.append(f"margin edge: {home if d >= 0 else away} by {abs(d):.1f}")
    hr, ar = context["home_rest_days"], context["away_rest_days"]
    if hr is not None and ar is not None and hr != ar:
        edges.append(f"rest edge: {home if hr > ar else away} by {abs(hr - ar)} day(s)")
    edge_line = ("  → " + "; ".join(edges) + "\n") if edges else ""

    return (
        f"Predict this NBA game (home team vs away team).\n\n"
        f"HOME: {home}\nAWAY: {away}\nDate: {context['game_date']}\n\n"
        f"REST — home {context['home_rest_days']}d (back-to-back: {context['home_b2b']}), "
        f"away {context['away_rest_days']}d (back-to-back: {context['away_b2b']})\n\n"
        f"FORM (recent games):\n{_form_line(home, hf)}\n{_form_line(away, af)}\n{edge_line}\n"
        f"INJURIES — {home}: {context['home_injuries'] or 'none reported'}\n"
        f"INJURIES — {away}: {context['away_injuries'] or 'none reported'}\n\n"
        f"NEWS:\n{context['news'] or '(none)'}\n"
    )


def _generate_prediction(
    client: anthropic.Anthropic, system: str, user: str, model: str
) -> GamePrediction:
    # Adaptive-thinking models count thinking tokens against max_tokens, so
    # leave headroom above the small structured payload.
    response = client.messages.parse(
        model=model,
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=GamePrediction,
    )
    return response.parsed_output


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def predict_game(
    session: Session,
    game_id: str,
    as_of: datetime | None = None,
    model_version: str | None = None,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
    use_news: bool = True,
) -> Prediction:
    """Generate, store, and return a prediction for one game as of a cutoff time.

    use_news=False skips RAG retrieval — used for historical backtests, where no
    point-in-time news exists, so the Voyage call would be pure waste.
    """
    as_of = as_of or datetime.now(timezone.utc)
    model = model or settings.analysis_model
    model_version = model_version or model
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)

    game = session.get(Game, game_id)
    if game is None:
        raise ValueError(f"game {game_id} not found")
    home = session.get(Team, game.home_team_id)
    away = session.get(Team, game.away_team_id)

    home_form = recent_form(session, game.home_team_id, game.game_date)
    away_form = recent_form(session, game.away_team_id, game.game_date)
    home_inj = current_injuries(session, game.home_team_id, as_of)
    away_inj = current_injuries(session, game.away_team_id, as_of)

    news_hits = []
    if use_news:
        news_query = f"injury, lineup, and roster news for {home.full_name} and {away.full_name}"
        news_hits = retrieval.retrieve_relevant_news(
            session,
            query=news_query,
            team_ids=[game.home_team_id, game.away_team_id],
            as_of=game.game_date,
        )
    news_text = "\n".join(f"- {h.title}: {h.chunk_text}" for h in news_hits)

    context = {
        "home_team": home.full_name,
        "away_team": away.full_name,
        "game_date": str(game.game_date),
        "home_rest_days": game.home_rest_days,
        "away_rest_days": game.away_rest_days,
        "home_b2b": game.is_back_to_back_home,
        "away_b2b": game.is_back_to_back_away,
        "home_form": asdict(home_form),
        "away_form": asdict(away_form),
        "home_injuries": home_inj,
        "away_injuries": away_inj,
        "news": news_text,
    }

    prediction = _generate_prediction(client, _SYSTEM, _build_user_prompt(context), model)
    market = market_line(session, game_id, as_of)
    edge = None
    if market.home_win_prob is not None:
        edge = round(prediction.home_win_probability - market.home_win_prob, 4)

    context_snapshot = {
        "as_of": as_of.isoformat(),
        "home_injuries": home_inj,
        "away_injuries": away_inj,
        "news_urls": [h.url for h in news_hits],
        "home_form": asdict(home_form),
        "away_form": asdict(away_form),
        "market": {"home_win_prob": market.home_win_prob, "home_margin": market.home_margin,
                   "n_books": market.n_books},
        "edge_vs_market": edge,
        "key_factors": prediction.key_factors,
    }

    stmt = (
        pg_insert(Prediction)
        .values(
            game_id=game_id,
            model_version=model_version,
            as_of=as_of,
            predicted_home_win_prob=prediction.home_win_probability,
            predicted_spread=prediction.predicted_home_margin,
            market_home_win_prob=market.home_win_prob,
            market_spread=market.home_margin,
            reasoning=prediction.reasoning,
            context_used=context_snapshot,
        )
        .on_conflict_do_update(
            constraint="predictions_game_model_asof_key",
            set_={
                "predicted_home_win_prob": prediction.home_win_probability,
                "predicted_spread": prediction.predicted_home_margin,
                "market_home_win_prob": market.home_win_prob,
                "market_spread": market.home_margin,
                "reasoning": prediction.reasoning,
                "context_used": json.loads(json.dumps(context_snapshot)),
            },
        )
        .returning(Prediction.id)
    )
    pred_id = session.execute(stmt).scalar_one()
    session.commit()
    return session.get(Prediction, pred_id)


def run_predictions(
    session: Session,
    game_date: date,
    as_of: datetime | None = None,
    model_version: str | None = None,
) -> list[Prediction]:
    """Predict every scheduled game on a date."""
    game_ids = session.execute(
        select(Game.game_id).where(Game.game_date == game_date, Game.status == "scheduled")
    ).scalars().all()

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return [predict_game(session, gid, as_of=as_of, model_version=model_version, client=client)
            for gid in game_ids]
