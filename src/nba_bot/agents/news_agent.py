"""News agent: read the day's NBA news into structured, player-level signals.

The injury report says who is out. It does not say that a starter is on a
20-minute limit in his first game back, that a coach is moving a sixth man into
the starting five, that a veteran sits the second night of back-to-backs, or that
a rookie has taken someone's minutes — and those are exactly the things that move
a player's line. Claude reads each new article once and emits what it finds as
small multipliers the projection can apply; nothing here ever sees a betting line.

Signals expire (a "probable tonight" is worthless in three days), and each carries
a confidence that scales how much of its multiplier is believed.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import anthropic
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from nba_bot import usage
from nba_bot.config import settings
from nba_bot.db.models import NewsArticle, NewsSignal, PlayerGameStats, Team
from nba_bot.features.on_off import normalize_name
from nba_bot.features.props import BASE_STATS, Adjustment

log = logging.getLogger("nba_bot.news")

# Characters of article text per extraction request (~10k tokens).
_BATCH_CHARS = 40_000
# Signals below this confidence are stored but never move a projection.
_MIN_CONFIDENCE = 0.5
# Taking a player out of the rotation moves every teammate's projection, and a wrong
# guess there is worse than no guess — so a news "out" has to be firm. Below this it
# only blocks bets on him, the same as "questionable".
_OUT_CONFIDENCE = 0.75
# An untagged article this short is a player-news blurb, cheap enough to always read.
_BLURB_CHARS = 600
_UNCERTAIN_KINDS = {"doubtful", "questionable"}

Kind = Literal["out", "doubtful", "questionable", "minutes_limit", "role_up", "role_down",
               "returning", "other"]


class Signal(BaseModel):
    article: int = Field(description="Number of the article this came from.")
    player: str = Field(description="Player's full name as commonly written.")
    team: str = Field(description="His NBA team's three-letter abbreviation, e.g. BOS. "
                                  "Empty string if the article doesn't make it clear.")
    kind: Kind
    minutes_mult: float = Field(
        description="Expected minutes relative to his recent normal: 1.0 = unchanged, "
                    "0.7 = a real restriction, 1.15 = a clearly bigger role. 0 if he won't play."
    )
    usage_mult: float = Field(
        description="Expected per-minute production relative to normal (shots, touches): "
                    "1.0 unless the article gives a specific reason. Keep within 0.85–1.15."
    )
    confidence: float = Field(description="0–1: how firmly the article supports this.")
    days_valid: int = Field(description="How many days this stays true, 1–10.")
    summary: str = Field(description="One sentence: what was reported and by whom.")


class Extraction(BaseModel):
    signals: list[Signal]


_SYSTEM = """You read NBA news for a player-projection system. For each article, pull out \
concrete, forward-looking facts about what a specific player's minutes or role will be in \
his team's upcoming games.

Worth reporting:
- Availability: ruled out, doubtful, questionable, a game-time decision, a rest night, a suspension.
- Minutes limits: a restriction after injury, ramp-up plans, a coach saying he'll be eased in.
- Role changes: entering or leaving the starting lineup, a larger or smaller role, a new \
rotation, a trade or signing that changes who plays, a two-way or G League move.
- Returns: back from injury, and whether with or without limits.

Not worth reporting: recaps of what a player already did, contract and salary news with no \
effect on playing time, trade rumours that haven't happened, opinion, awards, anything about \
a player's long-term outlook rather than the next week or so.

Be conservative. The multipliers adjust a projection built from his recent games, so only \
depart from 1.0 when the article gives a specific reason, and scale confidence to the source: \
a team announcement or a named beat reporter is firm; "could", "might", and speculation are \
not. If an article contains nothing that qualifies, return no signal for it. Most don't."""


def _batches(articles: list[NewsArticle]) -> list[list[NewsArticle]]:
    batches: list[list[NewsArticle]] = [[]]
    size = 0
    for a in articles:
        n = len(a.title or "") + len(a.raw_text or "")
        if batches[-1] and size + n > _BATCH_CHARS:
            batches.append([])
            size = 0
        batches[-1].append(a)
        size += n
    return [b for b in batches if b]


def _prompt(batch: list[NewsArticle]) -> str:
    parts = []
    for i, a in enumerate(batch, 1):
        when = a.published_at.strftime("%Y-%m-%d %H:%M UTC") if a.published_at else "unknown"
        parts.append(f"<article number=\"{i}\" source=\"{a.source}\" published=\"{when}\">\n"
                     f"{a.title or ''}\n\n{a.raw_text or ''}\n</article>")
    return "\n\n".join(parts)


def worth_reading(article: NewsArticle, team_ids: set[int] | None) -> bool:
    """Should this article be sent to the model for today's slate?

    Reading is the paid step, so only articles that can move a projection today go:
    those tagged with a team that plays, plus short untagged items — the player-news
    blurbs ("X: questionable Friday") name a player but often no team, and cost
    almost nothing. A long piece about teams that are off today waits; if they play
    tomorrow and it is still recent, it is read then.
    """
    if team_ids is None:
        return True
    tagged = set(article.team_ids or [])
    if tagged:
        return bool(tagged & team_ids)
    return len(article.raw_text or "") <= _BLURB_CHARS


