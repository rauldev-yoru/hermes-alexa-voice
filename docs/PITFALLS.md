# Pitfalls

Everything below was measured against a live Echo Show 5 (3rd gen) and a real
Amazon account, and each one cost hours. The code already handles them — this file
exists so nobody "optimises" them away.

## 1. `<audio>` in `Alexa.Speak` is fetched but never played

The system skill behind `Alexa.Speak` (Simon Says) downloads the file — you see a
`200` for the clip in your web server log — and then says nothing.

This looks exactly like a format problem, and it is not: 24 kHz and 16 kHz, 48 kbps
and 128 kbps all behave the same. **Custom audio only plays from inside a custom
skill response**, where you control the whole SSML.

## 2. Alexa only plays one audio format

`<audio src="…">` requires **MP3, 48 kbps, 16 kHz, mono**. Anything else is
downloaded and silently discarded. Anything over ~2 minutes is rejected.

Also: TTS providers output around **-28 dBFS**, which sounds faint on an Echo. The
renderer applies `loudnorm=I=-16:TP=-1.5:LRA=11`; removing it makes the voice sound
half-volume.

## 3. APL `datasources` bindings are dropped by the device

A document with

```json
{"type": "Text", "text": "${payload.text}"}
```

renders **only the root frame** on an Echo Show 5 — a blank coloured screen. A
literal `"LITERAL OK"` in the same document renders fine, which is the bisection
that proves it is the binding and not the layout.

Solution used here: build the document per message with every value inlined. No
`datasources`, no `parameters`, no `${}` anywhere. It costs ~1 KB per response.

Corollary: `resources`, `styles` and `alexa-layouts` imports are avoided for the
same reason — fewer resolution steps that can fail silently.

## 4. The viewport is not what you assume

An Echo Show 5 reports **960×480** (HUB profile), not 1280×800. A layout sized for
the bigger canvas clips text and looks broken.

Read `context.Viewport` from the request (the service logs it on every call) and
size for what the device says.

## 5. `lineHeight` / `letterSpacing` must be numbers

In APL these are numeric properties. Passing `"1.15"` as a string can invalidate
the whole style and make text disappear. Numbers only.

## 6. Skill screens are temporary

- session lifetime: **8 s**, not configurable
- screen on display: **~30 s** of inactivity
- widgets (the only persistent surface) are not available on every device — check
  `supportedInterfaces` in the request context

Anything that must stay visible needs a widget, or needs to be re-invoked.

## 7. `AudioPlayer` replaces your APL

While audio is playing, the device shows its native player and hides the skill's
screen. Building a "now playing" layout is fine, but expect it to be replaced one
second later.

## 8. alexapy auth chain (four separate traps)

| Trap | Symptom | Fix |
|---|---|---|
| `oauth_login=False` | every call redirects to `/ap/signin`, even with valid cookies | `oauth_login=True` |
| cookies only passed to `login(cookies=…)` | `Device API returned no data` — later calls are unauthenticated | seed the aiohttp cookie jar (`seed_cookie_jar`) |
| raw dict passed to `AlexaAPI` | `'dict' object has no attribute '_device_family'` | wrap it (`DeviceShim`) |
| iPhone/PitanguiBridge user agent | random auth failures | set a consistent UA after login |

## 9. Path prefixes are inconsistent

Tailscale Funnel with `--set-path` may or may not strip the prefix depending on
version. The service registers every route **twice**, with and without `/alexa`.

## 10. Signature verification fails on some distros for a boring reason

Fedora has no `/etc/pki/tls/certs/ca-bundle.crt`; a hardcoded path makes **every**
Alexa request fail with `400 invalid signature`. The config probes several known
locations and honours `SSL_CERT_FILE`.

## 11. Alexa+ MCP add-ons are not available everywhere

Amazon adopted MCP for Alexa+ (July 2026): you register an MCP server and Alexa+
calls your tools. It is the better long-term integration — but Alexa+ is limited to
a handful of marketplaces, and a device without it exposes no Alexa+ capability.
The custom skill is the route that works regardless.
