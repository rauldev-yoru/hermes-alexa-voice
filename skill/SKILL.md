---
name: alexa-voice-bridge
description: Use when connecting an AI agent or cron job to an Amazon Echo — proactive spoken notifications with a custom or built-in voice, on-screen cards, and a custom Alexa skill that answers questions. Covers setup, per-device capability checks, and what to do for every missing piece (no API key, no public URL, no ffmpeg, no cookies, screenless device).
---

# Alexa voice bridge

Turns an Echo into an output channel for an agent: a job finishes, the Echo says
it out loud — optionally in a custom TTS voice, with a chime and a card on screen.

The same skill answers free-text questions, plays a local audio library and shows
an image gallery, but the notification path is the reason it exists.

**Work through this file in order.** Do not install anything before step 0 —
half the pieces are optional, and the probe tells you which ones this machine and
this device actually need.

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
                                            Echo speaks + shows an APL card
```

`run_skill` is what makes it proactive: opening the skill fires a `LaunchRequest`,
the service pops the queued message and answers with custom audio. The user never
says anything.

## Step 0 — probe before you touch anything

```bash
python3 skill/scripts/probe.py          # human-readable
python3 skill/scripts/probe.py --json   # machine-readable
```

It reports, per area: what is present, what is missing, and **which fallback to
take**. Exit 1 means something blocking is missing. It changes nothing.

Then read [references/fallbacks.md](references/fallbacks.md) for the decision tree
behind each finding. Tell the user what they have and what they will lose before
proposing work.

## The three things that must be true

Everything else is an upgrade.

| # | Requirement | Verify |
|---|---|---|
| 1 | Service running | `curl -s http://127.0.0.1:<port>/health` |
| 2 | Public **https** endpoint, publicly trusted cert | `curl -sS <public_base>/health` → `{"ok":true}` |
| 3 | Alexa skill created, model built, endpoint set | console shows a successful build; Test = Development |

Proactive ringing adds two more: a `skill_id` and Amazon cookies. Without them the
skill still answers when the user speaks — it just cannot be woken by a cron. Say
that plainly instead of calling the setup broken.

## Step 1 — install

```bash
./install.sh                        # venvs, systemd --user unit (or manual command), config.json
skill/scripts/configure.py --show   # effective config after the three-layer merge
cli/venv/bin/python cli/doctor.py   # one line per check, with the fix
```

`install.sh` adapts: finds a python ≥3.10, prints the exact package-manager command
when ffmpeg/openssl are missing, installs a user unit when systemd is available and
the manual command when it is not, and detects Tailscale/cloudflared/nginx/Caddy
for publishing.

## Step 2 — configure what the user actually wants

Ask, then write. Never pick across the user's choices:

- **Voice**: custom TTS or the Echo's built-in voice? The built-in voice is free,
  needs no account and works immediately; a custom voice needs a provider key.
  See [references/tts-providers.md](references/tts-providers.md).
- **What it says and shows**: `brand_title`, `brand_welcome`, `brand_help`,
  `art_image`. See [references/customization.md](references/customization.md).
- **Optional features**: music (`media_root`), gallery (`image_roots`), agent
  answers (`agent_bin`). Off by default; the skill degrades gracefully without them.

```bash
configure.py --public-base https://host/alexa --skill-id amzn1.ask.skill.xxx
configure.py --no-custom-voice                     # built-in voice, no key needed
configure.py --welcome "Nova here. What do you need?" --brand NOVA
```

`configure.py` validates as it writes: a non-https `public_base`, a `voice_id` with
no key, a `media_root` that does not exist and an `art_image` that is not public
are reported instead of silently doing nothing.

## Step 3 — the Alexa skill (owner must be involved)

Follow [docs/SETUP.md](../../docs/SETUP.md): create the skill, paste the interaction
model (`service/skill/interaction_model.json`, or `.es.json`), enable **APL** and
**AudioPlayer**, set the endpoint, switch the test tab to Development.

If the account is brand new, the **owner** must complete Amazon's developer
registration — personal data and a legal agreement cannot be delegated.

## Step 4 — learn the device before suggesting features

```bash
skill/scripts/probe.py            # prints every device that has checked in
cat service/state/devices.json
```

The service records `interfaces`, `apl`, `audio_player`, viewport and locale for
every device that talks to it. Read that before proposing anything: a screenless
Echo Dot does not want a dashboard, and a round Echo Spot clips rectangular
layouts. Model landscape and capability mapping:
[references/devices.md](references/devices.md).

If the file is empty, ask the user to open the skill once
(`"alexa, open <invocation name>"`) and probe again.

## Step 5 — verify end to end

```bash
curl -s http://127.0.0.1:8099/health | jq .
curl -s https://HOST/alexa/health | jq .
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://HOST/alexa -d '{}'   # 400 expected
cli/venv/bin/python cli/alexa.py devices        # cookies work
cli/venv/bin/python cli/alexa.py run-skill      # the Echo opens the skill silently
cli/venv/bin/python cli/notify.py "install check"
```

