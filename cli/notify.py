#!/usr/bin/env python3
"""Send a spoken notification to an Echo — the tool for cron jobs and scripts.

The message is read out loud with the configured custom voice (ElevenLabs by
default), optionally preceded by a chime, and shown on screen if the device has
one. It works from any process because the delivery path is:

    notify.py  ->  POST /notify (queue)  ->  run_skill (open the skill)
               ->  LaunchRequest  ->  skill answers with the queued message

Usage::

    notify.py "backup finished"
    notify.py "render done" --title ComfyUI --subtitle "chisa/tanda3"
    notify.py "something urgent" --voice turbo --no-chime --force
    notify.py "test" --dry-run --json

Every flag overrides ``config.json`` for that call only. Exit codes: **0** when
delivered *or* intentionally skipped (quiet hours / duplicate), **1** on error —
so a cron can decide without parsing output.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "service"))
sys.path.insert(0, str(REPO / "cli"))

from app.config import (  # noqa: E402
    DEVICE_NAME,
    HOST,
    NOTIFY_CFG,
    PORT,
    STATE_DIR,
)

NOTIFY_URL = f"http://{HOST}:{PORT}/notify"
HISTORY_PATH = STATE_DIR / "notify_history.json"
LOG_PATH = STATE_DIR / "notify.log"

VOICE_ALIASES = {
    "v4": "eleven_v4",
    "v3": "eleven_v3",
    "turbo": "eleven_v4_turbo",
    "flash": "eleven_flash_v2_5",
    "eleven_v4": "eleven_v4",
    "eleven_v3": "eleven_v3",
    "eleven_v4_turbo": "eleven_v4_turbo",
    "eleven_flash_v2_5": "eleven_flash_v2_5",
}


def log(line: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {line}\n")


def in_quiet_hours(spec: str | None, now: dt.datetime | None = None) -> bool:
    """True when the current local hour falls inside e.g. "23-8" (wraps midnight)."""
    if not spec or "-" not in spec:
        return False
    try:
        start_s, end_s = spec.split("-", 1)
        start, end = int(start_s), int(end_s)
    except ValueError:
        return False
    hour = (now or dt.datetime.now()).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _history() -> dict:
    if HISTORY_PATH.exists():
        try:
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _key(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]


def remember(text: str) -> None:
    """Record a delivered message for `duplicate`."""
    history = _history()
    history[_key(text)] = time.time()
    cutoff = time.time() - 7 * 86400
    history = {k: v for k, v in history.items() if float(v) > cutoff}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history), encoding="utf-8")


def duplicate(text: str, minutes: int, record: bool = True) -> bool:
    """Remember what was sent, so a chatty cron cannot spam the speaker.

    ``record=False`` only checks; `main` records after a successful delivery so
    a failed attempt does not block the cron's retry.
    """
    if minutes <= 0:
        return False
    last = _history().get(_key(text), 0)
    if time.time() - float(last) < minutes * 60:
        return True
    if record:
        remember(text)
    return False


def queue_notification(payload: dict) -> dict:
    with httpx.Client(timeout=20) as client:
        resp = client.post(NOTIFY_URL, json=payload)
    if resp.status_code != 200:
        raise SystemExit(f"notify endpoint returned {resp.status_code}: {resp.text[:240]}")
    return resp.json()


def launch(device: str | None) -> None:
    from alexa import run_skill

    asyncio.run(run_skill(device))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Speak a notification on an Echo with the configured voice.")
    parser.add_argument("text", help="what the assistant says")
    parser.add_argument("--title", default=NOTIFY_CFG["title"], help="screen header")
    parser.add_argument("--subtitle", default=NOTIFY_CFG["subtitle"], help="screen footer")
    parser.add_argument("--voice", default=None,
                        help="v4 | v3 | turbo | flash (config default: %s)" % NOTIFY_CFG["model"])
    parser.add_argument("--device", default=DEVICE_NAME, help="Echo name substring")
    parser.add_argument("--no-chime", action="store_true", help="no sound before the voice")
    parser.add_argument("--no-screen", action="store_true", help="voice only, no screen")
    parser.add_argument("--quiet-hours", default=NOTIFY_CFG["quiet_hours"],
                        help='silent window, e.g. "23-8"')
    parser.add_argument("--dedupe-minutes", type=int, default=NOTIFY_CFG["dedupe_minutes"],
                        help="ignore the same text repeated within N minutes")
    parser.add_argument("--force", action="store_true", help="ignore quiet hours and dedupe")
    parser.add_argument("--dry-run", action="store_true", help="queue but do not ring the Echo")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    model = NOTIFY_CFG["model"]
    if args.voice:
        model = VOICE_ALIASES.get(args.voice.strip().lower())
        if not model:
            print(f"unknown voice: {args.voice}", file=sys.stderr)
            return 1

    if not args.force and in_quiet_hours(args.quiet_hours):
        log(f"SKIP quiet_hours={args.quiet_hours} text={args.text[:60]!r}")
        print(json.dumps({"ok": True, "skipped": "quiet_hours"}) if args.json
              else f"silenced by quiet hours ({args.quiet_hours})")
        return 0

    if not args.force and duplicate(args.text, args.dedupe_minutes, record=False):
        log(f"SKIP duplicate text={args.text[:60]!r}")
        print(json.dumps({"ok": True, "skipped": "duplicate"}) if args.json
              else f"duplicate: already sent less than {args.dedupe_minutes} min ago")
        return 0

    # config.json `notify.chime` / `notify.screen` are the defaults; the flags
    # can only turn them off for one call.
    chime = bool(NOTIFY_CFG.get("chime", True)) and not args.no_chime
    screen = bool(NOTIFY_CFG.get("screen", True)) and not args.no_screen
    payload = {
        "text": args.text,
        "title": args.title,
        "subtitle": args.subtitle,
        "chime": chime,
        "screen": screen,
        "model": model,
    }
    queued = queue_notification(payload)
    if args.verbose:
        print(f"queued: {json.dumps(queued, ensure_ascii=False)}")

    if args.dry_run:
        log(f"DRY text={args.text[:60]!r} model={model}")
        print(json.dumps({"ok": True, "dry_run": True, **queued}) if args.json
              else "queued (dry-run, Echo not rung)")
        return 0

    launch(args.device)
    if args.dedupe_minutes > 0:
        remember(args.text)
    log(f"SENT text={args.text[:60]!r} model={model} chime={chime} screen={screen}")
    print(json.dumps({"ok": True, "sent": True, **queued}) if args.json
          else "sent — the Echo should speak in a few seconds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
