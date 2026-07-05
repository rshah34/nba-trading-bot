# NBA Trading Bot for 2026-27 Season

An agent pipeline that ingests live NBA data and news, produces an **independent pre-game win probability**
for each matchup, compares that to the betting market to find mispricings, and
rigorously backtests itself against closing lines.

## What it does

- **Ingests structured data** — schedules, scores, rest/back-to-backs, team & player
  box scores (`nba_api`), injury reports (ESPN), and live betting odds across
  sportsbooks (The Odds API).
- **Ingests unstructured news via RAG** — NBA news is chunked, embedded with Voyage,
  stored in `pgvector`, and semantically retrieved per matchup.
- **Predicts with an LLM agent** — for each game, Claude reasons over point-in-time
  stats, injuries, key-player production, and retrieved news to output a calibrated
  win probability + predicted margin. It is blind to the betting line, so the
  estimate is independent.
- **Finds edge** — the model's probability is compared to the **de-vigged** market
  consensus to compute the disagreement (edge).
- **Scores itself** — after games resolve, an evaluation agent computes Brier score,
  log-loss, pick accuracy, calibration, and **closing-line value (CLV)**.
- **Backtests** — replays a completed season point-in-time through the identical
  Analysis → Evaluation path, with a cost estimate before spending and a calibration
  report after.
- **Runs live** — a daily pipeline (`pregame` / `postgame` phases) orchestrates the
  whole loop for the season, with per-step error isolation for unattended operation.

---

## Architecture

```
                         ┌───────────────────────────────────────────────┐
   DATA SOURCES          │              POSTGRES + pgvector              │
   ┌──────────────┐      │  teams · games · team/player_game_stats ·     │
   │ nba_api      │─────▶│  injuries · odds · news_articles/news_chunks ·│
   │ ESPN (inj.)  │      │  predictions · prediction_evaluations         │
   │ The Odds API │      └───────────────────────────────────────────────┘
   │ ESPN RSS     │                 ▲                    │
   └──────────────┘                 │                    │
          │                         │ point-in-time      │
          ▼                         │ (as_of cutoff)     ▼
   ┌──────────────┐          ┌──────────────┐      ┌──────────────────┐
   │  DATA AGENT  │          │   ANALYSIS   │      │ EVALUATION AGENT │
   │ ingest games │          │    AGENT     │      │ Brier · log-loss │
   │ odds · news  │          │ blind win-   │─────▶│ CLV · calibration│
   │ box/player   │          │ prob + edge  │      │ (after games)    │
   └──────────────┘          └──────────────┘      └──────────────────┘
          │                         │                      │
          └──────────  BACKTEST (replay a season)  ────────┘
                 LIVE DAILY PIPELINE (pregame / postgame)
```

## Getting started

### Prerequisites
- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- Docker (for Postgres + pgvector)
- API keys: [Anthropic](https://console.anthropic.com/), [The Odds API](https://the-odds-api.com/) (free tier: 500 req/mo), [Voyage](https://www.voyageai.com/) (free tier)

### Setup

```bash
# 1. Configure secrets
cp .env.example .env
#    then fill in POSTGRES_PASSWORD (openssl rand -hex 24) + the three API keys

# 2. Start Postgres + pgvector (reads POSTGRES_* from .env)
docker compose up -d

# 3. Install dependencies
uv sync

# 4. Apply the schema (auto-applied on first container boot; safe to run anyway)
uv run nba-bot init-db
```

## Usage

All commands run via `uv run nba-bot <command>`.

### Daily operation (in season)
```bash
nba-bot daily-pregame    # ingest data/injuries/results → capture odds → predict today's games
nba-bot daily-postgame   # ingest final results/box scores → mark closing lines → evaluate
```

### Individual steps
```bash
nba-bot ingest           # teams, games (today+tomorrow), injuries, recent box/player stats
nba-bot ingest-odds      # snapshot current odds (run a few times/day to capture line movement)
nba-bot mark-closing     # flag the last pre-tip odds per book as the closing line
nba-bot ingest-news      # fetch NBA news → embed → store for RAG
nba-bot predict          # win prob + edge for today's scheduled games
nba-bot evaluate         # score resolved predictions → Brier / log-loss / CLV track record
nba-bot search-news "..."# debug RAG retrieval
```

### Backtesting
```bash
# Estimates cost first; add --run to actually spend. --slice start|mid|end, --tag labels the run.
nba-bot backtest --season 2025-26 --limit 100 --model claude-haiku-4-5 --slice mid --run
```