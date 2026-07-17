"""Four Factors math + prompt rendering. DB/network-free (pure functions)."""

from nba_bot.agents.analysis_agent import _style_section
from nba_bot.features.four_factors import _poss, _ratio

_HFF = {"games": 10, "pace": 99.5, "efg": 0.55, "tov_pct": 0.13, "oreb_pct": 0.28,
        "ft_rate": 0.25, "def_efg": 0.53, "def_tov_pct": 0.14, "dreb_pct": 0.73,
        "def_ft_rate": 0.24}
_AFF = {**_HFF, "efg": 0.51, "def_efg": 0.57, "pace": 96.0}


def test_possession_formula():
    # FGA - OREB + TOV + 0.44*FTA
    assert _poss(90, 10, 14, 25) == 90 - 10 + 14 + 0.44 * 25


def test_ratio_rounds_and_guards_zero():
    assert _ratio(1, 3) == 0.333
    assert _ratio(5, 0) is None  # no division by zero


def test_style_section_renders_with_data():
    out = _style_section("Home", "Away", _HFF, _AFF)
    assert "STYLE" in out
    assert "Home eFG% 55.0% vs Away defense 57.0%" in out  # offense vs opposing defense
    assert "Pace: Home 99.5 vs Away 96.0" in out


def test_style_section_empty_without_data():
    # Missing/zero-game profiles -> section omitted so the prompt degrades to form-only.
    assert _style_section("Home", "Away", None, _AFF) == ""
    assert _style_section("Home", "Away", {"games": 0}, _AFF) == ""
