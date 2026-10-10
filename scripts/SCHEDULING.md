# Scheduling the daily pipeline

Two commands. Every step inside a run is fault-isolated (a flaky API call is logged and skipped, the run continues), and every run appends a one-line summary to `logs/pipeline-runs.jsonl`.

| Run | Command | What it does |
|-----|---------|--------------|
| daily | `nba-bot daily` | final scores + box scores → **settle** yesterday's bets (CLV + P&L) → injuries + news → player signals → Claude predicts each game → current game + prop odds → **choose the day's 2–3 picks** → **text them** |
| close | `nba-bot daily-close` | re-capture game lines + the prop markets we hold → these become the **closing prices** |

## Why local (not cloud)

`nba_api` (stats.nba.com) blocks datacenter/cloud IP ranges but works from a normal residential connection, so ingestion must run from your Mac. Local scheduling also means no secrets leave the machine, and it is what lets the bot text you through the Mac's own Messages app.

## Install (macOS, launchd)

```bash
scripts/schedule.sh install     # renders the plist templates with this repo's path and loads them
scripts/schedule.sh status      # confirm they're loaded
scripts/schedule.sh uninstall   # stop + remove them
```

Two jobs, at **local machine time** (defaults assume a **Pacific** machine):

| Job | Default (PT) | Runs | Why then |
|-----|--------------|------|----------|
| daily | 14:30 | `daily` | ~90 min before the first evening tips; **the best-bets text** |
| close | 15:30 and 18:30 | `daily-close` | before the eastern and the western tips — the closing prices |

**Why one run.** It is built for a laptop that is reliably awake in the afternoon and not much else. Everything the picks depend on — last night's results, the injury report, the news, the game predictions — is refreshed in the same run, minutes before the prices are taken, so nothing rests on an earlier job having happened.

**The picks.** Each day the bot records and texts at most `MAX_DAILY_BETS` bets (default 3), chosen across game winners and player props by edge — the probability it would size on, minus the market's. When both kinds qualify, the best of each is included. They are chosen in the afternoon because props are won on knowing who plays, which firms up through the day (with *stale* availability the projections are worse than with none), and because the price recorded is then the one on offer when the text reaches you.

**Afternoon games are not bet.** A game that tips before the run (weekend and holiday matinees) is skipped: no prediction, no pick. To cover them, move the daily job's `Hour` earlier on those days or run `nba-bot daily` by hand in the morning; the afternoon run then keeps to the same daily cap and doesn't pay again for games already predicted.

**Why the close is its own run.** CLV — did you get a better number than the close — only exists if the bet and the close are different snapshots. Merge them and CLV is always ~0, which guts the metric the whole system is scored on.

**Timezone:** launchd uses local machine time. If the Mac isn't on Pacific, edit `Hour` in the `scripts/launchd/` templates and re-run `scripts/schedule.sh install`.

### When the Mac is asleep at run time

A sleeping Mac runs nothing. launchd fires each missed job once, together, the next time it wakes, so `scripts/run_phase.sh` queues them and runs the daily run first, then the close. It also waits for Wi-Fi, starts Docker if the database isn't up, and holds the Mac awake until the run finishes.

What a late run can and can't recover:

- **Results and settlement** — always, just later. The run reaches back to the oldest game still waiting on a result (up to two weeks), so a few days with the laptop shut are caught up on the next run.
- **Bets** — only on games that haven't tipped yet. Wake up after tip and that game is skipped.
- **Closing prices** — only if a `close` run lands between the bet and the tip; otherwise that bet has no CLV.

To have the runs happen on time with nobody at the keyboard, the Mac has to stay awake from mid-afternoon to the last close run: leave it **plugged in with the lid open** and turn on System Settings → Battery → Options → *Prevent automatic sleeping on power adapter when the display is off* (the display still sleeps). A closed lid sleeps the Mac regardless.

If the laptop is usually out of the house or shut in the afternoon, this needs an always-on machine on a home connection instead — any spare Mac running the same setup (the texts go through Messages, so it has to be a Mac). A cloud server is not a drop-in: NBA's data endpoints refuse datacenter addresses.

## Getting the texts

