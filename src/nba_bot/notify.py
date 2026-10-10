"""Send the daily digest to a phone.

Two channels, both free and account-less:

* `imessage` — sends a real text through the Mac's own Messages app (AppleScript).
  The pipeline already has to run on this Mac, so no SMS provider is needed. The
  first send prompts once for permission to control Messages.
* `ntfy` — a push notification via ntfy.sh. Works from any machine, including a
  server. The topic name is the only secret, so make it unguessable.

A failed send never fails the pipeline: the digest is always written to
`logs/digest-<date>.txt` first.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import date
from pathlib import Path

import httpx

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


def channels() -> list[str]:
    return [c.strip().lower() for c in settings.notify_channels.split(",") if c.strip()]


def _send_imessage(text: str) -> None:
    if not settings.notify_imessage_to:
        raise RuntimeError("NOTIFY_IMESSAGE_TO is not set")
    subprocess.run(
        ["osascript", "-e", _IMESSAGE_SCRIPT, text, settings.notify_imessage_to],
        check=True, capture_output=True, text=True, timeout=60,
    )


def _send_ntfy(text: str, title: str) -> None:
    if not settings.notify_ntfy_topic:
        raise RuntimeError("NOTIFY_NTFY_TOPIC is not set")
    resp = httpx.post(
        f"{settings.notify_ntfy_server.rstrip('/')}/{settings.notify_ntfy_topic}",
        content=text.encode(),
        headers={"Title": title.encode("ascii", "ignore").decode(), "Tags": "basketball"},
        timeout=20,
    )
    resp.raise_for_status()


def send(text: str, title: str = "NBA bot", log_dir: str | Path = "logs",
         kind: str = "digest", deliver: bool = True) -> dict:
    """Write the message to disk, then deliver it on every configured channel.

    `deliver=False` only writes the file. Returns {channel: "ok" | error}; never
    raises on a delivery failure.
    """
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{kind}-{date.today():%Y-%m-%d}.txt").write_text(text + "\n")
    if not deliver:
        return {}

    result: dict[str, str] = {}
    for channel in channels():
        try:
            if channel == "imessage":
                _send_imessage(text)
            elif channel == "ntfy":
                _send_ntfy(text, title)
            else:
                raise ValueError(f"unknown channel {channel!r} (expected imessage or ntfy)")
            result[channel] = "ok"
        except subprocess.CalledProcessError as exc:
            result[channel] = f"osascript failed: {exc.stderr.strip() or exc}"
        except Exception as exc:  # noqa: BLE001 — delivery must never break the run
            result[channel] = f"{type(exc).__name__}: {exc}"
        if result[channel] != "ok":
            log.warning("notify via %s failed: %s", channel, result[channel])
    return result
