#!/usr/bin/env python3
"""Direct Echo control over the unofficial Alexa API (alexapy).

This is the transport layer the rest of the CLI builds on. The auth chain below
is the part that took the longest to get right, and every step was measured
against a live account:

* Cookies are harvested from a browser where Amazon is already logged in and
  stored as ``{"cookies": {...}}`` in ``state/credentials/amazon_cookies.json``
  (override with ``cookies_file`` in config).
* ``oauth_login=True`` is **required**. With ``oauth_login=False`` alexapy sends
  an iPhone/PitanguiBridge user agent and Amazon redirects every call to
  ``/ap/signin`` even with a perfectly valid session.
* ``AlexaLogin.login(cookies=...)`` only *validates* using those cookies per
  request; the aiohttp cookie jar stays empty and every later ``AlexaAPI`` call
  goes out unauthenticated. Seed the jar explicitly (``seed_cookie_jar``).
* ``AlexaAPI`` expects an object with private attributes, not the raw dict from
  ``/api/devices-v2/device`` → ``DeviceShim``.

Commands::

    alexa.py devices
    alexa.py speak "text" [--voice Mia] [--whisper] [--pitch -2st]
    alexa.py announce "text"
    alexa.py volume 40
    alexa.py dnd on|off
    alexa.py play|pause|next|previous|stop
    alexa.py routines
    alexa.py routine "name"
    alexa.py run-skill              # open the skill without saying a word
    alexa.py state
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from http.cookies import SimpleCookie
from pathlib import Path

# Import the service config (single source of truth for paths and settings).
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "service"))

from app.config import (  # noqa: E402
    COOKIES_FILE,
    DEVICE_NAME,
    SKILL_ID,
    STATE_DIR,
)

ALEXA_UA = "AmazonWebView/Amazon Alexa/2.2.556530.0/iOS/16.6/iPhone"
# Device families that are real Echo speakers/displays (VOX = the phone app).
ECHO_FAMILIES = ("KNIGHT", "ECHO", "ROOK", "AEOB", "AEOC")


def build_ssml(text: str, voice: str | None = None, rate: str | None = None,
               pitch: str | None = None, whisper: bool = False,
               volume: str | None = None) -> str:
    """Wrap text in SSML. A string that is already <speak> passes through."""
    if text.lstrip().startswith("<speak"):
        return text
    inner = text
    if voice:
        inner = f'<voice name="{voice}">{inner}</voice>'
    attrs = []
    if rate:
        attrs.append(f'rate="{rate}"')
    if pitch:
        attrs.append(f'pitch="{pitch}"')
    if volume:
        attrs.append(f'volume="{volume}"')
    if attrs:
        inner = f'<prosody {" ".join(attrs)}>{inner}</prosody>'
    if whisper:
        inner = f'<amazon:effect name="whispered">{inner}</amazon:effect>'
    return f"<speak>{inner}</speak>"


class DeviceShim:
    """Minimal stand-in for HA's AlexaDevice: AlexaAPI only reads these attrs."""

    def __init__(self, dev: dict):
        self._device_family = dev.get("deviceFamily")
        self._device_type = dev.get("deviceType")
        self._locale = dev.get("locale") or "en-US"
        self._cluster_members = [
            m.get("serialNumber") for m in (dev.get("clusterMembers") or [])
        ]
        self.device_serial_number = dev.get("serialNumber")
        self.account_name = dev.get("accountName")
        self.raw = dev


def seed_cookie_jar(login, cookies: dict) -> None:
    """Copy harvested cookies into the aiohttp session jar for .amazon.com."""
    from aiohttp import CookieJar

    jar = CookieJar(unsafe=True)
    for name, value in cookies.items():
        ck = SimpleCookie()
        ck[name] = value
        ck[name]["domain"] = ".amazon.com"
        ck[name]["path"] = "/"
        jar.update_cookies(ck)
    login._session._cookie_jar = jar


async def connect():
    """Return a logged-in AlexaLogin, or raise SystemExit with a clear reason."""
    from alexapy import AlexaLogin

    cred = Path(COOKIES_FILE)
    if not cred.is_file():
        raise SystemExit(
            f"missing Amazon cookies at {cred}\n"
            "Harvest them from a browser where amazon.com is logged in: see docs/COOKIES.md"
        )
    data = json.loads(cred.read_text(encoding="utf-8"))
    cookies = data.get("cookies") if isinstance(data, dict) else None
    if not cookies:
        raise SystemExit(f"{cred} has no 'cookies' object")

    workdir = cred.parent
    (workdir / ".storage").mkdir(parents=True, exist_ok=True)
    login = AlexaLogin(
        url="amazon.com",
        email=str(data.get("email") or "user@example.com"),
        password="",
        outputpath=lambda name: str(workdir / name),
        oauth_login=True,
        debug=False,
    )
    await login.login(cookies=cookies)
    if not login.customer_id and not await login.test_loggedin(cookies, rebuild_session=False):
        raise SystemExit(
            f"Amazon login failed ({login.status}). The cookies are probably stale — "
            "re-harvest them from the browser."
        )

    login._headers["User-Agent"] = ALEXA_UA
    login._headers["Accept-Language"] = "*"
    seed_cookie_jar(login, cookies)
    await login.get_csrf()
    return login


