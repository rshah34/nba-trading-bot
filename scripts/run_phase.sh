#!/usr/bin/env bash
#
# Run one daily pipeline phase under a scheduler (launchd/cron), logging to a
# dated file. Usage: run_phase.sh {pregame|postgame}
#
# launchd/cron give a minimal environment, so we resolve the repo, load a login
# shell PATH, and cd in before invoking uv.
set -euo pipefail

PHASE="${1:?usage: run_phase.sh {pregame|odds|postgame}}"
case "$PHASE" in
  pregame)  CMD="daily-pregame" ;;   # ingest → odds → predict → bet
  odds)     CMD="ingest-odds" ;;     # near-tip odds snapshot → becomes the closing line (for CLV)
  postgame) CMD="daily-postgame" ;;  # ingest finals → mark-closing → evaluate → settle
  *) echo "unknown phase: $PHASE (expected pregame|odds|postgame)" >&2; exit 2 ;;
esac

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# Common install locations for uv; launchd starts with a bare PATH.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

mkdir -p logs
LOG="logs/${PHASE}-$(date +%Y-%m-%d).log"
{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') : ${PHASE} (nba-bot ${CMD}) ====="
  uv run nba-bot "$CMD"
  echo
} >> "$LOG" 2>&1
