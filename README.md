# NBA Trading Bot for 2026-27 Season

An agent pipeline that ingests live NBA data and news, prices **game lines and player props**
independently of the betting market, bets (on paper) where it disagrees, texts you the day's
best bets, and scores itself against closing lines.

## What it does

- **Ingests structured data** — schedules, scores, rest/back-to-backs, team & player
  box scores (`nba_api`), injury reports (ESPN), and live betting odds across
  sportsbooks (The Odds API).
- **Predicts with an LLM agent** — for each game, Claude reasons over point-in-time
  stats, injuries, key-player production, and the strongest news reports to output a calibrated
  win probability + predicted margin. It is blind to the betting line, so the
  estimate is independent.
- **Finds edge** — the model's probability is compared to the **de-vigged** market
  consensus to compute the disagreement (edge).
- **Scores itself** — after games resolve, an evaluation agent computes Brier score,
  log-loss, pick accuracy, calibration, and **closing-line value (CLV)**.
- **Backtests** — replays a completed season point-in-time through the identical
  Analysis → Evaluation path, with a cost estimate before spending and a calibration
  report after.
- **Prices player props** — a statistical projection for every player stat (points,
  rebounds, assists, threes, steals, blocks, turnovers and the combos) as a full
  distribution: minutes × per-minute rate × opponent × a team **minutes budget** and
  **stat budget** that hand playing time and production to whoever is left when
  rotation players are out, plus the schedule (back-to-backs, layoffs, likely
  blowouts). Backtestable on two full seasons for free (`nba-bot props-backtest`).
- **Reads the news** — eight feeds, full article text. Claude turns each new article
  into structured player signals (out, minutes limit, role change, returning). Reports
  of the same thing are consolidated and ranked by how firmly and widely they are
  sourced, then feed both models. The injury report only says who is out; the news
  says who is limited, starting, or resting.
- **Makes a few picks a day** — at most three bets across game winners and props,
  ranked by edge at current prices, sized by fractional Kelly on a probability blended
  toward the market, and settled against the result with CLV. Every book quoted sets
  the market; the bet itself is only made at the books you use (`MY_BOOKS`). Prop picks
  come with single-stat guidance (the points / rebounds / assists lines the model would bet).
- **Texts you** — the day's best bets before tip, as an iMessage sent from the Mac it
  runs on: each with a one-line reason and how far the price can move before it is a
  pass. A second text follows only if a late injury change undercuts a pick.
- **Runs live** — one scheduled afternoon run does the whole loop (settle, read, predict,
  pick, text), plus a near-tip run that re-checks the injury report and snapshots
  closing prices, with per-step error isolation.

---

## Architecture

```
                         ┌───────────────────────────────────────────────┐
   DATA SOURCES          │                   POSTGRES                    │
   ┌──────────────┐      │  teams · games · team/player_game_stats ·     │
   │ nba_api      │─────▶│  injuries · odds · news_articles/news_chunks ·│
   │ ESPN (inj.)  │      │  predictions · prediction_evaluations         │
   │ The Odds API │      └───────────────────────────────────────────────┘
   │ 8 news feeds │                 ▲                    │
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
         LIVE DAILY PIPELINE (one afternoon run + closing snapshot)
```

## Getting started

### Prerequisites
- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- Docker (for Postgres)
- API keys: [Anthropic](https://console.anthropic.com/), [The Odds API](https://the-odds-api.com/) (free tier: 500 req/mo)

### Setup

```bash
# 1. Configure secrets
cp .env.example .env
#    then fill in POSTGRES_PASSWORD (openssl rand -hex 24) + the two API keys

# 2. Start Postgres (reads POSTGRES_* from .env)
docker compose up -d

# 3. Install dependencies
uv sync

# 4. Apply the schema (every migration, in order; safe to re-run after pulling new ones)
uv run nba-bot init-db
```

## Usage

All commands run via `uv run nba-bot <command>`.

### Daily operation (in season)
```bash
nba-bot daily            # settle finished games → injuries + news → predict → current odds → 2–3 picks → text them
nba-bot daily-close      # near tip: re-check injuries (text if a pick is undercut) + the closing prices CLV is measured against
```
`scripts/schedule.sh install` runs these on a timer — see [scripts/SCHEDULING.md](scripts/SCHEDULING.md),
which also covers setting up the texts.

### Player props
```bash
nba-bot props-backtest   # score the projection model on a full season (free, no API calls)
nba-bot picks            # preview today's picks across games + props (--refresh-odds, --record)
nba-bot props-record     # paper record: CLV, ROI, and the model vs. the market on every line
nba-bot extract-signals  # read new articles into player signals
nba-bot digest           # print the best-bets text (--results for last night, --send to text it)
nba-bot notify-test      # send a test text
nba-bot costs            # what the Claude API has actually cost, by purpose and day
```

### Individual steps
```bash
nba-bot ingest           # teams, games (today+tomorrow), injuries, recent box/player stats
nba-bot ingest-odds      # snapshot current odds (run a few times/day to capture line movement)
nba-bot mark-closing     # flag the last pre-tip odds per book as the closing line
nba-bot ingest-news      # fetch NBA news from the RSS feeds
nba-bot predict          # win prob + edge for today's scheduled games
nba-bot evaluate         # score resolved predictions → Brier / log-loss / CLV track record
```

### Dashboard
```bash
nba-bot serve                 # read-only API on http://127.0.0.1:8000
cd dashboard && npm run dev   # the site on http://localhost:3000
```
Tonight's slate and picks, each game's injury report and projected box score, a projections page
that ranks every player on the slate in any stat, fantasy projections, a prop explorer for testing any
line against a player's game log and the model, team matchup tables, the news
reports, and the picks with their record. See [dashboard/README.md](dashboard/README.md).

### Backtesting
```bash
# First time on a past season: load it, then fetch its box scores (one call per game).
nba-bot backfill-box-scores --season 2025-26
# Estimates cost first; add --run to actually spend. --slice start|mid|end, --tag labels the run.
nba-bot backtest --season 2025-26 --limit 100 --model claude-haiku-4-5 --slice mid --run
```