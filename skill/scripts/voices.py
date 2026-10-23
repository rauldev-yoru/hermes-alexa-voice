#!/usr/bin/env python3
"""Discover, audition and pick a voice — the interactive half of the voice setup.

A new user has no voice id, maybe no account, and no idea which model to pick.
This script turns that into a short conversation instead of a guessing game.

    # 0. is there even a provider configured?
    voices.py --status

    # 1. what can this account use? (no voice id needed)
    voices.py --models                 # models + real cost multiplier
    voices.py --list                   # voices in the account, with labels
    voices.py --list --gender female --accent british

    # 2. hear them, do not guess
    voices.py --preview --name "Alice"         # 3 samples: neutral, emotion, brand
    voices.py --preview --voice-id <id> --text "Good evening."

    # 3. commit the choice
    voices.py --set --name "Alice" --model eleven_v4
    voices.py --set --voice-id <id> --model eleven_v4_turbo

    # machine-readable
    voices.py --list --json

Previews are written to `service/state/previews/` and served by the running
service, so they can be listened to (or sent to the Echo) before deciding.

With no API key the script says so and points at the fallback: the Echo's own
voice, which needs no account at all.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "service"))

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.exit("httpx is missing: run ./install.sh (it installs the cli venv)")

API = "https://api.elevenlabs.io/v1"

# Three samples that reveal different things about a voice: how it reads plain
# text, whether it honours emotion tags, and whether it fits the brand.
PREVIEWS = {
    "neutral": "The backup finished a few minutes ago. Everything went through.",
    "emotion": "[excited] It is done! [whispers] And it only took four minutes.",
    "brand": "All set. I will let you know as soon as the render is ready.",
}


def api_key() -> str | None:
    for var in ("ELEVENLABS_API_KEY", "ELEVENLABS_KEY"):
        value = os.getenv(var)
        if value:
            return value.strip()
    for path in (REPO / ".env", Path.home() / ".hermes" / ".env"):
        try:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("ELEVENLABS_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return None


def client() -> "httpx.Client":
    key = api_key()
    if not key:
        sys.exit(
            "No ElevenLabs key found.\n"
            "  Option A — use the Echo's built-in voice (free, no account):\n"
            "      skill/scripts/configure.py --no-custom-voice\n"
            "  Option B — create an account, then put the key in <repo>/.env:\n"
            "      ELEVENLABS_API_KEY=...\n"
            "  Then re-run this script."
        )
    return httpx.Client(timeout=30, headers={"xi-api-key": key})


def load_config() -> dict:
    path = Path(os.getenv("ALEXA_VOICE_CONFIG") or (REPO / "config.json"))
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def save_config(data: dict) -> Path:
    path = Path(os.getenv("ALEXA_VOICE_CONFIG") or (REPO / "config.json"))
    base: dict = {}
    example = REPO / "config.example.json"
    if example.is_file():
        try:
            base = json.loads(example.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            base = {}
    base.update(data)
    path.write_text(json.dumps(base, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
def cmd_status(as_json: bool) -> int:
    cfg = load_config()
    key = api_key()
    state = {
        "api_key": bool(key),
        "voice_id": cfg.get("voice_id") or None,
        "voice_model": cfg.get("voice_model") or "eleven_v4",
        "mode": "custom voice" if cfg.get("voice_id") else "built-in device voice",
    }
    if as_json:
        print(json.dumps(state, indent=2))
        return 0
    print(f"provider key : {'present' if key else 'MISSING'}")
    print(f"voice id     : {state['voice_id'] or '(none)'}")
    print(f"model        : {state['voice_model']}")
    print(f"mode         : {state['mode']}")
    if not key:
        print("\nWithout a key the Echo speaks with its own voice — valid, and free.")
        print("To use a custom voice: create an account, add the key to .env, then")
        print("run   voices.py --list   and   voices.py --preview --name <voice>")
    return 0


def cmd_models(as_json: bool) -> int:
    # A TTS-scoped key has no `models_read` permission. That is common and not an
    # error worth failing on — fall back to the published cost weights.
    STATIC = [
        {"model_id": "eleven_v4", "cost_multiplier": 1.0, "notes": "emotion tags, best quality"},
        {"model_id": "eleven_v3", "cost_multiplier": 1.0, "notes": "emotion tags"},
        {"model_id": "eleven_multilingual_v2", "cost_multiplier": 1.0, "notes": "no tags"},
        {"model_id": "eleven_v4_turbo", "cost_multiplier": 0.5, "notes": "half price, slightly flatter"},
        {"model_id": "eleven_flash_v2_5", "cost_multiplier": 0.5, "notes": "cheapest, reads tags literally"},
    ]
    with client() as c:
        resp = c.get(f"{API}/models")
    if resp.status_code == 200:
        rows = []
        for m in resp.json():
            model_id = m.get("model_id")
            rates = m.get("model_rates") or {}
            rows.append({
                "model_id": model_id,
                "cost_multiplier": rates.get("character_cost_multiplier", 1.0),
                "languages": len(m.get("languages") or []),
                "notes": "emotion tags" if model_id in ("eleven_v3", "eleven_v4") else "reads tags literally",
            })
        rows.sort(key=lambda r: (r["cost_multiplier"], r["model_id"]))
        source = "from the provider API"
    elif resp.status_code in (401, 403):
        rows = STATIC
        source = ("this key cannot read the model list (no models_read permission) — "
                  "values are the provider's published cost weights")
    else:
        sys.exit(f"models: HTTP {resp.status_code} {resp.text[:160]}")

    if as_json:
        print(json.dumps({"source": source, "models": rows}, indent=2, ensure_ascii=False))
        return 0
    print(f"{'model':26} {'cost':>6}  notes")
    for r in rows:
        print(f"{r['model_id']:26} {r['cost_multiplier']:>6}  {r['notes']}")
    print(f"\n({source})")
    print("cost 1.0 = list price, 0.5 = half price. Only v3/v4 honour [emotion] tags.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    with client() as c:
        resp = c.get(f"{API}/voices")
    if resp.status_code != 200:
        sys.exit(f"voices: HTTP {resp.status_code} {resp.text[:160]}")
    voices = (resp.json() or {}).get("voices") or []

    def keep(v: dict) -> bool:
        labels = v.get("labels") or {}
        for field, wanted in (("gender", args.gender), ("accent", args.accent),
                              ("use_case", args.use_case)):
            if wanted and str(labels.get(field) or "").lower() != wanted.lower():
                return False
        if args.search:
            haystack = f"{v.get('name','')} {v.get('description','')}".lower()
            if args.search.lower() not in haystack:
                return False
        return True

    picked = [v for v in voices if keep(v)]
    rows = [{
        "name": v.get("name"),
        "voice_id": v.get("voice_id"),
        "description": v.get("description"),
        "accent": (v.get("labels") or {}).get("accent"),
        "gender": (v.get("labels") or {}).get("gender"),
        "use_case": (v.get("labels") or {}).get("use_case"),
        "category": v.get("category"),
    } for v in picked]
    rows.sort(key=lambda r: (r["name"] or "").lower())

    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("No voice matched those filters. Try without --gender/--accent.")
        return 1
    print(f"{len(rows)} of {len(voices)} voices")
    for r in rows:
        print(f"  {r['name']:26} {r['voice_id']}  "
              f"{r['accent'] or '-':10} {r['gender'] or '-':7} {r['use_case'] or '-'}")
    print("\nNext:  voices.py --preview --name \"<one of those>\"")
    return 0


def resolve_voice(args: argparse.Namespace) -> tuple[str, str]:
    """(voice_id, name) from --voice-id or --name."""
    if args.voice_id:
        return args.voice_id, args.voice_id[:10]
    if not args.name:
        sys.exit("give --voice-id <id> or --name \"<voice name>\" (see --list)")
    with client() as c:
        resp = c.get(f"{API}/voices")
    voices = (resp.json() or {}).get("voices") or []
    needle = args.name.strip().lower()
    exact = [v for v in voices if str(v.get("name", "")).lower() == needle]
    partial = [v for v in voices if needle in str(v.get("name", "")).lower()]
    match = (exact or partial)
    if not match:
        sys.exit(f"no voice named {args.name!r} in this account (run --list)")
    if len(match) > 1 and not exact:
        names = ", ".join(v.get("name", "?") for v in match[:8])
        sys.exit(f"{args.name!r} matches several voices: {names}. Be more specific.")
    return match[0].get("voice_id"), match[0].get("name")


def _slug(text: str) -> str:
    """Filesystem/URL-safe label: no spaces, commas or parentheses."""
    keep = "-_"
    return "".join(c if (c.isalnum() or c in keep) else "-" for c in text).strip("-").lower()


def cmd_preview(args: argparse.Namespace) -> int:
    voice_id, name = resolve_voice(args)
    model = args.model or load_config().get("voice_model") or "eleven_v4"
    out_dir = REPO / "service" / "state" / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    # "Alice - Clear, Engaging Educator" -> "alice"
    slug = _slug(str(name).split(" - ")[0]) or _slug(voice_id)[:12]

    samples = {"custom": args.text} if args.text else PREVIEWS
    written = []
    with client() as c:
        for label, text in samples.items():
            resp = c.post(
                f"{API}/text-to-speech/{voice_id}",
                headers={"accept": "audio/mpeg", "content-type": "application/json"},
                json={"text": text, "model_id": model,
                      "voice_settings": {"stability": 0.4, "similarity_boost": 0.8}},
                params={"output_format": "mp3_44100_128"},
            )
            if resp.status_code != 200:
                print(f"  {label}: HTTP {resp.status_code} {resp.text[:120]}", file=sys.stderr)
                continue
            path = out_dir / f"{slug}_{label}.mp3"
            path.write_bytes(resp.content)
            written.append((label, path))

    if not written:
        return 1
    if args.json:
        print(json.dumps({"voice_id": voice_id, "name": name, "model": model,
                          "files": [str(p) for _l, p in written]}, indent=2))
        return 0
    print(f"{name} ({voice_id}) with {model}:")
    for label, path in written:
        print(f"  {label:8} {path}")
    print("\nListen to them (or play them on the Echo), then:")
    print(f'  voices.py --set --voice-id {voice_id} --model {model}')
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    voice_id, name = resolve_voice(args)
    cfg = load_config()
    model = args.model or cfg.get("voice_model") or "eleven_v4"
    cfg["voice_id"] = voice_id
    cfg["voice_model"] = model
    cfg["voice_enabled"] = True
    if args.stability is not None:
        cfg["voice_stability"] = args.stability
    if args.similarity is not None:
        cfg["voice_similarity"] = args.similarity
    path = save_config(cfg)
    if args.json:
        print(json.dumps({"ok": True, "config": str(path), "voice_id": voice_id,
                          "name": name, "model": model}, indent=2))
        return 0
    print(f"voice  : {name} ({voice_id})")
    print(f"model  : {model}")
    print(f"written: {path}")
    print("\nRestart the service so it picks up the new voice, then:")
    print('  cli/venv/bin/python cli/notify.py "voice check"')
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Find and choose the voice the Echo speaks with.")
    p.add_argument("--status", action="store_true", help="what is configured right now")
    p.add_argument("--models", action="store_true", help="models and their real cost multiplier")
    p.add_argument("--list", action="store_true", help="voices available in the account")
    p.add_argument("--preview", action="store_true", help="render samples for one voice")
    p.add_argument("--set", action="store_true", help="write the choice to config.json")
    p.add_argument("--voice-id", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--model", default=None,
                   help="eleven_v4 | eleven_v3 | eleven_v4_turbo | eleven_flash_v2_5")
    p.add_argument("--text", default=None, help="custom preview line instead of the 3 defaults")
    p.add_argument("--stability", type=float, default=None, help="0-1: lower is more expressive")
    p.add_argument("--similarity", type=float, default=None, help="0-1: sticks to the source voice")
    p.add_argument("--gender", default=None, help="filter --list (female/male/neutral)")
    p.add_argument("--accent", default=None, help="filter --list (american/british/...)")
    p.add_argument("--use-case", dest="use_case", default=None, help="filter --list")
    p.add_argument("--search", default=None, help="filter --list by name/description")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.status:
        return cmd_status(args.json)
    if args.models:
        return cmd_models(args.json)
    if args.list:
        return cmd_list(args)
    if args.preview:
        return cmd_preview(args)
    if args.set:
        return cmd_set(args)

    p.print_help()
    print("\nStart with:  voices.py --status")
    return 0


if __name__ == "__main__":
    sys.exit(main())
