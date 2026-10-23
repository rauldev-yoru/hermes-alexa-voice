# Devices: know what the user's Echo can do before suggesting features

Echo models differ a lot, and the differences are exactly the ones that produce
"it works for you but not for me" bug reports. **Never assume**: the device tells
you what it supports, and this service records it.

## How to find out what the device supports

```bash
skill/scripts/probe.py                 # shows every device that has checked in
cat service/state/devices.json         # raw record
```

`devices.json` is written on every request from `context.System.device` and
`context.Viewport`. Example:

```json
{
  "amzn1.ask.device.ABC…": {
    "interfaces": ["Alexa.Presentation.APL", "AudioPlayer"],
    "apl": true,
    "audio_player": true,
    "viewport": {"width": 960, "height": 480, "mode": "HUB", "shape": "RECTANGLE"},
    "locale": "en-US",
    "first_seen": "…", "last_seen": "…"
  }
}
```

The file stays empty until the device talks to the service once. If it is empty,
ask the user to open the skill on the Echo (`"alexa, open <invocation name>"`) and
run the probe again.

## What each capability unlocks

| Field | True ⇒ | False ⇒ |
|---|---|---|
| `apl` | custom screens (cards, gallery, tap-to-act) | only a `Standard`/`Simple` card, or nothing on a screenless device |
| `audio_player` | play your own audio files (music library) | TTS only; the music intent must say it is unavailable |
| `viewport.width/height` | size APL for exactly this canvas | fall back to a safe 960×480 |
| `interfaces` containing `Alexa.Presentation.APL.Video` | video playback is possible | do not offer video |

Two capability families worth checking explicitly when a user asks for something
exotic: `Alexa.Presentation.APL.Video` and the widget interfaces
(`Alexa.Presentation.APL.Widgets`). **Widgets are the only persistent surface** —
a skill screen is torn down after ~30 s. If the device lacks the widget
interface, say so instead of promising a permanent dashboard.

## Rough model landscape (orientative — verify with the probe)

Treat this as a starting hypothesis, never as fact; firmware and locale change
things.

| Device class | Screen | APL | Video | AudioPlayer | Typical viewport |
|---|---|---|---|---|---|
| Echo Dot / Echo (no screen) | — | — | — | yes | — |
| Echo Show 5 / 8 / 10 / 15 / 21 | yes | yes | model-dependent | yes | 960×480 on 5", larger on 8"+ |
| Echo Spot (2017) | small | limited | no | yes | round profile, not a rectangle |
| Fire TV + Alexa | yes | yes (landscape TV profile) | yes | yes | 1280×720 or 1920×1080 |
| Alexa app on phone | — | — | — | yes | — |

Two consequences that catch people:

- **Round screens** (Echo Spot) clip rectangular layouts; check
  `viewport.shape` and keep the important content centred.
- **Landscape TV profiles** expose a much larger canvas; a layout tuned for
  960×480 looks tiny there instead of clipped. Read the viewport, do not hardcode.

## Rules for the agent

1. **Read before recommending.** Run the probe, or read `devices.json`, before
   proposing a feature. Suggesting "let's build a dashboard on screen" to someone
   with an Echo Dot is wasted work.
2. **Say what they lose.** "Your device has no screen, so notifications will be
   voice only — the text card is silently dropped."
3. **Do not suggest enabling an interface the device does not expose.** Enabling
   `AudioPlayer` in the skill only helps if the device supports it.
4. **Locale matters.** The interaction model must match the device locale
   (`es-US` skill vs an `en-US` device answers "I don't know that one"). The probe
   shows the locale the device actually reports.
