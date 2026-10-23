"""Custom TTS voice rendered into the format Alexa accepts inside SSML.

Alexa's SSML ``<audio src="...">`` element requires **MP3, 48 kbps, 16 kHz,
mono**. Higher sample rates are downloaded by the device and then silently
dropped, which looks exactly like "the file is fine but nothing plays".

Two hard-won facts (Echo Show 5, firmware 2026-10):

* The system skill behind ``Alexa.Speak`` (Simon Says) **fetches the file and
  never plays it**. Custom audio only works from inside a *custom skill*
  response, where the SSML is ours end to end.
* ElevenLabs output sits around -28 dBFS and sounds faint on an Echo. Everything
  is normalised to -16 LUFS with a -1.5 dBTP ceiling before it ships.

The provider is ElevenLabs; the voice id and model come from config, and with
``voice_id`` empty the service falls back to the device's built-in voice (Polly)
without any code change.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import httpx

from .config import (
    ART_IMAGE,
    CHIME_ENABLED,
    FFMPEG,
    PUBLIC_BASE,
    REPO_DIR,
    STATE_DIR,
    VOICE_ENABLED,
    VOICE_ID,
    VOICE_MODEL,
)

VOICE_DIR = STATE_DIR / "voice"
VOICE_DIR.mkdir(parents=True, exist_ok=True)

CHIME = VOICE_DIR / "chime.mp3"
MIN_BYTES = 2000
CACHE_VERSION = "v1"
ELEVEN_BASE = "https://api.elevenlabs.io/v1"

# Where to look for ELEVENLABS_API_KEY when it is not in the process env.
_ENV_CANDIDATES = [
    REPO_DIR / ".env",
    Path(os.environ.get("HERMES_HOME", "")) / ".env" if os.environ.get("HERMES_HOME") else None,
    Path.home() / ".hermes" / ".env",
]


def _api_key() -> str | None:
    key = os.getenv("ELEVENLABS_API_KEY")
    if key:
        return key.strip()
    for path in _ENV_CANDIDATES:
        if path is None:
            continue
        try:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("ELEVENLABS_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            continue
    return None


def available() -> tuple[bool, str]:
    """(ok, reason) — what `doctor` reports for the voice pipeline."""
    if not VOICE_ENABLED:
        return False, "voice disabled in config (set voice_id to enable)"
    if not _api_key():
        return False, "ELEVENLABS_API_KEY not found (env, <repo>/.env or ~/.hermes/.env)"
    if not Path(FFMPEG).exists():
        return False, f"ffmpeg not found at {FFMPEG}"
    return True, "ok"


def ensure_chime() -> None:
    """Create a small two-tone chime the first time it is needed.

    Keeps the repo self-contained: no binary asset to ship, no download step.
    """
    if CHIME.exists() and CHIME.stat().st_size > MIN_BYTES:
        return
    try:
        subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error",
             "-f", "lavfi", "-i",
             "sine=frequency=880:duration=0.12,volume=0.35",
             "-f", "lavfi", "-i",
             "sine=frequency=1320:duration=0.18,volume=0.30",
             "-filter_complex",
             "[0][1]concat=n=2:v=0:a=1,afade=t=out:st=0.24:d=0.08",
             "-ac", "1", "-ar", "16000", "-b:a", "48k",
             "-codec:a", "libmp3lame", str(CHIME)],
            check=True, timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        pass


def _synthesize(text: str, raw: Path, voice_id: str, model_id: str) -> None:
    key = _api_key()
    if not key:
        raise RuntimeError("no ELEVENLABS_API_KEY")
    settings: dict[str, float] = {"stability": 0.4, "similarity_boost": 0.8}
    if model_id.startswith("eleven_flash") or model_id.endswith("_turbo"):
        settings["style"] = 0.45
    payload = {"text": text, "model_id": model_id, "voice_settings": settings}
    with httpx.Client(timeout=45) as client:
        resp = client.post(
            f"{ELEVEN_BASE}/text-to-speech/{voice_id}",
            headers={"xi-api-key": key, "accept": "audio/mpeg",
                     "content-type": "application/json"},
            content=json.dumps(payload),
            params={"output_format": "mp3_44100_128"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"ElevenLabs {resp.status_code}: {resp.text[:200]}")
    raw.write_bytes(resp.content)


def _to_alexa_mp3(src: Path, dst: Path) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", str(src),
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
         "-ac", "1", "-ar", "16000", "-b:a", "48k",
         "-codec:a", "libmp3lame", str(dst)],
        check=True, timeout=45,
    )


def _concat_chime(src: Path, dst: Path) -> None:
    """Chime followed by the spoken line, as a single clip."""
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", str(CHIME), "-i", str(src),
         "-filter_complex", "[0][1]concat=n=2:v=0:a=1",
         "-ac", "1", "-ar", "16000", "-b:a", "48k",
         "-codec:a", "libmp3lame", str(dst)],
        check=True, timeout=45,
    )


def render(text: str, *, chime: bool = False, voice_id: str | None = None,
           model_id: str | None = None) -> str | None:
    """Public URL for `text` spoken in the configured voice, or None.

    Returns None whenever the pipeline is unavailable, so callers fall back to
    the built-in voice instead of going silent.
    """
    text = text.strip()
    if not text or not VOICE_ENABLED:
        return None
    voice = voice_id or VOICE_ID
    model = model_id or VOICE_MODEL
    if not voice:
        return None

    use_chime = chime and CHIME_ENABLED
    if use_chime:
        ensure_chime()
        use_chime = CHIME.exists()

    stem = hashlib.sha1(f"{voice}|{model}|{text}".encode("utf-8")).hexdigest()[:12]
    plain = VOICE_DIR / f"{stem}{CACHE_VERSION}.mp3"
    out = VOICE_DIR / f"{stem}{CACHE_VERSION}c.mp3" if use_chime else plain

    if out.exists() and out.stat().st_size > MIN_BYTES:
        return f"{PUBLIC_BASE}/voice/{out.name}"

    raw = VOICE_DIR / f"{stem}_raw.mp3"
    try:
        _synthesize(text, raw, voice, model)
        _to_alexa_mp3(raw, plain)
        if plain.stat().st_size <= MIN_BYTES:
            return None
        if use_chime:
            _concat_chime(plain, out)
        return f"{PUBLIC_BASE}/voice/{out.name}"
    except Exception:  # noqa: BLE001
        return None
    finally:
        raw.unlink(missing_ok=True)


def prerender(texts: list[str]) -> int:
    """Warm the cache for fixed phrases so the first reply has no latency."""
    return sum(1 for text in texts if render(text))


def art_url() -> str | None:
    """The illustration shown next to notifications, or None."""
    if ART_IMAGE:
        return ART_IMAGE
    candidate = STATE_DIR / "art" / "portrait.png"
    if candidate.is_file():
        return f"{PUBLIC_BASE}/art/portrait.png"
    return None
