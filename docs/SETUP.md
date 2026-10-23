# Setup: public endpoint + Alexa skill

Two things must exist before a notification can reach the speaker:

1. a **public https URL** that Amazon can call, serving this project;
2. an **Alexa custom skill** pointing at it.

## 1. Publish the endpoint

Alexa requires https on port 443 with a certificate from a public CA. Self-signed,
plain http and non-443 ports are rejected. Pick one:

### Tailscale Funnel (easiest, free, real certificate)

```bash
sudo tailscale funnel --bg --set-path /alexa 8099
tailscale funnel status
```

Your URL becomes `https://<node>.<tailnet>.ts.net/alexa`. Set exactly that in
`config.json` → `public_base`.

The service registers its routes **with and without** the `/alexa` prefix, so it
works whether or not the proxy strips it.

### cloudflared quick tunnel

```bash
cloudflared tunnel --url http://127.0.0.1:8099
```

It prints `https://<random>.trycloudflare.com` — use it verbatim as `public_base`
(no path suffix). Quick tunnels change URL on restart; make a named tunnel for
anything permanent.

### nginx / Caddy with Let's Encrypt

```nginx
location /alexa {
    proxy_pass http://127.0.0.1:8099;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
}
```

`public_base = https://your.domain/alexa`.

Keep both `proxy_set_header` lines. A proxy on the same host connects from
`127.0.0.1`, so the service tells local `notify.py` calls apart from internet
traffic by these headers. Without them, anyone who can reach the public URL can
queue messages on `/notify`. Tailscale Funnel and cloudflared send them already.

### Verify

```bash
curl -sS https://YOUR-URL/health | jq .
# unsigned POSTs must be rejected when verification is on:
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://YOUR-URL -d '{}'   # 400
```

## 2. Create the Alexa skill

You need an Amazon developer account (free). If it is brand new, the account owner
must complete the registration and accept the developer agreement — that part
cannot be automated for you.

1. <https://developer.amazon.com/alexa/console/ask> → **Create Skill**
2. Name → **Custom** → **Provision your own** → Create skill
3. **Interaction Model → JSON Editor**: paste `service/skill/interaction_model.json`
   (`.es.json` for Spanish) → **Save Model** → **Build Model**
4. **Interfaces**: enable
   - **Alexa Presentation Language** — the screens
   - **Audio Player** — playing audio

   → **Save Interfaces** → **Build Model** again
5. **Endpoint → HTTPS**:
   - Default region: your `public_base` (e.g. `https://host/alexa`)
   - Certificate type: *"My development endpoint has a certificate from a trusted
     certificate authority"*
   - **Save Endpoints**
6. **Test** tab → change *"Test is disabled"* to **Development**
7. Copy the **Skill ID** (`amzn1.ask.skill.…`) into `config.json` → `skill_id`

## 3. Check it end to end

```bash
cli/venv/bin/python cli/doctor.py          # expects 0 failures
cli/venv/bin/python cli/alexa.py devices   # needs Amazon cookies
cli/venv/bin/python cli/alexa.py run-skill # the Echo opens the skill silently
cli/venv/bin/python cli/notify.py "hello"  # the Echo speaks
```

On the Echo you should be able to say the invocation name from the interaction
model plus *"play …"*, *"ask …"* or *"show the pictures"*.

## Notes

- Development skills are limited to accounts/devices you add as testers, and expire
  after long inactivity — re-save the model to reactivate.
- If you change the invocation name, rebuild the model or Alexa will not find it.
- The endpoint must answer within **8 seconds**. This service always does: the
  agent path answers immediately and speaks the result later.
