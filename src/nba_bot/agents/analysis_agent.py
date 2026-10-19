"""Analysis Agent: the game-winner model's live predictions.

Each game gets the home team's chance to win from the two teams' point margins this
season, home court, and the scoring each side is missing tonight — read from the
same injury report and news the prop projections use (`features/games.py`). Blind
to the betting line: the de-vigged market probability is stored beside it only to
score the edge. Every prediction records its `as_of` time and what it saw.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nba_bot.agents import props_agent
from nba_bot.db.models import NOT_STARTED, REAL_GAMES, Game, Odds, Prediction, Team
from nba_bot.features import games as model
from nba_bot.features.props import PropsData
from nba_bot.markets import MarketLine, market_line_from_odds


def market_line(session: Session, game_id: str, as_of: datetime) -> MarketLine:
    """Consensus market line for a game from odds captured at or before as_of."""
    rows = session.execute(
        select(Odds).where(Odds.game_id == game_id, Odds.captured_at <= as_of)
    ).scalars().all()
    return market_line_from_odds(list(rows))


def _summary(abbr: dict[int, str], game: Game, form, missing: dict[int, list], names) -> str:
    """One line on why: both margins, and who each side is without."""
    def margin(team: int) -> str:
        f = form.get(team)
        return f"{f.margin if f else 0.0:+.1f}"

    home, away = abbr[game.home_team_id], abbr[game.away_team_id]
    parts = [f"{home} margin {margin(game.home_team_id)} vs {away} {margin(game.away_team_id)}, "
             f"at home"]
    for team in (game.home_team_id, game.away_team_id):
        if missing[team]:
            who = ", ".join(names[pid] for pid, _ in missing[team][:3])
            pts = sum(p for _, p in missing[team])
            parts.append(f"{abbr[team]} without {who} ({pts:.0f} points a game)")
    return "; ".join(parts)


def run_predictions(
    session: Session, game_date: date, *, data: PropsData, as_of: datetime | None = None,
) -> list[Prediction]:
    """Predict every game on a date that hasn't tipped, with the newest availability.

    Free and deterministic, so each run re-predicts rather than reusing an earlier
    call: the latest injury report is the one that should price the bet. A game
    where either team hasn't played MIN_GAMES yet is skipped.
    """
    as_of = as_of or datetime.now(timezone.utc)
    games = session.execute(
        # Preseason is exhibition, and a call made after tip is not a prediction.
        select(Game).where(Game.game_date == game_date, Game.status == "scheduled",
                           REAL_GAMES, NOT_STARTED)
    ).scalars().all()
    if not games:
        return []
    slate = props_agent.slate_for(session, data, game_date, as_of)
    abbr = {t.team_id: t.abbreviation for t in session.execute(select(Team)).scalars()}

    out = []
    for game in games:
        teams = (game.home_team_id, game.away_team_id)
        if any(t not in slate.form or len(slate.form[t].dates) < model.MIN_GAMES for t in teams):
            continue
        missing = {}
        for team, opp in (teams, teams[::-1]):
            view = props_agent.team_view(session, data, slate, team, opp, team == game.home_team_id)
            missing[team] = model.regulars_missing(data, team, game_date, view.out)
        prob, margin = model.win_probability(
            model.expected_margin(slate.form, *teams),
            sum(p for _, p in missing[teams[0]]), sum(p for _, p in missing[teams[1]]))
        market = market_line(session, game.game_id, as_of)
        context = {
            "as_of": as_of.isoformat(),
            "key_factors": [_summary(abbr, game, slate.form, missing, data.names)],
            "missing": {abbr[t]: [{"player": data.names[pid], "pts": round(p, 1)}
                                  for pid, p in missing[t]] for t in teams},
            "market": {"home_win_prob": market.home_win_prob, "home_margin": market.home_margin,
                       "n_books": market.n_books},
            "edge_vs_market": (round(prob - market.home_win_prob, 4)
                               if market.home_win_prob is not None else None),
        }
        values = {"predicted_home_win_prob": round(prob, 4), "predicted_spread": round(margin, 2),
                  "market_home_win_prob": market.home_win_prob, "market_spread": market.home_margin,
                  "context_used": context}
        pred_id = session.execute(
            pg_insert(Prediction)
            .values(game_id=game.game_id, model_version=model.GAME_MODEL, as_of=as_of, **values)
            .on_conflict_do_update(constraint="predictions_game_model_asof_key", set_=values)
            .returning(Prediction.id)
        ).scalar_one()
        out.append(pred_id)
    session.commit()
    return [session.get(Prediction, pid) for pid in out]
