"""Path traversal: /media, /voice, /thumbs and /art only serve their own files."""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app import main, media


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def files(media_root):
    (media_root / "album").mkdir(exist_ok=True)
    (media_root / "album" / "song one.mp3").write_bytes(b"ID3" + b"\0" * 64)
    (media_root / "notes.txt").write_text("not audio")
    secret = media_root.parent / "secret.mp3"
    secret.write_bytes(b"ID3 secret")
    link = media_root / "escape.mp3"
    if not link.exists():
        os.symlink(secret, link)
    main.VOICE_DIR.mkdir(parents=True, exist_ok=True)
    (main.VOICE_DIR / "abc.mp3").write_bytes(b"ID3 voice")
    (main.VOICE_DIR.parent / "outside.mp3").write_bytes(b"ID3 outside")
    media.THUMB_DIR.mkdir(parents=True, exist_ok=True)
    (media.THUMB_DIR / "t.jpg").write_bytes(b"\xff\xd8 jpg")
    yield


@pytest.mark.parametrize("rel", ["album/song one.mp3"])
def test_resolve_accepts_files_inside_root(rel):
    assert media.resolve(rel) is not None


@pytest.mark.parametrize("rel", [
    "../secret.mp3",
    "album/../../secret.mp3",
    "/etc/passwd",
    str(os.path.abspath("/etc/hosts")),
    "escape.mp3",          # symlink pointing outside the root
    "notes.txt",           # not an audio extension
    "album",               # a directory
    "",
])
def test_resolve_rejects_everything_else(rel):
    assert media.resolve(rel) is None


@pytest.mark.parametrize("prefix", ["", "/alexa"])
def test_media_route_serves_and_refuses(client, prefix):
    ok = client.get(f"{prefix}/media/album/song%20one.mp3")
    assert ok.status_code == 200 and ok.content.startswith(b"ID3")
    for bad in ("..%2Fsecret.mp3", "album/..%2F..%2Fsecret.mp3", "%2Fetc%2Fpasswd",
                "escape.mp3", "notes.txt"):
        resp = client.get(f"{prefix}/media/{bad}")
        assert resp.status_code == 404, bad
        assert b"secret" not in resp.content


@pytest.mark.parametrize("prefix", ["", "/alexa"])
def test_voice_route(client, prefix):
    assert client.get(f"{prefix}/voice/abc.mp3").status_code == 200
    for bad in ("..%2Foutside.mp3", "%2E%2E%2Foutside.mp3", "missing.mp3"):
        assert client.get(f"{prefix}/voice/{bad}").status_code == 404, bad


@pytest.mark.parametrize("prefix", ["", "/alexa"])
def test_thumb_and_art_routes(client, prefix):
    assert client.get(f"{prefix}/thumbs/t.jpg").status_code == 200
    assert client.get(f"{prefix}/thumbs/..%2Fvoice%2Fabc.mp3").status_code == 404
    assert client.get(f"{prefix}/art/..%2Fvoice%2Fabc.mp3").status_code == 404
    assert client.get(f"{prefix}/art/missing.png").status_code == 404


def test_safe_file_rejects_sibling_prefix(tmp_path):
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice-evil").mkdir()
    (tmp_path / "voice-evil" / "x.mp3").write_bytes(b"x")
    assert main._safe_file(tmp_path / "voice", "../voice-evil/x.mp3", (".mp3",)) is None
