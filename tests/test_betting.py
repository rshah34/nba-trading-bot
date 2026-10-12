"""Bet-decision math: odds conversion, Kelly, edge/side selection, CLV, sim."""

from nba_bot.betting import (
    american_to_decimal,
    decide_bet_decimal,
    kelly_fraction,
    simulate_paper_trade,
)


def test_american_to_decimal():
    assert american_to_decimal(100) == 2.0        # even money
    assert abs(american_to_decimal(-110) - (1 + 100 / 110)) < 1e-9
    assert american_to_decimal(200) == 3.0        # +200 -> 3.0


def test_kelly_fraction():
    # p=0.6 at even money (b=1): f = (0.6*2 - 1)/1 = 0.2
    assert round(kelly_fraction(0.6, 2.0), 4) == 0.2
    # no edge -> non-positive
    assert kelly_fraction(0.5, 2.0) == 0.0


def test_decide_bet_takes_the_edge_side_and_sizes_fractionally():
    # Market -110/-110 => fair ~50/50. Model loves home at 0.60 => ~10% edge.
    d = decide_bet_decimal(0.60, american_to_decimal(-110), american_to_decimal(-110), min_edge=0.04, kelly_multiplier=0.25, max_stake=0.05)
    assert d.side == "home"
    assert d.edge > 0.09
    assert 0 < d.stake_fraction <= 0.05      # capped
    assert d.expected_value > 0


def test_decide_bet_picks_away_when_model_fades_home():
    d = decide_bet_decimal(0.35, american_to_decimal(-110), american_to_decimal(-110))
    assert d.side == "away"
    assert d.bet_prob == 0.65                 # 1 - model home prob
    assert d.edge > 0.09


def test_decide_bet_no_bet_when_within_threshold():
    # Model agrees with the market -> no edge -> no bet.
    d = decide_bet_decimal(0.52, american_to_decimal(-110), american_to_decimal(-110), min_edge=0.04)
    assert d.side == "none"
    assert d.stake_fraction == 0.0


def test_decide_bet_respects_the_vig_via_devig():
    # Heavy favorite -300/+250: fair home ~0.74. Model at 0.75 is NOT a real edge.
    d = decide_bet_decimal(0.75, american_to_decimal(-300), american_to_decimal(250), min_edge=0.04)
    assert d.side == "none"


def test_game_bet_is_priced_at_your_own_books():
    even = american_to_decimal(-110)
    mine = {"home": ("kalshi", 1.95), "away": ("draftkings", 1.87)}
    d = decide_bet_decimal(0.60, even, even, prices=mine)
    assert (d.side, d.sportsbook, d.decimal_odds) == ("home", "kalshi", 1.95)
    # Playable to the price that leaves half a point under the 60% we size on.
    assert d.playable_odds == round(1 / 0.595, 3) < d.decimal_odds
    # No bet when your books don't offer the side, or only at a price with no room.
    assert decide_bet_decimal(0.60, even, even, prices={"away": ("draftkings", 1.87)}).side == "none"
    assert decide_bet_decimal(0.60, even, even, prices={"home": ("draftkings", 1.6)}).side == "none"


def test_simulate_paper_trade_tracks_bankroll_and_clv():
    bets = [
        {"stake_fraction": 0.04, "decimal_odds": 2.0, "won": True, "clv": 0.02},
        {"stake_fraction": 0.03, "decimal_odds": 1.9, "won": False, "clv": 0.01},
        {"stake_fraction": 0.05, "decimal_odds": 2.5, "won": True, "clv": -0.01},
    ]
    res = simulate_paper_trade(bets, starting_bankroll=1.0)
    assert res.n_bets == 3
    assert res.win_rate == round(2 / 3, 3)
    assert res.avg_clv == round((0.02 + 0.01 - 0.01) / 3, 4)
    assert res.clv_positive_rate == round(2 / 3, 3)


