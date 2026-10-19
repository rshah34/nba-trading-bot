"""The research agent: Claude looks for where the prop projections are systematically
wrong, using the fixed tools in `tools.py`, and every fix it believes in is scored
once on the held-back season.

Claude's job is the part statistics can't do: knowing which few of the endless ways
to slice the data are worth a look. Whether an idea holds up is decided by the tools,
not by the model. Nothing here changes the projection model — a fix that passes is
a proposal, applied by hand and re-checked with the full backtest.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import anthropic
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nba_bot import usage
from nba_bot.config import settings
from nba_bot.db.models import ResearchFinding, ResearchRun
from nba_bot.research import tools
from nba_bot.research.table import TARGETS

log = logging.getLogger("nba_bot.research")

MODEL = "claude-sonnet-5-5"
# The first run at "medium" stopped after 7 turns and 6% of its budget, never cutting
# the data by more than one field at a time.
EFFORT = "high"
DEFAULT_BUDGET = 2.0         # dollars per run
MAX_TURNS = 60
_WARN_AT = 0.8               # share of the budget at which Claude is told to wrap up
_PRIOR_SHOWN = 60            # earlier ideas listed in the opening message

SYSTEM = """You are a quantitative sports researcher auditing an NBA player-prop \
projection model. Your job is to find where its projections are systematically wrong, \
and to prove it.

