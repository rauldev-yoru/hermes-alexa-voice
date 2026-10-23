# hermes-alexa-voice

**Give your AI agent a voice in the physical world.** A finished download, a render,
a failing service — instead of a log line nobody reads, your Echo says it out loud,
in a voice you chose, with a card on the screen.

Built for [Hermes Agent](https://hermes-agent.nousresearch.com), but the delivery
path is generic: anything that can run a Python script can speak through it.

```bash
./cli/venv/bin/python cli/notify.py "backup finished, 12 GB"
```

The Echo says it in your ElevenLabs voice, plays a chime first, and shows the text
on its screen.

---

## Why another Alexa integration

Everything else in this space assumes **Home Assistant in the middle**. This does
not: it talks to the unofficial Alexa API directly (`alexapy`) and owns a small
custom skill, which is what makes the audio actually play.

| Existing route | Why it does not cover this |
|---|---|
| HA + `alexa_media_player` + ElevenLabs | needs a whole Home Assistant install as a hub |
| `notify.alexa_media` with `<audio>` | the SSML is rendered by Simon Says, which **fetches the file and never plays it** |
| Alexa+ MCP add-ons | Alexa+ is only available in a handful of countries |
| `spokestack/alexa-custom-tts` | custom voice inside a skill, but no proactive notifications |
| Voice notifier plugins for coding agents | desktop audio only, not a physical Echo |

What is here: a custom skill that answers with **your** TTS audio, plus
`run_skill` to open it without a voice command — the two pieces that turn an Echo
into an agent output channel.

## What you get

- **Proactive notifications** — cron/agent → Echo, custom voice, chime, on-screen text
- **Configurable delivery** — quiet hours, dedupe, per-call voice, `--dry-run`, `--json`
- **Screens (APL)** — dark card with an illustration, tappable controls, image gallery
- **Ask the agent** — free-text question → agent answer spoken back (optional)
- **Music library** — serve your own audio to the Echo with pause/resume/next
- **`doctor`** — one command that reports what is broken and the command that fixes it

## Install

```bash
git clone https://github.com/rauldev-yoru/hermes-alexa-voice
cd hermes-alexa-voice
./install.sh
$EDITOR config.json                    # public_base, voice_id, skill_id
./cli/venv/bin/python cli/doctor.py
```

### Install the skill into your agent

The repo doubles as a Hermes skill, so the agent can drive the whole setup and
the fallbacks itself:

```bash
hermes skills install https://raw.githubusercontent.com/rauldev-yoru/hermes-alexa-voice/main/skill/SKILL.md
```

It brings `SKILL.md` plus `references/` (fallbacks, TTS providers, device
capabilities, customisation). The skill is a **guide, not the code**: it starts by
telling the agent to clone the repo, because `install.sh`, `cli/`, `docs/` and
`skill/scripts/` live in the checkout — the installer only ships the skill file and
its references.

Then the one-time Alexa console work (create the skill, paste the interaction
model, point it at your public https URL): **[docs/SETUP.md](docs/SETUP.md)**.
And harvest Amazon cookies: **[docs/COOKIES.md](docs/COOKIES.md)**.

Requirements: Python ≥3.10, `ffmpeg`, `openssl`, and a **public https endpoint**
with a publicly trusted certificate (Tailscale Funnel, cloudflared, or nginx).
Alexa will not call a self-signed endpoint.

## Use it

```bash
# simple
notify.py "render finished"

# with context on screen
notify.py "training done: loss 0.041" --title LoRA --subtitle "run 2"

# never wake the house, always deliver important stuff
notify.py "disk warning" --quiet-hours 23-8 --force

# half the price, still expressive
notify.py "queued" --voice turbo

# scripts and crons
notify.py "deploy ok" --json --dedupe-minutes 30
```

Exit code is `0` when delivered **or** intentionally skipped, `1` on error — so a
cron can use it without parsing output.

### Cron example

```cron
*/15 * * * * cd /opt/hermes-alexa-voice && ./cli/venv/bin/python cli/notify.py "backup still running" --dedupe-minutes 120
```

Full flag reference and configuration keys: **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

## Architecture

```
cron / agent ──► notify.py ──► POST /notify (queue) + run_skill (open the skill)
                                    │
                              Alexa cloud
                                    │  LaunchRequest
                                    ▼
                        FastAPI service ── renders TTS → mp3 48 kbps/16 kHz mono
                                    │      serves it over public https
                                    ▼
                        Echo: chime → voice → APL card
```

Components:

| Path | Role |
|---|---|
| `service/app/` | FastAPI skill endpoint: signature verification, TTS render, APL, media |
| `cli/alexa.py` | Echo control over the unofficial API (devices, speak, volume, routines, run-skill) |
| `cli/notify.py` | the notification tool — queue + ring |
| `cli/doctor.py` | preflight diagnostics with actionable fixes |
| `skill/SKILL.md` | install/repair guide written for an agent to follow unattended |
| `skill/references/` | fallbacks per missing piece, TTS providers, device capabilities, customisation |
| `skill/scripts/probe.py` | environment report: what works, what is missing, which fallback to take |
| `skill/scripts/configure.py` | validated edits to `config.json` (voice, phrases, image, language) |
| `docs/PITFALLS.md` | the failures that cost hours, and why they happen |

## Honest limitations

- **Skill screens are not persistent.** The session lasts 8 s and the screen ~30 s;
  only widgets persist, and not every Echo supports them.
- **Playback screens do not work.** While AudioPlayer runs, the device replaces your
  APL with its native player.
- **This uses an unofficial Amazon API.** It can break without notice, and cookies
  must be re-harvested periodically.
- **Custom audio needs a custom skill.** No shortcut makes `<audio>` play from
  `Alexa.Speak`.
- **Alexa+ MCP add-ons are the future** — when Alexa+ reaches your country, the
  same service can be republished as an MCP server.

## License

MIT — see [LICENSE](LICENSE).