def test_simulate_paper_trade_empty():
    res = simulate_paper_trade([])
    assert res.n_bets == 0 and res.final_bankroll == 1.0


# --------------------------------------------------------------------------- #
# Player props.
# --------------------------------------------------------------------------- #
from nba_bot.betting import PropQuote, decide_prop, settle_prop  # noqa: E402


def _quotes(*lines_and_prices):
    return [PropQuote(book, line, american_to_decimal(over), american_to_decimal(under))
            for book, line, over, under in lines_and_prices]


def _flat(p_over):
    return lambda line: (p_over, 1 - p_over, 0.0)


def test_decide_prop_blends_toward_the_market_and_takes_the_best_price():
    quotes = _quotes(("dk", 24.5, -110, -110), ("fd", 24.5, -105, -115), ("mgm", 24.5, -112, -108))
    d = decide_prop(quotes, _flat(0.62))
    assert d.side == "over" and d.sportsbook == "fd"          # best over price
    assert d.n_books == 3
    assert d.market_prob < d.bet_prob < d.model_prob          # sized on the blend
    assert d.edge == round(d.bet_prob - d.market_prob, 4)
    assert 0 < d.stake_fraction <= 0.02


def test_decide_prop_bets_the_under_when_the_model_is_low():
    d = decide_prop(_quotes(("dk", 24.5, -110, -110), ("fd", 24.5, -110, -110)), _flat(0.36))
    assert d.side == "under"


def test_decide_prop_guards():
    two = _quotes(("dk", 24.5, -110, -110), ("fd", 24.5, -110, -110))
    assert decide_prop(two, _flat(0.53)) is None              # edge too small after blending
    assert decide_prop(two, _flat(0.95)) is None              # too good to be true
    assert decide_prop(two[:1], _flat(0.62)) is None          # one book is not a market
    # A book hanging a line far from ours isn't quoting the same bet.
    apart = _quotes(("dk", 24.5, -110, -110), ("fd", 27.5, -110, -110))
    assert decide_prop(apart, _flat(0.62)) is None


def _sloped(line):
    """A model where each point of line is worth four points of probability."""
    over = 0.5 - 0.04 * (line - 25.5)
    return over, 1 - over, 0.0


def test_decide_prop_bets_only_at_your_books_but_hears_every_book():
    quotes = _quotes(("dk", 24.5, -110, -110), ("fd", 24.5, 105, -125), ("mgm", 24.5, -110, -110))
    d = decide_prop(quotes, _flat(0.62), books=frozenset({"dk"}))
    assert d.sportsbook == "dk" and d.n_books == 3            # fd's better price isn't ours to take
    assert decide_prop(quotes, _flat(0.62), books=frozenset({"caesars"})) is None


def test_decide_prop_carries_a_nearby_line_to_ours():
    # The market is 25.5 at even money; our book hangs 24.5 at the same price. The
    # model agrees with the market exactly, so the only edge is the off-market line.
    quotes = _quotes(("dk", 24.5, -110, -110), ("fd", 25.5, -110, -110), ("mgm", 25.5, -110, -110))
    assert decide_prop(quotes, _sloped, books=frozenset({"dk"})) is None   # too small to bet alone
    d = decide_prop(quotes, _sloped, books=frozenset({"dk"}), min_edge=0.005)
    assert (d.side, d.line, d.n_books) == ("over", 24.5, 3)
    assert d.model_prob == 0.54                               # the model at 24.5
    assert d.market_prob == round((0.5 + 0.54 + 0.54) / 3, 4)  # the others, moved a point


def test_decide_prop_says_how_far_the_bet_stays_playable():
    quotes = _quotes(("dk", 24.5, 100, -120), ("fd", 24.5, -110, -110))
    d = decide_prop(quotes, lambda line: (0.66 - 0.04 * (line - 24.5), 0.34 + 0.04 * (line - 24.5), 0.0))
    assert d.side == "over" and d.decimal_odds == 2.0
    assert d.playable_odds == round(1 / (d.bet_prob - 0.005), 3)
    # Each half point costs two points of probability; even money stops paying
    # once the blend falls under 50.5%.
    assert d.playable_line == 24.5 + 0.5 * int((d.bet_prob - 0.505) / 0.02)


