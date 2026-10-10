#!/usr/bin/env bash
#
# Run one of the day's two jobs under a scheduler (launchd/cron), logging to a
# dated file. Usage: run_phase.sh daily|close
#
# Built for a laptop that is often asleep at the scheduled time. launchd fires
# every missed job at once on the next wake, so this wrapper:
#   - queues the jobs and runs them one at a time, the daily run first;
#   - waits for the network, and starts Docker/Postgres if they aren't up;
#   - keeps the Mac awake until the phase finishes.
# launchd/cron also give a minimal environment, so it resolves the repo and PATH.
set -euo pipefail

PHASE="${1:?usage: run_phase.sh daily|close}"
# The number is the order they run in when both are due at the same moment.
case "$PHASE" in
  daily) ORDER=1; CMD=(daily) ;;         # settle -> injuries + news -> predict -> odds -> picks -> text
  close) ORDER=2; CMD=(daily-close) ;;   # near-tip snapshot -> the closing prices CLV uses
  *) echo "unknown phase: $PHASE (expected daily|close)" >&2; exit 2 ;;
esac

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# Common install locations for uv and docker; launchd starts with a bare PATH.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"

mkdir -p logs/.queue
LOG="logs/${PHASE}-$(date +%Y-%m-%d).log"
QUEUE="logs/.queue"
LOCK="logs/.run.lock"
TICKET="$QUEUE/${ORDER}-${PHASE}.$$"
HELD=0

say() { echo "$(date '+%H:%M:%S') [$PHASE] $*" >> "$LOG"; }

cleanup() {
  rm -f "$TICKET"
  [ "$HELD" = 1 ] && rm -rf "$LOCK"
  return 0
}
trap cleanup EXIT

# Drop tickets and locks left behind by a run that died (crash, power loss).
reap() {
  local f pid
  for f in "$QUEUE"/*; do
    [ -e "$f" ] || continue
    pid="${f##*.}"
    kill -0 "$pid" 2>/dev/null || rm -f "$f"
  done
  if [ -d "$LOCK" ]; then
    pid="$(cat "$LOCK/pid" 2>/dev/null || true)"
    { [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; } || rm -rf "$LOCK"
  fi
}

: > "$TICKET"
sleep "${NBA_BOT_SETTLE_SECONDS:-5}"   # let every job fired by the same wake join the queue first
WAITED=0
while :; do
  reap
  FIRST="$(ls "$QUEUE" | sort | head -1)"
  if [ "$FIRST" = "$(basename "$TICKET")" ] && mkdir "$LOCK" 2>/dev/null; then
    echo $$ > "$LOCK/pid"
    HELD=1
    break
  fi
  if [ "$WAITED" -ge 5400 ]; then
    say "gave up after 90 min waiting for the other job to finish"
    exit 1
  fi
  sleep 5
  WAITED=$((WAITED + 5))
done
[ "$WAITED" -gt 0 ] && say "waited ${WAITED}s for the other job"

# Wi-Fi takes a few seconds to come back after a wake.
for _ in $(seq 1 24); do
  curl -s --max-time 5 -o /dev/null https://site.api.espn.com && break
  sleep 5
done

# Postgres lives in Docker, which may not be running after a restart.
db_ready() { docker compose exec -T db sh -c 'pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; }
if ! db_ready; then
  say "database not up; starting Docker"
  docker info >/dev/null 2>&1 || open -ga Docker
  for _ in $(seq 1 60); do
    docker info >/dev/null 2>&1 && break
    sleep 5
  done
  docker compose up -d >> "$LOG" 2>&1 || true
  for _ in $(seq 1 24); do
    db_ready && break
    sleep 5
  done
  db_ready || say "database still not reachable; running anyway so the failure is logged"
fi

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') : ${PHASE} (nba-bot ${CMD[*]}) ====="
  if [ -n "${NBA_BOT_DRY_RUN:-}" ]; then
    # Exercises the queueing and checks above without running the pipeline.
    echo "dry run: would run nba-bot ${CMD[*]}"
    sleep "${NBA_BOT_DRY_RUN}"
  else
    # caffeinate holds off idle sleep until the run is done.
    caffeinate -i uv run nba-bot "${CMD[@]}"
  fi
  echo
} >> "$LOG" 2>&1
