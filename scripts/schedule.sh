#!/usr/bin/env bash
#
# Install / remove the launchd jobs that run the daily pipeline unattended.
# Renders the plist templates with this repo's absolute path (so nothing
# machine-specific is committed) into ~/Library/LaunchAgents and loads them.
#
#   scripts/schedule.sh install     # render + load both jobs
#   scripts/schedule.sh uninstall   # unload + remove both jobs
#   scripts/schedule.sh status      # show whether they're loaded
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
JOBS=(com.nba-bot.pregame com.nba-bot.odds com.nba-bot.postgame)
ACTION="${1:-status}"

case "$ACTION" in
  install)
    mkdir -p "$AGENTS" "$REPO/logs"
    for job in "${JOBS[@]}"; do
      dest="$AGENTS/$job.plist"
      sed "s#__REPO__#$REPO#g" "$REPO/scripts/launchd/$job.plist.template" > "$dest"
      launchctl unload "$dest" 2>/dev/null || true
      launchctl load -w "$dest"
      echo "loaded $job  ($dest)"
    done
    echo "Done. Edit the Hour in each plist for your timezone, then re-run 'install'."
    ;;
  uninstall)
    for job in "${JOBS[@]}"; do
      dest="$AGENTS/$job.plist"
      launchctl unload "$dest" 2>/dev/null || true
      rm -f "$dest"
      echo "removed $job"
    done
    ;;
  status)
    launchctl list | grep -E "nba-bot" || echo "no nba-bot jobs loaded"
    ;;
  *)
    echo "usage: scripts/schedule.sh {install|uninstall|status}" >&2
    exit 2
    ;;
esac
