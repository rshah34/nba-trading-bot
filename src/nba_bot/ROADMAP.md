# Where the project stands

A bot that prices NBA bets on its own, compares its numbers to the sportsbooks',
records the bets it would make, texts the best ones, and grades itself afterwards.
It never places a bet.

## What runs each game day

| Run | Command | Does |
|-----|---------|------|
| daily (14:30 PT) | `daily` | final scores + box scores → settle yesterday's bets (win/loss, CLV) → injuries + news → player signals → Claude predicts each game → current game + prop odds → **pick the day's 2–3 best bets and text them** |
| close (15:30, 18:30 PT) | `daily-close` | re-read the injury report and **text an update if it undercuts a pick**; re-capture prices near tip — the closing lines the bets are graded against |

Scheduling, texts and API budgets: [scripts/SCHEDULING.md](../../scripts/SCHEDULING.md).

## The two models

**Game model** (`agents/analysis_agent.py`) — Claude reads form, rest, key players,
injuries and the strongest news reports for each game and outputs a win probability, blind to the betting
line. Measured on 300-game slices of 2025-26: about a coin flip against the market
(Brier ~0.243). Two richer inputs — a Four Factors style matchup and on-off splits for
absent players — tested no better (0.249 and 0.246) and were removed. Kept running
because it is cheap (~$0.01 a game) and the live news/injury signal can't be backtested.

**Props model** (`features/props.py`) — plain statistics, no LLM: minutes × per-minute
rate × opponent × team minutes and stat budgets × game context, as a full distribution
per stat. Backtested free on 2024-25 and 2025-26 (`props-backtest`):

- beats trailing-10 and season averages in every market but blocks (level with the season average there), unbiased, calibrated to ~1–2 pts;
- **who plays is the biggest lever** — pooled error on 2025-26 is 2.750 with no
  availability knowledge, 2.705 with the true rotation, and 2.757 with *yesterday's*
  rotation (stale availability is worse than none). Hence props are priced late, on
  fresh news, and re-checked near tip;
- added October 2026, fit on 2024-25 and confirmed on 2025-26 (2.721 → 2.705 with the
  rotation known; points+rebounds+assists 6.264 → 6.216). In order of size: a **stat
  budget** (a team's points, rebounds and assists get redistributed when the players
  who supply them are out), **layoffs** (a returning player is eased in), likely
  **blowouts**, **back-to-backs**, and home court (real but nearly invisible);
- rejected: per-player with/without splits for an absent teammate (worse than
  nothing), and widening the spread whenever minutes look uncertain (not needed once
  the means were fixed; it is widened only for a short log, a layoff or a debut).

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

**Your books.** Nine books are fetched for the price of one region and all of them set
the market's fair probability, but a bet is only made at a book in `MY_BOOKS`
(DraftKings, Hard Rock, Kalshi), at the best of their prices. When your
book hangs a different line from the rest, the others' probabilities are carried to
its line with the model's own distribution, so a line that is a point off shows up as
the edge it is. Each pick stores the worst price (and line) still worth taking.

**Late-scratch guard** (`picks.recheck`, in the close runs). A prop is re-projected
on the newest injury report and texted as a PASS if the player is out or in doubt, or
if the bet is no longer worth the price taken. A game pick gets a HEADS UP when a
24-minute player on the backed side is newly out or one returns for the opponent.
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

## The dashboard (`dashboard/`, API in `api/`)

A read-only site for exploring everything the bot knows: tonight's slate with projected
box scores, a slate screener, fantasy projections, every player's
game log with a prop explorer (set any line, see hit rates and the model's
probability), team matchup tables, the consolidated news, and the picks with their
record. See [dashboard/README.md](../../dashboard/README.md).

## Not built

- Per-market calibration of prop probabilities — needs a few weeks of live lines first.
- Choosing the prop market by evidence. Points+rebounds+assists is the one market the
  free plan buys; the single-stat guidance sent with each pick is graded, which is
  the read on whether points, rebounds or assists alone would do better.

## Known limits

- The pipeline has to run on a Mac on a home connection: NBA's data endpoints refuse
  cloud servers, and the texts go through Messages.
- Until a team has played a game that counts, its roster comes from preseason
  appearances (camp invitees included), so the minutes and stat budgets stay off.
- Backtests of the game model can't see injuries, news or odds, which exist only live.
