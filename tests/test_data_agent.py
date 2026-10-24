"""Data-agent tests: team alignment map and the roster sync. DB/network-free."""

from collections import Counter

import pandas as pd
import pytest
from nba_api.stats.static import teams as static_teams

from nba_bot.agents import data_agent
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


class _FakeSession:
    """Answers the known-teams query; records what would be written."""

    def __init__(self, teams):
        self.teams, self.deleted, self.added, self.committed = teams, False, [], False

    def execute(self, stmt):
        if stmt.is_delete:
            self.deleted = True
            return None
        return type("R", (), {"scalars": lambda _self: iter(self.teams)})()

    def add_all(self, rows):
        self.added = list(rows)

    def commit(self):
        self.committed = True


def _rosters(monkeypatch, rows):
    df = pd.DataFrame([(pid, tid, f"Player {pid}") for pid, tid in rows],
                      columns=["player_id", "team_id", "player_name"])
    monkeypatch.setattr(data_agent.nba_stats, "get_rosters", lambda season: df)


def test_sync_rosters_replaces_the_list(monkeypatch):
    _rosters(monkeypatch, [(10, 1), (11, 2), (12, 99)])   # 99: not an NBA team
    session = _FakeSession({1, 2})
    assert data_agent.sync_rosters(session) == 2
    assert session.deleted and session.committed
    assert {(r.player_id, r.team_id) for r in session.added} == {(10, 1), (11, 2)}
    assert {r.player_name for r in session.added} == {"Player 10", "Player 11"}


def test_sync_rosters_refuses_a_partial_list(monkeypatch):
    _rosters(monkeypatch, [(10, 1)])
    session = _FakeSession({1, 2})
    with pytest.raises(ValueError, match="1 of 2 teams"):
        data_agent.sync_rosters(session)
    assert not session.deleted          # yesterday's rosters stay
