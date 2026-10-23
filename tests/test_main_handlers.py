"""Request handlers: Alexa's 8 s budget, playback URLs, agent answers, tasks."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from app import agent, main


def _body(resp) -> dict:
    return json.loads(resp.body)


def test_voice_budget_fits_inside_alexas_8_seconds():
    assert main.VOICE_BUDGET < 8.0


def test_slow_tts_falls_back_to_builtin_voice(monkeypatch):
    release = threading.Event()

    def slow_render(text, **_kw):
        release.wait(2)
        return "https://example.test/voice/x.mp3"

    monkeypatch.setattr(main.voice, "render", slow_render)
    monkeypatch.setattr(main, "VOICE_BUDGET", 0.05)

    async def timed():
        # Measured inside the loop: that is when the response reaches Alexa.
        start = time.monotonic()
        resp = await main.voice_reply("hello there")
        elapsed = time.monotonic() - start
        release.set()
        return resp, elapsed

    resp, elapsed = asyncio.run(timed())
    assert elapsed < 1.0
    ssml = _body(resp)["response"]["outputSpeech"]["ssml"]
    assert "<audio" not in ssml and "hello there" in ssml


def test_fast_tts_uses_custom_audio(monkeypatch):
    monkeypatch.setattr(main.voice, "render",
                        lambda text, **_kw: "https://example.test/voice/a&b.mp3")
    ssml = _body(asyncio.run(main.voice_reply("hi")))["response"]["outputSpeech"]["ssml"]
    assert ssml == '<speak><audio src="https://example.test/voice/a&amp;b.mp3"/></speak>'


def test_spoken_text_is_escaped():
    ssml = _body(main.reply("a < b & <break/>"))["response"]["outputSpeech"]["ssml"]
    assert ssml == "<speak>a &lt; b &amp; &lt;break/&gt;</speak>"


@pytest.fixture
def track(media_root):
    (media_root / "my album").mkdir(exist_ok=True)
    path = media_root / "my album" / "song #1.mp3"
    path.write_bytes(b"ID3" + b"\0" * 64)
    yield "my album/song #1.mp3"
    main._QUEUES.clear()
    main._OFFSETS.clear()


def _stream_url(resp) -> str:
    return _body(resp)["response"]["directives"][0]["audioItem"]["stream"]["url"]


def test_resume_url_is_encoded(track):
    # The Play path used Track.url (quoted); Resume/Next built the URL raw, so a
    # space or '#' in a file name broke exactly those tracks.
    ctx = {"System": {"device": {"deviceId": "d1"}}}
    main._OFFSETS["d1"] = (track, 42_000)
    resp = asyncio.run(main.handle_resume_or_next({}, ctx, advance=False))
    url = _stream_url(resp)
    assert url == "https://example.test/alexa/media/my%20album/song%20%231.mp3"
    assert _body(resp)["response"]["directives"][0]["audioItem"]["stream"][
        "offsetInMilliseconds"] == 42_000


def test_next_url_is_encoded(track):
    ctx = {"System": {"device": {"deviceId": "d2"}}}
    main._QUEUES["d2"] = (["other.mp3", track], 0)
    resp = asyncio.run(main.handle_resume_or_next({}, ctx, advance=True))
    assert _stream_url(resp).endswith("/media/my%20album/song%20%231.mp3")


def test_playback_events_track_offsets(track):
    ctx = {"System": {"device": {"deviceId": "d3"}}}
    asyncio.run(main.handle_playback_event(
        {"token": f"0|{track}", "offsetInMilliseconds": 5000}, ctx,
        "AudioPlayer.PlaybackStopped"))
    assert main._OFFSETS["d3"] == (track, 5000)
    asyncio.run(main.handle_playback_event({"token": f"0|{track}"}, ctx,
                                           "AudioPlayer.PlaybackFinished"))
    assert "d3" not in main._OFFSETS


def test_agent_answer_is_made_speakable(monkeypatch):
    raw = "## Result\n**Done**: see https://example.com/x\n```py\nprint(1)\n```" + " more" * 3000

    async def fast_ask(_q):
        return raw

    monkeypatch.setattr(main, "AGENT_ENABLED", True)
    monkeypatch.setattr(main.agent, "ask", fast_ask)
    body = _body(asyncio.run(main._ask_agent("what?", {})))
    ssml = body["response"]["outputSpeech"]["ssml"]
    assert "**" not in ssml and "```" not in ssml and "https://" not in ssml
    assert len(ssml) < 8000  # Alexa's outputSpeech limit
    assert body["response"]["card"]["content"].startswith("## Result")


def test_slow_agent_is_deferred_and_tracked(monkeypatch):
    async def scenario():
        gate = asyncio.Event()

        async def slow_ask(_q):
            await gate.wait()
            return "late answer"

        spoken = []

        async def fake_notify_task(task, label, device):
            spoken.append(await task)

        monkeypatch.setattr(main, "AGENT_ENABLED", True)
        monkeypatch.setattr(main, "SYNC_BUDGET", 0.05)
        monkeypatch.setattr(main.agent, "ask", slow_ask)
        monkeypatch.setattr(main.agent, "notify_task", fake_notify_task)
        resp = await main._ask_agent("slow?", {})
        assert "outputSpeech" in _body(resp)["response"]
        assert main._TASKS  # agent task + notifier are referenced
        gate.set()
        for _ in range(50):
            if not main._TASKS:
                break
            await asyncio.sleep(0.01)
        return spoken

    assert asyncio.run(scenario()) == ["late answer"]
    assert not main._TASKS


def test_warmup_task_is_retained(monkeypatch):
    async def scenario():
        gate = threading.Event()
        monkeypatch.setattr(main.voice, "prerender", lambda texts: gate.wait(2) and 0)
        await main._warm_voice()
        held = set(main._TASKS)
        gate.set()
        for _ in range(100):
            if not main._TASKS:
                break
            await asyncio.sleep(0.01)
        return held

    held = asyncio.run(scenario())
    assert len(held) == 1  # a strong reference existed while it ran
    assert not main._TASKS


def test_agent_speaks_through_the_cli_venv(tmp_path, monkeypatch):
    # The service venv has no alexapy; notify.py must run in the CLI venv.
    fake = tmp_path / "python"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(agent, "CLI_PYTHON", fake)
    assert agent._cli_python() == str(fake)
    monkeypatch.setattr(agent, "CLI_PYTHON", tmp_path / "missing")
    import sys
    assert agent._cli_python() == sys.executable


def test_agent_speak_command(monkeypatch, tmp_path):
    fake = tmp_path / "python"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setattr(agent, "CLI_PYTHON", fake)
    seen = {}

    async def fake_exec(*cmd, **_kw):
        seen["cmd"] = cmd

        class Proc:
            returncode = 0

            async def communicate(self):
                return b"sent", b""

        return Proc()

    monkeypatch.setattr(agent.asyncio, "create_subprocess_exec", fake_exec)
    ok, _ = asyncio.run(agent.speak("**hello** world", "Kitchen"))
    assert ok
    assert seen["cmd"][0] == str(fake)
    assert seen["cmd"][2] == "hello world"
    assert seen["cmd"][-2:] == ("--device", "Kitchen")


def test_unknown_request_types_return_empty():
    assert _body(asyncio.run(main.dispatch({"request": {"type": "System.ExceptionEncountered"}}))) == {}
