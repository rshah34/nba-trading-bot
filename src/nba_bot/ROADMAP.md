# Roadmap

A living checklist of where the project stands and what's next. The 2026-27 NBA
season starts in **October**, which is the real deadline — everything below is
about being ready to run a genuine forward track record when it tips off.

---

## ✅ Done

- [x] **Data layer** — games/scores/rest (`nba_api`), injuries (ESPN), odds (The Odds API), team + **player** box scores (V3)
- [x] **Storage** — Postgres + `pgvector`; full schema (teams, games, stats, injuries, odds, news, predictions, evaluations)
- [x] **News RAG** — RSS → chunk → Voyage embeddings → pgvector → point-in-time cosine retrieval
- [x] **Analysis Agent** — blind, point-in-time Claude prediction → win prob + edge vs. de-vigged market
- [x] **Evaluation Agent** — Brier, log-loss, CLV, beat-spread, calibration
- [x] **Backtesting** — season replay, `--slice`, cost estimate, calibration report, `--tag` for A/B
- [x] **Prompt tuning v2** — reason-first schema + calibration anchor + comparative features (winner acc ~0.57 → ~0.61, A/B-validated)
- [x] **Player stats infrastructure** — schema, V3 ingestion, `player_form` (dynamically-computed "star")
- [x] **Live daily pipeline** — `daily-pregame` / `daily-postgame` with per-step error isolation (offseason no-op verified)

---

## 🎯 Before the season (priority)