Set these in `.env`, then run `nba-bot notify-test`:

```bash
NOTIFY_IMESSAGE_TO=+15551234567     # your own number or Apple ID
BANKROLL=2000                       # optional: shows stakes in dollars too
```

The bot sends one kind of text: the day's best bets (or "No bets today", so silence means it didn't run). It goes out as an iMessage through the Messages app on this Mac — no SMS provider, no cost. The first send triggers a one-time macOS prompt to let the terminal control Messages; accept it.

Every digest is also written to `logs/digest-<date>.txt`, so a failed send loses nothing. `nba-bot digest` previews today's text (`--send` delivers it) and `nba-bot digest --results` shows how last night's bets finished.

## Claude API cost

Two things call Claude, and both are logged per call to `logs/api-usage.jsonl` (`nba-bot costs` totals it):

| Call | When | Model | Measured cost |
|------|------|-------|---------------|
| Game prediction | once per game per day | `ANALYSIS_MODEL` (claude-sonnet-5) | ~$0.01 a game, so under $0.10 on a full night |
| News reading | new articles about teams playing today (each article once) | `NEWS_MODEL` (claude-haiku-5-5) | ~$0.02 per 140 articles |

Nothing is paid for twice: a game already predicted today is skipped on a re-run, each article is read once, and on a day with no games the run only settles results — no model is called and no odds are bought. `claude-opus-5-5` as `NEWS_MODEL` reads the same articles for ~40x the price; on a side-by-side it flagged 17 players out to Haiku's 15, agreeing on 12.

## Odds API budget

Game lines cost one credit per market per snapshot. Props cost **one credit per market per game**, which is what makes them expensive.

**On the free plan (500 credits/month)** — the shipped `.env.example` settings:

| Purchase | Setting | Credits / month (≈7.5 games a night) |
|----------|---------|--------------------------------------|
| Game lines, moneyline only, 3 snapshots a day | `GAME_MARKETS=h2h` | ~90 |
| Props, one market, one snapshot per game | `PROP_MARKETS=player_points_rebounds_assists` | ~225 |
| Closing prices for the prop bets held | automatic | whatever is left, ~100–135 |

One prop market is the most the free plan holds; a second would take ~450 on its own. Points+rebounds+assists is the one to keep: it covers the three main stats for one credit, and it is where the model beat the naive baseline by the most in the season backtest.

The bot paces itself so the month never runs dry: each game is bought once, no game lines are bought on a day with nothing to play, and the closing snapshot — the only optional purchase — runs only while the rest of the month's essentials are still covered. If credits get tight you lose CLV on some prop bets, never the bets themselves. `ODDS_QUOTA_RESERVE` is a last-resort floor that keeps game lines alive.

**On a paid plan**, unset `PROP_MARKETS` and `GAME_MARKETS` to price all 12 prop markets and spreads/totals: about 3,500 credits a month.

## Monitoring

```bash
tail -f logs/daily-$(date +%F).log           # live console output of today's run
tail -5 logs/pipeline-runs.jsonl             # one-line pass/fail summary per run
nba-bot props-record                         # prop paper record: CLV, ROI, model vs. market
nba-bot bets                                 # game-line paper record
```

## Alternative: cron

```cron
30 14 * * * /ABSOLUTE/PATH/nba-trading-bot/scripts/run_phase.sh daily
30 15,18 * * * /ABSOLUTE/PATH/nba-trading-bot/scripts/run_phase.sh close
```

On macOS, cron needs Full Disk Access granted to `/usr/sbin/cron` in System Settings → Privacy. launchd is the native, recommended path.

## Season-start checklist

1. Docker running (`docker compose up -d`), then `nba-bot ingest` to confirm it works from your connection.
2. `nba-bot notify-test` — you should get a text.
3. `scripts/schedule.sh install`.
4. Props stay unbet for each player's first 5 games with his team (opening-week projections lean on last season's role); every line is still priced and logged. `nba-bot props-record` shows the model vs. the market on those lines from night one.
5. After a week of resolved games, re-fit game calibration on the live model: `nba-bot calibrate --model-version claude-sonnet-5 --save`.
6. Judge by CLV, not by a week of P&L.
