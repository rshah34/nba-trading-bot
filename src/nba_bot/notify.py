"""Text the daily digest to a phone through the Mac's own Messages app.

The pipeline already has to run on this Mac, so no SMS provider is needed: an
AppleScript hands the message to Messages, which sends it as an iMessage. The first
send prompts once for permission to control Messages.

A failed send never fails the pipeline: the digest is always written to
`logs/digest-<date>.txt` first.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import date
from pathlib import Path

from nba_bot.config import settings

log = logging.getLogger("nba_bot.notify")

# Text and recipient travel as arguments, never spliced into the script, so a
# quote or newline in a player name can't break (or inject into) the AppleScript.
_IMESSAGE_SCRIPT = """on run argv
    tell application "Messages"
        set svc to 1st account whose service type = iMessage
        send (item 1 of argv) to participant (item 2 of argv) of svc
    end tell
end run"""


def send(text: str, log_dir: str | Path = "logs", kind: str = "digest") -> str:
    """Write the message to disk, then text it. Returns "ok", "off" (no recipient
    configured), or the error — it never raises on a delivery failure."""
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{kind}-{date.today():%Y-%m-%d}.txt").write_text(text + "\n")

    if not settings.notify_imessage_to:
        return "off"
    try:
        subprocess.run(
            ["osascript", "-e", _IMESSAGE_SCRIPT, text, settings.notify_imessage_to],
            check=True, capture_output=True, text=True, timeout=60,
        )
        return "ok"
    except subprocess.CalledProcessError as exc:
        result = f"osascript failed: {exc.stderr.strip() or exc}"
    except Exception as exc:  # noqa: BLE001 — delivery must never break the run
        result = f"{type(exc).__name__}: {exc}"
    log.warning("text not sent: %s", result)
    return result
