"""Alexa custom-skill endpoint: the voice and screen surface for an agent.

Routes are registered twice (``/x`` and ``/alexa/x``) because a reverse proxy
configured with a path prefix (Tailscale Funnel ``--set-path``, nginx, Caddy)
may or may not strip it depending on version and config.

Returning fast is non-negotiable: Alexa gives the endpoint 8 seconds. Anything
that needs the agent runs as a background task and speaks the answer through the
Echo later, via ``cli/notify.py``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from . import agent, apl, media, voice
from .config import (
    AGENT_ENABLED,
    BRAND_HELP,
    BRAND_TITLE,
    BRAND_WELCOME,
    DEVICE_NAME,
    GALLERY_ROOTS,
    HOST,
    MEDIA_ROOT,
    PUBLIC_BASE,
    SKILL_ID,
    SKIP_VERIFY,
    STATE_DIR,
    SYNC_BUDGET,
    s,
    summary,
)
from .verify import SignatureError, check_application, verify_request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("alexa.skill")

app = FastAPI(title="Alexa voice bridge", docs_url=None, redoc_url=None)

WELCOME = BRAND_WELCOME
HELP = BRAND_HELP

# In-memory per-device state. Fine for a single-household service; restarting
# loses the queue and the playback offsets, nothing else.
_QUEUES: dict[str, tuple[list[str], int]] = {}
_OFFSETS: dict[str, tuple[str, int]] = {}
_TASKS: set[asyncio.Task] = set()
# FIFO, one entry per notify.py call: each call rings the Echo once, and each
# LaunchRequest takes one message. A single slot let two close crons overwrite
# each other — the first message was lost and the second launch got WELCOME.
_PENDING: deque[dict] = deque(maxlen=20)
_GALLERY: dict[str, list[str]] = {}
_ALLOWED_MODELS = {"eleven_v4", "eleven_v3", "eleven_v4_turbo", "eleven_flash_v2_5"}
# Alexa abandons the request after 8 s; past this the built-in voice answers.
VOICE_BUDGET = 6.0
NOTIFY_MAX_CHARS = 2000
# Set by reverse proxies (Tailscale Funnel/Serve, cloudflared, nginx when
# configured). Their presence means the "loopback" peer is really the internet.
_PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip",
                  "cf-connecting-ip", "tailscale-funnel-request")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
ART_DIR = STATE_DIR / "art"
VOICE_DIR = STATE_DIR / "voice"


def ssml(text: str) -> str:
    return f"<speak>{escape(text)}</speak>"


def reply(
    text: str | None = None,
    *,
    end: bool = True,
    reprompt: str | None = None,
    directives: list[dict] | None = None,
    card_title: str | None = None,
    card_text: str | None = None,
    full_text: bool = False,
    audio_url: str | None = None,
    card_image: bool = False,
    apl_screen: bool = False,
    apl_subtitle: str = "",
) -> JSONResponse:
    response: dict[str, Any] = {"shouldEndSession": end}
    if audio_url:
        # Custom voice. Only valid inside a skill response — the system skill
        # behind Alexa.Speak fetches the file and never plays it.
        response["outputSpeech"] = {
            "type": "SSML",
            "ssml": f'<speak><audio src="{escape(audio_url, quote=True)}"/></speak>',
        }
    elif text:
        response["outputSpeech"] = {"type": "SSML", "ssml": ssml(text)}
    if reprompt:
        response["reprompt"] = {"outputSpeech": {"type": "SSML", "ssml": ssml(reprompt)}}
    directives = list(directives or [])
    if apl_screen:
        directives.append(apl.render_directive(text or "", subtitle=apl_subtitle or BRAND_TITLE))
    response["directives"] = directives

    art = voice.art_url() if card_image else None
    if art:
        response["card"] = {
            "type": "Standard",
            "title": (card_title or BRAND_TITLE)[:120],
            "text": (card_text or text or "")[:4000],
            "image": {"smallImageUrl": art, "largeImageUrl": art},
        }
    elif card_title or full_text:
        card: dict[str, Any] = {"type": "Simple", "title": card_title or BRAND_TITLE}
        if card_text:
            card["content"] = card_text
        elif full_text and text:
            card["content"] = text
        response["card"] = card
    return JSONResponse({"version": "1.0", "response": response})


async def voice_reply(text: str, *, chime: bool = False, model: str | None = None,
                      **kwargs: Any) -> JSONResponse:
    """Reply with the custom voice, falling back to the built-in one."""
    url = None
    try:
        url = await asyncio.wait_for(
            asyncio.to_thread(voice.render, text, chime=chime, model_id=model),
            timeout=VOICE_BUDGET,
        )
    except Exception:  # noqa: BLE001
        url = None
    if url is None:
        log.info("custom voice unavailable, using built-in: %r", text[:60])
    return reply(text, audio_url=url, **kwargs)


def empty() -> JSONResponse:
    """Response for AudioPlayer/System events: Alexa only wants an empty 200."""
    return JSONResponse({})


def play_directive(token: str, url: str, offset: int = 0) -> dict:
    return {
        "type": "AudioPlayer.Play",
        "playBehavior": "REPLACE_ALL",
        "audioItem": {
            "stream": {"token": token, "url": url, "offsetInMilliseconds": offset}
        },
    }


def stop_directive() -> dict:
    return {"type": "AudioPlayer.Stop"}


def _slot(intent: dict, *names: str) -> str:
    slots = intent.get("slots") or {}
    for name in names:
        value = (slots.get(name) or {}).get("value")
        if value:
            return value.strip()
    return ""


def _media_url(rel: str) -> str:
    # Same encoding as media.Track.url: a raw space or '#' in a file name makes
    # the Echo fail the stream, so Resume/Next broke on exactly those tracks.
    return f"{PUBLIC_BASE}/media/{quote(rel)}"


def _device_id(ctx: dict) -> str:
    return ((ctx.get("System") or {}).get("device") or {}).get("deviceId", "unknown")


# --------------------------------------------------------------------------- #
# Intent handlers
# --------------------------------------------------------------------------- #
async def handle_launch(event: dict, ctx: dict) -> JSONResponse:
    item = _PENDING.popleft() if _PENDING else None
    if item:
        log.info("launch delivered queued message: %r", item["text"][:70])
        return await voice_reply(
            item["text"],
            chime=item.get("chime", True),
            model=item.get("model"),
            card_title=item.get("title", BRAND_TITLE),
            card_image=item.get("screen", True),
            apl_screen=item.get("screen", True),
            apl_subtitle=item.get("subtitle", ""),
        )
    return await voice_reply(
        WELCOME, end=False, reprompt=WELCOME, card_title=BRAND_TITLE,
        card_image=True, apl_screen=True, apl_subtitle="",
    )


async def handle_play(event: dict, ctx: dict) -> JSONResponse:
    intent = event["intent"]
    query = _slot(intent, "que", "cancion", "song", "consulta", "query")
    if not query:
        return reply(s("what_to_play"), end=False, reprompt=s("what_to_play"))

    tracks = media.search(query)
    if not tracks:
        return reply(
            s("nothing_found", query=query, catalogue=media.describe()),
            card_title="No results", card_text=f"Query: {query}",
        )

    beeps = [
        t for t in tracks
        if re.search(r"(beep|bip|alarm|notificaci|tone)", media.normalize(t.title))
    ]
    if beeps and len(beeps) == len(tracks):
        return reply(s("only_beeps"), card_title="No music")

    first = tracks[0]
    _QUEUES[_device_id(ctx)] = ([t.rel for t in tracks], 0)
    log.info("play %r -> %s (%d candidates)", query, first.rel, len(tracks))

    # No APL here on purpose: while AudioPlayer runs the device swaps to its own
    # native player and any APL of ours is replaced.
    return await voice_reply(
        s("playing", title=first.title.replace("_", " ")),
        directives=[play_directive(f"0|{first.rel}", first.url)],
        card_title=s("playing_subtitle"), card_text=first.title,
    )


async def handle_ask(event: dict, ctx: dict) -> JSONResponse:
    intent = event["intent"]
    question = _slot(intent, "consulta", "pregunta", "question", "query", "que")
    if not question:
        return reply(s("ask_prompt"), end=False, reprompt=s("ask_reprompt"))
    return await _ask_agent(question, ctx)


async def _ask_agent(question: str, ctx: dict) -> JSONResponse:
    if not AGENT_ENABLED:
        return reply(s("no_agent"), card_title=BRAND_TITLE, full_text=True)

    task = asyncio.create_task(agent.ask(question))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)

    try:
        answer = await asyncio.wait_for(asyncio.shield(task), timeout=SYNC_BUDGET)
        # Raw agent output is markdown of any length; Alexa rejects outputSpeech
        # over 8000 chars. Speak the cleaned version, keep the full one on the card.
        spoken = agent.for_speech(answer) or s("done")
        return reply(spoken, card_title=BRAND_TITLE, card_text=answer[:4000])
    except asyncio.TimeoutError:
        notifier = asyncio.create_task(agent.notify_task(task, question[:60], DEVICE_NAME))
        _TASKS.add(notifier)
        notifier.add_done_callback(_TASKS.discard)
        log.info("agent slow, notifying async: %r", question[:80])
        return reply(s("agent_slow"), card_title=s("processing"), card_text=question)


async def handle_list(event: dict, ctx: dict) -> JSONResponse:
    tracks = media.catalogue()
    if not tracks:
        return reply(s("empty_library"))
    by_folder: dict[str, list[str]] = {}
    for track in tracks:
        by_folder.setdefault(track.folder, []).append(track.title)
    lines = [f"{folder}: {', '.join(sorted(titles))}"
             for folder, titles in sorted(by_folder.items())]
    return await voice_reply(
        media.describe(), card_title="Library", card_text="\n\n".join(lines)
    )


async def handle_resume_or_next(event: dict, ctx: dict, advance: bool = False,
                                delta: int = 1) -> JSONResponse:
    device = _device_id(ctx)
    queue, index = _QUEUES.get(device, ([], -1))

    # Resume: continue the last track from the offset Amazon reported.
    if not advance:
        saved = _OFFSETS.get(device)
        if saved:
            rel, offset = saved
            if media.resolve(rel) is not None:
                try:
                    idx = queue.index(rel)
                except ValueError:
                    idx = index
                _QUEUES[device] = (queue or [rel], idx)
                log.info("resume %s at %ss", rel, offset // 1000)
                return JSONResponse({
                    "version": "1.0",
                    "response": {
                        "directives": [
                            play_directive(f"{idx}|{rel}", _media_url(rel), offset)
                        ],
                        "shouldEndSession": True,
                    },
                })

    if not queue:
        return reply(s("no_queue"))
    next_index = index + delta if advance else index
    if next_index < 0:
        next_index = 0
    if next_index >= len(queue):
        return reply(s("end_of_queue"))
    rel = queue[next_index]
    _QUEUES[device] = (queue, next_index)
    if media.resolve(rel) is None:
        return reply(s("cannot_open"))
    return JSONResponse({
        "version": "1.0",
        "response": {
            "directives": [play_directive(f"{next_index}|{rel}", _media_url(rel))],
            "shouldEndSession": True,
        },
    })


async def handle_playback_event(event: dict, ctx: dict, kind: str) -> JSONResponse:
    """Remember where audio stopped so Resume picks up mid-track."""
    device = _device_id(ctx)
    token = event.get("token") or ""
    offset = int(event.get("offsetInMilliseconds") or 0)
    rel = token.split("|", 1)[1] if "|" in token else token
    if kind == "AudioPlayer.PlaybackStopped" and rel:
        _OFFSETS[device] = (rel, offset)
        log.info("stopped %s at %ss", rel, offset // 1000)
    elif kind in ("AudioPlayer.PlaybackFinished", "AudioPlayer.PlaybackFailed"):
        _OFFSETS.pop(device, None)
    return empty()


async def handle_gallery(event: dict, ctx: dict) -> JSONResponse:
    """Grid of the newest images from the configured roots."""
    if not GALLERY_ROOTS:
        return await voice_reply(s("gallery_empty"))
    images = media.recent_images(limit=4)
    if not images:
        return await voice_reply(s("gallery_empty"))

    items: list[dict[str, str]] = []
    for path in images:
        name = media.thumbnail(path)
        if not name:
            continue
        items.append({
            "art": f"{PUBLIC_BASE}/thumbs/{name}",
            "title": path.stem.replace("_", " ")[:40],
        })
    if not items:
        return await voice_reply(s("gallery_failed"))

    log.info("gallery: %d images", len(items))
    _GALLERY[_device_id(ctx)] = [str(path) for path in images]
    return await voice_reply(
        s("gallery_spoken", count=len(items)),
        directives=[apl.gallery_directive(items, title=s("gallery_title"),
                                          subtitle=f"{len(items)} newest")],
        card_title="Gallery",
        card_text="\n".join(item["title"] for item in items),
    )


async def handle_apl_event(event: dict, ctx: dict) -> JSONResponse:
    """A tap on the APL screen: the Echo Show doubles as a remote control."""
    args = event.get("arguments") or []
    action = str(args[0]) if args else ""
    log.info("APL tap -> %r", action)
    if action == "pause":
        return JSONResponse({"version": "1.0",
                             "response": {"directives": [{"type": "AudioPlayer.Stop"}]}})
    if action == "next":
        return await handle_resume_or_next(event, ctx, advance=True, delta=1)
    if action == "previous":
        return await handle_resume_or_next(event, ctx, advance=True, delta=-1)
    if action == "open":
        index = 0
        if len(args) > 1:
            try:
                index = int(args[1])
            except (TypeError, ValueError):
                index = 0
        paths = _GALLERY.get(_device_id(ctx), [])
        if 0 <= index < len(paths):
            source = Path(paths[index])
            name = media.thumbnail(source, width=880, height=390)
            if name:
                caption = source.stem.replace("_", " ")
                return reply(
                    None,
                    directives=[apl.gallery_directive(
                        [{"art": f"{PUBLIC_BASE}/thumbs/{name}", "title": caption}],
                        title="IMAGE", subtitle=caption,
                    )],
                )
        return reply(s("cannot_open"))
    return empty()


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
async def dispatch(payload: dict) -> JSONResponse:
    request = payload.get("request") or {}
    ctx = payload.get("context") or {}
    kind = request.get("type", "")

    if kind == "LaunchRequest":
        return await handle_launch(request, ctx)
    if kind == "SessionEndedRequest":
        return empty()
    if kind in ("AudioPlayer.PlaybackStopped", "AudioPlayer.PlaybackFinished",
                "AudioPlayer.PlaybackFailed"):
        return await handle_playback_event(request, ctx, kind)
    if kind == "Alexa.Presentation.APL.UserEvent":
        return await handle_apl_event(request, ctx)
    if kind.startswith(("AudioPlayer.", "System.", "PlaybackController.")):
        return empty()
    if kind != "IntentRequest":
        return empty()

    intent = request.get("intent") or {}
    name = intent.get("name", "")

    if name in ("PlayMusicIntent", "ReproducirIntent"):
        return await handle_play(request, ctx)
    if name in ("AskIntent", "PreguntarIntent", "AMAZON.FallbackIntent",
                "AMAZON.KendraSearchIntent"):
        return await handle_ask(request, ctx)
    if name in ("ListIntent", "ListarIntent", "AMAZON.MoreIntent"):
        return await handle_list(request, ctx)
    if name in ("GalleryIntent", "GaleriaIntent", "MostrarGaleriaIntent"):
        return await handle_gallery(request, ctx)
    if name == "AMAZON.HelpIntent":
        return await voice_reply(HELP, end=False, reprompt=s("what_to_play"))
    if name == "AMAZON.CancelIntent":
        return await voice_reply(s("done"))
    if name == "AMAZON.StopIntent":
        return await voice_reply(s("done"), directives=[stop_directive()])
    if name == "AMAZON.PauseIntent":
        return JSONResponse({"version": "1.0",
                             "response": {"directives": [{"type": "AudioPlayer.Stop"}]}})
    if name == "AMAZON.ResumeIntent":
        return await handle_resume_or_next(request, ctx, advance=False)
    if name == "AMAZON.NextIntent":
        return await handle_resume_or_next(request, ctx, advance=True)

    # Unknown intent: hand the raw slot text to the agent as a last resort.
    return await handle_ask(request, ctx)


# --------------------------------------------------------------------------- #
# Routes (registered with and without the /alexa prefix)
# --------------------------------------------------------------------------- #
async def _skill_entry(request: Request) -> JSONResponse:
    body = await request.body()
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return JSONResponse({"error": "bad json"}, status_code=400)

    try:
        ctx = payload.get("context") or {}
        device = (ctx.get("System") or {}).get("device") or {}
        viewport = ctx.get("Viewport") or {}
        log.info(
            "device caps: interfaces=%s viewport=%sx%s profile=%s",
            ",".join(sorted((device.get("supportedInterfaces") or {}).keys()))[:220],
            viewport.get("currentPixelWidth"),
            viewport.get("currentPixelHeight"),
            viewport.get("mode") or "-",
        )
    except Exception:  # noqa: BLE001
        pass

    if not isinstance(payload, dict):
        return JSONResponse({"error": "bad json"}, status_code=400)

    if not SKIP_VERIFY:
        try:
            await verify_request(dict(request.headers), body, payload)
            check_application(payload, SKILL_ID)
        except SignatureError as exc:
            log.warning("signature rejected: %s", exc)
            return JSONResponse({"error": "invalid signature"}, status_code=400)

    try:
        return await dispatch(payload)
    except Exception as exc:  # noqa: BLE001
        log.exception("dispatch failed")
        return reply(s("internal_error", error=type(exc).__name__))


app.post("/")(_skill_entry)
app.post("/alexa")(_skill_entry)


async def _media(request: Request, path: str):
    resolved = media.resolve(path)
    if resolved is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(resolved, media_type=None, filename=resolved.name)


for _prefix in ("", "/alexa"):
    app.get(f"{_prefix}/media/{{path:path}}", response_model=None)(_media)
    app.head(f"{_prefix}/media/{{path:path}}", response_model=None)(_media)


def _safe_file(directory: Path, name: str, suffixes: tuple[str, ...]) -> Path | None:
    target = (directory / name).resolve()
    if not str(target).startswith(str(directory.resolve()) + "/"):
        return None
    if not target.is_file() or target.suffix.lower() not in suffixes:
        return None
    return target


async def _art_file(request: Request, name: str):
    target = _safe_file(ART_DIR, name, (".png", ".jpg", ".jpeg", ".webp"))
    return JSONResponse({"error": "not found"}, status_code=404) if target is None else FileResponse(target)


async def _thumb_file(request: Request, name: str):
    target = media.thumb_path(name)
    return JSONResponse({"error": "not found"}, status_code=404) if target is None else FileResponse(target, media_type="image/jpeg")


async def _voice_file(request: Request, name: str):
    """Serve rendered clips so Alexa SSML <audio src> can fetch them."""
    target = _safe_file(VOICE_DIR, name, (".mp3",))
    return JSONResponse({"error": "not found"}, status_code=404) if target is None else FileResponse(target, media_type="audio/mpeg")


for _prefix in ("", "/alexa"):
    app.get(f"{_prefix}/art/{{name}}", response_model=None)(_art_file)
    app.head(f"{_prefix}/art/{{name}}", response_model=None)(_art_file)
    app.get(f"{_prefix}/thumbs/{{name}}", response_model=None)(_thumb_file)
    app.head(f"{_prefix}/thumbs/{{name}}", response_model=None)(_thumb_file)
    app.get(f"{_prefix}/voice/{{name}}", response_model=None)(_voice_file)
    app.head(f"{_prefix}/voice/{{name}}", response_model=None)(_voice_file)


def _health() -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "config": summary(),
        "tracks": len(media.catalogue()),
        "queues": len(_QUEUES),
        "background_tasks": len(_TASKS),
        "pending_notify": len(_PENDING),
    })


app.get("/health")(_health)
app.get("/alexa/health")(_health)


async def _notify(request: Request):
    """Queue a notification for the next skill launch. Loopback only.

    The peer address alone is not enough: every reverse proxy in docs/SETUP.md
    runs on this host, so a request from the internet also arrives from
    127.0.0.1. Proxied requests are recognised by their forwarding headers and
    by a Host header that is not this machine.
    """
    peer = (request.client.host if request.client else "") or ""
    host_header = (request.headers.get("host") or "").strip().lower()
    host_name = host_header.rsplit(":", 1)[0].strip("[]") if host_header else ""
    if (peer not in _LOOPBACK_HOSTS
            or any(h in request.headers for h in _PROXY_HEADERS)
            or host_name not in (_LOOPBACK_HOSTS | {HOST.lower()})):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "expected a JSON object"}, status_code=400)
    text = str(body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)
    if len(text) > NOTIFY_MAX_CHARS:
        # Past Alexa's limits (2 min of audio, 8000 chars of SSML) the Echo
        # would answer "problem with the skill" instead of speaking.
        return JSONResponse({"error": f"text longer than {NOTIFY_MAX_CHARS} chars"},
                            status_code=400)

    model = str(body.get("model") or "").strip()
    if model and model not in _ALLOWED_MODELS:
        return JSONResponse({"error": f"unknown model {model!r}"}, status_code=400)

    item = {
        "text": text,
        "title": str(body.get("title") or BRAND_TITLE)[:60],
        "subtitle": str(body.get("subtitle") or "")[:60],
        "chime": bool(body.get("chime", True)),
        "screen": bool(body.get("screen", True)),
        "model": model or None,
    }
    _PENDING.append(item)
    log.info("queued for next launch (%d pending): %r", len(_PENDING), text[:70])
    return JSONResponse({
        "ok": True,
        "queued": text[:120],
        "options": {k: v for k, v in item.items() if k != "text"},
    })


app.post("/notify")(_notify)
app.post("/alexa/notify")(_notify)


@app.on_event("startup")
async def _warm_voice() -> None:
    """Pre-render the fixed phrases so the first reply has no latency."""
    async def warm() -> None:
        try:
            ready = await asyncio.to_thread(voice.prerender, [WELCOME, HELP])
            log.info("voice warm: %d/2 fixed phrases ready", ready)
        except Exception:  # noqa: BLE001
            log.exception("voice warm-up failed")

    # Keep a reference: the event loop only holds tasks weakly.
    task = asyncio.create_task(warm())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
