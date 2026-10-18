"""Prop projection model: distribution, projection, minutes budget. DB/network-free."""

from datetime import date, timedelta

import pytest

from nba_bot.features import props
from nba_bot.features.props import (
    Adjustment,
    GameLine,
    budget_adjustment,
    budget_ratio,
    over_under_probs,
    project,
    project_team,
)


def _log(n, minutes=30.0, pts=20, start=date(2026, 1, 1), **stats):
    return [
        GameLine(game_id=f"g{i}", game_date=start + timedelta(days=2 * i), season="2025-26",
                 team_id=1, opp_id=2, minutes=minutes, pts=pts, **stats)
        for i in range(n)
    ]


def test_normalize_name_folds_accents_suffixes_punctuation():
    assert props.normalize_name("Luka Dončić") == "luka doncic"
    assert props.normalize_name("Jaren Jackson Jr.") == "jaren jackson"
    assert props.normalize_name("R.J. Barrett") == "rj barrett"
    # Sportsbook, ESPN and box-score spellings must land on the same key.
    assert props.normalize_name("Nikola Jokić") == props.normalize_name("Nikola Jokic")


def test_over_under_probabilities_are_a_distribution():
    for mean, phi, line in ((22.0, 3.1, 24.5), (4.2, 1.4, 4.5), (0.6, 1.1, 0.5), (8.0, 1.0, 7.5)):
        over, under, push = over_under_probs(mean, phi, line)
        assert push == 0
        assert over + under == pytest.approx(1.0)
        assert 0 < over < 1


def test_whole_number_line_can_push():
    over, under, push = over_under_probs(5.0, 1.4, 5.0)
    assert push > 0.1
    assert over + under + push == pytest.approx(1.0)


def test_higher_line_is_harder_to_go_over_and_overdispersion_fattens_the_tail():
    assert over_under_probs(20, 3, 18.5)[0] > over_under_probs(20, 3, 24.5)[0]
    # Same mean, far-away line: the over-dispersed player gets there more often.
    assert over_under_probs(20, 3.5, 34.5)[0] > over_under_probs(20, 1.0, 34.5)[0]


def test_projection_needs_a_minimum_sample():
    assert project(_log(props.DEFAULT_PARAMS.min_games - 1)) is None
    assert project(_log(props.DEFAULT_PARAMS.min_games)) is not None


def test_steady_player_projects_his_average_and_combos_add_up():
    proj = project(_log(30, minutes=32, pts=24, reb=8, ast=5))
    assert proj.minutes == pytest.approx(32.0)
    assert proj.mean("player_points") == pytest.approx(24.0)
    assert proj.mean("player_points_rebounds_assists") == pytest.approx(37.0)


def test_minutes_follow_a_recent_role_change_while_rate_stays_put():
    # 20 games at 20 minutes, then 6 at 34 with the same per-minute scoring.
    log = _log(20, minutes=20, pts=10) + _log(6, minutes=34, pts=17, start=date(2026, 3, 1))
    proj = project(log)
    assert proj.minutes > 28                       # short half-life: mostly the new role
    assert proj.mean("player_points") / proj.minutes == pytest.approx(0.5, rel=0.02)


def test_opponent_factor_and_adjustment_scale_the_mean():
    base = project(_log(20)).mean("player_points")
    vs_soft = project(_log(20), opp_factors={"pts": 1.1}).mean("player_points")
    limited = project(_log(20), adjustment=Adjustment(0.8, {"pts": 0.8})).mean("player_points")
    assert vs_soft == pytest.approx(base * 1.1)
    assert limited == pytest.approx(base * 0.8)


def test_budget_ratio_reads_a_short_or_crowded_rotation():
    full = [34, 33, 32, 30, 28, 24, 20, 18, 15]            # exactly the budget
    assert sum(full) == props.ROTATION_BUDGET
    assert budget_ratio(full) == pytest.approx(1.0)
    assert budget_ratio(full[1:]) > 1                       # a starter is out
    assert budget_ratio(full + [30]) < 1                    # an extra starter arrived
    assert budget_ratio(full + [8, 5]) == pytest.approx(1.0)  # deep bench doesn't count
    assert budget_ratio([30, 30]) == props._BUDGET_CLIP[1]  # clipped, not 3.85


def test_bench_absorbs_more_of_spare_minutes_than_a_starter():
    bench, starter = budget_adjustment(18, 1.2), budget_adjustment(34, 1.2)
    assert bench.minutes_mult > starter.minutes_mult
    assert starter.minutes_mult == pytest.approx(1.0)   # already near the ceiling
    assert starter.mult("pts") > 1                      # ...but his usage still rises
    assert budget_adjustment(18, 0.85).minutes_mult < 1
    assert budget_adjustment(25, 1.01).stat_mults == {}  # inside the dead band


def test_project_team_raises_the_survivors_when_a_starter_is_missing():
    roster = {i: _log(30, minutes=m, pts=round(m / 2)) for i, m in
              enumerate([34, 33, 32, 30, 28, 24, 20, 18, 15])}
    healthy = project_team(roster)
    short = project_team({pid: log for pid, log in roster.items() if pid != 0})
    assert short[7].mean("player_points") > healthy[7].mean("player_points") * 1.05
    assert props.BUDGET_UP_NOTE in short[7].notes
    # Without the budget the absence is invisible to the projection.
    flat = project_team({pid: log for pid, log in roster.items() if pid != 0}, use_budget=False)
    assert flat[7].mean("player_points") == pytest.approx(healthy[7].mean("player_points"))


