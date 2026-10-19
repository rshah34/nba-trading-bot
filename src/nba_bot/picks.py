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

from nba_bot import betting
from nba_bot.agents import analysis_agent, betting_agent, props_agent
from nba_bot.config import settings
from nba_bot.db.models import Bet, Game, PropBet, Team
from nba_bot.features.props import MARKET_LABELS, PropsData, load_props_data

# No single game carries more than this share of bankroll across its picks.
_MAX_GAME_STAKE = 0.05
# A player with this many minutes a night changing status is worth a heads-up on a
# game pick.
_KEY_MINUTES = 24.0


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
    dry_run: bool = False,
) -> dict:
    """Price everything bettable, choose the day's picks, and record them.

    `MAX_DAILY_BETS` caps the whole day however many times this runs. `dry_run`
    chooses but records nothing.
    """
    as_of = as_of or datetime.now(timezone.utc)
    games = betting_agent.game_candidates(session, game_date, as_of=as_of)
    props = props_agent.price_props(session, game_date, data=data, as_of=as_of,
                                    log_lines=not dry_run)
    slots = settings.max_daily_bets - picks_made(session, game_date)
    chosen = choose(games, props, slots)

    placed = 0
    if not dry_run:
        placed += betting_agent.record_game_bets(
            session, [c for c in chosen if c["kind"] == "game"], as_of)
        placed += props_agent.record_prop_bets(
            session, [c for c in chosen if c["kind"] == "prop"], as_of)
    return {"date": str(game_date), "game_candidates": len(games), "prop_candidates": len(props),
            "slots": max(slots, 0), "placed": placed, "picks": chosen}


# --------------------------------------------------------------------------- #
# Late-scratch guard.
# --------------------------------------------------------------------------- #
def _status_changes(session: Session, team_id: int, game_date: date, since: datetime,
                    as_of: datetime) -> tuple[list[str], list[str]]:
    """(players ruled out, players no longer out) of a game between two moments."""
    def out(at: datetime) -> set[str]:
        return {i["player"]
                for i in analysis_agent.current_injuries(session, team_id, at, game_date)
                if (i["status"] or "").lower() == "out"}

    before, after = out(since), out(as_of)
    return sorted(after - before), sorted(before - after)


def recheck(
    session: Session, game_date: date, *, data: PropsData | None = None,
    as_of: datetime | None = None,
) -> list[str]:
    """Re-read today's open picks against the newest rosters and injury report and
    return an update line for each one the news has undercut. Each pick is flagged
    once.

    A prop is re-projected with the rotation as it now stands: it is a PASS if the
    player has left the team, is out or in doubt, or if the bet is no longer worth
    its price. A game pick gets a HEADS UP when a regular on the team we backed is
    newly out, or one is back for the other side — the game model isn't re-run, so
    that is news, not a verdict. Stale availability was the one input that tested
    worse than none.
    """
    as_of = as_of or datetime.now(timezone.utc)
    data = data or load_props_data(session)
    slate = props_agent.slate_for(session, data, game_date, as_of)
    abbr = {t.team_id: t.abbreviation for t in session.execute(select(Team)).scalars()}
    live = (Game.game_date == game_date, Game.status == "scheduled")
    updates: list[str] = []

    def tipped(game: Game) -> bool:
        return game.tipoff_at is not None and game.tipoff_at <= as_of

    def changes(team_id: int, since: datetime) -> list[str]:
        gone, back = _status_changes(session, team_id, game_date, since, as_of)
        return [f"{p} now out" for p in gone] + [f"{p} no longer out" for p in back]

    views: dict[tuple[str, int], props_agent.TeamView] = {}
    for bet, game in session.execute(
        select(PropBet, Game).join(Game, PropBet.game_id == Game.game_id)
        .where(*live, PropBet.settled_at.is_(None), PropBet.flag.is_(None))
    ).all():
        if tipped(game) or bet.team_id is None:
            continue
        home = bet.team_id == game.home_team_id
        opp = game.away_team_id if home else game.home_team_id
        view = views.get((game.game_id, bet.team_id))
        if view is None:
            view = views[(game.game_id, bet.team_id)] = props_agent.team_view(
                session, data, slate, bet.team_id, opp, home)

        if data.current_team.get(bet.player_id) != bet.team_id:
            why = "he is no longer on the team"
        elif bet.player_id in view.out:
            why = "he is now out, so the bet will void"
        elif bet.player_id in view.blocked:
            why = f"he is now {view.blocked[bet.player_id]}"
        else:
            proj = view.projections.get(bet.player_id)
            if proj is None:
                continue
            over, under, push = proj.probs(bet.market, float(bet.line))
            if push >= 1:
                continue
            model = (over if bet.side == "over" else under) / (1 - push)
            w = betting.PROP_MARKET_WEIGHT
            if w * float(bet.market_prob) + (1 - w) * model >= 1 / float(bet.decimal_odds):
                continue   # still worth the price it was taken at
            moved = changes(bet.team_id, bet.decided_at)
            why = "; ".join([*moved, f"the model now has it {model:.0%}, "
                                     f"down from {float(bet.model_prob):.0%}"])
        bet.flag = why
        updates.append(f"PASS: {bet.player_name} {bet.side} {float(bet.line):g} "
                       f"{MARKET_LABELS.get(bet.market, bet.market)}. {why[0].upper()}{why[1:]}.")

    def regular(name: str, team_id: int) -> bool:
        pid = props_agent._resolve_player(data, name, {team_id})
        recent = data.log_before(pid, game_date)[-5:] if pid is not None else []
        return bool(recent) and sum(g.minutes for g in recent) / len(recent) >= _KEY_MINUTES

    for bet, game in session.execute(
        select(Bet, Game).join(Game, Bet.game_id == Game.game_id)
        .where(*live, Bet.settled_at.is_(None), Bet.flag.is_(None))
    ).all():
        if tipped(game):
            continue
        ours, theirs = ((game.home_team_id, game.away_team_id) if bet.side == "home"
                        else (game.away_team_id, game.home_team_id))
        gone, _ = _status_changes(session, ours, game_date, bet.decided_at, as_of)
        _, back = _status_changes(session, theirs, game_date, bet.decided_at, as_of)
        news = ([f"{p} is now out" for p in gone if regular(p, ours)]
                + [f"{p} is back for {abbr.get(theirs, '?')}" for p in back if regular(p, theirs)])
        if not news:
            continue
        bet.flag = "; ".join(news)
        updates.append(f"HEADS UP: {abbr.get(ours, '?')} to win "
                       f"{'vs' if bet.side == 'home' else 'at'} {abbr.get(theirs, '?')}. "
                       f"{bet.flag[0].upper()}{bet.flag[1:]}.")
    session.commit()
    return updates
