# Where the project stands

A bot that prices NBA bets on its own, compares its numbers to the sportsbooks',
records the bets it would make, texts the best ones, and grades itself afterwards.
It never places a bet.

## What runs each game day

| Phase | Command | Does |
|-------|---------|------|
| pregame | `daily-pregame` | results, injuries, news → Claude predicts each game |
| props | `daily-props` | fresh injuries + news → player signals → current game + prop odds → **pick the day's 2–3 best bets and text them** |
| close | `daily-close` | re-capture prices near tip — the closing lines the bets are graded against |
| postgame | `daily-postgame` | final scores + box scores → settle bets (win/loss, CLV) |

Scheduling, texts and API budgets: [scripts/SCHEDULING.md](../../scripts/SCHEDULING.md).

## The two models

**Game model** (`agents/analysis_agent.py`) — Claude reads form, rest, key players,
injuries and the strongest news reports for each game and outputs a win probability, blind to the betting
line. Measured on 300-game slices of 2025-26: about a coin flip against the market
(Brier ~0.243). Two richer inputs — a Four Factors style matchup and on-off splits for
absent players — tested no better (0.249 and 0.246) and were removed. Kept running
because it is cheap (~$0.01 a game) and the live news/injury signal can't be backtested.

**Props model** (`features/props.py`) — plain statistics, no LLM: minutes × per-minute
rate × opponent × a team minutes budget, as a full distribution per stat. Backtested
free on all of 2025-26 (`props-backtest`):

- beats trailing-10 and season averages in all 12 markets, unbiased, calibrated to ~1–2 pts;
- **who plays is the biggest lever** — pooled error 2.740 with no availability
  knowledge, 2.703 with the true rotation, and 2.747 with *yesterday's* rotation
  (stale availability is worse than none). Hence props are priced late, on fresh news;
- rejected: per-player with/without splits for an absent teammate (worse than nothing).

The backtest measures projection quality, not profit: there are no free historical prop
lines. Whether it beats a sportsbook can only be shown live.

## How to judge it once the season starts

- `nba-bot props-record` — prop bets (record, ROI, CLV) and, more telling early on, the
  model against the market on every line it priced: if a 50/50 blend of the two beats
  the market's own Brier score, the model is adding information.
- `nba-bot bets` — the same for game winners.
- `nba-bot costs` — what the Claude API has actually cost.
- Wait for 100+ settled prop bets that beat the closing price on average before
  considering real money. A good week is noise.

## The daily picks (`picks.py`)

At most three bets a day, across game winners and props, ranked by edge — the
probability the bot would size on minus the market's. When both kinds qualify the best
of each is in. Only the picks are recorded, so the paper record is what was texted.
Each prop pick carries single-stat guidance — the points, rebounds and assists lines
at which the model alone would take the over or the under — graded afterwards in
`props-record`.

**News** (`agents/news_agent.py`): each article is read once into player signals;
reports of the same thing are consolidated, and a later report replaces an earlier one
only if it is about as firm, so a thin new item can't displace a well-sourced one.

Safeguards: every bet is sized on a blend of model and market, not the raw model (props
half and half; games three parts market, since that model is the weaker one); an edge
too large to be believed is skipped; one bet per player; at most 5% of bankroll on any
one game; no bet on a player in his first 5 games with a team; a team with a
questionable rotation player is priced both ways and only bets that survive both are kept.

## Running on the free plans

- Odds API (500 credits/month): one prop market (points+rebounds+assists) and
  moneyline-only game lines; closing prices are bought only with what is left over.
- Claude: Haiku reads the news (~a cent a day), Sonnet predicts games (~$3 a month).

## Not built

- Props on the dashboard (it shows game predictions and game bets only).
- A "returning player" adjustment beyond what the news signals carry.
- Per-market calibration of prop probabilities — needs a few weeks of live lines first.

## Known limits

- The pipeline has to run on a Mac on a home connection: NBA's data endpoints refuse
  cloud servers, and the texts go through Messages.
- Opening-week rotations come from preseason appearances and are unreliable.
- Backtests of the game model can't see injuries, news or odds, which exist only live.
