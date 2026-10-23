"""cli/notify.py: config defaults are honoured, dedupe never eats a failed send."""
from __future__ import annotations

import datetime as dt
import sys

import pytest

import notify


@pytest.fixture
def cli(monkeypatch, tmp_path):
    monkeypatch.setattr(notify, "HISTORY_PATH", tmp_path / "history.json")
    monkeypatch.setattr(notify, "LOG_PATH", tmp_path / "notify.log")
    sent = {"payloads": [], "launches": 0, "fail": False}

    def fake_queue(payload):
        sent["payloads"].append(payload)
        return {"ok": True, "queued": payload["text"]}

    def fake_launch(_device):
        if sent["fail"]:
            raise SystemExit("run_skill failed: cookies are stale")
        sent["launches"] += 1

    monkeypatch.setattr(notify, "queue_notification", fake_queue)
    monkeypatch.setattr(notify, "launch", fake_launch)

    def run(*argv):
        monkeypatch.setattr(sys, "argv", ["notify.py", *argv])
        return notify.main()

    run.sent = sent
    return run


def test_config_chime_and_screen_defaults_are_honoured(cli, monkeypatch):
    # `notify.chime: false` in config.json used to do nothing at all.
    monkeypatch.setitem(notify.NOTIFY_CFG, "chime", False)
    monkeypatch.setitem(notify.NOTIFY_CFG, "screen", False)
    assert cli("quiet one", "--force") == 0
    payload = cli.sent["payloads"][-1]
    assert payload["chime"] is False and payload["screen"] is False


def test_flags_still_turn_things_off(cli):
    assert cli("x", "--no-chime", "--no-screen", "--force") == 0
    payload = cli.sent["payloads"][-1]
    assert payload["chime"] is False and payload["screen"] is False


def test_defaults_ring_with_chime_and_screen(cli):
    assert cli("hello", "--force") == 0
    payload = cli.sent["payloads"][-1]
    assert payload["chime"] is True and payload["screen"] is True
    assert cli.sent["launches"] == 1


def test_failed_delivery_does_not_block_the_retry(cli):
    cli.sent["fail"] = True
    with pytest.raises(SystemExit):
        cli("backup finished", "--dedupe-minutes", "30")
    cli.sent["fail"] = False
    assert cli("backup finished", "--dedupe-minutes", "30") == 0
    assert cli.sent["launches"] == 1  # the retry was delivered, not skipped


def test_successful_delivery_is_deduped(cli, capsys):
    assert cli("same text", "--dedupe-minutes", "30") == 0
    assert cli("same text", "--dedupe-minutes", "30", "--json") == 0
    assert cli.sent["launches"] == 1
    assert '"skipped": "duplicate"' in capsys.readouterr().out


def test_dedupe_zero_never_skips(cli):
    cli("again", "--dedupe-minutes", "0")
    cli("again", "--dedupe-minutes", "0")
    assert cli.sent["launches"] == 2


def test_unknown_voice_is_an_error(cli):
    assert cli("x", "--voice", "gpt") == 1
    assert cli.sent["payloads"] == []


@pytest.mark.parametrize("spec,hour,quiet", [
    ("23-8", 23, True), ("23-8", 3, True), ("23-8", 8, False), ("23-8", 12, False),
    ("9-17", 9, True), ("9-17", 17, False), ("5-5", 5, False), ("bad", 3, False),
    (None, 3, False),
])
def test_quiet_hours(spec, hour, quiet):
    assert notify.in_quiet_hours(spec, dt.datetime(2026, 1, 1, hour)) is quiet
