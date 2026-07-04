"""Client for The Odds API v4 (https://the-odds-api.com/).

Free tier is 500 requests/month; the odds endpoint costs 1 request per region
per market group, so we fetch h2h+spreads+totals for the `us` region in one call.
The `/sports` endpoint (used only to check season status) is free.

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
DEFAULT_MARKETS = ("h2h", "spreads", "totals")

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
    markets: tuple[str, ...] = DEFAULT_MARKETS,
    regions: str = "us",
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
            "regions": regions,
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