async def pick_echo(login, name: str | None = None):
    from alexapy import AlexaAPI

    devices = await AlexaAPI.get_devices(login) or []
    if name:
        low = name.lower()
        match = next((d for d in devices if low in str(d.get("accountName", "")).lower()), None)
    else:
        match = next(
            (d for d in devices if d.get("deviceFamily") in ECHO_FAMILIES
             and d.get("deviceFamily") != "VOX"),
            None,
        )
    if match is None:
        found = ", ".join(str(d.get("accountName")) for d in devices) or "none"
        raise SystemExit(f"no Echo matched {name!r} (found: {found})")
    return match, AlexaAPI(DeviceShim(match), login)


async def run_skill(device: str | None = None, skill_id: str | None = None) -> dict:
    """Open the skill on an Echo without any voice command.

    This is the trick that makes proactive notifications possible: the skill's
    LaunchRequest fires, it reads the queued message, and answers with custom
    audio.
    """
    skill = skill_id or SKILL_ID
    if not skill:
        raise SystemExit("no skill_id configured (set it in config.json)")
    login = await connect()
    try:
        echo, api = await pick_echo(login, device or DEVICE_NAME)
        await api.run_skill(skill)
        return {"device": echo.get("accountName"), "skill": skill}
    finally:
        try:
            await login.close()
        except Exception:  # noqa: BLE001
            pass


async def main_async(args: argparse.Namespace) -> int:
    from alexapy import AlexaAPI

    if args.cmd == "run-skill":
        print(json.dumps(await run_skill(args.device, args.skill_id), ensure_ascii=False))
        return 0

    login = await connect()
    try:
        if args.cmd == "devices":
            devices = await AlexaAPI.get_devices(login) or []
            print(f"{len(devices)} devices")
            for d in devices:
                print(json.dumps({
                    "name": d.get("accountName"),
                    "family": d.get("deviceFamily"),
                    "type": d.get("deviceType"),
                    "serial": d.get("serialNumber"),
                    "online": d.get("online"),
                }, ensure_ascii=False))
            return 0

        if args.cmd == "routines":
            automations = await AlexaAPI.get_automations(login) or []
            for a in automations:
                print(json.dumps({
                    "name": a.get("name"),
                    "automationId": a.get("automationId"),
                    "enabled": a.get("enabled"),
                }, ensure_ascii=False))
            return 0

        if args.cmd == "routine":
            _dev, api = await pick_echo(login, args.device or DEVICE_NAME)
            automations = await AlexaAPI.get_automations(login) or []
            low = args.name.lower()
            hit = next((a for a in automations if low in str(a.get("name", "")).lower()), None)
            if hit is None:
                names = ", ".join(str(a.get("name")) for a in automations)
                print(f"routine not found. available: {names}")
                return 1
            await api.run_routine(hit.get("name"))
            print(f"routine '{hit.get('name')}' triggered")
            return 0

        dev, api = await pick_echo(login, args.device or DEVICE_NAME)

        if args.cmd == "speak":
            msg = build_ssml(args.text, voice=args.voice, rate=args.rate,
                             pitch=args.pitch, whisper=args.whisper)
            await api.send_tts(msg)
            print(f"TTS sent to {dev.get('accountName')} (voice={args.voice or 'device default'})")
        elif args.cmd == "announce":
            await api.send_announcement(args.text)
            print(f"announcement sent: {args.text}")
        elif args.cmd == "volume":
            await api.set_volume(float(args.level))
            print(f"volume -> {args.level}")
        elif args.cmd == "dnd":
            await api.set_dnd_state(args.value == "on")
            print(f"do-not-disturb -> {args.value}")
        elif args.cmd in ("play", "pause", "next", "previous", "stop"):
            await getattr(api, {"play": "play", "pause": "pause", "next": "next",
                                "previous": "previous", "stop": "stop"}[args.cmd])()
            print(f"{args.cmd} sent")
        elif args.cmd == "state":
            print("dnd:", await AlexaAPI.get_dnd_state(login))
            st = await api.get_state()
            print("state:", json.dumps(st, ensure_ascii=False)[:400] if st else None)
        return 0
    finally:
        try:
            await login.close()
        except Exception:  # noqa: BLE001
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Control Amazon Echo devices (unofficial API)")
    p.add_argument("--device", help="Echo name substring (default: first speaker)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("devices")
    sp = sub.add_parser("speak", help="say something with the device's own voice")
    sp.add_argument("text")
    sp.add_argument("--voice", help="Polly voice, e.g. Mia / Lupe / Enrique")
    sp.add_argument("--rate", help="x-slow|slow|medium|fast|x-fast or a percentage")
    sp.add_argument("--pitch", help='e.g. "+2st", "-3st", "high", "low"')
    sp.add_argument("--whisper", action="store_true")
    sp = sub.add_parser("announce", help="announcement (beep + voice)")
    sp.add_argument("text")
    sp = sub.add_parser("volume"); sp.add_argument("level", type=int)
    sp = sub.add_parser("dnd"); sp.add_argument("value", choices=["on", "off"])
    for name in ("play", "pause", "next", "previous", "stop", "routines", "state"):
        sub.add_parser(name)
    sp = sub.add_parser("routine"); sp.add_argument("name")
    sp = sub.add_parser("run-skill", help="open the skill without a voice command")
    sp.add_argument("--skill-id", default=None)
    return p


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
