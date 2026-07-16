"""Data-agent tests: team conference/division alignment map. DB/network-free."""

from collections import Counter

from nba_api.stats.static import teams as static_teams

from nba_bot.agents.data_agent import _TEAM_ALIGNMENT


def test_alignment_covers_every_nba_team_by_abbreviation():
    # Every team nba_api reports must have an alignment (and no stale extras), or
    # sync_teams would silently write NULL conference/division again.
    abbrs = {t["abbreviation"] for t in static_teams.get_teams()}
    assert abbrs == set(_TEAM_ALIGNMENT), abbrs.symmetric_difference(_TEAM_ALIGNMENT)


def test_alignment_has_two_balanced_conferences():
    conf = Counter(c for c, _ in _TEAM_ALIGNMENT.values())
    assert conf == {"East": 15, "West": 15}


def test_alignment_has_six_divisions_of_five():
    div = Counter(d for _, d in _TEAM_ALIGNMENT.values())
    assert len(div) == 6
    assert set(div.values()) == {5}
