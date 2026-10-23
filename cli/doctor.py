#!/usr/bin/env python3
"""Preflight check: is this machine able to run the Alexa voice bridge?

Designed for an agent (or a human in a hurry) to run right after install and
know exactly what is missing. Every check prints one of::

    [OK]    working
    [WARN]  works, but a feature will be degraded
    [FAIL]  this blocks delivery — the fix is printed next to it

Exit code: 0 when there is no FAIL, 1 otherwise.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "service"))

results: list[tuple[str, str, str]] = []  # (level, name, detail)


def ok(name: str, detail: str = "") -> None:
    results.append(("OK", name, detail))


def warn(name: str, detail: str) -> None:
    results.append(("WARN", name, detail))


def fail(name: str, detail: str) -> None:
    results.append(("FAIL", name, detail))


def check_python() -> None:
    v = sys.version_info
    if v >= (3, 10):
        ok("python", f"{v.major}.{v.minor}.{v.micro} ({sys.executable})")
    else:
        fail("python", f"{v.major}.{v.minor} is too old — need 3.10+")


def _service_python() -> str:
    """Prefer the service venv's interpreter for import checks."""
    candidate = REPO / "service" / "venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def check_module(module: str, package: str, level: str = "FAIL") -> bool:
    """Check an import in the interpreter that will actually run the service."""
    import subprocess

    python = _service_python()
    proc = subprocess.run([python, "-c", f"import {module}"],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        ok(module, f"importable in {python}")
        return True
    (fail if level == "FAIL" else warn)(
        module, f"missing in {python} — {python} -m pip install {package}"
    )
    return False


def check_bins() -> None:
    for name, why in (("ffmpeg", "required to convert TTS audio into Alexa's format"),
                      ("ffprobe", "required to read track durations"),
                      ("openssl", "required to validate Amazon's request signature")):
        path = shutil.which(name)
        if path:
            ok(name, path)
        else:
            fail(name, f"not found — {why}")


def check_config() -> None:
    try:
        from app.config import (  # noqa: F401
            CA_BUNDLE, CFG, COOKIES_FILE, FFMPEG, GALLERY_ROOTS, MEDIA_ROOT,
            PUBLIC_BASE, SKILL_ID, SKIP_VERIFY, STATE_DIR, VOICE_ENABLED, VOICE_ID,
        )
    except Exception as exc:  # noqa: BLE001
        fail("config", f"cannot import service config: {exc}")
        return

    if CFG.get("public_base", "").startswith("https://"):
        ok("public_base", PUBLIC_BASE)
    else:
        fail("public_base",
             f"{PUBLIC_BASE} is not https — Alexa only calls https endpoints with a "
             "publicly trusted certificate")

    if SKIP_VERIFY:
        warn("signature verification", "DISABLED — only acceptable on localhost")
    elif CA_BUNDLE:
        ok("signature verification", f"enabled, CA bundle {CA_BUNDLE}")
    else:
        fail("signature verification",
             "enabled but no CA bundle found — set SSL_CERT_FILE or CA_BUNDLE")

    if VOICE_ENABLED:
        if os.getenv("ELEVENLABS_API_KEY"):
            ok("voice", f"ElevenLabs key in env, voice {VOICE_ID[:8]}…")
        else:
            key_files = [REPO / ".env", Path.home() / ".hermes" / ".env"]
            if any(p.is_file() and "ELEVENLABS_API_KEY" in p.read_text(errors="ignore")
                   for p in key_files):
                ok("voice", f"ElevenLabs key found in a .env file, voice {VOICE_ID[:8]}…")
            else:
                fail("voice", "voice_id is set but ELEVENLABS_API_KEY is nowhere — "
                              "put it in <repo>/.env or the environment")
    else:
        warn("voice", "no voice_id configured — the device's built-in voice will be used")

    if SKILL_ID:
        ok("skill_id", SKILL_ID)
    else:
        fail("skill_id", "missing — notifications cannot ring the Echo without it")

    if COOKIES_FILE.is_file():
        try:
            data = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
            count = len((data or {}).get("cookies") or {})
            if count >= 5:
                ok("amazon cookies", f"{count} cookies in {COOKIES_FILE}")
            else:
                fail("amazon cookies", f"only {count} cookies — re-harvest them")
        except json.JSONDecodeError:
            fail("amazon cookies", f"{COOKIES_FILE} is not valid JSON")
    else:
        fail("amazon cookies", f"missing {COOKIES_FILE} — see docs/COOKIES.md")

    if MEDIA_ROOT is None:
        warn("media library", "media_root unset — music playback disabled")
    elif MEDIA_ROOT.is_dir():
        ok("media library", str(MEDIA_ROOT))
    else:
        warn("media library", f"{MEDIA_ROOT} does not exist")

    if GALLERY_ROOTS:
        existing = [str(p) for p in GALLERY_ROOTS if p.is_dir()]
        (ok if existing else warn)("gallery", ", ".join(existing) or "configured paths missing")
    else:
        warn("gallery", "no image_roots — the gallery screen is disabled")

    ok("state dir", str(STATE_DIR))


def check_service() -> None:
    try:
        import httpx
    except ImportError:
        warn("service health", "httpx missing, cannot probe the local service")
        return
    try:
        from app.config import HOST, PORT, PUBLIC_BASE
    except Exception:  # noqa: BLE001
        return
    try:
        r = httpx.get(f"http://{HOST}:{PORT}/health", timeout=5)
        body = r.json()
        ok("local service", f"{HOST}:{PORT} ok, {body.get('tracks', 0)} tracks")
    except Exception as exc:  # noqa: BLE001
        fail("local service", f"http://{HOST}:{PORT}/health unreachable ({exc.__class__.__name__})"
                              " — start it: systemctl --user start alexa-voice")
        return
    try:
        r = httpx.get(f"{PUBLIC_BASE}/health", timeout=10)
        ok("public endpoint", f"{PUBLIC_BASE}/health -> {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        fail("public endpoint",
             f"{PUBLIC_BASE}/health unreachable ({exc.__class__.__name__}) — expose the "
             "service over https (Tailscale Funnel, cloudflared, nginx…)")


def check_alexa() -> None:
    try:
        import alexapy  # noqa: F401
    except ImportError:
        warn("alexapy", "not installed in this interpreter — run: pip install -r cli/requirements.txt")
        return
    ok("alexapy", "installed")


def main() -> int:
    check_python()
    for module, package in (("fastapi", "fastapi"), ("uvicorn", "uvicorn"),
                            ("httpx", "httpx"), ("cryptography", "cryptography")):
        check_module(module, package)
    check_bins()
    check_config()
    check_service()
    check_alexa()

    if "--json" in sys.argv[1:]:
        print(json.dumps({
            "checks": [{"level": lvl, "name": name, "detail": detail}
                       for lvl, name, detail in results],
            "failures": sum(1 for lvl, _n, _d in results if lvl == "FAIL"),
            "warnings": sum(1 for lvl, _n, _d in results if lvl == "WARN"),
        }, indent=2, ensure_ascii=False))
        return 1 if any(lvl == "FAIL" for lvl, _n, _d in results) else 0

    width = max(len(name) for _level, name, _detail in results) + 2
    for level, name, detail in results:
        print(f"[{level:4}] {name:<{width}} {detail}")

    failures = sum(1 for level, _n, _d in results if level == "FAIL")
    warnings = sum(1 for level, _n, _d in results if level == "WARN")
    print(f"\n{failures} failure(s), {warnings} warning(s)")
    if failures:
        print("\nFix the FAIL lines above, then run `doctor` again.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
