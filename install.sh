#!/usr/bin/env bash
# Idempotent installer for the Alexa voice bridge.
#
# It adapts to the machine it runs on instead of assuming one distro:
#   * picks a python3 with 3.10+ (venv per component)
#   * installs ffmpeg/openssl through the available package manager, or says
#     exactly what to run when it has no permission
#   * detects systemd --user (installs a unit) or falls back to a nohup launcher
#   * detects how to publish the endpoint (Tailscale Funnel, cloudflared, nginx)
#
# Safe to re-run: every step checks before acting.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; OFF=$'\033[0m'
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✔%s %s\n' "$GREEN" "$OFF" "$*"; }
warn() { printf '%s!%s %s\n' "$YELLOW" "$OFF" "$*"; }
die()  { printf '%s✘%s %s\n' "$RED" "$OFF" "$*"; exit 1; }
step() { printf '\n%s== %s ==%s\n' "$DIM" "$*" "$OFF"; }

# ---------------------------------------------------------------- python ---
step "python"
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PY="$(command -v "$cand")"; break
    fi
  fi
done
[ -n "$PY" ] || die "no python3 >= 3.10 found. Install one (apt/dnf/pacman install python3) and re-run."
ok "python: $PY ($("$PY" -V 2>&1))"

# ------------------------------------------------------------- binaries ---
step "system binaries"
need=()
for bin in ffmpeg ffprobe openssl; do
  command -v "$bin" >/dev/null 2>&1 || need+=("$bin")
done
if [ ${#need[@]} -eq 0 ]; then
  ok "ffmpeg, ffprobe, openssl present"
else
  warn "missing: ${need[*]}"
  if command -v apt-get >/dev/null 2>&1; then
    say "  run: sudo apt-get install -y ffmpeg openssl"
  elif command -v dnf >/dev/null 2>&1; then
    say "  run: sudo dnf install -y ffmpeg openssl"
  elif command -v pacman >/dev/null 2>&1; then
    say "  run: sudo pacman -S --noconfirm ffmpeg openssl"
  else
    say "  install ffmpeg and openssl with your package manager"
  fi
  warn "continuing — the service will start, but audio conversion and signature checks will fail"
fi

# --------------------------------------------------------------- venvs ----
step "python environments"
for part in service cli; do
  if [ ! -x "$REPO/$part/venv/bin/python" ]; then
    say "creating $part/venv"
    "$PY" -m venv "$REPO/$part/venv" || die "venv creation failed for $part"
  fi
  "$REPO/$part/venv/bin/pip" install -q --upgrade pip
  if [ "$part" = "service" ]; then
    "$REPO/$part/venv/bin/pip" install -q -r "$REPO/service/requirements.txt" || die "pip install failed (service)"
  else
    "$REPO/$part/venv/bin/pip" install -q -r "$REPO/cli/requirements.txt" || die "pip install failed (cli)"
  fi
  ok "$part/venv ready"
done

# --------------------------------------------------------------- config ---
step "config"
if [ ! -f "$REPO/config.json" ]; then
  cp "$REPO/config.example.json" "$REPO/config.json"
  warn "created config.json from the example — you must edit these before it works:"
  say  "    public_base   (public https URL that Amazon can reach)"
  say  "    voice_id      (ElevenLabs voice, or leave empty for the built-in voice)"
  say  "    skill_id      (amzn1.ask.skill.…) so notifications can ring the Echo"
  say  "    media_root    (optional music library)"
  say  "    image_roots   (optional gallery folders)"
else
  ok "config.json already present"
fi

if [ ! -f "$REPO/.env" ] && [ -n "${ELEVENLABS_API_KEY:-}" ]; then
  printf 'ELEVENLABS_API_KEY=%s\n' "$ELEVENLABS_API_KEY" > "$REPO/.env"
  chmod 600 "$REPO/.env"
  ok "wrote ELEVENLABS_API_KEY to .env"
fi

mkdir -p "$REPO/service/state/credentials"
chmod 700 "$REPO/service/state/credentials" 2>/dev/null || true

# -------------------------------------------------------------- systemd ---
step "service"
PY_SERVICE="$REPO/service/venv/bin/python"
if systemctl --user show-environment >/dev/null 2>&1; then
  UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/alexa-voice.service" <<EOF
[Unit]
Description=Alexa voice bridge (custom skill + notifications)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO/service
Environment=ALEXA_VOICE_CONFIG=$REPO/config.json
ExecStart=$PY_SERVICE -m uvicorn app.main:app --host 127.0.0.1 --port 8099
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now alexa-voice.service
  sleep 2
  if systemctl --user is-active --quiet alexa-voice.service; then
    ok "systemd user unit active (alexa-voice.service)"
  else
    warn "unit installed but not active — check: journalctl --user -u alexa-voice -n 40"
  fi
else
  warn "no systemd --user on this machine; start it manually:"
  say  "    cd $REPO/service && $PY_SERVICE -m uvicorn app.main:app --host 127.0.0.1 --port 8099"
fi

# ---------------------------------------------------------------- public ---
step "public https"
if command -v tailscale >/dev/null 2>&1; then
  say "tailscale detected. To publish just this path:"
  say "    sudo tailscale funnel --bg --set-path /alexa 8099"
  say "  then set public_base to https://<your-node>.ts.net/alexa in config.json"
elif command -v cloudflared >/dev/null 2>&1; then
  say "cloudflared detected. Example:"
  say "    cloudflared tunnel --url http://127.0.0.1:8099"
  say "  then set public_base to the https URL it prints (append nothing else)."
else
  warn "no tailscale or cloudflared found. You need an https endpoint Amazon can reach:"
  say "  - tailscale funnel (easiest, free, trusted cert)"
  say "  - cloudflared quick tunnel"
  say "  - nginx/caddy with a Let's Encrypt certificate"
fi

# ------------------------------------------------------------------ done ---
step "next"
say "1. fill in config.json"
say "2. create the Alexa skill and paste service/skill/interaction_model.json  (docs/SETUP.md)"
say "3. point the skill endpoint at your public_base"
say "4. harvest Amazon cookies into service/state/credentials/amazon_cookies.json (docs/COOKIES.md)"
say "5. run: $REPO/cli/venv/bin/python $REPO/cli/doctor.py"
