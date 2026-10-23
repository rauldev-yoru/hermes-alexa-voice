# Amazon cookies (the unofficial API's auth)

`alexapy` drives the same web API the Alexa app uses, so it needs a logged-in
Amazon session. There is no OAuth registration to fill in — you copy the cookies
from a browser that is already logged in.

Store them at `service/state/credentials/amazon_cookies.json` (or whatever
`cookies_file` points to), mode `600`:

```json
{
  "email": "you@example.com",
  "cookies": {
    "session-id": "…",
    "session-token": "…",
    "ubid-main": "…",
    "at-main": "…",
    "x-main": "…",
    "sst-main": "…"
  }
}
```

## Harvest from a browser

Requirements: a browser profile where **amazon.com** shows you logged in.

### DevTools (any Chromium/Firefox)

1. Open <https://www.amazon.com> and confirm you are signed in.
2. DevTools → **Application** (Chrome) / **Storage** (Firefox) → **Cookies** →
   `https://www.amazon.com`. For `HttpOnly` cookies use the *Network* tab:
   reload, click the document request, copy the `Cookie:` request header.
3. Keep every `session-*`, `at-*`, `ubid-*`, `x-*` and `sst-*` value.
4. Paste into the JSON above and `chmod 600` the file.

### Automated (Playwright / Selenium)

```python
cookies = {c["name"]: c["value"] for c in context.cookies(["https://www.amazon.com"])}
json.dump({"email": EMAIL, "cookies": cookies}, open(target, "w"))
```

`context.cookies()` includes `HttpOnly` cookies, so this is the reliable route.

### From an existing alexapy/HA install

Home Assistant keeps a pickle per account under
`config/.storage/alexa_media.<email>.pickle`. `alexapy` can load it directly; if you
already run HA, point `cookies_file` at an exported JSON instead of re-harvesting.

## They expire

Amazon rotates session cookies. The symptom is a one-line failure:

```
Amazon login failed (login_failed). The cookies are probably stale — re-harvest them.
```

Re-harvest; do not patch the code. If a harvest works and then dies within a day,
check that the browser session was not "sign out of all devices"-ed and that the
account is not hitting an OTP challenge.

## Safety

These cookies are equivalent to a logged-in session. Keep the file `600`, never
commit it (`.gitignore` already excludes `state/` and `credentials/`), and revoke
with *"Sign out of all devices"* in Amazon if it leaks.
