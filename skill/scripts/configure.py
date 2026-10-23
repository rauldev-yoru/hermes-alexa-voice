#!/usr/bin/env python3
"""Configure the bridge without hand-editing JSON — and validate as you go.

Everything an agent needs to change lives in `config.json`; this script edits it
safely (keeps unknown keys, validates the values that break silently) and can
print the effective configuration after the three-layer merge.

    # what is set right now (defaults + config.json + env)
    configure.py --show

    # first-run essentials
    configure.py --public-base https://host/alexa --skill-id amzn1.ask.skill.xxx
    configure.py --voice-id <elevenlabs id> --voice-model eleven_v4
    configure.py --no-custom-voice                 # use the device's own voice

    # personalise what the user sees and hears
    configure.py --brand AGENT --welcome "Here. Ask me something."
    configure.py --help-text "Ask a question, play something, or show pictures."
    configure.py --art https://host/static/agent.png

    # optional features
    configure.py --media-root ~/Music --image-roots ~/Pictures,~/Art
    configure.py --agent-bin /usr/local/bin/hermes --agent-profile default
    configure.py --device "Kitchen" --quiet-hours 23-8 --dedupe-minutes 10

    # inspect without writing
    configure.py --public-base https://x.y/z --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("ALEXA_VOICE_CONFIG") or (REPO / "config.json"))


def load() -> dict:
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError as exc:
            sys.exit(f"{CONFIG_PATH} is not valid JSON: {exc}")
    return {}


def validate(cfg: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) — errors mean it will not work, warnings mean degraded."""
    errors: list[str] = []
    warnings: list[str] = []

    public = str(cfg.get("public_base") or "")
    if not public.startswith("https://"):
        errors.append(
            f"public_base={public!r} is not https:// — Alexa never calls http or a "
            "self-signed endpoint. Publish it through a tunnel or a reverse "
            "proxy with a real certificate and use that URL (docs/SETUP.md)."
        )
    if public.endswith("/"):
        warnings.append("public_base ends with '/' — the skill path will be '//health'; drop it")

    if cfg.get("skip_verify"):
        warnings.append(
            "skip_verify=true: ANYONE who finds the endpoint can trigger the skill. "
            "Localhost development only."
        )

    voice_id = str(cfg.get("voice_id") or "")
    if voice_id:
        has_key = bool(os.getenv("ELEVENLABS_API_KEY"))
        if not has_key:
            for candidate in (REPO / ".env", Path.home() / ".hermes" / ".env"):
                if candidate.is_file() and "ELEVENLABS_API_KEY" in candidate.read_text(errors="ignore"):
                    has_key = True
                    break
        if not has_key:
            warnings.append(
                "voice_id is set but ELEVENLABS_API_KEY is missing — every render "
                "fails and the device's built-in voice is used instead. Add the key "
                "to .env, or empty voice_id to make that official."
            )
    else:
        warnings.append(
            "no voice_id: the Echo's built-in voice is used (fine, and free). Set "
            "voice_id + ELEVENLABS_API_KEY for a custom voice."
        )

    if not cfg.get("skill_id"):
        warnings.append(
            "no skill_id: notifications cannot ring the Echo by themselves — the "
            "user has to open the skill first."
        )

    media = cfg.get("media_root")
    if media and not Path(str(media)).expanduser().is_dir():
        warnings.append(f"media_root={media!r} does not exist — music will be empty")

    roots = cfg.get("image_roots") or []
    missing = [r for r in roots if not Path(str(r)).expanduser().is_dir()]
    if missing:
        warnings.append(f"image_roots missing on disk: {missing}")

    art = cfg.get("art_image")
    if art and not str(art).startswith("https://"):
        warnings.append(
            "art_image should be a public https URL: the Echo fetches it, so a "
            "localhost path shows nothing."
        )

    return errors, warnings


def main() -> int:
    p = argparse.ArgumentParser(description="Edit and validate config.json")
    p.add_argument("--show", action="store_true", help="print the effective config and exit")
    p.add_argument("--dry-run", action="store_true", help="validate without writing")

    p.add_argument("--public-base")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--skill-id")
    p.add_argument("--device", help="Echo name substring")

    p.add_argument("--voice-id", help="ElevenLabs voice id")
    p.add_argument("--voice-model", help="eleven_v4 | eleven_v3 | eleven_v4_turbo | eleven_flash_v2_5")
    p.add_argument("--no-custom-voice", action="store_true", help="empty voice_id: built-in voice")
    p.add_argument("--no-chime", action="store_true")

    p.add_argument("--brand", help="brand_title shown on screen")
    p.add_argument("--welcome", help="fixed phrase when the skill opens")
    p.add_argument("--help-text", help="fixed phrase for 'help'")
    p.add_argument("--art", help="public https URL of the illustration")
    p.add_argument("--language", help="JSON file with a 'strings' object for another language")

    p.add_argument("--media-root")
    p.add_argument("--image-roots", help="comma-separated folders")
    p.add_argument("--agent-bin")
    p.add_argument("--agent-profile")

    p.add_argument("--quiet-hours", help='e.g. "23-8"')
    p.add_argument("--dedupe-minutes", type=int)

    args = p.parse_args()

    if args.show:
        sys.path.insert(0, str(REPO / "service"))
        try:
            from app.config import summary  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            sys.exit(f"cannot load config: {exc}")
        print(json.dumps(summary(), indent=2, ensure_ascii=False))
        return 0

    cfg = load()

    mapping = {
        "public_base": args.public_base,
        "host": args.host,
        "port": args.port,
        "skill_id": args.skill_id,
        "device_name": args.device,
        "voice_id": args.voice_id,
        "voice_model": args.voice_model,
        "brand_title": args.brand,
        "brand_welcome": args.welcome,
        "brand_help": args.help_text,
        "art_image": args.art,
        "media_root": args.media_root,
        "agent_bin": args.agent_bin,
        "agent_profile": args.agent_profile,
    }
    for key, value in mapping.items():
        if value is not None:
            cfg[key] = value

    if args.no_custom_voice:
        cfg["voice_id"] = ""
    if args.no_chime:
        cfg["chime"] = False
    if args.image_roots is not None:
        cfg["image_roots"] = [r.strip() for r in args.image_roots.split(",") if r.strip()]
    if args.language:
        try:
            strings = json.loads(Path(args.language).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            sys.exit(f"--language: {exc}")
        if not isinstance(strings, dict):
            sys.exit("--language: expected a JSON object of key -> text")
        cfg.setdefault("strings", {}).update(strings.get("strings", strings))

    if args.quiet_hours is not None or args.dedupe_minutes is not None:
        notify = cfg.setdefault("notify", {})
        if args.quiet_hours is not None:
            notify["quiet_hours"] = args.quiet_hours
        if args.dedupe_minutes is not None:
            notify["dedupe_minutes"] = args.dedupe_minutes

    errors, warnings = validate(cfg)

    if args.dry_run:
        print("(dry run — nothing written)")
    else:
        # Merge over the example so a fresh install keeps every documented key.
        base: dict = {}
        example = REPO / "config.example.json"
        if example.is_file():
            try:
                base = json.loads(example.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                base = {}
        base.update(cfg)
        CONFIG_PATH.write_text(json.dumps(base, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {CONFIG_PATH}")

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    if not errors and not warnings:
        print("config looks good")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
