"""Build the text messages: today's best bets, and last night's results."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from nba_bot.agents import betting_agent, props_agent
from nba_bot.config import settings
from nba_bot.db.models import Bet, Game, PropBet, Team
from nba_bot.features.props import MARKET_LABELS

_BOOKS = {"draftkings": "DK", "fanduel": "FD", "betmgm": "MGM", "williamhill_us": "Caesars",
          "espnbet": "ESPN", "betrivers": "Rivers", "fanatics": "Fanatics", "bovada": "Bovada",
          "betonlineag": "BetOnline", "hardrockbet": "HardRock"}


def _american(decimal_odds: float) -> str:
    if decimal_odds >= 2:
        return f"+{round((decimal_odds - 1) * 100)}"
    return f"-{round(100 / (decimal_odds - 1))}"


def _stake(fraction: float) -> str:
    """Stake as units (1u = 1% of bankroll), plus dollars when a bankroll is set."""
    units = f"{fraction * 100:.1f}u"
    return f"{units} (${fraction * settings.bankroll:,.0f})" if settings.bankroll > 0 else units


def _local_time(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone().strftime("%-I:%M%p").lower()


def _teams(session: Session) -> dict[int, str]:
    return {t.team_id: t.abbreviation for t in session.execute(select(Team)).scalars()}


def best_bets(session: Session, game_date: date) -> str:
    """The pre-game text: every bet recorded for a date, best first."""
    teams = _teams(session)
    props = props_agent.todays_bets(session, game_date)
    games = session.execute(
        select(Bet, Game).join(Game, Bet.game_id == Game.game_id)
        .where(Game.game_date == game_date).order_by(Bet.edge.desc())
    ).all()

    lines = [f"NBA best bets, {game_date:%a %b %-d}"]
    if not props and not games:
        lines.append("No bets today: nothing cleared the edge threshold.")
        return "\n".join(lines)

    if props:
        tips = dict(session.execute(
            select(Game.game_id, Game.tipoff_at).where(Game.game_date == game_date)
        ).all())
        lines.append(f"\nPROPS ({len(props)})")
        for i, b in enumerate(props, 1):
            book = _BOOKS.get(b.sportsbook, b.sportsbook)
            lines.append(
                f"{i}. {b.player_name} ({teams.get(b.team_id, '?')}) {b.side.upper()} "
                f"{float(b.line):g} {MARKET_LABELS.get(b.market, b.market)} "
                f"{_american(float(b.decimal_odds))} {book}"
            )
            when = _local_time(tips.get(b.game_id))
            lines.append(
                f"   proj {float(b.proj_mean):.1f} in {float(b.proj_minutes or 0):.0f}m | "
                f"model {float(b.model_prob):.0%}, mkt {float(b.market_prob):.0%}, "
                f"edge {float(b.edge):+.1%} | EV {float(b.ev):+.1%} | "
                f"{_stake(float(b.stake_fraction))}" + (f" | tip {when}" if when else "")
            )
            if b.notes:
                lines.append(f"   {b.notes}")

    if games:
        lines.append(f"\nGAMES ({len(games)})")
        for bet, game in games:
            side = game.home_team_id if bet.side == "home" else game.away_team_id
            lines.append(
                f"- {teams.get(side, '?')} ML {_american(float(bet.decimal_odds))} "
                f"({teams.get(game.away_team_id, '?')} @ {teams.get(game.home_team_id, '?')}) | "
                f"model {float(bet.model_prob):.0%} vs mkt {float(bet.market_prob):.0%} | "
                f"{_stake(float(bet.stake_fraction))}"
            )

    total = sum(float(b.stake_fraction) for b in props) + sum(float(b.stake_fraction) for b, _ in games)
    lines.append(f"\nTotal at risk: {_stake(total)}. Paper trading: verify the price before betting.")
    return "\n".join(lines)


def results(session: Session, since_hours: int = 30) -> str | None:
    """The morning-after text: what settled, and the running record. None if nothing did."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    props = session.execute(
        select(PropBet).where(PropBet.settled_at >= cutoff).order_by(PropBet.pnl.desc())
    ).scalars().all()
    games = session.execute(select(Bet).where(Bet.settled_at >= cutoff)).scalars().all()
    if not props and not games:
        return None

    teams = _teams(session)
    yesterday = (datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)).date()
    lines = [f"NBA results, {yesterday:%a %b %-d}"]
    marks = {"won": "W", "lost": "L", "push": "P", "void": "VOID"}
    night = 0.0
    for b in props:
        night += float(b.pnl or 0)
        actual = f"{float(b.actual_value):g}" if b.actual_value is not None else "DNP"
        clv = f" | CLV {float(b.clv) * 100:+.1f}pts" if b.clv is not None else ""
        lines.append(
            f"{marks.get(b.result, '?')} {b.player_name} ({teams.get(b.team_id, '?')}) "
            f"{b.side} {float(b.line):g} {MARKET_LABELS.get(b.market, b.market)}: {actual} "
            f"({float(b.pnl or 0) * 100:+.2f}u{clv})"
        )
    for b in games:
        night += float(b.pnl or 0)
        clv = f" | CLV {float(b.clv) * 100:+.1f}pts" if b.clv is not None else ""
        lines.append(f"{'W' if b.won else 'L'} game ML {b.side} ({float(b.pnl or 0) * 100:+.2f}u{clv})")
    lines.append(f"\nNight: {night * 100:+.2f}u")

    rec = props_agent.track_record(session)
    if rec["n_bets"]:
        clv = f", avg CLV {rec['avg_clv'] * 100:+.1f}pts" if rec["avg_clv"] is not None else ""
        lines.append(f"Props to date: {rec['wins']}-{rec['losses']}-{rec['pushes']}, "
                     f"{rec['units']:+.1f}u, ROI {rec['roi']:+.1%}{clv}")
    game_rec = betting_agent.track_record(session)
    if game_rec["n_bets"]:
        clv = (f", avg CLV {game_rec['avg_clv'] * 100:+.1f}pts"
               if game_rec["avg_clv"] is not None else "")
        lines.append(f"Games to date: {game_rec['n_bets']} bets, ROI {game_rec['roi']:+.1%}{clv}")
    return "\n".join(lines)
