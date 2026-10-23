# TTS providers

The custom voice is **one function**. Everything else in the service is
provider-agnostic, so swapping ElevenLabs for something else is a small, local
change.

## The contract

> Picking a voice for a *user* (as opposed to writing an adapter) is a
> conversation, not a config value: see [voice-setup.md](voice-setup.md).


```python
# service/app/voice.py
def render(text: str, *, chime: bool = False,
           voice_id: str | None = None,
           model_id: str | None = None) -> str | None:
    """Return a PUBLIC https URL of the spoken line, or None."""
```

Rules that the caller (`main.voice_reply`) depends on:

1. **Return `None` on any failure.** The caller then answers with the device's
   built-in voice. Raising would leave the user in silence.
2. **Cache by content** (voice + model + text hash). Alexa retries, and a cron
   that repeats a phrase should not pay twice or add latency.
3. **Output must be `mp3`, 48 kbps, 16 kHz, mono.** Anything else is downloaded by
   the Echo and silently dropped. The reference implementation pipes the
   provider's audio through:

   ```bash
   ffmpeg -i raw.mp3 -af "loudnorm=I=-16:TP=-1.5:LRA=11" \
          -ac 1 -ar 16000 -b:a 48k -codec:a libmp3lame out.mp3
   ```

   `loudnorm` is not cosmetic: most TTS output sits around -28 dBFS and sounds
   faint on an Echo.
4. **The URL must be publicly reachable over https.** A LAN or tailnet-only URL
   is unreachable by the device.
5. Optionally prepend a chime (the reference concatenates with ffmpeg rather than
   sending two `<audio>` tags — more reliable).

## Choosing the provider

`config.json`:

```json
{
  "voice_enabled": true,
  "voice_id": "<provider-specific id>",
  "voice_model": "<provider-specific model>",
  "chime": true
}
```

`voice_id` empty ⇒ `VOICE_ENABLED` is false and the built-in voice is used. That
is the correct fallback for every provider, and it should stay the default for
someone who has not decided yet.

## Worked examples

### A. ElevenLabs (the reference implementation)

```python
resp = http_client.post(
    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
    headers=auth_header,   # provider key, read from the environment
    json={"text": text, "model_id": model_id,
          "voice_settings": {"stability": 0.4, "similarity_boost": 0.8}},
    params={"output_format": "mp3_44100_128"},
)
```

`eleven_v3` / `eleven_v4` honour **audio tags** (`[excited]`, `[whispers]`,
`[laughs]`); `flash` models read them literally. Only v3/v4 support emotion.

### B. A local engine (Piper) — no cloud, no key, no cost

```python
import subprocess
subprocess.run(
    ["piper", "--model", voice_id, "--output_file", str(raw)],
    input=text.encode(), check=True,
)          # raw is a WAV
_mp3 = _convert(raw)      # the same ffmpeg line as above
```

`voice_id` becomes a model path (e.g. `~/.local/share/piper/es_ES-sharvard-medium.onnx`).
Slower on CPU and less expressive, but private and free — a good default when the
user has no key and objects to cloud TTS.

### C. Any HTTP TTS (OpenAI, Google, Azure, a home server)

All of them reduce to: POST the text, get bytes, run the ffmpeg line.

```python
resp = http_client.post(url, headers=auth_header,
                        json={"input": text, "voice": voice_id, "model": model_id},
                        timeout=45)
```

If the provider streams WAV or PCM, write it to a temp file and let ffmpeg do the
conversion — do not hand-roll the container.

### D. A provider with async/batch semantics

Some services return a job id instead of audio. Two options:

- poll inside `render()` with a hard timeout **below 6 seconds** (the service
  answers Alexa at 6 s, Alexa gives up at 8 s);
- or pre-render the fixed phrases at startup (`prerender()`) and accept the
  built-in voice for dynamic ones.

## Testing a new provider

```bash
# 1. render one clip and check the format
service/venv/bin/python - <<'PY'
from app import voice
print(voice.available())          # (ok, reason)
print(voice.render("test line"))  # expect a public URL
PY

# 2. verify what Alexa will actually receive
ffprobe -v error -show_entries stream=codec_name,sample_rate,channels,bit_rate \
        -of default=nw=1 state/voice/<hash>.mp3
# expect: mp3 / 16000 / 1 / 48000
```

Then open the skill on the device: if the voice comes out as the built-in one,
`voice.available()` or the render call is failing — the service log says which
(`custom voice unavailable, using built-in`).

## What not to do

- Do not raise from `render()`: silence is worse than the fallback voice.
- Do not skip `loudnorm` and then "fix" it by raising the device volume.
- Do not return a signed URL that expires in less than a minute — Alexa may
  prefetch the clip well before it plays.
- Do not cache the *provider response* without the voice id in the key: switching
  voices would keep replaying the old one.
