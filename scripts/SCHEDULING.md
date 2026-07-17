# Scheduling the daily pipeline

The bot's live loop is two commands:

- `nba-bot daily-pregame` — ingest data / injuries / results → capture odds → predict today's games → record sized paper bets.
- `nba-bot daily-postgame` — ingest final scores + box scores → mark closing lines → evaluate predictions → settle bets (CLV + P&L).

Each step is fault-isolated (one flaky API call is logged and skipped, the run continues), and every run appends a one-line summary to `logs/pipeline-runs.jsonl`.

## Why local (not cloud)

`nba_api` (stats.nba.com) blocks datacenter/cloud IP ranges but works from a normal residential connection, so ingestion must run from your Mac. Local scheduling also means no secrets leave the machine. The one tradeoff — the Mac must be awake at run time — is handled below.

## Install (macOS, launchd)

```bash
scripts/schedule.sh install     # renders the plist templates with this repo's path and loads them
scripts/schedule.sh status      # confirm they're loaded
scripts/schedule.sh uninstall   # stop + remove them
```

Three jobs, at **local machine time** (defaults assume a **Pacific** machine):

| Job | Default (PT) | What runs | Why then |
|-----|---------|-----------|----------|
| postgame | 09:00 (next day) | ingest finals → mark-closing → evaluate → **settle** + CLV | all games (incl. West-coast, ~10pm PT) are final; laptop awake |
| pregame  | 10:00 | full pregame: ingest → odds → predict → **bet** | bet early so the line can move before the close is captured |
| odds     | 15:30 | `ingest-odds` only (cheap; no nba_api/Claude) | ~30 min before the ~4pm PT evening tips — becomes the **closing line** |

**Why bet-time and the odds/close capture are separate runs (CLV):** CLV — did you get a better
number than the close — only exists if you **bet early** (pregame, 10:00) and capture the line
again **near tip** (odds, 15:30). If you merge them into one run, your bet line *is* the closing
line and CLV is always ~0, which guts the metric the whole system is scored on. The morning→afternoon
gap is the CLV runway. (Games tipping before ~10am PT — a few weekend/holiday noon-ET games — won't
get bet; a fine trade for keeping CLV alive.)

**Timezone:** launchd uses local machine time, so these PT hours fire correctly once the Mac is
physically on Pacific — no change needed at move time. If you install while still on Eastern, either
wait until you've moved, or bump each `Hour` by +3 temporarily. Edit `Hour` in
`~/Library/LaunchAgents/com.nba-bot.{pregame,odds,postgame}.plist` (or the `scripts/launchd/`
templates), then re-run `scripts/schedule.sh install`.

### Keeping the Mac awake for the runs

launchd runs a missed job once on the next wake, so if the laptop is asleep at 10:00 it runs when
you next open it (a little late, but it runs). To guarantee on-time runs, schedule wakes with
`pmset` (needs `sudo`) a couple minutes before the runs:

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 09:58:00
```

The 10:00 pregame most needs an on-time wake (it locks in your bet prices); the 15:30 odds and
09:00 postgame usually land while you're already using the machine.

## Monitoring

```bash
tail -f logs/pregame-$(date +%F).log         # live console output of today's run
cat logs/pipeline-runs.jsonl | tail -5       # one-line pass/fail summary per run
nba-bot bets                                 # the paper-trade track record (CLV/ROI)
```

A healthy `pipeline-runs.jsonl` line looks like:
`{"ts": "...", "phase": "pregame", "ok": true, "steps": {"ingest": true, "odds": true, "predict": true, "bets": true}}`

## Alternative: cron

If you prefer cron, the same wrapper works — `crontab -e`:

```cron
0  9 * * * /ABSOLUTE/PATH/nba-trading-bot/scripts/run_phase.sh postgame
0 10 * * * /ABSOLUTE/PATH/nba-trading-bot/scripts/run_phase.sh pregame
30 15 * * * /ABSOLUTE/PATH/nba-trading-bot/scripts/run_phase.sh odds
```

On macOS, cron needs Full Disk Access granted to `/usr/sbin/cron` in System Settings → Privacy. launchd is the native, recommended path.

## Season-start checklist

1. Backfill/refresh once games are scheduled; confirm `nba-bot ingest` works from your connection.
2. `scripts/schedule.sh install`, adjust the hours for your timezone.
3. After a week of resolved games, re-fit calibration on the live model and save it:
   `nba-bot calibrate --model-version claude-sonnet-5 --save`
4. Watch `nba-bot bets` — CLV is the metric that matters.
