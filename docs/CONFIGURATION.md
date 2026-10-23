# Configuration

Three layers, later wins:

1. defaults in `service/app/config.py`
2. `config.json` (path from `$ALEXA_VOICE_CONFIG`, else `<repo>/config.json`)
3. environment variables, one per key: `ALEXA_<KEY>`

Start from [`config.example.json`](../config.example.json).

## Keys

| Key | Default | Meaning |
|---|---|---|
| `public_base` | `http://127.0.0.1:8099` | Public https URL Amazon calls. Must match the skill endpoint. |
| `host` / `port` | `127.0.0.1` / `8099` | Local bind. Keep it on loopback behind a proxy. |
| `media_root` | `null` | Audio library (read-only). `null` disables music. |
| `image_roots` | `[]` | Folders for the picture gallery. Empty disables it. |
| `brand_title` | `AGENT` | Header on cards and the APL screen. |
| `brand_welcome` | see example | Fixed LaunchRequest phrase. |
| `brand_help` | see example | Fixed Help phrase. |
| `art_image` | `null` | Public URL of the illustration next to notifications. |
| `voice_enabled` | `true` | Master switch for custom TTS. |
| `voice_id` | `""` | ElevenLabs voice id. Empty → device's built-in voice. |
| `voice_model` | `eleven_v4` | `eleven_v4`, `eleven_v3`, `eleven_v4_turbo`, `eleven_flash_v2_5`. |
| `chime` | `true` | Play a chime before the voice. |
| `agent_bin` | `null` | Agent CLI to answer free-text questions (e.g. the `hermes` binary). |
| `agent_profile` | `null` | Profile passed to the agent CLI. |
| `agent_timeout` | `240` | Seconds before the agent task is killed. |
| `agent_sync_budget` | `4.0` | Seconds Alexa waits before the answer is deferred to a notification. |
| `device_name` | `null` | Echo name substring. `null` = first speaker found. |
| `skill_id` | `null` | `amzn1.ask.skill.…` — required for `run_skill`, i.e. for notifications. |
| `cookies_file` | `state/credentials/amazon_cookies.json` | Amazon session cookies. |
| `skip_verify` | `false` | Disable signature checks. **Localhost development only.** |
| `state_dir` | `service/state` | Runtime files (voice cache, thumbs, logs). `$REPO` is expanded. |
| `strings` | `{}` | Override any spoken phrase by key (see below). |
| `notify` | see example | Defaults for the `notify` CLI. |

### `strings`

Every user-facing phrase lives in `DEFAULT_STRINGS` (English). Override any key to
run the skill in another language without touching code:

```json
{
  "strings": {
    "what_to_play": "¿Qué quieres que reproduzca?",
    "playing": "Reproduciendo {title}.",
    "done": "Listo.",
    "gallery_spoken": "Aquí tienes las {count} imágenes más recientes."
  }
}
```

`{placeholders}` are filled by the service; a bad placeholder falls back to the
template instead of crashing.

## `notify` CLI

```
notify.py TEXT [--title T] [--subtitle S] [--voice v4|v3|turbo|flash]
               [--device NAME] [--no-chime] [--no-screen]
               [--quiet-hours 23-8] [--dedupe-minutes N]
               [--force] [--dry-run] [--json] [--verbose]
```

| Flag | Effect |
|---|---|
| `--quiet-hours 23-8` | Silent inside the window (wraps midnight). `--force` overrides. |
| `--dedupe-minutes N` | Skip identical text sent within N minutes. `--force` overrides. |
| `--dry-run` | Queue the message but do not ring the Echo. |
| `--json` | Machine-readable output for scripts. |
| `--no-screen` | Voice only, no APL card. |
| `--no-chime` | Skip the chime. |
| `--voice` | `v4` (best), `v3`, `turbo` (half price), `flash` (cheap, no emotion tags). |

Exit codes: `0` delivered **or** intentionally skipped, `1` error.

Logs: `service/state/notify.log` (`SENT` / `SKIP` / `DRY` lines).

## CLI (Echo control)

```
alexa.py devices
alexa.py speak "text" [--voice Mia] [--whisper] [--pitch -2st] [--rate slow]
alexa.py announce "text"
alexa.py volume 40
alexa.py dnd on|off
alexa.py play|pause|next|previous|stop
alexa.py routines
alexa.py routine "name"
alexa.py run-skill
alexa.py state
```

## `doctor`

`cli/venv/bin/python cli/doctor.py` prints one line per check (`[OK]`, `[WARN]`,
`[FAIL]`) with the fix next to every failure, and exits `1` when something blocks
delivery. Run it first whenever something stops working.
