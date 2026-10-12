"""Client for The Odds API v4 (https://the-odds-api.com/).

Free tier is 500 credits/month. The odds endpoint costs one credit per market per
region, and a named list of up to ten bookmakers is billed as one region — which
is how the books we want (some in `us`, some in `us2`, the exchanges in `us_ex`)
come back for the price of one.

`parse_event` is deliberately network-free so odds parsing can be unit-tested
against a recorded payload without spending quota.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "basketball_nba"

_retry = retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))


@dataclass
class BookOdds:
    """One sportsbook's line for a single game, flattened to our `odds` columns."""

    sportsbook: str
    home_moneyline: int | None = None
    away_moneyline: int | None = None
    spread_home: float | None = None
    spread_home_price: int | None = None
    spread_away_price: int | None = None
    total_points: float | None = None
    over_price: int | None = None
    under_price: int | None = None


@dataclass
class OddsEvent:
    commence_time: datetime
    home_team: str
    away_team: str
    books: list[BookOdds]


@_retry
def fetch_nba_odds(
    api_key: str,
    markets: tuple[str, ...],
    bookmakers: str,
    odds_format: str = "american",
) -> tuple[list[dict], dict[str, str | None]]:
    """Fetch current NBA odds. Returns (raw_events, quota_headers).

    Raw events are passed to `parse_event`. quota_headers surfaces
    x-requests-remaining / x-requests-used so callers can log usage.
    """
    resp = httpx.get(
        f"{ODDS_API_BASE}/sports/{SPORT_KEY}/odds",
        params={
            "apiKey": api_key,
            "bookmakers": bookmakers,
            "markets": ",".join(markets),
            "oddsFormat": odds_format,
            "dateFormat": "iso",
        },
        timeout=20,
    )
    resp.raise_for_status()
    quota = {
        "remaining": resp.headers.get("x-requests-remaining"),
        "used": resp.headers.get("x-requests-used"),
    }
    return resp.json(), quota


def _outcome(outcomes: list[dict], name: str) -> dict | None:
    for o in outcomes:
        if o.get("name") == name:
            return o
    return None


def parse_event(event: dict) -> OddsEvent:
    """Flatten one Odds API event into an OddsEvent with per-book lines. No network."""
    home = event["home_team"]
    away = event["away_team"]
    books: list[BookOdds] = []

    for bm in event.get("bookmakers", []):
        line = BookOdds(sportsbook=bm.get("key", "unknown"))
        for market in bm.get("markets", []):
            outcomes = market.get("outcomes", [])
            key = market.get("key")
            if key == "h2h":
                h, a = _outcome(outcomes, home), _outcome(outcomes, away)
                line.home_moneyline = h["price"] if h else None
                line.away_moneyline = a["price"] if a else None
            elif key == "spreads":
                h, a = _outcome(outcomes, home), _outcome(outcomes, away)
                if h:
                    line.spread_home = h.get("point")
                    line.spread_home_price = h.get("price")
                if a:
                    line.spread_away_price = a.get("price")
            elif key == "totals":
                over, under = _outcome(outcomes, "Over"), _outcome(outcomes, "Under")
                if over:
                    line.total_points = over.get("point")
                    line.over_price = over.get("price")
                if under:
                    line.under_price = under.get("price")
        books.append(line)

    return OddsEvent(
        commence_time=datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00")),
        home_team=home,
        away_team=away,
        books=books,
    )


# --------------------------------------------------------------------------- #
# Player props. Not in the bulk feed — one request per event, and each request
# costs one credit per market returned. A full 12-market slate is ~12 credits a
# game, so the free tier's 500/month covers one market a night.
# --------------------------------------------------------------------------- #
@dataclass
class PropOffer:
    """One book's over/under on one player line."""

    sportsbook: str
    market: str
    player: str
    line: float
    over_price: int | None = None
    under_price: int | None = None


@_retry
def fetch_events(api_key: str) -> tuple[list[dict], dict[str, str | None]]:
    """Upcoming NBA events (id, teams, commence_time) and the current quota.

    Free — no quota cost — which makes it the way to read how many credits are
    left before deciding what to buy.
    """
    resp = httpx.get(
        f"{ODDS_API_BASE}/sports/{SPORT_KEY}/events",
        params={"apiKey": api_key, "dateFormat": "iso"},
        timeout=20,
    )
    resp.raise_for_status()
    quota = {
        "remaining": resp.headers.get("x-requests-remaining"),
        "used": resp.headers.get("x-requests-used"),
    }
    return resp.json(), quota


@_retry
def fetch_event_props(
    api_key: str, event_id: str, markets: tuple[str, ...], bookmakers: str
) -> tuple[dict, dict[str, str | None]]:
    """Player-prop odds for one event. Returns (raw_event, quota_headers)."""
    resp = httpx.get(
        f"{ODDS_API_BASE}/sports/{SPORT_KEY}/events/{event_id}/odds",
        params={
            "apiKey": api_key,
            "bookmakers": bookmakers,
            "markets": ",".join(markets),
            "oddsFormat": "american",
            "dateFormat": "iso",
        },
        timeout=20,
    )
    resp.raise_for_status()
    quota = {
        "remaining": resp.headers.get("x-requests-remaining"),
        "used": resp.headers.get("x-requests-used"),
        "last": resp.headers.get("x-requests-last"),
    }
    return resp.json(), quota


def parse_event_props(event: dict) -> list[PropOffer]:
    """Flatten one event's prop markets into per-book over/under offers. No network.

    Each outcome is {name: Over|Under, description: <player>, price, point}; the two
    sides of a line are paired on (player, point).
    """
    offers: dict[tuple[str, str, str, float], PropOffer] = {}
    for bm in event.get("bookmakers", []):
        book = bm.get("key", "unknown")
        for market in bm.get("markets", []):
            key = market.get("key")
            for o in market.get("outcomes", []):
                player, point, side = o.get("description"), o.get("point"), o.get("name")
                if not player or point is None or side not in ("Over", "Under"):
                    continue
                offer = offers.setdefault(
                    (book, key, player, float(point)),
                    PropOffer(sportsbook=book, market=key, player=player, line=float(point)),
                )
                if side == "Over":
                    offer.over_price = o.get("price")
                else:
                    offer.under_price = o.get("price")
    return list(offers.values())
