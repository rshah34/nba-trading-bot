"""Notification delivery + digest formatting. DB/network-free."""

import subprocess

from nba_bot import digest, notify
from nba_bot.config import settings


def test_message_is_written_to_disk_even_with_no_channels(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "notify_channels", "")
    assert notify.send("hello", log_dir=tmp_path) == {}
    assert next(tmp_path.glob("digest-*.txt")).read_text() == "hello\n"


def test_imessage_passes_text_as_an_argument_not_into_the_script(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "notify_channels", "imessage")
    monkeypatch.setattr(settings, "notify_imessage_to", "+15555550100")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    text = 'De\'Aaron "Fox" over 24.5\nline two'
    assert notify.send(text, log_dir=tmp_path) == {"imessage": "ok"}
    cmd = calls[0]
    assert cmd[0] == "osascript" and cmd[-2:] == [text, "+15555550100"]
    assert "Fox" not in cmd[2]          # the script itself never contains the message


def test_a_failing_channel_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "notify_channels", "imessage, ntfy, carrier-pigeon")
    monkeypatch.setattr(settings, "notify_imessage_to", "")
    monkeypatch.setattr(settings, "notify_ntfy_topic", "")
    result = notify.send("hi", log_dir=tmp_path)
    assert set(result) == {"imessage", "ntfy", "carrier-pigeon"}
    assert all(v != "ok" for v in result.values())


def test_american_odds_formatting():
    assert digest._american(1.909) == "-110"
    assert digest._american(2.2) == "+120"
    assert digest._american(2.0) == "+100"


def test_stake_shows_dollars_only_when_a_bankroll_is_set(monkeypatch):
    monkeypatch.setattr(settings, "bankroll", 0.0)
    assert digest._stake(0.015) == "1.5u"
    monkeypatch.setattr(settings, "bankroll", 2000.0)
    assert digest._stake(0.015) == "1.5u ($30)"