def extract_signals(
    session: Session,
    client: anthropic.Anthropic | None = None,
    lookback_hours: int = 48,
    model: str | None = None,
    team_ids: set[int] | None = None,
) -> dict:
    """Mine not-yet-read recent articles for player signals. Each article is read once.

    `team_ids` limits reading to what matters for those teams (see `worth_reading`);
    None reads everything recent.
    """
    model = model or settings.news_model
    now = datetime.now(timezone.utc)
    recent = session.execute(
        select(NewsArticle)
        .where(
            NewsArticle.signals_extracted_at.is_(None),
            or_(NewsArticle.published_at >= now - timedelta(hours=lookback_hours),
                NewsArticle.published_at.is_(None)),
        )
        .order_by(NewsArticle.published_at)
    ).scalars().all()
    articles = [a for a in recent if worth_reading(a, team_ids)]
    if not articles:
        return {"articles": 0, "signals": 0, "left_unread": len(recent)}

    client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)
    teams = {t.abbreviation.upper(): t.team_id for t in session.execute(select(Team)).scalars()}
    players: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for pid, name, team_id in session.execute(
        select(PlayerGameStats.player_id, PlayerGameStats.player_name, PlayerGameStats.team_id)
        .distinct()
    ):
        players[normalize_name(name)].append((pid, team_id))

    stored = failed = 0
    for batch in _batches(articles):
        try:
            response = client.messages.parse(
                model=model,
                max_tokens=16000,
                system=_SYSTEM,
                messages=[{"role": "user", "content": _prompt(batch)}],
                output_format=Extraction,
            )
        except anthropic.APIError as exc:
            # Leave the batch unread so the next run retries it.
            log.warning("signal extraction failed for %d article(s): %s", len(batch), exc)
            failed += len(batch)
            continue
        usage.record("news", model, response)
        parsed = response.parsed_output if response.stop_reason != "refusal" else None
        for sig in (parsed.signals if parsed else []):
            if not 1 <= sig.article <= len(batch):
                continue
            article = batch[sig.article - 1]
            team_id = teams.get(sig.team.strip().upper())
            matches = players.get(normalize_name(sig.player), [])
            on_team = {pid for pid, t in matches if t == team_id}
            ids = on_team or {pid for pid, _ in matches}
            published = article.published_at or now
            session.add(NewsSignal(
                article_id=article.id, team_id=team_id, player_name=sig.player,
                player_id=next(iter(ids)) if len(ids) == 1 else None, kind=sig.kind,
                minutes_mult=min(max(sig.minutes_mult, 0.0), 1.4),
                usage_mult=min(max(sig.usage_mult, 0.85), 1.15),
                confidence=min(max(sig.confidence, 0.0), 1.0), summary=sig.summary,
                published_at=published,
                expires_at=published + timedelta(days=min(max(sig.days_valid, 1), 10)),
                model=model,
            ))
            stored += 1
        for a in batch:
            a.signals_extracted_at = now
        session.commit()
    return {"articles": len(articles), "signals": stored, "failed_articles": failed,
            "left_unread": len(recent) - len(articles)}


def active_signals(session: Session, as_of: datetime) -> dict[int, list[NewsSignal]]:
    """player_id -> unexpired signals published at or before the cutoff, newest first."""
    rows = session.execute(
        select(NewsSignal)
        .where(
            NewsSignal.player_id.is_not(None),
            NewsSignal.expires_at > as_of,
            or_(NewsSignal.published_at <= as_of, NewsSignal.published_at.is_(None)),
        )
        .order_by(NewsSignal.published_at.desc())
    ).scalars().all()
    out: dict[int, list[NewsSignal]] = defaultdict(list)
    for r in rows:
        out[r.player_id].append(r)
    return out


@dataclass
class PlayerView:
    out: bool = False
    uncertain: bool = False
    reason: str = ""
    adjustment: Adjustment | None = None


def player_view(signals: list) -> PlayerView:
    """Collapse a player's signals (newest first) into what the projection should do.

    The newest confident signal of each kind wins — news supersedes older news. A
    multiplier is believed in proportion to its confidence.
    """
    view = PlayerView()
    minutes = usage = 1.0
    notes: list[str] = []
    seen: set[str] = set()
    for s in signals:
        conf = float(s.confidence)
        if conf < _MIN_CONFIDENCE or s.kind in seen:
            continue
        seen.add(s.kind)
        if s.kind == "out" and conf >= _OUT_CONFIDENCE:
            return PlayerView(out=True, reason=s.summary)
        if s.kind == "out" or s.kind in _UNCERTAIN_KINDS:
            view.uncertain = True
            view.reason = view.reason or s.summary
            continue
        m = 1 + (float(s.minutes_mult) - 1) * conf
        u = 1 + (float(s.usage_mult) - 1) * conf
        if abs(m - 1) >= 0.02 or abs(u - 1) >= 0.02:
            minutes *= m
            usage *= u
            notes.append(f"news: {s.summary}")
    if notes:
        minutes = min(max(minutes, 0.6), 1.25)
        usage = min(max(usage, 0.85), 1.15)
        view.adjustment = Adjustment(
            minutes_mult=minutes,
            stat_mults=dict.fromkeys(BASE_STATS, minutes * usage),
            notes=notes,
        )
    return view
