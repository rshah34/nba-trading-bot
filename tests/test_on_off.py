"""On-off name matching, shrinkage, and prompt rendering. DB/network-free."""

from nba_bot.agents.analysis_agent import _onoff_section
from nba_bot.features.on_off import _avg_opp_quality, normalize_name


def _impact(**kw) -> dict:
    base = {
        "player": "Star Player", "regime": "newly_out", "mpg": 34.2, "games_with": 40,
        "games_without": 8, "net_rtg_with": 3.2, "net_rtg_without": -5.1,
        "delta_shrunk": 6.4, "opp_quality_with": 0.5, "opp_quality_without": 1.2,
    }
    return {**base, **kw}


def test_normalize_name_folds_accents_suffixes_punctuation():
    assert normalize_name("Luka Dončić") == "luka doncic"
    assert normalize_name("Jaren Jackson Jr.") == "jaren jackson"
    assert normalize_name("R.J. Barrett") == "rj barrett"
    # ESPN vs box-score spellings must land on the same key.
    assert normalize_name("Nikola Jokić") == normalize_name("Nikola Jokic")


def test_avg_opp_quality_ignores_unknown_teams():
    assert _avg_opp_quality([1, 2], {1: 2.0, 2: 4.0}) == 3.0
    assert _avg_opp_quality([1, 99], {1: 2.0}) == 2.0  # 99 unknown -> skipped
    assert _avg_opp_quality([99], {1: 2.0}) is None


def test_onoff_section_states_regime_so_form_is_not_double_counted():
    out = _onoff_section("Home", "Away", [_impact()], [])
    assert "ON-OFF" in out
    assert "OVERSTATES" in out  # newly_out => form still includes him
    assert "8g WITHOUT" in out
    assert "opponent strength faced" in out


def test_onoff_section_flags_long_term_absence_as_already_priced_in():
    out = _onoff_section("Home", "Away", [_impact(regime="long_term_out")], [])
    assert "do not double-count" in out


def test_onoff_section_empty_when_nothing_notable():
    assert _onoff_section("Home", "Away", [], []) == ""
    assert _onoff_section("Home", "Away", None, None) == ""
