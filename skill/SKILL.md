---
name: alexa-voice-bridge
description: Use when connecting an AI agent to an Amazon Echo — proactive spoken notifications with a custom TTS voice, on-screen cards via APL, and a custom Alexa skill that answers free-text questions. Covers install, diagnosis and the failure modes that are not obvious.
---

# Alexa voice bridge

Turns an Echo into an **output channel for an agent**: a cron finishes a job, the
Echo speaks it in a custom voice (ElevenLabs, or the device's own voice), with an
optional chime and an on-screen card.

The same skill also answers questions, plays audio from a local library and shows
a picture gallery — but the reason this exists is the notification path.

## Architecture

```
cron / agent  ──►  notify.py
                     │  POST /notify  (queue the text)
                     │  run_skill     (open the skill on the device, no voice command)
                     ▼
              Alexa cloud  ──►  LaunchRequest  ──►  service (FastAPI)
                                                      │ renders TTS clip (48 kbps / 16 kHz mono)
                                                      │ serves it over public https
                                                      ▼
                                            Echo speaks + shows APL card
```

`run_skill` is the trick that makes it proactive: opening the skill fires a
`LaunchRequest`, the service pops the queued message and answers with custom
audio. No voice command from the user at any point.

## Install

```bash
./install.sh                      # venvs, systemd unit, config.json from example
$EDITOR config.json               # public_base, voice_id, skill_id, media_root
cli/venv/bin/python cli/doctor.py # every check, with the fix printed next to failures
```

`install.sh` is idempotent and adapts: it finds a python ≥3.10, reports the exact
package-manager command when ffmpeg/openssl are missing, installs a systemd
`--user` unit when available and prints the manual command when not, and detects
Tailscale/cloudflared for publishing.

## What must be true (verify each one, in order)

| # | Requirement | How to verify |
|---|---|---|
| 1 | Public **https** URL with a publicly trusted cert | `curl -sS https://HOST/alexa/health` → `{"ok": true}` |
| 2 | Service listening locally | `curl -sS http://127.0.0.1:8099/health` |
| 3 | `public_base` in config equals that URL | `cli/venv/bin/python cli/doctor.py` |
| 4 | Alexa skill created, endpoint set, model built | console shows build ✅, Test = Development |
| 5 | Amazon cookies present | `service/state/credentials/amazon_cookies.json` exists, ≥5 cookies |
| 6 | `skill_id` set | `alexa.py run-skill` prints a device name |

If 1–6 hold, `notify.py "test"` reaches the speaker. If the Echo stays silent,
the log at `service/state/notify.log` and the service journal say which half
broke.

## Alexa console (one-time, needs the account owner)

1. <https://developer.amazon.com/alexa/console/ask> → **Create Skill**
2. Name it → *Custom* → **Provision your own** → Create
3. Interaction Model → **JSON Editor** → paste `service/skill/interaction_model.json`
   (or `.es.json` for Spanish) → **Save Model** → **Build Model**
4. **Interfaces** → enable **Alexa Presentation Language** (needed for screens)
   and **Audio Player** (needed to play audio) → Save → Build
5. **Endpoint** → HTTPS → `https://HOST/alexa` → certificate type
   *"My development endpoint has a certificate from a trusted certificate authority"*
6. **Test** tab → dropdown to **Development**

If this is a brand-new developer account, the owner must complete the developer
registration (personal data + agreement) — that step cannot be delegated.

## Amazon cookies

`alexapy` needs a logged-in Amazon session. Harvest it from **a browser where
amazon.com is already logged in** (see `docs/COOKIES.md`) and store:

```json
{ "email": "you@example.com", "cookies": { "session-id": "...", "at-main": "...", "...": "..." } }
```

at `service/state/credentials/amazon_cookies.json` (chmod 600).

Cookies rotate. Symptom: the CLI exits with *"login failed … cookies are probably
stale"* → re-harvest. Do not try to fix it in code.

## Verify end-to-end, in this order

```bash
# 1. service answers
curl -s http://127.0.0.1:8099/health | jq .

# 2. public path reaches it
curl -s https://HOST/alexa/health | jq .

# 3. signature verification is on and rejects unsigned requests
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://HOST/alexa -d '{}'   # expect 400

# 4. Alexa can list devices (cookies work)
cli/venv/bin/python cli/alexa.py devices

# 5. the skill opens without a voice command
cli/venv/bin/python cli/alexa.py run-skill

# 6. a real notification
cli/venv/bin/python cli/notify.py "install check"
```

A successful notification leaves this in `service/state/notify.log`:
`SENT text=… model=…` — and a `GET /voice/<hash>.mp3 200` in the service log,
coming from an Amazon IP (that request is the Echo fetching the clip).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Alexa says *"There was a problem with the requested skill's response"* | service returned 400/500 | service log shows why; a 400 usually means signature verification failed (CA bundle) |
| `chain verification failed: Error loading file /etc/pki/tls/certs/ca-bundle.crt` | distro has no such path (Fedora) | already auto-detected; set `SSL_CERT_FILE` if the auto-detect finds nothing |
| The Echo downloads the clip but stays silent | `<audio>` sent through `Alexa.Speak`/Simon Says | custom audio only works **inside a custom skill response** |
| Audio plays at 24 kHz → silence | Alexa only plays **48 kbps, 16 kHz, mono** in `<audio>` | the renderer already normalises; do not "improve" the bitrate |
| Voice is very quiet | TTS provider output ≈ -28 dBFS | `loudnorm=I=-16:TP=-1.5` is applied; keep it |
| APL shows only the background | `datasources` bindings do not reach the device | inline every value into the document; never send `${…}` |
| APL text clipped | layout sized for 1280×800, device reports 960×480 | read `Viewport` from the request and size for it |
| `Device API returned no data` | `login(cookies=…)` validated without seeding the cookie jar | `seed_cookie_jar()` — already in `cli/alexa.py` |
| Every call redirects to `/ap/signin` | `oauth_login=False` sends a mobile UA | `oauth_login=True` |
| `'dict' object has no attribute '_device_family'` | `AlexaAPI` wants an object, not the raw dict | `DeviceShim` |
| Screen disappears after ~30 s | skill screens are not persistent | expected: session is 8 s, screen ~30 s. Only widgets persist, and not every device supports them |
| Music: our APL is replaced instantly | AudioPlayer hands over to the native player | expected; do not build screens for playback |

## Hard constraints (learned against a live Echo Show 5, firmware 2026-10)

- **`<audio>` inside `Alexa.Speak` (Simon Says) is fetched but never played.**
  It looks like a format bug; it is not. Only a custom skill renders it.
- **APL `datasources` are dropped by the device.** Literal text renders; a
  `${payload.text}` binding renders nothing at all — only the root frame.
- **The viewport is not what you assume.** Read `context.Viewport`; a "big" Echo
  Show may report 960×480.
- **Skill screens are temporary.** Session 8 s, screen ~30 s, not configurable.
- **Alexa+ MCP add-ons** (the modern way to plug an agent into Alexa) require
  Alexa+, which is only available in a handful of countries. The custom skill is
  the route that works everywhere.