def test_quota_month_pacing():
    from datetime import date as d

    from nba_bot.agents.props_agent import credits_to_hold_back, days_until_reset

    assert days_until_reset(d(2026, 10, 20), 1) == 11       # Oct 21..31
    assert days_until_reset(d(2026, 10, 31), 1) == 0        # refills tomorrow
    assert days_until_reset(d(2026, 11, 1), 1) == 29        # a fresh 30-day month
    assert days_until_reset(d(2026, 11, 10), 15) == 4       # mid-month billing date
    # One prop market + moneyline-only game lines: ~10.5 credits a day of essentials.
    assert credits_to_hold_back(29, 1, 1) == 29 * 10.5
    assert credits_to_hold_back(0, 12, 3) == 0


def test_a_news_capped_player_does_not_soak_up_spare_minutes():
    roster = {i: _log(30, minutes=m) for i, m in enumerate([33, 32, 30, 28, 24, 20])}
    capped = project_team(roster, extra={5: Adjustment(0.7, dict.fromkeys(props.BASE_STATS, 0.7))})
    assert capped[5].minutes == pytest.approx(20 * 0.7, abs=0.1)
    assert capped[4].minutes > 24      # his teammates still do


def test_stat_guide_brackets_the_projection_and_skips_the_bet_market():
    proj = project(_log(30, minutes=32, pts=24, reb=8, ast=5))
    guide = props.stat_guide(proj, "player_points_rebounds_assists")
    assert set(guide) == {"pts", "reb", "ast"}
    pts = guide["pts"]
    assert pts["over_at"] < pts["proj"] < pts["under_at"]       # only bet away from the projection
    assert proj.probs("player_points", pts["over_at"])[0] >= props.GUIDE_PROB
    assert proj.probs("player_points", pts["over_at"] + 1)[0] < props.GUIDE_PROB   # the highest such line
    assert proj.probs("player_points", pts["under_at"])[1] >= props.GUIDE_PROB
    # The stat already bet is left out.
    assert "pts" not in props.stat_guide(proj, "player_points")


def test_stat_guide_has_no_over_line_for_a_stat_he_rarely_records():
    proj = project(_log(30, minutes=20, pts=6, reb=2, ast=0) + _log(2, minutes=20, pts=6, reb=2, ast=1))
    assert props.stat_guide(proj, "player_points")["ast"]["over_at"] is None


# --------------------------------------------------------------------------- #
# Game context: layoffs, the schedule, blowouts, and the stat budget.
# --------------------------------------------------------------------------- #
def _ctx(**kw):
    # The log's last game is 2026-02-28; by default the team last played then too.
    base = dict(game_date=date(2026, 3, 2), team_id=1, home=True,
                team_dates=(date(2026, 2, 26), date(2026, 2, 28)))
    return props.Context(**{**base, **kw})


def test_a_player_back_from_a_layoff_is_eased_in_and_harder_to_call():
    log = _log(30)                                            # last game 2026-02-28
    missed = (date(2026, 3, 1), date(2026, 3, 3), date(2026, 3, 5), date(2026, 3, 7))
    assert props.layoff_adjustment(log, _ctx()) is None       # he played the team's last game
    back = props.layoff_adjustment(log, _ctx(game_date=date(2026, 3, 9), team_dates=missed))
    assert back.minutes_mult == back.mult("pts") == 0.92
    assert back.spread_mult > 1 and "missing 4 games" in back.notes[0]
    assert props.layoff_adjustment(log, _ctx(team_id=9)).minutes_mult == props._DEBUT_MULT
    # Projecting a game further ahead: his debut, or his return, comes in an earlier one.
    assert props.layoff_adjustment(log, _ctx(team_id=9, unplayed_before=1)) is None
    late = _ctx(game_date=date(2026, 3, 9), team_dates=missed, unplayed_before=2)
    assert props.layoff_adjustment(log, late) is None


def test_context_reads_the_schedule_and_the_matchup_by_role():
    rested = props.context_adjustment(34, _ctx())
    tired = props.context_adjustment(34, _ctx(game_date=date(2026, 3, 1)))   # played yesterday
    assert tired.mult("reb") == pytest.approx(rested.mult("reb") * props._B2B_MULT)
    # Home helps scoring, not rebounding.
    away = props.context_adjustment(34, _ctx(home=False))
    assert rested.mult("pts") > 1 > away.mult("pts") and rested.mult("reb") == 1
    # A heavy favourite rests its starters and plays its bench; an underdog's starters stay on.
    assert props.context_adjustment(34, _ctx(expected_margin=12)).minutes_mult < 1
    assert props.context_adjustment(16, _ctx(expected_margin=12)).minutes_mult > 1
    assert props.context_adjustment(34, _ctx(expected_margin=-12)).minutes_mult == 1


def test_stat_budget_hands_missing_production_to_the_bench_first():
    roster = {i: _log(30, minutes=m, pts=round(m / 2), reb=4, ast=3) for i, m in
              enumerate([34, 33, 32, 30, 28, 24, 20, 18, 15])}
    plain = project_team(roster)
    totals = {s: sum(p.means[s] for p in plain.values()) / props._ROTATION_SHARE[s]
              for s in props.BASE_STATS}
    same = project_team(roster, context=_ctx(home=True, team_totals=totals))
    more = project_team(roster, context=_ctx(home=True, team_totals={**totals, "ast": totals["ast"] * 1.2}))
    assert same[8].means["ast"] == pytest.approx(plain[8].means["ast"], rel=0.02)
    bench, starter = more[8].means["ast"] / same[8].means["ast"], more[0].means["ast"] / same[0].means["ast"]
    assert bench > starter > 1                                # the team's assists come from someone
    assert more[8].means["reb"] == pytest.approx(same[8].means["reb"])   # only the short stat moves


def test_a_thin_log_widens_the_spread():
    thin, deep = project(_log(8, pts=20)), project(_log(40, pts=20))
    assert thin.phis["player_points"] > deep.phis["player_points"]