def test_decide_prop_judges_each_line_on_its_own_probability():
    quotes = _quotes(("dk", 24.5, -110, -110), ("fd", 24.5, -110, -110),
                     ("mgm", 26.5, -110, -110), ("czr", 26.5, -110, -110))
    d = decide_prop(quotes, lambda line: (0.60, 0.40, 0.0) if line < 25 else (0.42, 0.58, 0.0))
    assert (d.side, d.line) == ("over", 24.5)                 # the bigger disagreement


def test_settle_prop():
    assert settle_prop("over", 24.5, 27, 1.9, 0.01) == ("won", 0.009)
    assert settle_prop("under", 24.5, 27, 1.9, 0.01) == ("lost", -0.01)
    assert settle_prop("under", 5.0, 5, 1.9, 0.01) == ("push", 0.0)


# --------------------------------------------------------------------------- #
# Blended game bets + the daily picks.
# --------------------------------------------------------------------------- #
from types import SimpleNamespace  # noqa: E402

from nba_bot import picks  # noqa: E402


def test_game_bet_is_sized_on_the_model_market_blend():
    even = american_to_decimal(-110)
    raw = decide_bet_decimal(0.62, even, even, min_edge=0.03)
    blended = decide_bet_decimal(0.62, even, even, market_weight=0.5, min_edge=0.03)
    assert blended.model_prob == 0.62 and blended.bet_prob == 0.56
    assert blended.edge == round(raw.edge / 2, 4)          # half the raw disagreement
    assert blended.stake_fraction < raw.stake_fraction
    # A disagreement that only clears the bar unblended is no bet once blended.
    assert decide_bet_decimal(0.55, even, even, market_weight=0.5, min_edge=0.03).side == "none"


def _cand(kind, edge, game="g1", stake=0.02):
    return {"kind": kind, "edge": edge, "game_id": game,
            "decision": SimpleNamespace(stake_fraction=stake)}


def test_picks_take_the_best_of_each_kind_then_the_biggest_edges():
    games = [_cand("game", 0.04, "g1"), _cand("game", 0.035, "g2")]
    props = [_cand("prop", 0.09, "g3"), _cand("prop", 0.08, "g4"), _cand("prop", 0.07, "g5")]
    chosen = picks.choose(games, props, 3)
    assert [(c["kind"], c["edge"]) for c in chosen] == [("prop", 0.09), ("prop", 0.08), ("game", 0.04)]
    # Props alone would fill the card on edge; the game pick is there for the mix.
    assert [c["kind"] for c in picks.choose(games, props, 2)] == ["prop", "game"]


def test_picks_with_one_kind_one_slot_or_none():
    props = [_cand("prop", 0.09, "g3"), _cand("prop", 0.08, "g4")]
    games = [_cand("game", 0.05, "g1")]
    assert len(picks.choose([], props, 3)) == 2                     # fewer candidates than slots
    assert picks.choose(games, props, 1)[0]["edge"] == 0.09         # one slot: simply the best
    assert picks.choose(games, props, 0) == []                      # day's quota already used


def test_picks_cap_the_stake_on_any_one_game():
    chosen = picks.choose([_cand("game", 0.05, "g1", stake=0.03)],
                          [_cand("prop", 0.09, "g1", stake=0.04)], 3)
    assert [c["decision"].stake_fraction for c in chosen] == [0.04, 0.01]   # 5% on the game in all


def test_game_bet_skips_an_edge_too_large_to_trust():
    d = decide_bet_decimal(0.54, american_to_decimal(457), american_to_decimal(-650),
                           market_weight=0.75, min_edge=0.025, max_edge=0.0625)
    assert d.side == "none" and "too large" in d.reason
