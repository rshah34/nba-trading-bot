"""Backtest tests: calibration bucketing + date parsing. DB/network-free."""

from datetime import date, datetime

from nba_bot.backtest import loader
from nba_bot.backtest.report import calibration_table


def test_calibration_table_buckets_and_rates():
    # 10 predictions at 0.70; 7 of them the home team actually won -> 70% realized.
    pairs = [(0.70, True)] * 7 + [(0.70, False)] * 3
    rows = calibration_table(pairs, n_bins=5)
    assert len(rows) == 1
    r = rows[0]
    assert r["bin"] == "0.6-0.8"
    assert r["n"] == 10
    assert r["avg_predicted"] == 0.7
    assert r["actual_win_rate"] == 0.7


def test_calibration_extremes_land_in_end_bins():
    rows = calibration_table([(1.0, True), (0.0, False), (0.5, True)], n_bins=5)
    bins = {r["bin"] for r in rows}
    assert "0.8-1.0" in bins  # p=1.0 clamped into the last bin, not out of range
    assert "0.0-0.2" in bins
    assert "0.4-0.6" in bins


def test_calibration_empty_is_empty():
    assert calibration_table([], n_bins=5) == []


def test_as_date_handles_strings_and_datetimes():
    assert loader._as_date("2025-10-21") == date(2025, 10, 21)
    assert loader._as_date(date(2025, 10, 21)) == date(2025, 10, 21)
    assert loader._as_date(datetime(2025, 10, 21, 3, 30)) == date(2025, 10, 21)
