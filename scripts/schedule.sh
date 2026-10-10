#!/usr/bin/env bash
#
# Install / remove the launchd jobs that run the daily pipeline unattended.
# Renders the plist templates with this repo's absolute path (so nothing
# machine-specific is committed) into ~/Library/LaunchAgents and loads them.
#
#   scripts/schedule.sh install     # replace whatever is installed with the current jobs
#   scripts/schedule.sh uninstall   # unload + remove every job
#   scripts/schedule.sh status      # show whether they're loaded
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
JOBS=(com.nba-bot.daily com.nba-bot.close)
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
    echo "Done. Edit the Hour in each plist for your timezone, then re-run 'install'."
    ;;
  uninstall)
    remove_all
    ;;
  status)
    launchctl list | grep -E "nba-bot" || echo "no nba-bot jobs loaded"
    ;;
  *)
    echo "usage: scripts/schedule.sh {install|uninstall|status}" >&2
    exit 2
    ;;
esac
