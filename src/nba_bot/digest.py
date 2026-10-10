"""Build the text messages: today's best bets, and last night's results."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nba_bot.agents import betting_agent, props_agent
from nba_bot.config import settings
from nba_bot.db.models import Bet, Game, PropBet, Team
from nba_bot.features.props import GUIDE_STATS, MARKET_LABELS

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


def pick_label(candidate: dict) -> str:
    """One-line description of a pick, for run logs."""
    d = candidate["decision"]
    if candidate["kind"] == "game":
        return f"game {candidate['game_id']} {d.side} ML"
    return (f"{candidate['player_name']} {d.side} {d.line:g} "
            f"{MARKET_LABELS.get(candidate['market'], candidate['market'])}")


def best_bets(session: Session, game_date: date) -> str:
    """The pre-game text: the day's picks — game winners and props together — with
    the biggest edge first."""
    teams = _teams(session)
    props = props_agent.todays_bets(session, game_date)
    game_bets = session.execute(
        select(Bet, Game).join(Game, Bet.game_id == Game.game_id)
        .where(Game.game_date == game_date)
    ).all()
    tips = dict(session.execute(
        select(Game.game_id, Game.tipoff_at).where(Game.game_date == game_date)
    ).all())

    lines = [f"NBA best bets, {game_date:%a %b %-d}"]
    if not props and not game_bets:
        lines.append("No bets today: nothing cleared the edge threshold.")
        return "\n".join(lines)

    def tail(model: float, market: float, edge: float, stake: float, game_id: str) -> str:
        when = _local_time(tips.get(game_id))
        return (f"model {model:.0%}, mkt {market:.0%}, edge {edge:+.1%} | {_stake(stake)}"
                + (f" | tip {when}" if when else ""))

    entries: list[tuple[float, list[str]]] = []
    for b in props:
        book = _BOOKS.get(b.sportsbook, b.sportsbook)
        block = [
            f"{b.player_name} ({teams.get(b.team_id, '?')}) {b.side.upper()} {float(b.line):g} "
            f"{MARKET_LABELS.get(b.market, b.market)} {_american(float(b.decimal_odds))} {book}",
            f"   proj {float(b.proj_mean):.1f} in {float(b.proj_minutes or 0):.0f}m | "
            + tail(float(b.model_prob), float(b.market_prob), float(b.edge),
                   float(b.stake_fraction), b.game_id),
        ]
        if b.notes:
            block.append(f"   {b.notes}")
        if b.stat_guide:
            def bound(v) -> str:
                return f"{v:g}" if v is not None else "–"

            block.append("   single stats (over if the line is at or below / under if at or above):")
            # In a fixed order: a JSON column doesn't keep the order it was written in.
            block.append("   " + " · ".join(
                f"{stat.upper()} {bound(b.stat_guide[stat]['over_at'])} / "
                f"{bound(b.stat_guide[stat]['under_at'])}"
                for stat, _market in GUIDE_STATS if stat in b.stat_guide))
        entries.append((float(b.edge), block))
    for bet, game in game_bets:
        side, other = ((game.home_team_id, game.away_team_id) if bet.side == "home"
                       else (game.away_team_id, game.home_team_id))
        where = "vs" if bet.side == "home" else "at"
        entries.append((float(bet.edge), [
            f"{teams.get(side, '?')} to win {where} {teams.get(other, '?')} "
            f"{_american(float(bet.decimal_odds))} (avg price, shop for better)",
            "   " + tail(float(bet.model_prob), float(bet.market_prob), float(bet.edge),
                         float(bet.stake_fraction), bet.game_id),
        ]))

    entries.sort(key=lambda e: e[0], reverse=True)
    for i, (_edge, block) in enumerate(entries, 1):
        lines.append("")
        lines.append(f"{i}. {block[0]}")
        lines.extend(block[1:])

    total = sum(float(b.stake_fraction) for b in props) + sum(
        float(b.stake_fraction) for b, _ in game_bets)
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
    # Dated by the games themselves: settlement can run a day or more late.
    game_ids = {b.game_id for b in props} | {b.game_id for b in games}
    played = session.execute(
        select(func.max(Game.game_date)).where(Game.game_id.in_(game_ids))
    ).scalar_one()
    lines = [f"NBA results, {played:%a %b %-d}"]
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
    matchups = {g.game_id: g for g in session.execute(
        select(Game).where(Game.game_id.in_(game_ids))).scalars()}
    for b in games:
        night += float(b.pnl or 0)
        g = matchups[b.game_id]
        side, other = ((g.home_team_id, g.away_team_id) if b.side == "home"
                       else (g.away_team_id, g.home_team_id))
        clv = f" | CLV {float(b.clv) * 100:+.1f}pts" if b.clv is not None else ""
        lines.append(
            f"{'W' if b.won else 'L'} {teams.get(side, '?')} to win "
            f"{'vs' if b.side == 'home' else 'at'} {teams.get(other, '?')} "
            f"({float(b.pnl or 0) * 100:+.2f}u{clv})"
        )
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
