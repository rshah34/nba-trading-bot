"""Track what each Claude call costs.

Every call appends its token usage to `logs/api-usage.jsonl`, so spend is a fact
you can read back (`nba-bot costs`) rather than an estimate. Recording never
raises: losing a log line must not cost a prediction.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("nba_bot.usage")

LOG_PATH = Path("logs") / "api-usage.jsonl"

# $ per million tokens (input, output). Output includes thinking tokens.
PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-4-8": (5.0, 25.0),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Dollar cost of one call, or None for a model with no known price."""
    price = PRICING.get(model)
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def record(kind: str, model: str, response, path: Path | None = None) -> None:
    """Append one call's usage. `kind` names the caller: 'game' or 'news'."""
    try:
        u = response.usage
        # Cache reads/writes are billed too; fold them in so nothing is undercounted.
        input_tokens = (u.input_tokens + (getattr(u, "cache_read_input_tokens", 0) or 0)
                        + (getattr(u, "cache_creation_input_tokens", 0) or 0))
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": u.output_tokens,
            "usd": cost_usd(model, input_tokens, u.output_tokens),
        }
        path = path or LOG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not record API usage: %s", exc)


def summarize(days: int = 30, path: Path | None = None) -> dict:
    """Calls, tokens and dollars per (kind, model) over the last `days`, plus per day."""
    path = path or LOG_PATH
    since = datetime.now(timezone.utc) - timedelta(days=days)
    groups: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
    )
    by_day: dict[str, float] = defaultdict(float)
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                e = json.loads(line)
                ts = datetime.fromisoformat(e["ts"])
            except (ValueError, KeyError):
                continue
            if ts < since:
                continue
            g = groups[(e["kind"], e["model"])]
            g["calls"] += 1
            g["input_tokens"] += e["input_tokens"]
            g["output_tokens"] += e["output_tokens"]
            g["usd"] += e.get("usd") or 0.0
            by_day[ts.astimezone().strftime("%Y-%m-%d")] += e.get("usd") or 0.0
    return {
        "groups": {f"{k} / {m}": v for (k, m), v in sorted(groups.items())},
        "by_day": dict(sorted(by_day.items())),
        "total_usd": round(sum(g["usd"] for g in groups.values()), 4),
    }
