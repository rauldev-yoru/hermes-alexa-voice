"""Shared test setup.

The service reads its configuration at import time, so the environment is fixed
here, before anything under ``service/app`` is imported:

* no real ``config.json`` (it may hold a live voice id / skill id)
* state in a throwaway directory
* voice disabled (empty ``voice_id``) so nothing ever calls ElevenLabs
* a known skill id, so the applicationId check is exercised
"""
from __future__ import annotations

import importlib.util
import itertools
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="alexa-voice-tests-"))
MEDIA = TMP / "media"
MEDIA.mkdir()
SKILL_ID = "amzn1.ask.skill.test-skill"

_ENV = {
    "ALEXA_VOICE_CONFIG": str(TMP / "no-config.json"),
    "ALEXA_STATE_DIR": str(TMP / "state"),
    "ALEXA_MEDIA_ROOT": str(MEDIA),
    "ALEXA_PUBLIC_BASE": "https://example.test/alexa",
    "ALEXA_VOICE_ID": "",
    "ALEXA_SKILL_ID": SKILL_ID,
    "ALEXA_SKIP_VERIFY": "0",
}
for _key in [k for k in os.environ if k.startswith("ALEXA_")]:
    del os.environ[_key]
os.environ.pop("ELEVENLABS_API_KEY", None)
os.environ.update(_ENV)

sys.path.insert(0, str(REPO / "service"))
sys.path.insert(0, str(REPO / "cli"))

_counter = itertools.count()


@pytest.fixture
def load_config(monkeypatch, tmp_path):
    """Import a fresh copy of config.py with a given config file and env."""

    def _load(file_data: dict | None = None, env: dict | None = None):
        for key in [k for k in os.environ if k.startswith("ALEXA_")]:
            monkeypatch.delenv(key)
        monkeypatch.setenv("ALEXA_STATE_DIR", str(tmp_path / "state"))
        cfg_file = tmp_path / "config.json"
        if file_data is not None:
            import json
            cfg_file.write_text(json.dumps(file_data), encoding="utf-8")
        monkeypatch.setenv("ALEXA_VOICE_CONFIG", str(cfg_file))
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        spec = importlib.util.spec_from_file_location(
            f"config_ut_{next(_counter)}", REPO / "service" / "app" / "config.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    return _load


@pytest.fixture
def media_root():
    return MEDIA