### 1. On-off injury-impact feature *(highest value)* — ✅ **BUILT** (`features/on_off.py`)
Quantifies a player's *real* impact from **with/without splits** rather than reputation:
- [x] Team **net rating** (pts/100 poss) **with vs. without** each key player, from `player_game_stats` × who actually played (`minutes > 0` — a 0:00 row is stored, so row-existence alone would lie).
- [x] **Regime labelling** solves the double-count: `newly_out` (form overstates the team — the actionable case), `long_term_out` (already priced into recent form — *don't* count twice), `returning` (form understates the team).
- [x] **Roster span** guard: only games from a player's first appearance for that team, so a midseason acquisition isn't blamed for October.
- [x] **Noise control**: min 3 games without / 5 games with, ≥15 mpg (deep-bench absences don't move a line), delta **shrunk** by sample size (`×n/(n+5)`), and avg opponent strength faced per split surfaced so the model can discount.
- [x] **Name matching**: `normalize_name` folds accents (Dončić→doncic), suffixes (Jr./III), punctuation to join ESPN injury names to box-score names.
- [x] Surfaced into the Analysis Agent prompt (ON-OFF section) + system prompt teaches the regime logic. Verified live: correctly ranks Markkanen (36 mpg, 32g/5g) first and filters a 1-game fringe player that naive `|delta|` sorting put on top.
- [ ] **A/B it** — see the oracle-availability ceiling test under Modeling experiments.
- *Why:* this is what makes the live pipeline beat the stats-only backtest ceiling.

### 2. Dress rehearsal
- [ ] Run `daily-pregame` / `daily-postgame` against a **seeded game-day** (verify the real predict → evaluate path fires, not just the offseason no-op).
- [ ] Re-run once **preseason games** appear (late September) against real live data.

### 3. Scheduling / deployment
- [ ] Decide: local `cron` (simple, needs the machine awake) vs. cloud (VM / GitHub Actions cron — runs unattended).
- [ ] Wire the two phases to fire at the right times (morning ingest+odds+preview, pre-tip final predict, post-game evaluate).
- [ ] Add a persistent daily log / run summary for unattended monitoring.

---

## 🧪 Modeling experiments (measurable via the backtest A/B loop)

### Stylistic matchup features (Four Factors + pace) *(the "team-strategy" signal)*
The team-strategy/trends idea, done in a way that is **orthogonal to net margin**.
A single strength scalar can't represent *how* a team wins or a style clash — a
three-happy offense vs. an elite perimeter defense, a fast team vs. a grind-it-out
team, an offensive-rebounding team vs. a poor defensive-rebounding one. Give the
model each team's **style profile** and the key mismatches, and let it reason about
the clash (which plays to the LLM's strength).

- **Data layer (prerequisite):** ✅ **DONE** (migration 004 + `store_box_score`)
  - [x] Extended `team_game_stats` with the raw counts the Four Factors need — `fgm/fga`, `fg3m/fg3a`, `ftm/fta`, `oreb`, `dreb`, `steals`, `blocks`. Both teams' rows are ingested per game, so *defensive* factors = the opponent's offensive row joined on `game_id`.
  - [x] V3 ingestion populates them: `data_agent.store_box_score` fetches the V3 box **once** and writes both `player_game_stats` and the authoritative team box (OREB/DREB split included). Retrofit old games with `nba-bot backfill-team-box --season 2025-26`.
- **Feature computation (point-in-time, games ≤ `as_of`):** ✅ **DONE** (`features/four_factors.py`)
  - [x] Per team, **offense and defense**: pace, eFG%, TOV%, OREB% (defense derived from the opponents' rows in the same games), FT rate — Dean Oliver's Four Factors. Rates aggregated Σnum/Σden across the window.
  - [x] Matchup deltas surfaced explicitly (each offense vs. the other's defense; OREB% vs. opp DREB%; pace vs. pace).
- **Integration & test:**
  - [x] STYLE section (both profiles + key mismatches) wired into the Analysis Agent prompt; degrades gracefully to form-only when raw counts are absent. System prompt updated to reason about the clash. Unit-tested (`tests/test_four_factors.py`); verified live.
  - [ ] **A/B via the backtest on a ~300-game slice** — PENDING: needs (1) `nba-bot backfill-team-box --season 2025-26` to populate historical raw counts, then (2) a tagged backtest run vs. the champion.
- *Why / honest caveat:* like other team-trend features this **may test neutral offline** (stats-only, no market to beat), but it's the feature most likely to be *orthogonal* to margin, and it compounds with the live injury/news signals — a style hole plus the injured player who plugs it is exactly where live edge appears. Complements **opponent-adjusted strength** below (both want richer team stats).

### Oracle-availability ceiling test (how to A/B on-off offline)
No historical injury reports exist, so a backtest can't know pre-tip availability. But for a
*completed* game we know who played, so `--oracle-injuries` derives absences from the game's own
box score (`on_off.oracle_absences`).
- [ ] Run a tagged A/B: `nba-bot backtest --season 2025-26 --limit 300 --slice mid --tag onoff-oracle --oracle-injuries --run` vs. the champion.
- *Read it honestly:* this peeks at the game and assumes **perfect** pre-tip knowledge (reality has game-time decisions), so it measures the feature's **ceiling**, never a track record. Its value is cheap falsification — if on-off can't help *with* perfect availability info, it won't help live.

- [ ] **Opponent-adjusted strength** — net rating / margin adjusted for schedule strength (raw margin ignores *who* you played). More principled than streak/momentum (which tested neutral).
- [ ] **Calibration layer** — fit Platt/isotonic on backtest predictions to correct systematic over/under-confidence, apply to live output.
- [ ] **Hybrid model** — blend the LLM estimate with a simple logistic-regression baseline (margin diff + rest + home) for a calibrated floor + LLM nuance.
- [ ] **Bigger-slice A/B** — n=100 can't resolve small effects; a decisive feature test needs ~300+ games (budget-aware).
- [ ] **Model transfer check** — confirm v2/v4 gains hold on Sonnet (live model), not just Haiku (backtest model).

---

## 🏗️ Infrastructure & hardening

- [ ] **CLV in backtest** — requires a historical odds dataset (free tier is live-only); optional, since CLV is best measured live.
- [x] **Injury name matching** — done: `features/on_off.normalize_name` (accents, suffixes, punctuation). Unmatched names are skipped; worth logging misses once live data flows.
- [ ] **Odds budget** — free tier is 500 req/month (~16/day); keep snapshots to a few per day.
- [ ] **Multiple predictions per game** — early "preview" + "final" near tip (schema already supports via `as_of`); wire into the pipeline timing.
- [ ] **Observability** — a `report`/dashboard command or artifact (calibration curve, accuracy over time, CLV distribution) for the season.

---

## 📊 Output & storytelling

- [ ] **Visual calibration report** — shareable calibration curve + accuracy-over-time + CLV distribution (portfolio piece).
- [ ] **Daily report** — tonight's games, model pick + confidence, edge vs. current line.
- [ ] **Season ROI analysis** — "what if I bet every game where edge > X" (paper-trading P&L + CLV).

---

## ⚠️ Known limitations (be honest about these)

- The **backtest measures the stats-only ceiling** — injuries, news, and market are live-only, so historical results understate the full system.
- Stats+rest alone is ≈ coin-flip on a large mid-season sample; the edge must come from the live signals.
- News/injury *report* history isn't reconstructable for free → forward paper-trading is the true evaluation.
- `nba_api` can be rate-limited / IP-blocked from datacenter IPs (works from residential connections).

---

### Components (`src/nba_bot/`)

| Module | Responsibility |
|--------|----------------|
| `data/` | API clients: `nba_stats` (games, box scores V3), `injuries` (ESPN), `odds_api` (The Odds API), `news_feeds` (RSS + team tagging), `embeddings` (Voyage) |
| `agents/data_agent.py` | Ingest teams, games, injuries, team+player box scores, odds; mark closing lines |
| `agents/analysis_agent.py` | Point-in-time context assembly → Claude structured prediction → edge vs. market |
| `agents/evaluation_agent.py` | Score resolved predictions (Brier, log-loss, CLV, beat-spread) |
| `rag/` | `ingest` (news → chunk → embed → store), `retrieval` (cosine KNN, point-in-time windowed) |
| `markets.py` | De-vig odds → fair probability, consensus line across books |
| `backtest/` | `loader` (season + player backfill), `runner` (predict+evaluate a slice, cost estimate), `report` (calibration) |
| `pipeline.py` | Daily orchestration: `run_pregame` / `run_postgame` with per-step error isolation |
| `cli/main.py` | Typer CLI (`nba-bot ...`) |

---

### Design principles

- **Point-in-time integrity.** Every prediction is stamped with an `as_of` cutoff, and
  all context (form, injuries, news, odds) is filtered to what was known at that moment.
  Backtests can't peek at the future, and even the news RAG window is bounded to game day.
- **Blind to the line.** The model never sees the betting odds. Its probability is an
  *independent* estimate, so "edge vs. market" and CLV are honest, not circular.
- **CLV as the north star.** Beating the closing line is the strongest evidence of a
  durable edge, and it inherently accounts for late-breaking news (which the market prices in).

---

## Tech stack

Python 3.11+ · PostgreSQL + `pgvector` (Docker) · SQLAlchemy · Pydantic Settings ·
Typer CLI · [Claude API](https://console.anthropic.com/) (`claude-sonnet-5`) ·
[Voyage](https://www.voyageai.com/) embeddings (`voyage-3`) · `nba_api` ·
[The Odds API](https://the-odds-api.com/) · `feedparser` · `uv` for env/deps.

---

## Results & honest findings

The backtest deliberately uses **only what can be reconstructed point-in-time** —
schedule, scores, rest, recent form, and player production. Injuries, news, and market
odds are **live-only** (historical injury reports and news archives don't exist for
free), so the backtest measures the *stats-only ceiling*.

- Prompt tuning (reason-first structured output + calibration anchor + comparative
  features) moved winner accuracy from ~0.57 → **~0.61** and improved Brier/log-loss on
  a fixed evaluation slice — validated with a rigorous A/B loop.
- Team-trend and player-production enrichments came out **neutral offline** — and that's
  the key finding: with no injuries/news/market, team net-margin already captures most of
  the predictable signal. The real edge lives in the **live** signals a backtest can't see.

This is *why* the live forward pipeline (with injuries + news + market + player-impact
combined) is the true test — and why the honest conclusion is stated plainly rather than
cherry-picked.

---
