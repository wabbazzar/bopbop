#!/usr/bin/env bash
# BopBop installer — idempotent. Sets up:
#   1. ~/.bopbop scaffold (via bopbop init)
#   2. Python venv for the server
#   3. signal-cli-rest-api container (docker) + device-link instructions
#   4. systemd user service (bopbop.service)
#   5. git pre-commit leak guard (when run inside a git checkout)
#
# Flags:
#   --no-signal     skip the signal-cli container step
#   --no-start      install but don't start the service
#   --harness NAME  select claude, codex, or ollama for the service
#
# Requires: python3, curl, systemd (user instance), and the selected agent CLI.
# Docker is only needed for the signal-cli container step.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOPBOP_HOME="${BOPBOP_HOME:-$HOME/.bopbop}"
UNIT_DIR="$HOME/.config/systemd/user"
NO_SIGNAL=0; NO_START=0; HARNESS_OVERRIDE=""
while (( $# )); do
    case "$1" in
        --no-signal) NO_SIGNAL=1; shift ;;
        --no-start) NO_START=1; shift ;;
        --harness) [[ $# -ge 2 ]] || { echo "--harness needs a value" >&2; exit 1; }; HARNESS_OVERRIDE="$2"; shift 2 ;;
        *) echo "unknown flag: $1" >&2; exit 1 ;;
    esac
done

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "Preflight"
for dep in python3 curl systemctl; do
    command -v "$dep" >/dev/null || { echo "missing dependency: $dep" >&2; exit 1; }
done
selected_harness="claude"
if [[ -f "$BOPBOP_HOME/env" ]]; then
    configured_harness=$(grep -E '^BOPBOP_AGENT_HARNESS=' "$BOPBOP_HOME/env" \
        | tail -1 | cut -d= -f2-)
    [[ -n "$configured_harness" ]] && selected_harness="$configured_harness"
fi
[[ -n "$HARNESS_OVERRIDE" ]] && selected_harness="$HARNESS_OVERRIDE"
case "$selected_harness" in
    claude) selected_bin="claude" ;;
    codex) selected_bin="codex" ;;
    ollama) selected_bin="ollama" ;;
    *) echo "unsupported BOPBOP_AGENT_HARNESS: $selected_harness" >&2; exit 1 ;;
esac
if ! command -v "$selected_bin" >/dev/null; then
    echo "WARNING: '$selected_bin' CLI not on PATH for harness '$selected_harness'." >&2
fi

step "Scaffold ~/.bopbop"
"$REPO_DIR/bin/bopbop" init
if [[ -n "$HARNESS_OVERRIDE" ]]; then
    sed -i "s/^BOPBOP_AGENT_HARNESS=.*/BOPBOP_AGENT_HARNESS=$selected_harness/" "$BOPBOP_HOME/env"
    echo "selected agent harness: $selected_harness"
fi

step "Python venv"
if [[ ! -x "$REPO_DIR/server/.venv/bin/uvicorn" ]]; then
    if command -v uv >/dev/null; then
        (cd "$REPO_DIR/server" && uv venv .venv && uv pip install -p .venv/bin/python -r requirements.txt)
    else
        python3 -m venv "$REPO_DIR/server/.venv"
        "$REPO_DIR/server/.venv/bin/pip" install -q -r "$REPO_DIR/server/requirements.txt"
    fi
    echo "venv created"
else
    echo "venv already present"
fi

if [[ $NO_SIGNAL -eq 0 ]]; then
    step "signal-cli-rest-api container"
    if ! command -v docker >/dev/null; then
        echo "docker not found — skipping. Run signal-cli-rest-api yourself and set SIGNAL_HTTP_URL." >&2
    elif docker ps --format '{{.Names}}' | grep -q '^bopbop-signal$'; then
        echo "container bopbop-signal already running"
    else
        mkdir -p "$BOPBOP_HOME/signal-cli"
        docker run -d --name bopbop-signal --restart unless-stopped \
            -p 127.0.0.1:8080:8080 \
            -v "$BOPBOP_HOME/signal-cli:/home/.local/share/signal-cli" \
            -e MODE=normal \
            bbernhard/signal-cli-rest-api:latest
        echo "container started on 127.0.0.1:8080"
    fi
    cat <<'EOF'

  Link your Signal account (one time):
    1. open http://127.0.0.1:8080/v1/qrcodelink?device_name=bopbop
    2. on your phone: Signal → Settings → Linked Devices → Link New Device
    3. scan the QR code
  Then set SIGNAL_ACCOUNT / SIGNAL_ALLOWED_USERS in ~/.bopbop/env.
EOF
fi

step "systemd user service"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/bopbop.service" <<EOF
[Unit]
Description=BopBop — personal coding agent (Signal + API)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR/server
EnvironmentFile=%h/.bopbop/env
ExecStart=$REPO_DIR/server/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8090
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=bopbop

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable bopbop.service >/dev/null 2>&1 || true
if [[ $NO_START -eq 0 ]]; then
    systemctl --user restart bopbop.service
    echo "service started (journalctl --user -u bopbop -f to watch)"
else
    echo "service installed (start with: systemctl --user start bopbop)"
fi

if [[ -d "$REPO_DIR/.git" ]]; then
    step "git leak guard"
    git -C "$REPO_DIR" config core.hooksPath .githooks
    echo "pre-commit leak check enabled"
fi

step "Doctor"
"$REPO_DIR/bin/bopbop" doctor || true

cat <<'EOF'

Done. Daily driver checklist:
  - edit ~/.bopbop/context/personality.md (who you are, what the agent may touch)
  - text yourself in Signal (Note to Self) — that's your agent now
  - bopbop doctor          re-check anytime
EOF