How the model projects a player's stat line for one game:
- Minutes from his recent games (recency-weighted, half-life about 4 games), and \
per-minute rates from a longer history (half-life about 20 games).
- An opponent factor: how much of each stat that opponent allows versus the league.
- A team minutes budget (rotation minutes add up to about 234) and a stat budget \
(the rotation's projections are scaled toward the team's typical totals), so when \
regulars sit, their minutes and production go to whoever plays.
- Adjustments it already makes: second night of a back-to-back (stats x0.975); a \
likely blowout (beyond a 6-point expected margin the favourite's starters lose \
minutes and the bench gains); a small home-court edge on points, assists, threes and \
blocks; a player back from a layoff eased in (fewer minutes by games missed); a \
first game for a new team (x0.80).
- Each stat's outcome is a negative binomial around the projected mean.

Your tools work on a table of every projected player-game (15+ projected minutes) \
over two seasons. You explore on 2024-25. 2025-26 is held back: you see it only \
through `confirm`, which scores one fix you have stated in full. Confirmations are \
limited, and every confirmation ever run raises the bar for the next, so spend them \
on your strongest ideas.

What gets bet is points + rebounds + assists (PRA), with points, rebounds and \
assists guidance alongside; threes, steals, blocks and turnovers only feed a fantasy \
page. A fix matters in proportion to how much it moves those.

Bias is projection minus actual: positive means the model projects too high. A \
worthwhile idea has a basketball mechanism - why would the model be wrong for these \
players in these games? "The model over-projects X" restates the number; it is not a \
reason. It shows up in exploration as a bias whose error bar is clear of zero, over a \
few hundred player-games and dozens of players at least. Single fields are a start; \
the model's blind spots are more likely where conditions meet (a role and a schedule \
spot, a role and a missing teammate), so cut by two or three fields together. Check \
that an effect is the one you think it is (is it back-to-backs, or road games?).

Before you confirm, `try_fix` the fix on the exploration season: `confirm` refuses \
any fix that cuts the whole model's error by less than the minimum in describe(), \
because smaller gains don't move a bet. A bias inside a slice the model already \
adjusts for means that adjustment is too strong or too weak. Most of a run should be \
exploring; confirm only the ideas you would bet on.

Ideas tested in earlier runs are listed in the first message; don't repeat one \
unless your version is materially different.

When you are done, or are told the budget is nearly spent, call `finish` with a \
short summary and the ideas you explored but did not confirm. Quote numbers as the \
tools gave them."""

_SCALAR = [{"type": "string"}, {"type": "number"}, {"type": "boolean"}]
_WHERE = {
    "type": "array",
    "description": "Conditions that all must hold; an empty list means every player-game.",
    "items": {
        "type": "object",
        "properties": {
            "field": {"type": "string", "description": "A field from describe()."},
            "op": {"type": "string", "enum": ["==", "!=", "<", "<=", ">", ">=", "in", "between"]},
            "value": {"anyOf": [*_SCALAR, {"type": "array", "items": {"anyOf": _SCALAR}}],
                      "description": "A value; a list for 'in'; [low, high] for 'between'."},
        },
        "required": ["field", "op", "value"],
        "additionalProperties": False,
    },
}
_STAT = {"type": "string", "enum": list(TARGETS)}


def _tool(name: str, description: str, properties: dict, strict: bool = True) -> dict:
    """A strict tool: every argument typed and present (optional ones are nullable),
    so a malformed call is rejected before it reaches the tools. `finish` isn't strict:
    with its nested candidates, the strict schemas together compile to a grammar the
    API refuses as too large, and it only reports — nothing runs on its arguments."""
    return {"name": name, "description": description, "strict": strict,
            "input_schema": {"type": "object", "properties": properties,
                             "required": list(properties), "additionalProperties": False}}


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


TOOLS = [
    _tool("describe", "The fields you can slice on, the stats, the rules for confirming, and "
                      "the model's overall bias on the exploration season. Call it first.", {}),
    _tool("measure", "The model's bias, its 95% error bar (clustered by player), MAE and the "
                     "actual average, per stat, in a slice of the exploration season. With "
                     "`by`, also split by a field (numeric fields with many values split into "
                     "quintiles).", {
        "where": _WHERE,
        "by": {"type": "string", "description": "A field to split the slice by; empty for none."},
        "stats": {"type": "array", "items": _STAT,
                  "description": "Stats to report; empty for pts, reb, ast, pra."}}),
    _tool("try_fix", "On the exploration season: what scaling one stat by a multiplier in a "
                     "slice does to the slice's bias and to the whole model's MAE and Brier. "
                     "A null multiplier uses the one that zeroes the slice's bias. 'pra' "
                     "scales points, rebounds and assists together.", {
        "where": _WHERE, "stat": _STAT,
        "multiplier": _nullable({"type": "number", "description": "Between 0.8 and 1.25."})}),
    _tool("confirm", "Score one fix on the held-back 2025-26 season. It passes only if the "
                     "slice is big enough in both seasons, the bias there runs the same way "
                     "and is clear of zero at the corrected threshold, and the whole model's "
                     "MAE improves with no worse Brier. Uses one of this run's confirmations; "
                     "a slice that is too small is refused without using one.", {
        "hypothesis": {"type": "string", "description": "The claim, in a sentence."},
        "rationale": {"type": "string", "description": "The basketball reason, briefly."},
        "where": _WHERE, "stat": _STAT,
        "multiplier": {"type": "number", "description": "Between 0.8 and 1.25, fixed now."}}),
    _tool("finish", "End the run with a summary, the ideas you looked at but did not "
                    "confirm, and (in an explore-only run) your strongest candidate fixes.", {
        "summary": {"type": "string", "description": "What you found, in a few sentences."},
        "explored": {"type": "array", "items": {
            "type": "object",
            "properties": {"idea": {"type": "string"},
                           "outcome": {"type": "string", "description": "What the numbers said."}},
            "required": ["idea", "outcome"], "additionalProperties": False}},
        "candidates": {"type": "array", "description": "Explore-only runs: fixes worth "
                       "confirming later, strongest first. Empty otherwise.", "items": {
            "type": "object",
            "properties": {
                "hypothesis": {"type": "string"},
                "rationale": {"type": "string"},
                "where": _WHERE, "stat": _STAT,
                "multiplier": {"type": "number", "description": "What try_fix found."},
                "evidence": {"type": "string", "description": "The exploration numbers."}},
            "required": ["hypothesis", "rationale", "where", "stat", "multiplier", "evidence"],
            "additionalProperties": False}}}, strict=False),
]
MODES = ("explore", "confirm")


def tools_for(mode: str) -> list[dict]:
    """An explore-only run is never offered `confirm`: the held-back season stays untouched."""
    return [t for t in TOOLS if mode == "confirm" or t["name"] != "confirm"]


# --------------------------------------------------------------------------- #
# Limits on starting a run — the same from the command line and the dashboard.
# --------------------------------------------------------------------------- #
MAX_BUDGET = 5.0             # dollars, one run
DAILY_CAP = 5.0              # dollars, every run started today together
_STALE = timedelta(minutes=30)   # a "running" run this old died with its process


def _start_of_today(now: datetime) -> datetime:
    local = now.astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def why_not_start(session: Session, budget: float, now: datetime | None = None) -> str | None:
    """The reason a run can't start now, or None if it can."""
    now = now or datetime.now(timezone.utc)
    if not 0 < budget <= MAX_BUDGET:
        return f"The budget must be more than $0 and at most ${MAX_BUDGET:g}."
    for run in session.execute(select(ResearchRun).where(ResearchRun.status == "running")).scalars():
        started = run.started_at if run.started_at.tzinfo else run.started_at.replace(tzinfo=timezone.utc)
        if now - started > _STALE:
            run.status, run.summary = "error", "Interrupted: the server stopped mid-run."
            run.finished_at = now
        else:
            return "A run is already going. Wait for it to finish."
    session.commit()
    spent = sum(float(r.cost_usd or 0) for r in session.execute(
        select(ResearchRun).where(ResearchRun.started_at >= _start_of_today(now))).scalars())
    if spent + budget > DAILY_CAP:
        return (f"That would take today's research spend past ${DAILY_CAP:g} "
                f"(${spent:.2f} spent so far).")
    return None


def start_run(session: Session, *, focus: str | None, budget: float, mode: str) -> ResearchRun:
    """Record a new run as started. `run_research` does the work."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    run = ResearchRun(model=MODEL, focus=focus or None, mode=mode, status="running",
                      budget_usd=budget, cost_usd=0, turns=0, log=[])
    session.add(run)
    session.commit()
    return run


def log_line(session: Session, run: ResearchRun, line: str) -> None:
    """Append a progress line the dashboard shows while the run works."""
    run.log = [*(run.log or []), {"ts": datetime.now(timezone.utc).isoformat(), "line": line}]
    session.commit()


# --------------------------------------------------------------------------- #
# The ledger: every earlier idea, and how worn the held-back season is.
# --------------------------------------------------------------------------- #
def confirmations_so_far(session: Session, season: str = tools.CONFIRM) -> int:
    return session.execute(
        select(func.count()).select_from(ResearchFinding)
        .where(ResearchFinding.confirm_season == season)
    ).scalar_one()


def prior_ideas(session: Session) -> list[str]:
    """One line per idea tested before: confirmed ones with their verdict, then the
    candidates and dropped ideas of earlier runs. Newest first."""
    lines = [
        f"- {f.hypothesis} [{tools.describe_slice(f.slice)}; {f.stat} x{float(f.multiplier):g}]: "
        f"{'PASSED' if f.verdict == 'pass' else 'failed'} confirmation"
        for f in session.execute(
            select(ResearchFinding).order_by(ResearchFinding.created_at.desc())
        ).scalars()
    ]
    for run in session.execute(
        select(ResearchRun).where(ResearchRun.status != "running")
        .order_by(ResearchRun.started_at.desc())
    ).scalars():
        lines += [f"- {c['hypothesis']} [{tools.describe_slice(c['where'])}; {c['stat']} "
                  f"x{c['multiplier']:g}]: candidate from an explore-only run, not yet confirmed "
                  f"({c['evidence']})" for c in run.candidates or []]
        lines += [f"- {e['idea']}: explored, not confirmed ({e['outcome']})"
                  for e in run.explored or []]
    return lines[:_PRIOR_SHOWN]


def _opening(focus: str | None, prior: list[str], mode: str, confirms: int) -> str:
    parts = [f"Focus for this run: {focus}." if focus
             else "No particular focus: look wherever you think the model is most likely wrong."]
    if mode == "explore":
        parts.append("This run is explore-only: confirm is not available and the held-back "
                     "season stays untouched. In finish, list your strongest candidate fixes "
                     "with the multiplier try_fix found, so a later run can confirm them; "
                     "list only fixes that clear the minimum whole-model gain in describe().")
    else:
        parts.append(f"You may run up to {confirms} confirmations; use only what your "
                     "evidence supports. Leave candidates empty in finish.")
    parts.append("Ideas tested before:\n" + "\n".join(prior) if prior
                 else "No ideas have been tested before.")
    parts.append("Start with describe.")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Running tools.
# --------------------------------------------------------------------------- #
def _dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def _call(ws: tools.Workspace, name: str, args: dict):
    if name == "describe":
        return ws.describe()
    if name == "measure":
        return ws.measure(args.get("where"), args.get("by") or None, args.get("stats") or None)
    if name == "try_fix":
        return ws.try_fix(args.get("where"), args["stat"], args.get("multiplier"))
    if name == "confirm":
        return ws.confirm(args["hypothesis"], args.get("where"), args["stat"], args["multiplier"])
    raise tools.ToolError(f"unknown tool {name!r}")


def _step_line(name: str, args: dict) -> str:
    return (f"{name}: {tools.describe_slice(args.get('where'))}"
            + (f" by {args['by']}" if args.get("by") else "")
            + (f" ({args['stat']})" if args.get("stat") else ""))


def _save_finding(session: Session, run: ResearchRun, c: tools.Confirmation,
                  rationale: str | None) -> ResearchFinding:
    finding = ResearchFinding(
        run_id=run.id, hypothesis=c.hypothesis, rationale=rationale, slice=c.where,
        stat=c.stat, multiplier=c.multiplier, explore_season=tools.EXPLORE,
        confirm_season=tools.CONFIRM, verdict=c.verdict, reasons=c.reasons,
        explore=json.loads(_dumps(c.explore)), confirm=json.loads(_dumps(c.confirm)),
        threshold_z=c.threshold_z,
    )
    session.add(finding)
    session.commit()   # the ledger survives a run that dies later
    return finding


# --------------------------------------------------------------------------- #
# The loop.
# --------------------------------------------------------------------------- #
def run_research(
    session: Session,
    run: ResearchRun,
    table: pd.DataFrame,
    *,
    client: anthropic.Anthropic | None = None,
    say: Callable[[str], None] = lambda _: None,
) -> ResearchRun:
    """Carry out a run recorded by `start_run`. Each step goes to the run's log (for
    the dashboard) and to `say` (for a terminal)."""
    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
    budget = float(run.budget_usd or DEFAULT_BUDGET)
    confirms = tools.CONFIRMS_PER_RUN if run.mode == "confirm" else 0
    ws = tools.Workspace(table, prior_confirmations=confirmations_so_far(session),
                         confirms_per_run=confirms)
    offered = tools_for(run.mode)
    run.prompt = _opening(run.focus, prior_ideas(session), run.mode, confirms)
    session.commit()
    messages: list[dict] = [{"role": "user", "content": run.prompt}]
    spent, warned, nudged = 0.0, False, False

    def note(line: str) -> None:
        say(line)
        log_line(session, run, line)

    def end(status: str, summary: str | None = None, explored: list | None = None,
            candidates: list | None = None) -> ResearchRun:
        run.status, run.cost_usd = status, round(spent, 4)
        run.summary = summary if summary is not None else run.summary
        run.explored = explored if explored is not None else run.explored
        run.candidates = candidates if candidates else run.candidates
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
        return run

    for turn in range(1, MAX_TURNS + 1):
        try:
            response = client.beta.messages.create(
                model=MODEL, max_tokens=16000, system=SYSTEM, tools=offered, messages=messages,
                output_config={"effort": EFFORT},
                cache_control={"type": "ephemeral"},      # the growing history is re-read each turn
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
        except anthropic.APIError as exc:
            log.exception("research call failed")
            return end("error", summary=f"API error: {exc}")
        spent += usage.record("research", response.model, response) or 0.0
        run.turns, run.cost_usd = turn, round(spent, 4)

        if response.stop_reason == "refusal":
            return end("refused", summary="The model declined to continue.")
        if response.stop_reason == "max_tokens":
            return end("error", summary="A response hit the token limit.")
        messages.append({"role": "assistant", "content": response.content})

        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            if nudged:
                text = " ".join(b.text for b in response.content if b.type == "text")
                return end("done", summary=text or None)
            nudged = True
            messages.append({"role": "user", "content": "Call finish to end the run."})
            continue

        results = []
        for call in calls:
            args = call.input or {}
            if call.name == "finish":
                note("finish")
                candidates = (args.get("candidates") or []) if run.mode == "explore" else []
                return end("done", summary=args.get("summary"),
                           explored=args.get("explored") or [], candidates=candidates)
            try:
                result = _call(ws, call.name, args)
                if isinstance(result, tools.Confirmation):
                    _save_finding(session, run, result, args.get("rationale"))
                    note(f"confirm: {result.hypothesis} -> {result.verdict.upper()}")
                    result = asdict(result)
                else:
                    note(_step_line(call.name, args))
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": _dumps(result)})
            except (tools.ToolError, KeyError, TypeError, ValueError) as exc:
                note(f"{call.name}: refused ({exc})")
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": f"Error: {exc}", "is_error": True})
        messages.append({"role": "user", "content": results})

        if spent >= budget:
            return end("out_of_budget", summary=f"Stopped at ${spent:.2f} of ${budget:.2f}.")
        if spent >= _WARN_AT * budget and not warned:
            warned = True
            messages.append({"role": "system", "content":
                             f"Budget nearly spent (${spent:.2f} of ${budget:.2f}). Make at most "
                             "one more tool call, then call finish."})
    return end("out_of_turns", summary=f"Stopped after {MAX_TURNS} turns.")
