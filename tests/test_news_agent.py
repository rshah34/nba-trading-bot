"""News signals -> what the projection should do. DB/network-free."""

from types import SimpleNamespace

from nba_bot.agents import news_agent
from nba_bot.agents.news_agent import player_view


def _sig(kind, minutes=1.0, usage=1.0, conf=0.9, summary="reported"):
    return SimpleNamespace(kind=kind, minutes_mult=minutes, usage_mult=usage,
                           confidence=conf, summary=summary)


def test_no_signals_means_no_change():
    view = player_view([])
    assert not view.out and not view.uncertain and view.adjustment is None


def test_confident_out_rules_him_out_and_speculation_is_ignored():
    assert player_view([_sig("out", minutes=0)]).out
    assert not player_view([_sig("out", minutes=0, conf=0.3)]).out


def test_a_shaky_out_blocks_his_bets_but_keeps_him_in_the_rotation():
    view = player_view([_sig("out", minutes=0, conf=0.6, summary="left the game early")])
    assert not view.out and view.uncertain and view.adjustment is None


def test_only_articles_that_can_matter_today_are_read():
    slate = {1, 2}
    art = lambda teams, n: SimpleNamespace(team_ids=teams, raw_text="x" * n)  # noqa: E731
    assert news_agent.worth_reading(art([2, 9], 5000), slate)        # a team that plays
    assert not news_agent.worth_reading(art([9], 300), slate)        # only teams that are off
    assert news_agent.worth_reading(art(None, 200), slate)           # untagged player blurb
    assert not news_agent.worth_reading(art(None, 5000), slate)      # untagged long read
    assert news_agent.worth_reading(art([9], 5000), None)            # no slate filter


def test_questionable_blocks_a_bet_without_touching_the_projection():
    view = player_view([_sig("questionable", summary="game-time decision")])
    assert view.uncertain and view.reason == "game-time decision" and view.adjustment is None


def test_multiplier_is_believed_in_proportion_to_confidence():
    view = player_view([_sig("minutes_limit", minutes=0.7, conf=0.5)])
    assert view.adjustment.minutes_mult == 0.85          # half of a 30% cut
    assert view.adjustment.mult("pts") == 0.85


def test_newest_signal_of_a_kind_wins_and_stacking_is_clipped():
    # Signals arrive newest first: the later "bigger role" report supersedes the older one.
    view = player_view([_sig("role_up", minutes=1.1, conf=1.0), _sig("role_up", minutes=1.4, conf=1.0)])
    assert round(view.adjustment.minutes_mult, 2) == 1.1
    stacked = player_view([_sig("role_up", minutes=1.4, conf=1.0), _sig("returning", minutes=1.4, conf=1.0)])
    assert stacked.adjustment.minutes_mult == 1.25


def test_batches_respect_the_size_budget_without_splitting_or_dropping_articles():
    articles = [SimpleNamespace(title="t", raw_text="x" * 15_000) for _ in range(5)]
    batches = news_agent._batches(articles)
    assert sum(len(b) for b in batches) == 5
    assert all(sum(len(a.raw_text) for a in b) <= news_agent._BATCH_CHARS for b in batches)
    # One oversized article still gets its own batch rather than being truncated.
    assert news_agent._batches([SimpleNamespace(title="t", raw_text="x" * 90_000)])[0]
