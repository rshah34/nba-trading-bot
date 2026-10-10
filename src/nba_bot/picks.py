"""The day's picks: the few bets, across game winners and player props, that the
bot is most confident in at current prices.

Both kinds of bet are measured the same way — the probability we'd size on (the
model blended halfway toward the market) minus the market's own de-vigged
probability — so their edges are comparable and one ranking covers both. Only the
picks are recorded, so the paper record is exactly what was texted.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nba_bot.agents import betting_agent, props_agent
from nba_bot.config import settings
from nba_bot.db.models import Bet, Game, PropBet
from nba_bot.features.props import PropsData

# No single game carries more than this share of bankroll across its picks.
_MAX_GAME_STAKE = 0.05
# The early run (afternoon tips only) may use this many of the day's picks, so the
# evening slate — most of the games — isn't left with none.
_EARLY_RUN_PICKS = 1


def choose(games: list[dict], props: list[dict], slots: int) -> list[dict]:
    """Pick up to `slots` bets from the two candidate lists (each best-first).

    When both kinds are on offer and there is room for two, the best of each is in:
    the day's card is meant to be a mix, and the two models fail in different ways.
    The remaining places go to the largest edges, whichever kind they are.
    """
    if slots <= 0:
        return []
    picked: list[dict] = []
    rest = list(games) + list(props)
    if slots >= 2 and games and props:
        picked = [games[0], props[0]]
        rest = games[1:] + props[1:]
    rest.sort(key=lambda c: c["edge"], reverse=True)
    picked += rest[: slots - len(picked)]
    picked.sort(key=lambda c: c["edge"], reverse=True)

    staked: dict[str, float] = defaultdict(float)
    out = []
    for c in picked:
        d = c["decision"]
        room = _MAX_GAME_STAKE - staked[c["game_id"]]
        if room <= 0.001:
            continue
        d.stake_fraction = round(min(d.stake_fraction, room), 4)
        staked[c["game_id"]] += d.stake_fraction
        out.append(c)
    return out


def picks_made(session: Session, game_date: date) -> int:
    """How many bets are already on the books for a date."""
    n_games = session.execute(
        select(func.count()).select_from(Bet).join(Game, Bet.game_id == Game.game_id)
        .where(Game.game_date == game_date)
    ).scalar_one()
    n_props = session.execute(
        select(func.count()).select_from(PropBet).join(Game, PropBet.game_id == Game.game_id)
        .where(Game.game_date == game_date)
    ).scalar_one()
    return n_games + n_props


def make_picks(
    session: Session,
    game_date: date,
    *,
    data: PropsData | None = None,
    as_of: datetime | None = None,
    window_hours: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Price everything bettable, choose the day's picks, and record them.

    `MAX_DAILY_BETS` caps the whole day however many times this runs. `dry_run`
    chooses but records nothing.
    """
    as_of = as_of or datetime.now(timezone.utc)
    games = betting_agent.game_candidates(session, game_date, as_of=as_of, window_hours=window_hours)
    props = props_agent.price_props(session, game_date, data=data, as_of=as_of,
                                    window_hours=window_hours, log_lines=not dry_run)
    slots = settings.max_daily_bets - picks_made(session, game_date)
    if window_hours is not None:
        slots = min(slots, _EARLY_RUN_PICKS)
    chosen = choose(games, props, slots)

    placed = 0
    if not dry_run:
        placed += betting_agent.record_game_bets(
            session, [c for c in chosen if c["kind"] == "game"], as_of)
        placed += props_agent.record_prop_bets(
            session, [c for c in chosen if c["kind"] == "prop"], as_of)
    return {"date": str(game_date), "game_candidates": len(games), "prop_candidates": len(props),
            "slots": max(slots, 0), "placed": placed, "picks": chosen}
