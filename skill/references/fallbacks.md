# Fallbacks: what to do when something is missing

Read this **before telling the user to install anything**. Half the pieces here
are optional, and the wrong advice costs them an hour for nothing.

Rule of thumb: a working Echo notification path needs only **three** things —
the service running, a public https endpoint, and the Alexa skill created. Voice,
music, gallery, agent answers and proactive ringing are all upgrades on top.

Run `skill/scripts/probe.py` first; it reports which of these cases apply.

---

## 1. No Amazon developer account

**Blocking.** There is no way around it: creating a custom skill requires an
Amazon developer account (free) and accepting the developer agreement.

- The **account owner** must do it — it needs personal data and a legal
  agreement. Do not attempt it on their behalf.
- If the account is brand new, the developer registration asks for identity
  details; the skill works in **Development** mode without publishing, so no
  certification or identity verification is needed for personal use.

While waiting, everything except the skill can be prepared: service, tunnel,
config, cookies.

## 2. No public https endpoint

**Blocking** (Alexa will not call anything else). Options, in order of effort:

| Option | Command | Notes |
|---|---|---|
| Mesh VPN with a funnel feature | publish only the skill path, keep the rest private | free, real certificate, minimal exposure |
| Quick tunnel tool | a temporary public URL from your tunnel provider | URL changes on restart; fine for testing |
| Reverse proxy + ACME | proxy the skill path to the local port | needs a domain; forward the Host header |
| Web server with automatic TLS | proxy the skill path to the local port | zero-config certificates |

The endpoint must serve **https on 443** with a certificate from a public CA and
answer in under **8 seconds**. Self-signed and plain http are rejected.

## 3. No `ffmpeg`

**Degraded, not blocking.** ffmpeg is only used to convert TTS audio into the one
format Alexa plays (`mp3`, 48 kbps, 16 kHz, mono) and to make thumbnails.

- Without it: **no custom voice** — set `voice_id` to `""` and the Echo answers
  with its built-in voice. Everything else keeps working.
- Install with the host's package manager (`apt install ffmpeg`,
  `dnf install ffmpeg`, `pacman -S ffmpeg`, `brew install ffmpeg`).
- Thumbnails fail too, so the gallery screen shows nothing; the rest of the APL
  screens are unaffected.

## 4. No `ELEVENLABS_API_KEY` (the common case)

**Degraded, not blocking.** This is the single most likely situation for a new
user, and the right answer is *not* "go create an account".

Say it like this: *"You can use the Echo's own voice — it costs nothing and takes
zero setup. A custom voice is optional; here is what it adds and what it costs."*

Then offer both:

```bash
# option A — built-in voice (no account, no key, no cost)
skill/scripts/configure.py --no-custom-voice

# option B — custom voice
#  1. get a key at elevenlabs.io (free tier works for a few minutes of speech)
#  2. put it in <repo>/.env as ELEVENLABS_API_KEY=...
configure.py --voice-id <voice id> --voice-model eleven_v4
```

What is lost with the built-in voice: the agent's identity in audio (it sounds
like Alexa), emotion tags, and per-phrase caching. What is kept: every
notification, every screen, every command.

Do **not** silently configure a voice id without a key: every render fails, the
fallback fires anyway, and the user wonders why setup "worked" but sounds
generic.

## 5. Key present but requests fail

Check in this order:

1. Request the provider's `/v1/models` endpoint with your key in its auth header —
   a 401 means the key is wrong or revoked.
2. A **TTS-scoped** key answers 401 on `/v1/user/subscription` — that is normal,
   not a failure.
3. Quota exhausted → the API returns 401/429 with a message; the service falls
   back to the built-in voice per request, so nothing breaks.
4. Wrong `voice_model` for the account → stick to `eleven_v4`, `eleven_v3` or
   `eleven_flash_v2_5` unless the user confirms access to another one.

## 6. A different TTS provider

Perfectly possible — the renderer is one module. See
[tts-providers.md](tts-providers.md) for the adapter contract and worked examples
(local Piper, OpenAI, Google, Azure, or a home-grown HTTP TTS).

Nothing outside `service/app/voice.py` assumes ElevenLabs.

## 7. No Amazon cookies

**Degraded.** Cookies are only needed to *ring the Echo on demand*
(`run_skill`). Without them:

- the skill still answers when the user says the invocation name;
- `notify.py` fails at the last step (the message is queued but nothing rings);
- `alexa.py devices/speak/volume` do not work either.

Harvest them from a logged-in browser — see [docs/COOKIES.md](../../docs/COOKIES.md).

## 8. No `skill_id`

**Degraded.** Same as above: no proactive ringing. Everything else works.

The skill id looks like `amzn1.ask.skill.<uuid>` and is on the first screen of
the skill in the developer console.

## 9. No `systemd --user`

Not a problem — it only decides how the process stays alive.

```bash
# docker
docker run -d --restart=unless-stopped -p 127.0.0.1:8099:8099 \
  -v "$PWD/config.json:/app/config.json:ro" alexa-voice

# or a supervisor of the user's choice; or, minimally:
nohup service/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8099 &
```

`probe.py` prints which managers exist on the host.

## 10. The device has no APL (older Echo, Echo Dot with no screen)

**Not blocking.** Send both the APL directive **and** a `Standard` card: APL
devices draw the document, the rest fall back to the card, and a device with no
screen at all only speaks.

`probe.py` reports `no APL` per device once the device has talked to the service
once (`state/devices.json`). Do not suggest screen features before checking.

## 11. The device has no `AudioPlayer`

No music playback from the local library — the rest works. Check
`state/devices.json` → `audio_player`.

## 12. `media_root` / `image_roots` unset

Both features are opt-in. Leaving them unset is a valid configuration: the skill
answers "the audio library is empty" instead of crashing, and the gallery intent
says it found no pictures.

Do not point `media_root` at a huge tree "so there is something": the catalogue
scans it on demand.

## 13. No agent CLI

`agent_bin` unset means free-text questions are answered with a fixed sentence.
Audio, screens, gallery and notifications are unaffected. Set `agent_bin` to the
agent's binary (and `agent_profile` if it needs one) to enable answers.

Latency matters: the agent gets `agent_sync_budget` seconds (default 4) before
Alexa is told "on it" and the answer is spoken later as a notification. A slow
agent therefore **needs** working notifications to deliver anything.

## 14. Port already in use

`probe.py` will show the service as unreachable and the log will say
`address already in use`. Change `port` in config and move the tunnel with it;
do not kill whatever is on the port without asking the user what it is.

## 15. No CA bundle

Signature verification needs a system CA bundle. The config probes several known
paths and honours `SSL_CERT_FILE`; if none exists, `certifi` is used.

Worst case: `verify.py` rejects every request with `no CA bundle found`. The fix
is installing `ca-certificates` — **never** `skip_verify` on a public endpoint.

## 16. Alexa+ (the MCP route) is not available

The modern integration — registering an MCP server so Alexa+ calls it as a tool —
requires **Alexa+**, which is only available in a handful of marketplaces. A
device without it exposes no Alexa+ capability in its request context.

The custom skill in this repo works everywhere. If Alexa+ reaches the user's
country later, the same service can be republished as an MCP server; nothing here
blocks that.