A delivered notification leaves `SENT` in `service/state/notify.log` and a
`GET /voice/<hash>.mp3 200` from an Amazon IP in the service log. That second line
is the device fetching the clip — the proof the voice path works.

## Fallbacks at a glance

Full reasoning and user-facing wording for each case:
[references/fallbacks.md](references/fallbacks.md).

| Situation | Severity | Do this |
|---|---|---|
| No developer account | blocking | owner registers; prepare everything else meanwhile |
| No public https endpoint | blocking | Tailscale Funnel / cloudflared / nginx+certbot — never self-signed |
| No `ffmpeg` | degraded | `voice_id: ""` → built-in voice; no thumbnails |
| No `ELEVENLABS_API_KEY` | degraded | **offer the built-in voice first**, then explain what a custom voice adds |
| No Amazon cookies | degraded | no proactive ringing; skill still answers by voice |
| No `skill_id` | degraded | same as above |
| No `systemd --user` | cosmetic | docker, supervisor, or `nohup` |
| Device without APL | cosmetic | cards are sent alongside; screenless devices just speak |
| Device without AudioPlayer | cosmetic | no music; TTS and cards still work |
| `media_root` / `image_roots` empty | by design | feature off; the skill says so instead of failing |
| No agent CLI | by design | questions get a fixed reply; everything else works |
| No CA bundle | blocking (public endpoint) | install `ca-certificates`; never `skip_verify` in production |
| Alexa+ unavailable | out of scope | custom skill works everywhere; the MCP route needs Alexa+ |

## Troubleshooting

Details and proof: [docs/PITFALLS.md](../../docs/PITFALLS.md).

| Symptom | Cause | Fix |
|---|---|---|
| Alexa: *"There was a problem with the requested skill's response"* | service returned 400/500 | the service log says why; a 400 is usually signature verification |
| `chain verification failed: … ca-bundle.crt` | distro has no such path | auto-detected; set `SSL_CERT_FILE` if detection finds nothing |
| The Echo fetches the clip and stays silent | `<audio>` sent through `Alexa.Speak`/Simon Says | custom audio only works inside a custom skill response |
| APL shows only the background | `datasources` bindings are dropped by devices | inline every value; never send `${…}` |
| APL text clipped | layout sized for 1280×800 | read `Viewport` and size for it |
| `Device API returned no data` | cookie jar not seeded | `seed_cookie_jar()` in `cli/alexa.py` |
| Every call redirects to `/ap/signin` | `oauth_login=False` | `oauth_login=True` |
| `'dict' object has no attribute '_device_family'` | `AlexaAPI` wants an object | `DeviceShim` |
| Screen disappears after ~30 s | skill screens are not persistent | expected; only widgets persist |
| Our APL is replaced during playback | AudioPlayer hands over to the native player | expected; do not build playback screens |

## Hard constraints

Each one looks like a bug and is not. Do not "fix" them.

- `<audio>` inside `Alexa.Speak` (Simon Says) is **fetched and never played**.
- Alexa plays exactly one format in `<audio>`: **mp3, 48 kbps, 16 kHz, mono**.
- **APL `datasources` bindings are dropped by devices** — inline everything.
- The viewport is whatever the device reports (often 960×480) — read it.
- Skill screens are **temporary**: session 8 s, screen ~30 s. Only widgets persist,
  and not every device exposes the widget interface.
- While `AudioPlayer` runs, the device replaces your APL with its native player.
- Volume: TTS output is ~-28 dBFS; `loudnorm=I=-16:TP=-1.5` is applied on purpose.

## Steady state

- **Notify**: `cli/notify.py "text" [--title T] [--subtitle S] [--voice v4|v3|turbo|flash] [--quiet-hours 23-8] [--dedupe-minutes N] [--dry-run] [--json]`.
  Exit 0 = delivered or intentionally skipped, 1 = error.
- **From a job engine**: `job_notify.py --source <name> --status done|error --summary "…"`.
- **When something stops working**: `cli/doctor.py` and `service/state/notify.log`
  before touching the console.

## References

| File | Contents |
|---|---|
| [references/fallbacks.md](references/fallbacks.md) | every missing piece and what to do instead |
| [references/tts-providers.md](references/tts-providers.md) | render contract, ElevenLabs, Piper, HTTP TTS |
| [references/devices.md](references/devices.md) | capabilities, viewports, model landscape |
| [references/customization.md](references/customization.md) | phrases, language, image, APL layout |
| [docs/PITFALLS.md](../../docs/PITFALLS.md) | why the constraints exist, with proof |
| [docs/SETUP.md](../../docs/SETUP.md) | publishing + developer console, step by step |
| [docs/COOKIES.md](../../docs/COOKIES.md) | harvesting the Amazon session |
| [docs/CONFIGURATION.md](../../docs/CONFIGURATION.md) | every config key and CLI flag |
