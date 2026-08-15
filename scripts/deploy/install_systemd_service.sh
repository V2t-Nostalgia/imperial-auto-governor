#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_ROOT/../.." && pwd)"
RUNTIME_ROOT="${IAG_RUNTIME_ROOT:-$PROJECT_ROOT/runtime}"
VENV_ROOT="${IAG_VENV_ROOT:-$PROJECT_ROOT/.venv}"
CONFIG="$RUNTIME_ROOT/agent_config.json"
SECRETS_ROOT="$RUNTIME_ROOT/secrets"
PASSWORD_FILE="$SECRETS_ROOT/frontend_password"
TLS_CERT="$SECRETS_ROOT/frontend_tls.crt"
TLS_KEY="$SECRETS_ROOT/frontend_tls.key"
CONSOLE_UNIT_SOURCE="$SCRIPT_ROOT/systemd/iag-agent-console.service"
CONSOLE_UNIT_TARGET="/etc/systemd/system/iag-agent-console.service"
OBSERVER_UNIT_SOURCE="$SCRIPT_ROOT/systemd/iag-agent-network-observer.service"
OBSERVER_UNIT_TARGET="/etc/systemd/system/iag-agent-network-observer.service"
LAN_IP="${IAG_LAN_IP:-$(hostname -I | awk '{print $1}')}"
SERVICE_USER="${IAG_SERVICE_USER:-${SUDO_USER:-$(id -un)}}"
SERVICE_GROUP="${IAG_SERVICE_GROUP:-$(id -gn "$SERVICE_USER")}"
SERVICE_HOME="${IAG_SERVICE_HOME:-$(getent passwd "$SERVICE_USER" | cut -d: -f6)}"
DISPLAY_VALUE="${IAG_DISPLAY:-:0}"
XAUTHORITY_VALUE="${IAG_XAUTHORITY:-$SERVICE_HOME/.Xauthority}"

if [[ ! -x "$VENV_ROOT/bin/python" ]]; then
  printf 'Missing isolated environment: %s\n' "$VENV_ROOT" >&2
  exit 2
fi
if [[ ! -f "$CONFIG" ]]; then
  printf 'Missing config: %s\n' "$CONFIG" >&2
  exit 2
fi
if [[ ! -f "$CONSOLE_UNIT_SOURCE" ]]; then
  printf 'Missing systemd unit: %s\n' "$CONSOLE_UNIT_SOURCE" >&2
  exit 2
fi
if [[ ! -f "$OBSERVER_UNIT_SOURCE" ]]; then
  printf 'Missing systemd unit: %s\n' "$OBSERVER_UNIT_SOURCE" >&2
  exit 2
fi
if [[ -z "$LAN_IP" ]]; then
  printf 'Could not determine a LAN IP. Set IAG_LAN_IP explicitly.\n' >&2
  exit 2
fi
if [[ -z "$SERVICE_HOME" ]]; then
  printf 'Could not determine home directory for service user %s.\n' "$SERVICE_USER" >&2
  exit 2
fi
if [[ "$PROJECT_ROOT$RUNTIME_ROOT$VENV_ROOT$SERVICE_HOME$XAUTHORITY_VALUE" =~ [[:space:]] ]]; then
  printf 'The systemd installer currently requires paths without whitespace.\n' >&2
  exit 2
fi

install -d -m 700 "$SECRETS_ROOT"
if [[ ! -s "$PASSWORD_FILE" ]]; then
  umask 077
  "$VENV_ROOT/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))' \
    > "$PASSWORD_FILE"
fi
chmod 600 "$PASSWORD_FILE"

if [[ ! -s "$TLS_CERT" || ! -s "$TLS_KEY" ]]; then
  rm -f -- "$TLS_CERT" "$TLS_KEY"
  umask 077
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 825 \
    -keyout "$TLS_KEY" \
    -out "$TLS_CERT" \
    -subj "/CN=$LAN_IP" \
    -addext "subjectAltName=IP:$LAN_IP,IP:127.0.0.1,DNS:localhost,DNS:$(hostname)"
fi
chmod 600 "$TLS_KEY" "$TLS_CERT"

"$VENV_ROOT/bin/python" - "$CONFIG" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding="utf-8"))
config.update(
    {
        "frontend_host": "0.0.0.0",
        "frontend_port": 8765,
        "frontend_auth_username": "iag",
        "frontend_auth_password_file": "secrets/frontend_password",
        "frontend_tls_cert": "secrets/frontend_tls.crt",
        "frontend_tls_key": "secrets/frontend_tls.key",
    }
)
temporary = path.with_name(path.name + ".tmp")
temporary.write_text(
    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
os.chmod(temporary, 0o600)
temporary.replace(path)
PY

UNIT_STAGING="$(mktemp -d)"
trap 'rm -rf -- "$UNIT_STAGING"' EXIT
"$VENV_ROOT/bin/python" - \
  "$CONSOLE_UNIT_SOURCE" "$UNIT_STAGING/iag-agent-console.service" \
  "$OBSERVER_UNIT_SOURCE" "$UNIT_STAGING/iag-agent-network-observer.service" \
  "$PROJECT_ROOT" "$RUNTIME_ROOT" "$VENV_ROOT" "$SERVICE_USER" "$SERVICE_GROUP" \
  "$SERVICE_HOME" "$DISPLAY_VALUE" "$XAUTHORITY_VALUE" <<'PY'
import sys
from pathlib import Path

console_source, console_target, observer_source, observer_target = map(Path, sys.argv[1:5])
keys = (
    "PROJECT_ROOT",
    "RUNTIME_ROOT",
    "VENV_ROOT",
    "SERVICE_USER",
    "SERVICE_GROUP",
    "SERVICE_HOME",
    "DISPLAY",
    "XAUTHORITY",
)
replacements = dict(zip(keys, sys.argv[5:], strict=True))
for source, target in ((console_source, console_target), (observer_source, observer_target)):
    text = source.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(f"@{key}@", value)
    if any(f"@{key}@" in text for key in keys):
        raise SystemExit(f"Unresolved systemd placeholder in {source}")
    target.write_text(text, encoding="utf-8")
PY

sudo install -o root -g root -m 0644 \
  "$UNIT_STAGING/iag-agent-console.service" "$CONSOLE_UNIT_TARGET"
sudo install -o root -g root -m 0644 \
  "$UNIT_STAGING/iag-agent-network-observer.service" "$OBSERVER_UNIT_TARGET"
sudo systemctl daemon-reload
sudo systemctl enable --now \
  iag-agent-network-observer.service \
  iag-agent-console.service

printf 'Console service:  %s / %s\n' \
  "$(systemctl is-enabled iag-agent-console.service)" \
  "$(systemctl is-active iag-agent-console.service)"
printf 'Observer service: %s / %s\n' \
  "$(systemctl is-enabled iag-agent-network-observer.service)" \
  "$(systemctl is-active iag-agent-network-observer.service)"
printf 'URL:     https://%s:8765/\n' "$LAN_IP"
printf 'User:    iag\n'
printf 'Password file: %s\n' "$PASSWORD_FILE"
printf 'Certificate:   %s\n' "$TLS_CERT"
