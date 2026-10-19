#!/usr/bin/env bash
#
# Install / remove the launchd job that runs the daily pipeline unattended.
# Renders the plist template with this repo's absolute path (so nothing
# machine-specific is committed) into ~/Library/LaunchAgents and loads them.
#
#   scripts/schedule.sh install     # replace whatever is installed with the current job
#   scripts/schedule.sh uninstall   # unload + remove every job
#   scripts/schedule.sh status      # show whether it's loaded, and the last runs
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
JOBS=(com.nba-bot.runner)
ACTION="${1:-status}"

# Unload and delete every nba-bot job that is installed, whatever set it came from.
remove_all() {
  local dest
  for dest in "$AGENTS"/com.nba-bot.*.plist; do
    [ -e "$dest" ] || continue
    launchctl unload "$dest" 2>/dev/null || true
    rm -f "$dest"
    echo "removed $(basename "$dest" .plist)"
  done
}

case "$ACTION" in
  install)
    mkdir -p "$AGENTS" "$REPO/logs"
    remove_all
    for job in "${JOBS[@]}"; do
      dest="$AGENTS/$job.plist"
      sed "s#__REPO__#$REPO#g" "$REPO/scripts/launchd/$job.plist.template" > "$dest"
      launchctl load -w "$dest"
      echo "loaded $job  ($dest)"
    done
    # Nothing is owed from before the install: a job's first run is its next time.
    for name in daily close; do
      [ -e "$REPO/logs/.last-$name" ] || date +%s > "$REPO/logs/.last-$name"
    done
    echo "Done. Runs at 14:30 (daily) and 15:30 + 18:30 (close), Pacific time."
    ;;
  uninstall)
    remove_all
    ;;
  status)
    launchctl list | grep -E "nba-bot" || echo "no nba-bot jobs loaded"
    tail -3 "$REPO/logs/pipeline-runs.jsonl" 2>/dev/null || true
    ;;
  *)
    echo "usage: scripts/schedule.sh {install|uninstall|status}" >&2
    exit 2
    ;;
esac
