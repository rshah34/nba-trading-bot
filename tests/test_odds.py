"""Odds parsing + team resolution tests. Network-free: uses a recorded payload."""

from datetime import date

from nba_bot.data import odds_api
from nba_bot.data.team_lookup import resolve_team_id

# Trimmed but structurally faithful sample of a The Odds API v4 event
# (GET /v4/sports/basketball_nba/odds?markets=h2h,spreads,totals).
SAMPLE_EVENT = {
    "id": "e1a2b3c4",
    "sport_key": "basketball_nba",
    "commence_time": "2026-11-04T02:30:00Z",  # 10:30pm ET on Nov 3
    "home_team": "Los Angeles Clippers",
    "away_team": "Boston Celtics",
    "bookmakers": [
        {
            "key": "draftkings",
            "title": "DraftKings",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Los Angeles Clippers", "price": -150},
                        {"name": "Boston Celtics", "price": 130},
                    ],
                },
                {
                    "key": "spreads",
                    "outcomes": [
                        {"name": "Los Angeles Clippers", "price": -110, "point": -3.5},
                        {"name": "Boston Celtics", "price": -110, "point": 3.5},
                    ],
                },
                {
                    "key": "totals",
                    "outcomes": [
                        {"name": "Over", "price": -105, "point": 224.5},
                        {"name": "Under", "price": -115, "point": 224.5},
                    ],
                },
            ],
        }
    ],
}


def test_parse_event_flattens_all_markets():
    ev = odds_api.parse_event(SAMPLE_EVENT)
    assert len(ev.books) == 1
    book = ev.books[0]
    assert book.sportsbook == "draftkings"
    assert book.home_moneyline == -150
    assert book.away_moneyline == 130
    assert book.spread_home == -3.5
    assert book.spread_home_price == -110
    assert book.spread_away_price == -110
    assert book.total_points == 224.5
    assert book.over_price == -105
    assert book.under_price == -115


def test_resolve_team_by_full_name():
    clippers = resolve_team_id("Los Angeles Clippers")
    celtics = resolve_team_id("Boston Celtics")
    assert clippers is not None and celtics is not None and clippers != celtics
    # matching is case-insensitive
    assert resolve_team_id("boston celtics") == celtics
    assert resolve_team_id("Nonexistent Team") is None
    assert resolve_team_id(None) is None


def test_match_game_id_tolerates_one_day_skew():
    from nba_bot.agents.data_agent import _match_game_id

    index = {(date(2026, 11, 3), 100, 200): "GAME_A"}
    # exact match
    assert _match_game_id(index, date(2026, 11, 3), 100, 200) == "GAME_A"
    # ET date landed a day early/late — still resolves via ±1 tolerance
    assert _match_game_id(index, date(2026, 11, 4), 100, 200) == "GAME_A"
    assert _match_game_id(index, date(2026, 11, 2), 100, 200) == "GAME_A"
    # wrong teams / far-off date -> no match
    assert _match_game_id(index, date(2026, 11, 3), 200, 100) is None
    assert _match_game_id(index, date(2026, 11, 10), 100, 200) is None


def test_parse_event_tolerates_missing_markets():
    ev = odds_api.parse_event({
        "id": "x",
        "commence_time": "2026-11-04T02:30:00Z",
        "home_team": "Boston Celtics",
        "away_team": "Miami Heat",
        "bookmakers": [{"key": "fanduel", "markets": []}],
    })
    book = ev.books[0]
    assert book.sportsbook == "fanduel"
    assert book.home_moneyline is None
    assert book.total_points is None
