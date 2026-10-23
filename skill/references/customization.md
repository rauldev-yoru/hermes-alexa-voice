# Customisation: messages, images, layout, language

Everything the user sees or hears is configurable without touching logic. Use
`skill/scripts/configure.py` (it validates as it writes) or edit `config.json`.

## Spoken and displayed text

| Config key | Where it appears |
|---|---|
| `brand_title` | header on the APL screen and on cards (default `AGENT`) |
| `brand_welcome` | the phrase when the skill opens with nothing queued |
| `brand_help` | the phrase for "help" |
| `art_image` | illustration shown next to notifications (public https URL) |
| `strings` | every other phrase, by key |

```bash
configure.py --brand "NOVA" \
             --welcome "Nova here. What do you need?" \
             --help-text "Ask me something, ask for music, or ask for pictures." \
             --art https://example.com/nova.png
```

### Another language

Every user-facing phrase lives in `DEFAULT_STRINGS` (`service/app/config.py`,
English). Override any subset:

```json
{
  "strings": {
    "what_to_play": "¿Qué quieres que reproduzca?",
    "playing": "Reproduciendo {title}.",
    "gallery_spoken": "Aquí tienes las {count} imágenes más recientes.",
    "done": "Listo.",
    "nothing_found": "No encontré nada parecido a {query}."
  }
}
```

`{placeholders}` are filled by the service; an unknown placeholder falls back to
the raw template instead of raising. The **interaction model** is a separate file
(`service/skill/interaction_model.json` for English, `.es.json` for Spanish) and
must match the skill locale chosen in the console — a mismatch means Alexa never
routes the utterance.

## The illustration

- Must be a **public https URL** the Echo can fetch. `GET /art/<name>` serves files
  from `service/state/art/`, so drop an image there and use
  `https://<your-host>/art/portrait.png`.
- Two sizes are not required (the same URL is used for `smallImageUrl` and
  `largeImageUrl`), but a ~1:1 or 4:3 image around 1200×800 looks right.
- Without `art_image` the card is still rendered, just without a picture.

## The APL screen

`service/app/apl.py` holds three documents: the notification card, the gallery
grid and the (rarely visible) now-playing layout. Customise the look here:

| What | Where |
|---|---|
| background colour | `"backgroundColor": "#0E0C13"` in each document |
| accent (title, progress bar) | `"#FF8FC7"` |
| body/secondary text | `"#FFFFFF"` / `"#9A97A6"` |
| illustration size | `"width": 200, "height": 200` in `build_document` |
| text column width | `"width": 600` (or 860 when there is no image) |
| font tiers | `_TIERS = ((70, 28), (150, 24), (260, 20))` — characters → font size |
| max characters | `_MAX_CHARS = 300`, then clamp on a sentence boundary |

Three rules that are not cosmetic (see [PITFALLS](../../docs/PITFALLS.md)):

1. **Inline every value.** No `datasources`, no `${...}` bindings — real devices
   drop them and render only the root frame.
2. **Size for the reported viewport** (often 960×480), not for a nicer number.
3. **Clean the text for the screen.** The same string goes to the TTS provider
   (which needs `[excited]`) and to APL (which would draw the brackets);
   `screen_text()` strips tags and clamps the length.

The **card** is sent alongside the APL directive, so devices without APL still show
something. Keep both.

## Interaction model

`service/skill/interaction_model.json`:

- `invocationName` — what the user says. Two words max; avoid brand names that
  collide with built-in skills.
- Intent samples — add the phrases the user actually uses. This file must be
  pasted in the console and **built**, or the phrases do not exist.

Intent names must match `service/app/main.py` (`PlayMusicIntent`, `AskIntent`,
`ListIntent`, `GalleryIntent`). Renaming one without the other silently falls
through to the free-text handler.

## Notifications

```json
{
  "notify": {
    "model": "eleven_v4",
    "chime": true,
    "screen": true,
    "title": null,
    "subtitle": "",
    "quiet_hours": "23-8",
    "dedupe_minutes": 5
  }
}
```

`title: null` means "use `brand_title`". `quiet_hours` wraps midnight. Every call
can override any of these with a flag — see [CONFIGURATION](../../docs/CONFIGURATION.md).
