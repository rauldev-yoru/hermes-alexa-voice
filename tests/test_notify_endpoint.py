"""/notify: loopback only, validated input, one message per launch."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app import main

LOCAL = dict(base_url="http://127.0.0.1:8099", client=("127.0.0.1", 50000))


@pytest.fixture(autouse=True)
def empty_queue():
    main._PENDING.clear()
    yield
    main._PENDING.clear()


@pytest.fixture
def local():
    return TestClient(main.app, **LOCAL)


@pytest.mark.parametrize("path", ["/notify", "/alexa/notify"])
def test_loopback_request_is_queued(local, path):
    resp = local.post(path, json={"text": "backup done", "title": "Backup",
                                  "model": "eleven_v4_turbo", "chime": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["queued"] == "backup done"
    assert body["options"]["chime"] is False
    assert len(main._PENDING) == 1


def test_ipv6_loopback_is_accepted():
    client = TestClient(main.app, base_url="http://127.0.0.1:8099", client=("::1", 50000))
    resp = client.post("/notify", json={"text": "hi"}, headers={"Host": "[::1]:8099"})
    assert resp.status_code == 200


def test_remote_peer_is_forbidden():
    client = TestClient(main.app, base_url="http://127.0.0.1:8099", client=("203.0.113.9", 1))
    assert client.post("/notify", json={"text": "x"}).status_code == 403
    assert not main._PENDING


@pytest.mark.parametrize("header", [
    {"X-Forwarded-For": "203.0.113.9"},
    {"Forwarded": "for=203.0.113.9"},
    {"X-Real-IP": "203.0.113.9"},
    {"CF-Connecting-IP": "203.0.113.9"},
    {"Tailscale-Funnel-Request": "?1"},
    {"X-Forwarded-Host": "node.tail.ts.net"},
])
def test_requests_through_a_local_proxy_are_forbidden(local, header):
    # Funnel / cloudflared / nginx connect from 127.0.0.1: the peer address alone
    # let anyone on the internet make the Echo speak.
    resp = local.post("/alexa/notify", json={"text": "x"}, headers=header)
    assert resp.status_code == 403
    assert not main._PENDING


def test_public_host_header_is_forbidden(local):
    resp = local.post("/notify", json={"text": "x"}, headers={"Host": "node.example.com"})
    assert resp.status_code == 403


@pytest.mark.parametrize("payload,status", [
    (b"not json", 400),
    (b"[1, 2]", 400),
    (b'"text"', 400),
    (b"{}", 400),
    (b'{"text": "   "}', 400),
    (b'{"text": "x", "model": "gpt-4"}', 400),
])
def test_invalid_input(local, payload, status):
    resp = local.post("/notify", content=payload,
                      headers={"content-type": "application/json"})
    assert resp.status_code == status, resp.text
    assert not main._PENDING


def test_text_too_long_is_rejected(local):
    resp = local.post("/notify", json={"text": "a" * (main.NOTIFY_MAX_CHARS + 1)})
    assert resp.status_code == 400
    assert local.post("/notify", json={"text": "a" * main.NOTIFY_MAX_CHARS}).status_code == 200


def test_title_and_subtitle_are_clamped(local):
    local.post("/notify", json={"text": "x", "title": "T" * 200, "subtitle": "S" * 200})
    item = main._PENDING[0]
    assert len(item["title"]) == 60 and len(item["subtitle"]) == 60


def _launch_speech(resp) -> str:
    import json

    return json.loads(resp.body)["response"]["outputSpeech"]["ssml"]


def test_two_notifications_before_launch_are_both_delivered(local):
    # Two crons finishing together: each notify.py queues and rings once.
    local.post("/notify", json={"text": "first job done"})
    local.post("/notify", json={"text": "second job done"})
    first = asyncio.run(main.handle_launch({}, {}))
    second = asyncio.run(main.handle_launch({}, {}))
    assert "first job done" in _launch_speech(first)
    assert "second job done" in _launch_speech(second)
    third = asyncio.run(main.handle_launch({}, {}))
    assert main.WELCOME[:20] in _launch_speech(third)


def test_health_reports_pending_count(local):
    local.post("/notify", json={"text": "x"})
    assert local.get("/health").json()["pending_notify"] == 1
