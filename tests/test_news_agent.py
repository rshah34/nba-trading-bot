"""News signals -> what the projection should do. DB/network-free."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from nba_bot.agents import news_agent
from nba_bot.agents.news_agent import player_view


_BASE = datetime(2026, 10, 20, tzinfo=timezone.utc)


def _sig(kind, minutes=1.0, usage=1.0, conf=0.9, summary="reported", hours_ago=None,
         player="Star Player", article=None):
    """A stand-in news signal. With no `hours_ago`, each call is older than the last,
    so a list written newest-first stays newest-first."""
    _sig.n += 1
    age = hours_ago if hours_ago is not None else _sig.n
    return SimpleNamespace(kind=kind, minutes_mult=minutes, usage_mult=usage, confidence=conf,
                           summary=summary, player_id=None, player_name=player,
                           article_id=article if article is not None else _sig.n,
                           published_at=_BASE - timedelta(hours=age))


_sig.n = 0


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


def test_a_weak_newer_report_does_not_overturn_a_firm_older_one():
    firm_old = _sig("out", minutes=0, conf=0.95, summary="team: out two weeks", hours_ago=30)
    weak_new = _sig("questionable", conf=0.5, summary="blog: might return", hours_ago=2)
    view = player_view([weak_new, firm_old])
    assert view.out and view.reason == "team: out two weeks"


def test_a_comparably_firm_newer_report_does_update_the_picture():
    old = _sig("out", minutes=0, conf=0.9, summary="out tonight", hours_ago=30)
    new = _sig("returning", minutes=0.8, conf=0.85, summary="cleared, on a minutes limit", hours_ago=2)
    view = player_view([old, new])
    assert not view.out and not view.uncertain
    assert view.adjustment.minutes_mult < 1          # the limit is applied


def test_reports_are_ranked_by_firmness_and_how_widely_they_are_reported():
    thin_new = _sig("role_up", conf=0.6, player="Bench Guy", hours_ago=1)
    wide_old = [_sig("out", minutes=0, conf=0.85, player="Star Player", hours_ago=h, article=a)
                for h, a in ((40, 101), (38, 102), (36, 103))]
    reports = news_agent.consolidate([thin_new, *wide_old])
    assert [r.signal.player_name for r in reports] == ["Star Player", "Bench Guy"]
    assert reports[0].sources == 3 and reports[1].sources == 1
    # Speculation never makes the list.
    assert news_agent.consolidate([_sig("out", conf=0.3)]) == []


def _story(*reports):
    """Signals newest first, from (kind, hours_ago, restates)."""
    out = []
    for i, (kind, hours, restates) in enumerate(reports):
        sig = _sig(kind, minutes=0 if kind == "out" else 1.0, conf=0.85, hours_ago=hours,
                   article=500 + i)
        sig.restates = restates
        out.append(sig)
    return out


def _since(*reports):
    report = news_agent.consolidate(_story(*reports))[0]
    return (_BASE - report.since).total_seconds() / 3600   # hours ago


def test_restating_a_known_injury_does_not_make_it_news_again():
    # Out for the season 400 hours ago, mentioned in passing ever since.
    assert _since(("out", 24, True), ("out", 96, True), ("out", 200, True),
                  ("out", 400, False)) == 400


def test_a_change_of_status_is_news():
    # Day-to-day three days ago, ruled out today.
    assert _since(("out", 5, False), ("questionable", 72, False)) == 5
    # Even when the article ruling him out is mostly a restatement.
    assert _since(("out", 5, True), ("questionable", 72, False)) == 5
    # Out, back, out again: the re-injury is the story.
    assert _since(("out", 3, False), ("returning", 50, False), ("out", 120, False)) == 3


def test_a_new_ruling_or_setback_with_the_same_status_is_news():
    # Out Friday, then ruled out again for Sunday's game.
    assert _since(("out", 4, False), ("out", 50, False)) == 4
    # Out two weeks, a restatement, then a setback.
    assert _since(("out", 6, False), ("out", 100, True), ("out", 300, False)) == 6


def test_players_are_matched_by_full_name_roster_name_or_a_surname_unique_on_his_team():
    players = news_agent.Players(
        by_name={"jusuf nurkic": {(1, 10), (1, 20)}, "santi aldama": {(2, 30)},
                 "jayden quaintance": {(3, 40)},                       # roster only: a rookie
                 "jalen williams": {(4, 50)}, "jaylin williams": {(5, 50)}},
        official={1: "Jusuf Nurkić", 2: "Santi Aldama", 3: "Jayden Quaintance"})
    assert news_agent.resolve_player(players, "Jayden Quaintance", 40) == 3
    assert news_agent.resolve_player(players, "Nurkić", 20) == 1      # surname, unique on team
    assert news_agent.resolve_player(players, "Aldama", None) is None  # no team to check against
    assert news_agent.resolve_player(players, "Williams", 50) is None  # two on the team
