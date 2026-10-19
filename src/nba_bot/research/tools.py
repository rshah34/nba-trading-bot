"""The research agent's tools, and the rules a finding has to pass.

Every tool is a fixed operation on the research table — no free-form code or SQL —
so the agent can only ask questions this module knows how to answer honestly:

* `measure`  — the model's bias and accuracy in a slice of the exploration season.
* `try_fix`  — what a multiplier on one stat in that slice would do, there.
* `confirm`  — the one look at the held-back season, for a fix stated in advance.

What makes a finding believable lives here, not in the prompt:

* Error bars treat a player's games as related (clustered by player), so 82 games
  of one player don't pass for 82 independent samples.
* The held-back season is only seen through `confirm`, a fix is fixed before it is
  scored there, and the bar rises with every confirmation ever run against that
  season — the season wears out, and the threshold says so.
* A fix must improve the whole model, not just its slice, so it can't win on one
  group of players by making everyone else worse.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist

import numpy as np
import pandas as pd

from nba_bot.research.table import FEATURES, OUTCOMES, TARGETS, over_probability

EXPLORE, CONFIRM = "2024-25", "2025-26"
MIN_ROWS, MIN_PLAYERS = 500, 40      # in the slice, in both seasons, for a confirmation
CONFIRMS_PER_RUN = 5
ALPHA = 0.05
MULTIPLIER_RANGE = (0.8, 1.25)
_OPS = ("==", "!=", "<", "<=", ">", ">=", "in", "between")
_MAX_GROUPS = 10
# Pre-game columns a slice may be cut on, beyond FEATURES: the projection itself and
# the test line are known before tip. Results (actual_*, over_*) are not.
_PREGAME = set(FEATURES) | {f"proj_{t}" for t in TARGETS} | {f"line_{t}" for t in TARGETS}


class ToolError(ValueError):
    """A request the tools refuse. The message goes back to the agent."""


# --------------------------------------------------------------------------- #
# Slices.
# --------------------------------------------------------------------------- #
def _field(name) -> str:
    """A field name as the agent sent it, tidied: models sometimes wrap it in quotes."""
    f = str(name).strip().strip("'\"").strip()
    if f not in _PREGAME:
        raise ToolError(f"unknown or post-game field {name!r}; see describe()")
    return f


def mask(df: pd.DataFrame, where: list[dict] | None) -> pd.Series:
    """Rows matching every condition: [{"field": "b2b", "op": "==", "value": true}, …]."""
    keep = pd.Series(True, index=df.index)
    for cond in where or []:
        f, op, v = _field(cond.get("field")), cond.get("op"), cond.get("value")
        if op not in _OPS:
            raise ToolError(f"op must be one of {_OPS}")
        col = df[f]
        if op == "in":
            if not isinstance(v, list):
                raise ToolError("'in' takes a list")
            m = col.isin(v)
        elif op == "between":
            if not (isinstance(v, list) and len(v) == 2):
                raise ToolError("'between' takes [low, high], both inclusive")
            m = col.between(v[0], v[1])
        else:
            m = {"==": col.eq, "!=": col.ne, "<": col.lt, "<=": col.le,
                 ">": col.gt, ">=": col.ge}[op](v)
        keep &= m.fillna(False).astype(bool)
    return keep


def describe_slice(where: list[dict] | None) -> str:
    if not where:
        return "every player-game"
    return " and ".join(f"{c['field']} {c['op']} {c['value']}" for c in where)


# --------------------------------------------------------------------------- #
# Numbers.
# --------------------------------------------------------------------------- #
def clustered_mean(values: pd.Series, groups: pd.Series) -> tuple[float, float]:
    """(mean, standard error) with the error clustered by group (player)."""
    n = len(values)
    if n == 0:
        return math.nan, math.nan
    mean = float(values.mean())
    sums = (values - mean).groupby(groups).sum()
    g = len(sums)
    if g < 2:
        return mean, math.nan
    se = math.sqrt(float((sums ** 2).sum()) * g / (g - 1)) / n
    return mean, se


def _bias(df: pd.DataFrame, target: str) -> dict:
    err = df[f"proj_{target}"] - df[f"actual_{target}"]
    mean, se = clustered_mean(err, df["player_id"])
    actual = float(df[f"actual_{target}"].mean())
    return {
        "bias": round(mean, 3),                         # + = the model projects too high
        "ci95": [round(mean - 1.96 * se, 3), round(mean + 1.96 * se, 3)],
        "bias_pct": round(100 * mean / actual, 1) if actual else None,
        "mae": round(float(err.abs().mean()), 3),
        "mean_actual": round(actual, 2),
    }


def _slice_summary(df: pd.DataFrame, targets: tuple[str, ...]) -> dict:
    return {"rows": int(len(df)), "players": int(df["player_id"].nunique()),
            **{t: _bias(df, t) for t in targets}}


def _with_fix(df: pd.DataFrame, rows: pd.Series, stat: str, mult: float) -> pd.DataFrame:
    """A copy with `stat` scaled by `mult` on the given rows. "pra" scales points,
    rebounds and assists together; any of those three moves PRA with it."""
    out = df.copy()
    parts = ("pts", "reb", "ast") if stat == "pra" else (stat,)
    for s in parts:
        delta = out.loc[rows, f"proj_{s}"] * (mult - 1)
        out.loc[rows, f"proj_{s}"] += delta
        if s in ("pts", "reb", "ast"):
            out.loc[rows, "proj_pra"] += delta
    return out


def _affected(stat: str) -> tuple[str, ...]:
    if stat == "pra":
        return ("pts", "reb", "ast", "pra")
    return (stat, "pra") if stat in ("pts", "reb", "ast") else (stat,)


def _brier(df: pd.DataFrame, target: str) -> pd.Series:
    p = over_probability(df[f"proj_{target}"], df[f"phi_{target}"], df[f"line_{target}"])
    return (p - df[f"over_{target}"].astype(float)) ** 2


@dataclass
class _Scores:
    """Whole-model accuracy on one season: pooled MAE over every stat, and the over/
    under Brier per row and stat (cached — only the rows a fix touches are redone)."""

    mae: float
    brier: dict[str, pd.Series] = field(default_factory=dict)

    @classmethod
    def of(cls, df: pd.DataFrame) -> _Scores:
        mae = float(np.mean([(df[f"proj_{t}"] - df[f"actual_{t}"]).abs().mean() for t in TARGETS]))
        return cls(mae, {t: _brier(df, t) for t in TARGETS})

    def pooled_brier(self) -> float:
        return float(np.mean([s.mean() for s in self.brier.values()]))

    def after(self, fixed: pd.DataFrame, rows: pd.Series, stat: str) -> _Scores:
        brier = dict(self.brier)
        for t in _affected(stat):
            redone = brier[t].copy()
            redone[rows] = _brier(fixed[rows], t)
            brier[t] = redone
        mae = float(np.mean([(fixed[f"proj_{t}"] - fixed[f"actual_{t}"]).abs().mean()
                             for t in TARGETS]))
        return _Scores(mae, brier)


# --------------------------------------------------------------------------- #
# The workspace the agent's tool calls run against.
# --------------------------------------------------------------------------- #
@dataclass
class Confirmation:
    hypothesis: str
    where: list[dict]
    stat: str
    multiplier: float
    explore: dict
    confirm: dict
    verdict: str            # "pass" | "fail"
    reasons: list[str]
    threshold_z: float


class Workspace:
    """Both seasons of the research table, and the state of one research run.

    `prior_confirmations` is how many confirmations have ever been run against the
    held-back season, across every earlier run: each one raises the bar."""

    def __init__(self, table: pd.DataFrame, prior_confirmations: int = 0,
                 confirms_per_run: int = CONFIRMS_PER_RUN):
        self.explore = table[table["season"] == EXPLORE].reset_index(drop=True)
        self.held = table[table["season"] == CONFIRM].reset_index(drop=True)
        if self.explore.empty or self.held.empty:
            raise ToolError(f"the research table needs both {EXPLORE} and {CONFIRM}")
        self.prior_confirmations = prior_confirmations
        self.confirms_left = confirms_per_run
        self.confirmations: list[Confirmation] = []
        self._explore_scores = _Scores.of(self.explore)
        self._held_scores: _Scores | None = None   # computed on the first confirmation

    # -- tools ------------------------------------------------------------- #
    def describe(self) -> dict:
        return {
            "seasons": {"explore": EXPLORE, "confirm": CONFIRM},
            "fields": FEATURES,
            "also_sliceable": "proj_<stat> and line_<stat> (known before tip)",
            "outcomes": OUTCOMES,
            "stats": list(TARGETS),
            "ops": list(_OPS),
            "baseline_explore": _slice_summary(self.explore, TARGETS),
            "rules": {
                "confirm_needs": f"{MIN_ROWS}+ rows and {MIN_PLAYERS}+ players in the slice in "
                                 "both seasons; the same direction of bias on the held-back "
                                 "season, clear of zero at the corrected threshold; and lower "
                                 "whole-model MAE with no worse Brier there",
                "confirmations_left_this_run": self.confirms_left,
                "confirmations_ever_on_this_season": self.prior_confirmations,
                "multiplier_range": list(MULTIPLIER_RANGE),
            },
        }

    def measure(self, where: list[dict] | None = None, by: str | None = None,
                stats: list[str] | None = None) -> dict:
        """Bias and accuracy in a slice of the exploration season, optionally split."""
        targets = self._targets(stats)
        df = self.explore[mask(self.explore, where)]
        out = {"slice": describe_slice(where), "overall": _slice_summary(df, targets)}
        if by:
            by = _field(by)
            col = df[by]
            if pd.api.types.is_numeric_dtype(col) and not pd.api.types.is_bool_dtype(col) \
                    and col.nunique() > _MAX_GROUPS:
                col = pd.qcut(col, 5, duplicates="drop").astype(str)
            groups = col.astype(str)
            if groups.nunique() > _MAX_GROUPS:
                raise ToolError(f"{by} has too many values to split on")
            out["by"] = {k: _slice_summary(g, targets) for k, g in df.groupby(groups)}
        return out

    def try_fix(self, where: list[dict] | None, stat: str, multiplier: float | None = None) -> dict:
        """What scaling `stat` by `multiplier` in the slice would do on the exploration
        season. Leave the multiplier out to use the one that zeroes the slice's bias."""
        stat = self._stat(stat)
        rows = mask(self.explore, where)
        if not rows.any():
            raise ToolError("the slice is empty")
        if multiplier is None:
            sub = self.explore[rows]
            multiplier = float(sub[f"actual_{stat}"].sum() / sub[f"proj_{stat}"].sum())
        multiplier = self._multiplier(multiplier)
        fixed = _with_fix(self.explore, rows, stat, multiplier)
        after = self._explore_scores.after(fixed, rows, stat)
        return {
            "slice": describe_slice(where), "stat": stat, "multiplier": round(multiplier, 4),
            "in_slice": {"before": _slice_summary(self.explore[rows], (stat,)),
                         "after": _slice_summary(fixed[rows], (stat,))},
            "whole_model": self._compare(self._explore_scores, after),
        }

    def confirm(self, hypothesis: str, where: list[dict] | None, stat: str,
                multiplier: float) -> Confirmation:
        """Score a fix, stated in full beforehand, on the held-back season."""
        if self.confirms_left <= 0:
            raise ToolError("no confirmations left this run")
        stat = self._stat(stat)
        multiplier = self._multiplier(multiplier)
        if multiplier == 1.0:
            raise ToolError("a multiplier of 1 changes nothing")
        e_rows, h_rows = mask(self.explore, where), mask(self.held, where)
        # Sizes are checked before any result is looked at, so a refusal costs nothing.
        for name, df, rows in (("exploration", self.explore, e_rows), ("held-back", self.held, h_rows)):
            n, players = int(rows.sum()), int(df.loc[rows, "player_id"].nunique())
            if n < MIN_ROWS or players < MIN_PLAYERS:
                raise ToolError(f"slice too small in the {name} season: {n} rows, {players} "
                                f"players (needs {MIN_ROWS} and {MIN_PLAYERS})")

        self.confirms_left -= 1
        k = self.prior_confirmations + len(self.confirmations) + 1
        z = NormalDist().inv_cdf(1 - ALPHA / (2 * k))   # Bonferroni over every look so far
        if self._held_scores is None:
            self._held_scores = _Scores.of(self.held)
        fixed = _with_fix(self.held, h_rows, stat, multiplier)
        after = self._held_scores.after(fixed, h_rows, stat)
        held = self.held[h_rows]
        err = held[f"proj_{stat}"] - held[f"actual_{stat}"]
        bias, se = clustered_mean(err, held["player_id"])
        whole = self._compare(self._held_scores, after)

        reasons = []
        expected = "too high" if multiplier < 1 else "too low"
        same_way = (bias > 0) == (multiplier < 1)
        reasons.append(f"held-back bias {bias:+.3f} (the fix assumes projections are {expected})"
                       + ("" if same_way else ": wrong direction"))
        clear = same_way and abs(bias) > z * se
        reasons.append(f"|bias| {abs(bias):.3f} vs {z:.2f} × SE {se:.3f} = {z * se:.3f}"
                       + (": clear of zero" if clear else ": not clear of zero"))
        better = after.mae < self._held_scores.mae
        reasons.append(f"whole-model MAE {whole['mae_before']} → {whole['mae_after']}"
                       + ("" if better else ": no better"))
        no_worse = after.pooled_brier() <= self._held_scores.pooled_brier()
        reasons.append(f"whole-model Brier {whole['brier_before']} → {whole['brier_after']}"
                       + ("" if no_worse else ": worse"))

        result = Confirmation(
            hypothesis=hypothesis, where=where or [], stat=stat, multiplier=round(multiplier, 4),
            explore=self.try_fix(where, stat, multiplier),
            confirm={"in_slice": {"before": _slice_summary(held, (stat,)),
                                  "after": _slice_summary(fixed[h_rows], (stat,))},
                     "whole_model": whole},
            verdict="pass" if same_way and clear and better and no_worse else "fail",
            reasons=reasons, threshold_z=round(z, 3),
        )
        self.confirmations.append(result)
        return result

    # -- helpers ----------------------------------------------------------- #
    @staticmethod
    def _targets(stats: list[str] | None) -> tuple[str, ...]:
        targets = tuple(stats or ("pts", "reb", "ast", "pra"))
        bad = [t for t in targets if t not in TARGETS]
        if bad:
            raise ToolError(f"unknown stats {bad}; use {list(TARGETS)}")
        return targets

    @staticmethod
    def _stat(stat: str) -> str:
        if stat not in TARGETS:
            raise ToolError(f"stat must be one of {list(TARGETS)}")
        return stat

    @staticmethod
    def _multiplier(m: float) -> float:
        lo, hi = MULTIPLIER_RANGE
        if not (lo <= m <= hi):
            raise ToolError(f"multiplier must be within {lo}–{hi}; a bigger miss than that is "
                            "a bug or a missing input, not a tweak")
        return float(m)

    @staticmethod
    def _compare(before: _Scores, after: _Scores) -> dict:
        return {"mae_before": round(before.mae, 5), "mae_after": round(after.mae, 5),
                "brier_before": round(before.pooled_brier(), 5),
                "brier_after": round(after.pooled_brier(), 5)}
