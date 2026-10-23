"""Configuration for the Alexa voice-bridge service.

Everything is configurable in three layers, later wins:

1. `DEFAULTS` below
2. a JSON file (``$ALEXA_VOICE_CONFIG`` or ``<repo>/config.json``)
3. environment variables (``ALEXA_<KEY>``)

There is **no personal path, hostname, voice id or brand name** in this module —
that is the whole point: the repo has to install cleanly on someone else's
machine, in a different distro, with a different Echo.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

# service/app/config.py -> service/app -> service -> repo root
APP_DIR = Path(__file__).resolve().parent
SERVICE_DIR = APP_DIR.parent
REPO_DIR = SERVICE_DIR.parent

DEFAULTS: dict[str, Any] = {
    # Public HTTPS base Amazon calls. MUST be reachable from the internet with a
    # publicly trusted certificate: Alexa refuses self-signed endpoints.
    "public_base": "http://127.0.0.1:8099",
    "host": "127.0.0.1",
    "port": 8099,
    # Audio library (read-only). null disables the music feature.
    "media_root": None,
    # Where the gallery looks for pictures. Empty list disables the gallery.
    "image_roots": [],
    # Branding shown on screen and in the fixed replies.
    "brand_title": "AGENT",
    "brand_welcome": "Here. Ask me something, or ask me to play music.",
    "brand_help": (
        "You can say: play something. Show the newest pictures. "
        "Or ask me a question and I will answer. If I take long, I will tell you."
    ),
    # Illustration shown next to notifications (public URL).
    "art_image": None,
    # Text-to-speech. `voice_id` empty -> fall back to the device's own voice.
    "voice_enabled": True,
    "voice_id": "",
    "voice_model": "eleven_v4",
    # How the voice performs. stability: lower = more expressive, higher = more
    # consistent. similarity_boost: how tightly it sticks to the source voice.
    # style: exaggeration (only affects models that support it).
    "voice_stability": 0.4,
    "voice_similarity": 0.8,
    "voice_style": 0.45,
    "chime": True,
    # Optional bridge to a local agent CLI (Hermes by default). `agent_bin` null
    # disables the free-text question feature.
    "agent_bin": None,
    "agent_profile": None,
    "agent_timeout": 240,
    "agent_sync_budget": 4.0,
    # Which Echo to talk to, by device name. null = first one found.
    "device_name": None,
    # Alexa skill id (amzn1.ask.skill.…). Required by `notify` to ring the Echo
    # proactively; without it notifications can only be spoken on demand.
    "skill_id": None,
    # Amazon cookies harvested from a logged-in browser (path to JSON).
    "cookies_file": None,
    # Alexa request signature verification. Off is ONLY for localhost dev.
    "skip_verify": False,
    # Runtime files (voice cache, thumbs, logs). "$REPO" is expanded.
    "state_dir": None,
    # Spoken/printed phrases. Override any key here or in config.json["strings"]
    # to run the skill in another language without touching code.
    "strings": {},
    # Defaults for the `notify` CLI (cron notifications).
    "notify": {
        "model": "eleven_v4",
        "chime": True,
        "screen": True,
        "title": None,
        "subtitle": "",
        "quiet_hours": None,
        "dedupe_minutes": 5,
    },
}

DEFAULT_STRINGS: dict[str, str] = {
    "what_to_play": "What should I play?",
    "nothing_found": "I found nothing like {query}. I have {catalogue}.",
    "only_beeps": "I only found tones and alarms, not music. Try another title.",
    "playing": "Playing {title}.",
    "no_queue": "There is no active playlist.",
    "end_of_queue": "That was the end of the list.",
    "cannot_open": "I could not open that file.",
    "ask_prompt": "I did not catch that. What do you want to ask?",
    "ask_reprompt": "Tell me your question.",
    "no_agent": "No agent is connected to this skill. I can still play audio and show pictures.",
    "agent_slow": "On it. I will answer through the speaker in a moment.",
    "done": "Done.",
    "internal_error": "I hit an internal problem: {error}.",
    "empty_library": "The audio library is empty.",
    "gallery_empty": "I found no pictures in the gallery.",
    "gallery_failed": "I could not prepare the thumbnails.",
    "gallery_spoken": "Here are the {count} newest pictures.",
    "playing_subtitle": "Playing",
    "gallery_title": "GALLERY",
    "processing": "Processing",
}


def _config_path() -> Path | None:
    explicit = os.getenv("ALEXA_VOICE_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    candidate = REPO_DIR / "config.json"
    return candidate if candidate.is_file() else None


_TRUE = {"1", "true", "yes", "on"}


def _as_bool(value: Any) -> bool:
    # bool("false") is True: a quoted "false" must not silently flip a switch
    # such as skip_verify.
    if isinstance(value, str):
        return value.strip().lower() in _TRUE
    return bool(value)


def _from_file() -> dict[str, Any]:
    path = _config_path()
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    for key, default in DEFAULTS.items():
        if isinstance(default, bool) and key in data:
            data[key] = _as_bool(data[key])
    return data


def _from_env() -> dict[str, Any]:
    """Every key can be overridden by an ``ALEXA_<KEY>`` environment variable."""
    out: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        raw = os.getenv(f"ALEXA_{key.upper()}")
        if raw is None:
            continue
        if isinstance(default, bool):
            out[key] = _as_bool(raw)
        elif isinstance(default, dict):
            # Nested sections (strings, notify) take a JSON object.
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                out[key] = parsed
        elif isinstance(default, int):
            try:
                out[key] = int(raw)
            except ValueError:
                pass
        elif isinstance(default, float):
            try:
                out[key] = float(raw)
            except ValueError:
                pass
        elif isinstance(default, list):
            out[key] = [p for p in raw.split(os.pathsep) if p]
        elif default is None and raw.strip().lower() in {"", "none", "null"}:
            out[key] = None
        else:
            out[key] = raw
    return out


CFG: dict[str, Any] = {**DEFAULTS, **_from_file(), **_from_env()}

# Spoken phrases: defaults (English) merged with whatever the config overrides.
STRINGS: dict[str, str] = {**DEFAULT_STRINGS, **(CFG.get("strings") or {})}

# Notification defaults, with the brand title as the fallback header.
NOTIFY_CFG: dict[str, Any] = {**DEFAULTS["notify"], **(CFG.get("notify") or {})}
if not NOTIFY_CFG.get("title"):
    NOTIFY_CFG["title"] = str(CFG.get("brand_title") or "AGENT")


def s(key: str, **kwargs: Any) -> str:
    """Format a configured string, falling back to the default on bad kwargs."""
    template = STRINGS.get(key) or DEFAULT_STRINGS.get(key, key)
    try:
        return template.format(**kwargs) if kwargs else template
    except (KeyError, IndexError, ValueError):
        return template


def _path(value: Any) -> Path | None:
    if value in (None, "", "none", "null"):
        return None
    return Path(str(value).replace("$REPO", str(REPO_DIR))).expanduser()


# --- public surface (imported by the rest of the app) ---------------------- #
PUBLIC_BASE: str = str(CFG["public_base"]).rstrip("/")
HOST: str = str(CFG["host"])
PORT: int = int(CFG["port"])

_state = _path(CFG["state_dir"])
STATE_DIR: Path = _state or (SERVICE_DIR / "state")
STATE_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DIR = STATE_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MEDIA_ROOT: Path | None = _path(CFG["media_root"])
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".opus", ".ogg", ".wav"}

GALLERY_ROOTS: list[Path] = [p for p in (_path(v) for v in (CFG["image_roots"] or [])) if p]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

BRAND_TITLE: str = str(CFG["brand_title"])
BRAND_WELCOME: str = str(CFG["brand_welcome"])
BRAND_HELP: str = str(CFG["brand_help"])
ART_IMAGE: str | None = CFG["art_image"] or None

VOICE_ENABLED: bool = bool(CFG["voice_enabled"]) and bool(CFG["voice_id"])
VOICE_ID: str = str(CFG["voice_id"] or "")
VOICE_MODEL: str = str(CFG["voice_model"])
VOICE_STABILITY: float = float(CFG["voice_stability"])
VOICE_SIMILARITY: float = float(CFG["voice_similarity"])
VOICE_STYLE: float = float(CFG["voice_style"])
CHIME_ENABLED: bool = bool(CFG["chime"])

AGENT_BIN: str | None = CFG["agent_bin"] or None
AGENT_PROFILE: str | None = CFG["agent_profile"] or None
AGENT_TIMEOUT: int = int(CFG["agent_timeout"])
SYNC_BUDGET: float = float(CFG["agent_sync_budget"])
AGENT_ENABLED: bool = bool(AGENT_BIN)

DEVICE_NAME: str | None = CFG["device_name"] or None
SKILL_ID: str | None = CFG["skill_id"] or None
COOKIES_FILE: Path = _path(CFG["cookies_file"]) or (STATE_DIR / "credentials" / "amazon_cookies.json")
SKIP_VERIFY: bool = bool(CFG["skip_verify"])

# External binaries: never assume a path, ask the system.
FFMPEG = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/usr/bin/ffprobe"

# Bundled CAs used to validate Amazon's signature chain. The path differs per
# distro (Fedora has no /etc/pki/tls/certs/ca-bundle.crt), so pick the first that
# exists — a hardcoded guess makes every Alexa request fail with 400.
_CA_CANDIDATES = [
    os.getenv("SSL_CERT_FILE", ""),
    os.getenv("CA_BUNDLE", ""),
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/certs/ca-bundle.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",  # macOS, Alpine, OpenBSD
]


def _find_ca_bundle(candidates: list[str]) -> str:
    found = next((p for p in candidates if p and os.path.exists(p)), "")
    if found:
        return found
    # Last resort: certifi ships with httpx, so it is always installed.
    try:
        import certifi
    except ImportError:
        return ""
    path = certifi.where()
    return path if os.path.exists(path) else ""


CA_BUNDLE = _find_ca_bundle(_CA_CANDIDATES)


def summary() -> dict[str, Any]:
    """Non-secret view of the effective config, for /health and `doctor`."""
    return {
        "public_base": PUBLIC_BASE,
        "listen": f"{HOST}:{PORT}",
        "state_dir": str(STATE_DIR),
        "media_root": str(MEDIA_ROOT) if MEDIA_ROOT else None,
        "image_roots": [str(p) for p in GALLERY_ROOTS],
        "voice": {"enabled": VOICE_ENABLED, "model": VOICE_MODEL,
                  "voice_id": (VOICE_ID[:6] + "…") if VOICE_ID else None},
        "agent": {"enabled": AGENT_ENABLED, "bin": AGENT_BIN, "profile": AGENT_PROFILE},
        "verify": not SKIP_VERIFY,
        "ffmpeg": FFMPEG,
        "ca_bundle": CA_BUNDLE or None,
    }
