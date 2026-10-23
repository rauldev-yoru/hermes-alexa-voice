"""Audio catalogue and image gallery served to the Echo.

The music feature is optional: with ``media_root`` unset the catalogue is simply
empty and the skill tells the user so. Nothing crashes.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .config import (
    AUDIO_EXTS,
    FFMPEG,
    FFPROBE,
    GALLERY_ROOTS,
    IMAGE_EXTS,
    MEDIA_ROOT,
    PUBLIC_BASE,
    STATE_DIR,
)


@dataclass(frozen=True)
class Track:
    path: Path
    rel: str
    title: str
    folder: str

    @property
    def url(self) -> str:
        return f"{PUBLIC_BASE}/media/{quote(self.rel)}"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


_CACHE: dict[str, object] = {"stamp": None, "tracks": []}


def catalogue(refresh: bool = False) -> list[Track]:
    """Scan the media root for playable audio. Empty when music is disabled."""
    root = MEDIA_ROOT
    if root is None or not root.exists():
        return []
    try:
        stamp = max(
            (p.stat().st_mtime for p in [root, *root.rglob("*")] if p.is_file() or p == root),
            default=0.0,
        )
    except OSError:
        stamp = 0.0
    if not refresh and _CACHE["stamp"] == stamp:
        return _CACHE["tracks"]  # type: ignore[return-value]

    tracks: list[Track] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
            continue
        tracks.append(
            Track(path=path, rel=str(path.relative_to(root)),
                  title=path.stem, folder=path.parent.name)
        )
    _CACHE["stamp"] = stamp
    _CACHE["tracks"] = tracks
    return tracks


def search(query: str, limit: int = 5) -> list[Track]:
    """Rank tracks against a spoken query: exact > substring > token overlap."""
    needle = normalize(query)
    if not needle:
        return []
    tokens = [t for t in needle.split() if len(t) > 1]
    scored: list[tuple[float, Track]] = []

    for track in catalogue():
        hay = normalize(f"{track.title} {track.folder}")
        score = 0.0
        if needle == normalize(track.title):
            score = 100.0
        elif needle in hay:
            score = 60.0 + 20.0 * (len(needle) / max(len(hay), 1))
        elif tokens:
            hits = sum(1 for t in tokens if t in hay)
            if hits:
                score = 40.0 * (hits / len(tokens))
        if score:
            scored.append((score, track))

    scored.sort(key=lambda pair: (-pair[0], len(pair[1].title)))
    return [track for _score, track in scored[:limit]]


def resolve(rel: str) -> Path | None:
    """Map a public request back to a file, refusing anything outside the root."""
    if MEDIA_ROOT is None:
        return None
    candidate = (MEDIA_ROOT / rel).resolve()
    root = MEDIA_ROOT.resolve()
    if not str(candidate).startswith(str(root) + "/"):
        return None
    if not candidate.is_file() or candidate.suffix.lower() not in AUDIO_EXTS:
        return None
    return candidate


_DURATIONS: dict[str, float] = {}


def duration(rel: str) -> float:
    """Track length in seconds (ffprobe, cached). 0.0 when unknown."""
    if rel in _DURATIONS:
        return _DURATIONS[rel]
    path = resolve(rel)
    if path is None:
        return 0.0
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        value = float((out.stdout or "0").strip() or 0)
    except (subprocess.SubprocessError, ValueError):
        value = 0.0
    _DURATIONS[rel] = value
    return value


def clock(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def describe() -> str:
    tracks = catalogue()
    if not tracks:
        return "no tracks yet"
    folders = sorted({t.folder for t in tracks})
    return f"{len(tracks)} tracks in {len(folders)} folders ({', '.join(folders)})"


# --------------------------------------------------------------------------- #
# Image gallery
# --------------------------------------------------------------------------- #
THUMB_DIR = STATE_DIR / "thumbs"
THUMB_DIR.mkdir(parents=True, exist_ok=True)


def gallery_enabled() -> bool:
    return bool(GALLERY_ROOTS)


def recent_images(limit: int = 4) -> list[Path]:
    """Newest images across the configured gallery roots."""
    files: list[Path] = []
    for root in GALLERY_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            try:
                if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
                    files.append(path)
            except OSError:
                continue
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def thumbnail(path: Path, width: int = 390, height: int = 300) -> str | None:
    """Cropped JPEG thumbnail, cached by path+size. Returns the file name."""
    key = hashlib.sha1(f"{path}|{width}x{height}".encode()).hexdigest()[:12]
    out = THUMB_DIR / f"{key}.jpg"
    if out.exists() and out.stat().st_size > 500:
        return out.name
    try:
        subprocess.run(
            [FFMPEG, "-y", "-loglevel", "error", "-i", str(path),
             "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height}",
             "-q:v", "4", str(out)],
            check=True, timeout=25,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return out.name if out.exists() else None


def thumb_path(name: str) -> Path | None:
    target = (THUMB_DIR / name).resolve()
    if not str(target).startswith(str(THUMB_DIR.resolve()) + "/"):
        return None
    return target if target.is_file() else None
