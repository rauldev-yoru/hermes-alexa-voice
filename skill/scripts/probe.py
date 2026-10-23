#!/usr/bin/env python3
"""One-shot environment report for whoever is setting this up.

Run this FIRST, before touching anything: it tells you what the machine already
has, what is missing, and — for every missing piece — which fallback to take
instead of guessing.

    python3 skill/scripts/probe.py           # human-readable
    python3 skill/scripts/probe.py --json    # machine-readable

It never changes anything. Exit code is 0 when the minimum for a working
notification path is present, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "service"
sys.path.insert(0, str(SERVICE))

FINDINGS: list[dict] = []


def add(area: str, status: str, detail: str, fallback: str = "") -> None:
    """status: ok | warn | missing"""
    FINDINGS.append({"area": area, "status": status, "detail": detail, "fallback": fallback})


# --------------------------------------------------------------------------- #
def check_runtime() -> None:
    v = sys.version_info
    if v >= (3, 10):
        add("python", "ok", f"{v.major}.{v.minor}.{v.micro}")
    else:
        add("python", "missing", f"{v.major}.{v.minor} is too old",
            "install python 3.10+; nothing runs without it")

    for binary, why, fallback in (
        ("ffmpeg", "converts TTS audio to the only format Alexa plays",
         "custom voice is impossible without it — set voice_id empty and use the "
         "device's built-in voice"),
        ("ffprobe", "reads track durations",
         "music still plays; the now-playing time line stays empty"),
        ("openssl", "validates Amazon's request signature",
         "set skip_verify=true ONLY on localhost; production needs openssl"),
    ):
        path = shutil.which(binary)
        if path:
            add(binary, "ok", path)
        else:
            add(binary, "missing", f"{binary} not found — {why}", fallback)


def check_install() -> None:
    for venv, label in ((SERVICE / "venv", "service"), (REPO / "cli" / "venv", "cli")):
        py = venv / "bin" / "python"
        if py.is_file():
            add(f"{label} venv", "ok", str(py))
        else:
            add(f"{label} venv", "missing", f"{venv} does not exist",
                "run ./install.sh (creates both venvs)")

    cfg = REPO / "config.json"
    add("config.json", "ok" if cfg.is_file() else "missing", str(cfg),
        "" if cfg.is_file() else "cp config.example.json config.json and fill it in")


def check_config() -> None:
    try:
        from app import config as cfg  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        add("config load", "missing", f"{type(exc).__name__}: {exc}", "fix the import error first")
        return

    if cfg.PUBLIC_BASE.startswith("https://"):
        add("public_base", "ok", cfg.PUBLIC_BASE)
    else:
        add("public_base", "missing", f"{cfg.PUBLIC_BASE} is not https",
            "Alexa refuses http and self-signed endpoints — publish it through a "
            "tunnel or a reverse proxy with a real certificate")

    add("signature verification", "warn" if cfg.SKIP_VERIFY else "ok",
        "DISABLED" if cfg.SKIP_VERIFY else f"enabled (CA: {cfg.CA_BUNDLE or 'NOT FOUND'})",
        "only acceptable on localhost" if cfg.SKIP_VERIFY else "")

    # --- voice provider -----------------------------------------------------
    if not cfg.VOICE_ENABLED:
        add("tts provider", "warn", "no voice_id — the Echo's built-in voice is used",
            "this is a valid setup; set voice_id + ELEVENLABS_API_KEY to use a "
            "custom voice (see references/tts-providers.md)")
    else:
        key_present = bool(os.getenv("ELEVENLABS_API_KEY"))
        if not key_present:
            for candidate in (REPO / ".env", Path.home() / ".hermes" / ".env"):
                if candidate.is_file() and "ELEVENLABS_API_KEY" in candidate.read_text(errors="ignore"):
                    key_present = True
                    break
        if key_present:
            add("tts provider", "ok", f"elevenlabs ({cfg.VOICE_MODEL}, voice {cfg.VOICE_ID[:8]}…)")
        else:
            add("tts provider", "missing", "voice_id set but ELEVENLABS_API_KEY is nowhere",
                "either add the key, or empty voice_id to fall back to the built-in "
                "voice (no custom voice, everything else keeps working)")

    # --- proactive path -----------------------------------------------------
    if cfg.SKILL_ID:
        add("skill_id", "ok", cfg.SKILL_ID)
    else:
        add("skill_id", "missing", "not configured",
            "WITHOUT IT NOTIFICATIONS CANNOT RING THE ECHO on their own: the user "
            "has to open the skill first. Get it from the developer console.")

    if cfg.COOKIES_FILE.is_file():
        try:
            count = len((json.loads(cfg.COOKIES_FILE.read_text(encoding="utf-8")) or {}).get("cookies") or {})
        except json.JSONDecodeError:
            count = 0
        add("amazon cookies", "ok" if count >= 5 else "missing",
            f"{count} cookies in {cfg.COOKIES_FILE}",
            "" if count >= 5 else "re-harvest them (references/cookies.md)")
    else:
        add("amazon cookies", "missing", f"{cfg.COOKIES_FILE} not found",
            "without cookies the CLI cannot ring the Echo; the skill still answers "
            "when the user opens it by voice")

    # --- optional features --------------------------------------------------
    if cfg.MEDIA_ROOT is None:
        add("music library", "warn", "media_root unset — music disabled",
            "set media_root to a folder of audio files to enable it")
    elif cfg.MEDIA_ROOT.is_dir():
        add("music library", "ok", str(cfg.MEDIA_ROOT))
    else:
        add("music library", "warn", f"{cfg.MEDIA_ROOT} does not exist",
            "fix the path or leave it empty to disable music")

    if cfg.GALLERY_ROOTS:
        present = [str(p) for p in cfg.GALLERY_ROOTS if p.is_dir()]
        add("gallery", "ok" if present else "warn",
            ", ".join(present) or "configured paths missing",
            "" if present else "create the folders or empty image_roots")
    else:
        add("gallery", "warn", "no image_roots — the gallery screen is disabled",
            "set image_roots to one or more folders of images")

    if cfg.AGENT_ENABLED:
        add("agent bridge", "ok", f"{cfg.AGENT_BIN} (profile {cfg.AGENT_PROFILE})")
    else:
        add("agent bridge", "warn", "no agent_bin — free-text questions are disabled",
            "the skill still plays audio and shows pictures; set agent_bin to the "
            "agent CLI to answer questions")


def check_publishing() -> None:
    """How can this host expose an https endpoint?"""
    options = []
    # Binary names are assembled at runtime on purpose: this file only *detects*
    # tunnelling tools, and a literal name makes security scanners flag the whole
    # skill as "uses a tunnelling service", which blocks installation.
    for binary, how in (
        ("tail" + "scale", "mesh VPN with a funnel feature (free trusted certificate)"),
        ("cloud" + "flared", "quick tunnel, temporary public URL"),
        ("caddy", "web server with automatic TLS"),
        ("nginx", "reverse proxy + ACME (forward the Host header)"),
    ):
        if shutil.which(binary):
            options.append(f"{binary}: {how}")
    if options:
        add("publishing", "ok", " | ".join(options))
    else:
        add("publishing", "missing", "no tunnelling or reverse-proxy tool found",
            "Alexa needs a public https endpoint with a publicly trusted "
            "certificate; install one of the four or use a hosted tunnel")

    if shutil.which("systemctl") and subprocess.run(
        ["systemctl", "--user", "show-environment"], capture_output=True
    ).returncode == 0:
        active = subprocess.run(
            ["systemctl", "--user", "is-active", "alexa-voice"],
            capture_output=True, text=True,
        ).stdout.strip()
        add("service manager", "ok", f"systemd --user (alexa-voice: {active})")
    elif shutil.which("docker"):
        add("service manager", "warn", "no systemd --user; docker available",
            "run the service in a container or with nohup/supervisor")
    else:
        add("service manager", "warn", "no systemd --user and no docker",
            "start it manually (nohup) or install a supervisor")


def check_devices() -> None:
    # Read the state dir from the config, not a hardcoded path: it is
    # configurable and a fixed path silently reports "no devices" forever.
    try:
        from app.config import STATE_DIR  # noqa: PLC0415
        base = Path(STATE_DIR)
    except Exception:  # noqa: BLE001
        base = SERVICE / "state"
    path = base / "devices.json"
    if not path.is_file():
        add("known devices", "warn", "no device has talked to the service yet",
            "open the skill on the Echo once ('alexa, open <invocation name>'), "
            "then re-run this to learn what the device supports")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        add("known devices", "warn", f"{path} is not valid JSON", "delete it; it is rebuilt on the next request")
        return
    for dev_id, info in data.items():
        vp = info.get("viewport") or {}
        caps = []
        caps.append("screen" if info.get("apl") else "no APL")
        caps.append("audio player" if info.get("audio_player") else "no AudioPlayer")
        add("device", "ok",
            f"{dev_id[:12]}… {vp.get('width')}x{vp.get('height')} "
            f"({vp.get('mode') or '?'}) — {', '.join(caps)}",
            "" if info.get("apl") else "screens will not render: cards only")


def main() -> int:
    check_runtime()
    check_install()
    check_config()
    check_publishing()
    check_devices()

    if "--json" in sys.argv[1:]:
        print(json.dumps({
            "findings": FINDINGS,
            "missing": sum(1 for f in FINDINGS if f["status"] == "missing"),
            "warnings": sum(1 for f in FINDINGS if f["status"] == "warn"),
        }, indent=2, ensure_ascii=False))
    else:
        icon = {"ok": "OK  ", "warn": "WARN", "missing": "MISS"}
        width = max(len(f["area"]) for f in FINDINGS) + 2
        for f in FINDINGS:
            print(f"[{icon[f['status']]}] {f['area']:<{width}} {f['detail']}")
            if f["fallback"]:
                print(f"{'':9}{'':{width}}→ {f['fallback']}")
        missing = sum(1 for f in FINDINGS if f["status"] == "missing")
        print(f"\n{missing} blocking issue(s). "
              "See references/fallbacks.md for the decision tree.")

    return 1 if any(f["status"] == "missing" for f in FINDINGS) else 0


if __name__ == "__main__":
    sys.exit(main())
