"""Analysis Agent: for a single game, assemble point-in-time context (recent form,
injuries, and the strongest news reports — all filtered to an `as_of` cutoff), ask Claude
for an independent win-probability estimate, then compare it to the market to
compute edge.

The model is deliberately BLIND to the betting line so its estimate is
independent; the de-vigged market probability is computed separately and only
used to score edge. Every prediction records its `as_of` time and a snapshot of
exactly what context it saw (context_used), so backtests replay honestly.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

import anthropic
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot import usage
from nba_bot.agents import news_agent
from nba_bot.config import settings
from nba_bot.db.models import (
    REAL_GAMES,
    Game,
    Injury,
    Odds,
    PlayerGameStats,
    Prediction,
    Team,
    TeamGameStats,
)
from nba_bot.markets import MarketLine, market_line_from_odds

log = logging.getLogger("nba_bot.analysis")

# Injury statuses worth surfacing to the model (skip healthy/available noise).
_RELEVANT_INJURY_STATUSES = {"out", "doubtful", "questionable", "day-to-day", "gtd"}
# One sync writes the whole league report within seconds; rows this close to the
# newest one belong to the same report.
_REPORT_BATCH_WINDOW = timedelta(minutes=10)


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
            REAL_GAMES,
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


def player_form(session: Session, team_id: int, as_of: date, n_games: int = 10,
                top_k: int = 4) -> list[dict]:
    """Top contributors for a team over its last n_games before as_of, by recent
    production. 'Star' is computed here, not declared — a breakout or a decline
    shows up in the trailing numbers. Point-in-time (games strictly before as_of).
    """
    recent_games = session.execute(
        select(Game.game_id)
        .join(PlayerGameStats, PlayerGameStats.game_id == Game.game_id)
        .where(PlayerGameStats.team_id == team_id, REAL_GAMES, Game.game_date < as_of)
        .group_by(Game.game_id, Game.game_date)
        .order_by(Game.game_date.desc())
        .limit(n_games)
    ).scalars().all()
    if not recent_games:
        return []

    rows = session.execute(
        select(
            PlayerGameStats.player_name,
            func.count().label("gp"),
            func.avg(PlayerGameStats.minutes),
            func.avg(PlayerGameStats.points),
            func.avg(PlayerGameStats.rebounds),
            func.avg(PlayerGameStats.assists),
        )
        .where(PlayerGameStats.team_id == team_id, PlayerGameStats.game_id.in_(recent_games))
        .group_by(PlayerGameStats.player_name)
    ).all()

    players = []
    for name, gp, mpg, ppg, rpg, apg in rows:
        ppg, rpg, apg = float(ppg), float(rpg), float(apg)
        players.append({
            "name": name, "gp": gp, "mpg": round(float(mpg), 1),
            "ppg": round(ppg, 1), "rpg": round(rpg, 1), "apg": round(apg, 1),
            "impact": ppg + 0.5 * (rpg + apg),  # simple, transparent ranking
        })
    players.sort(key=lambda p: p["impact"], reverse=True)
    return players[:top_k]


def current_injuries(session: Session, team_id: int, as_of: datetime) -> list[dict]:
    """A team's injuries on the most recent report at or before the cutoff.

    Each sync appends the whole league report, and a player who recovers simply stops
    being listed — so "latest row per player" would keep him injured forever. Only
    rows from the newest sync count.
    """
    latest = session.execute(
        select(func.max(Injury.reported_at)).where(Injury.reported_at <= as_of)
    ).scalar_one_or_none()
    if latest is None:
        return []
    rows = session.execute(
        select(Injury)
        .where(
            Injury.team_id == team_id,
            Injury.reported_at <= latest,
            Injury.reported_at >= latest - _REPORT_BATCH_WINDOW,
        )
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
    "Weigh each team's leading contributors and their recent production; if a key player "
    "is out, adjust for the scoring/impact lost. You are NOT given the betting line — this "
    "is your own independent estimate."
)


def _form_line(team: str, f: dict) -> str:
    if not f.get("games"):
        return f"  {team}: no games played yet"
    return f"  {team}: {f['wins']}-{f['losses']}, avg margin {f['avg_margin']:+.1f}"


def _players_line(players: list, injuries: list) -> str:
    """Compact 'Name pts/reb/ast' list for top contributors, flagging any who are out."""
    if not players:
        return "n/a"
    injured = {i["player"] for i in injuries}
    return ", ".join(
        f"{p['name']} {p['ppg']:.0f}/{p['rpg']:.0f}/{p['apg']:.0f}"
        + (" (OUT)" if p["name"] in injured else "")
        for p in players
    )


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
        f"KEY PLAYERS (recent per-game pts/reb/ast):\n"
        f"  {home}: {_players_line(context['home_players'], context['home_injuries'])}\n"
        f"  {away}: {_players_line(context['away_players'], context['away_injuries'])}\n\n"
        f"INJURIES — {home}: {context['home_injuries'] or 'none reported'}\n"
        f"INJURIES — {away}: {context['away_injuries'] or 'none reported'}\n\n"
        f"NEWS (player reports, most reliable first):\n{context['news'] or '(none)'}\n"
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
    usage.record("game", model, response)
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
) -> Prediction:
    """Generate, store, and return a prediction for one game as of a cutoff time."""
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
    home_players = player_form(session, game.home_team_id, game.game_date)
    away_players = player_form(session, game.away_team_id, game.game_date)
    # News = the reports about these two teams that are most firmly and widely
    # sourced as of the cutoff (already read out of every article by the news agent).
    reports = news_agent.team_news(session, [game.home_team_id, game.away_team_id], as_of)
    team_names = {game.home_team_id: home.full_name, game.away_team_id: away.full_name}
    news_lines = [
        f"- {r.signal.player_name}"
        + (f" ({team_names[r.signal.team_id]})" if r.signal.team_id in team_names else "")
        + f" — {r.signal.kind.replace('_', ' ')}"
        + (f", {r.sources} sources" if r.sources > 1 else "")
        + f": {r.signal.summary}"
        for r in reports
    ]
    news_text = "\n".join(news_lines)

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
        "home_players": home_players,
        "away_players": away_players,
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
        "news": news_lines,
        "home_form": asdict(home_form),
        "away_form": asdict(away_form),
        "home_players": [p["name"] for p in home_players],
        "away_players": [p["name"] for p in away_players],
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
    force: bool = False,
) -> list[Prediction]:
    """Predict every scheduled game on a date that doesn't have a prediction yet.

    A game already predicted by this model is skipped: the phase can run more than
    once in a day (a manual run, a job catching up after sleep), and each repeat
    would otherwise pay for the whole slate again. `force=True` re-predicts.
    """
    game_ids = session.execute(
        # Preseason is exhibition — predicting it would pollute the track record.
        select(Game.game_id).where(
            Game.game_date == game_date, Game.status == "scheduled", REAL_GAMES
        )
    ).scalars().all()
    if not force and game_ids:
        done = set(session.execute(
            select(Prediction.game_id).where(
                Prediction.game_id.in_(game_ids),
                Prediction.model_version == (model_version or settings.analysis_model),
            )
        ).scalars())
        game_ids = [gid for gid in game_ids if gid not in done]
    if not game_ids:
        return []

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    predictions = []
    for gid in game_ids:
        # One game failing (an API error, a refusal) must not cost the rest of the slate.
        try:
            predictions.append(
                predict_game(session, gid, as_of=as_of, model_version=model_version, client=client)
            )
        except Exception:  # noqa: BLE001
            session.rollback()
            log.exception("prediction failed for game %s", gid)
    return predictions
