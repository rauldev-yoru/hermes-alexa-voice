# Setting up a voice from zero

A new user has no voice id, probably no provider account, and no idea which model
to pick. This is the conversation to have with them, in this order. Do not skip
step 1 — a surprising number of users are better served by the built-in voice.

Run everything from the cloned repo.

## Step 1 — ask whether they even want a custom voice

Say it plainly, with the trade-off:

> "The Echo can speak with its own voice — free, no account, works right now — or
> with a custom voice you pick. The custom one sounds like a persona instead of a
> smart speaker, supports emotion tags, and costs money per character. Which do
> you want?"

| | Built-in (device) voice | Custom voice |
|---|---|---|
| Cost | zero | per character (a 100-char notice ≈ $0.008 at list price) |
| Setup | `configure.py --no-custom-voice` | account + key + voice id |
| Emotion tags | no | yes, on v3/v4 |
| Identity | sounds like Alexa | sounds like whoever you choose |

If they pick the built-in voice, you are done with this file. Everything else in
this project works the same.

## Step 2 — get a key (only if they want custom)

1. Create an account at <https://elevenlabs.io> (the free tier is enough to
   audition voices).
2. Profile → **API key** → copy it.
3. Put it in `<repo>/.env`:

   ```bash
   echo 'ELEVENLABS_API_KEY=...' >> .env
   chmod 600 .env
   ```

4. Confirm: `skill/scripts/voices.py --status` → `provider key : present`.

**Key scopes matter.** A TTS-scoped key cannot read the model list or the
subscription — `--models` falls back to the published cost weights and says so.
That is not a setup failure; do not chase it.

## Step 3 — find the voice by listening, not by guessing

```bash
# what exists in this account
skill/scripts/voices.py --list
skill/scripts/voices.py --list --gender female --accent british
skill/scripts/voices.py --list --search "warm"

# three samples per voice: neutral, emotion tags, brand-line
skill/scripts/voices.py --preview --name "Alice"
```

The preview writes three clips to `service/state/previews/`:

| Sample | What it tells you |
|---|---|
| `neutral` | how it reads ordinary text — this is what most notifications sound like |
| `emotion` | `[excited] … [whispers] …` — whether the tags land, and whether the emotion fits |
| `brand` | a realistic assistant line — does this voice sound like the persona? |

Send them to the user, or play them on the Echo:

```bash
cli/venv/bin/python cli/notify.py "preview ready" --title Voice --no-chime
```

Pick with the user on the **neutral** sample first — it dominates in practice —
then confirm the emotion sample is not cartoonish for their taste.

## Step 4 — choose the model

```bash
skill/scripts/voices.py --models
```

The only two things that matter:

- **`eleven_v4` / `eleven_v3`** — honour `[emotion]` tags. Same price (multiplier
  1.0). v4 is the better default; v3 is the fallback if a tag misbehaves.
- **`eleven_v4_turbo` / `eleven_flash_v2_5`** — half price. Turbo is ~25 % faster
  and slightly flatter; flash reads the tags as literal words, so it cannot carry
  a persona.

Rule of thumb: if the user cares about personality, use v4. If they are sending
hundreds of short notices a day and the voice is utilitarian, turbo halves the
bill for a small quality loss. Never pick flash if the copy contains tags.

## Step 5 — commit the choice

```bash
skill/scripts/voices.py --set --name "Alice" --model eleven_v4
```

That writes `voice_id`, `voice_model` and `voice_enabled: true` to `config.json`.
Restart the service, then prove it:

```bash
cli/venv/bin/python cli/notify.py "this is the new voice"
```

If the Echo still uses its built-in voice, the render failed — the service log
says `custom voice unavailable, using built-in` and `voices.py --status` shows
whether the key and id are both set.

## Step 6 — give it a personality

Three independent layers. Do them in this order.

### 6a. The words it says

```bash
skill/scripts/configure.py \
  --brand "NOVA" \
  --welcome "Nova here. What do you need?" \
  --help-text "Ask me something, ask for music, or ask for the newest pictures."
```

`brand_title` is also the default header on every notification card.

### 6b. How the voice performs

```json
{
  "voice_stability": 0.4,
  "voice_similarity": 0.8,
  "voice_style": 0.45
}
```

| Parameter | Lower | Higher | When to move it |
|---|---|---|---|
| `stability` | more expressive, more variation | flatter, more consistent | drop to ~0.3 for a playful persona, raise to ~0.6 for a formal one |
| `similarity_boost` | looser, drifts from the source voice | sticks tightly to it | keep at 0.75-0.85; below 0.6 the voice stops sounding like the one they picked |
| `style` | none | exaggerated (turbo/flash only) | 0.4-0.5 if they use those models |

Changing any of these re-renders the cache (the settings are part of the cache
key), so the next notification already sounds different.

### 6c. Emotion in the copy

Only v3/v4 honour these. Put them in the text the agent sends:

```
"[excited] Build finished! [laughs] Two minutes faster than last time."
"[whispers] The backup failed. [sighs] I will retry in ten minutes."
```

Available: `[excited]` `[whispers]` `[laughs]` `[sighs]` `[tired]` `[nervous]`
`[frustrated]` `[sad]` `[angry]` `[shouting]` `[sarcastically]`, and combinations
like `[whispering, fearful]`.

The screen copy strips the tags automatically (`screen_text()`), so the card shows
clean text while the speaker performs the emotion — no extra work.

## Step 7 — calibrate on the device

The last mile is always the speaker, not the sample:

1. Send a typical notice and stand where the user will be.
2. Too quiet → the renderer already applies loudness normalisation; raise the Echo
   volume, do not re-encode louder.
3. Too slow/fast → pick another voice; rate is a voice property here, not a knob.
4. Chime annoying at night → `configure.py` and set `quiet_hours`, or `--no-chime`.

## Checklist

- [ ] `voices.py --status` → key present, voice id set
- [ ] `voices.py --preview` → three samples the user actually listened to
- [ ] model chosen with the tag/cost rule, not at random
- [ ] `voices.py --set` written, service restarted
- [ ] `notify.py "test"` → the Echo speaks in that voice
- [ ] `brand_welcome` / `brand_help` / `art_image` personalised
- [ ] stability/similarity tuned if the persona needed it
- [ ] quiet hours configured if the user sleeps near the Echo
