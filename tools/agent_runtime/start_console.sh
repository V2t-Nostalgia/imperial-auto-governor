#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_ROOT="$(cd "$SOURCE_ROOT/.." && pwd)"
VENV_ROOT="${IAG_VENV_ROOT:-/opt/venvs/iag-agent}"
CONFIG="${1:-$RUNTIME_ROOT/agent_config.json}"
HOST="${IAG_FRONTEND_HOST:-0.0.0.0}"
PORT="${IAG_FRONTEND_PORT:-8765}"
TLS_CERT="${IAG_TLS_CERT:-$RUNTIME_ROOT/secrets/frontend_tls.crt}"
TLS_KEY="${IAG_TLS_KEY:-$RUNTIME_ROOT/secrets/frontend_tls.key}"

if [[ ! -x "$VENV_ROOT/bin/python" ]]; then
  printf 'Missing isolated environment: %s\n' "$VENV_ROOT" >&2
  printf 'Run agent_runtime/install_ubuntu.sh first.\n' >&2
  exit 2
fi
if [[ ! -f "$CONFIG" ]]; then
  printf 'Missing config: %s\n' "$CONFIG" >&2
  exit 2
fi
if [[ "$("$VENV_ROOT/bin/python" -c 'import platform; print(platform.python_version())')" != "3.11.14" ]]; then
  printf 'The IAG console requires the dedicated Python 3.11.14 environment.\n' >&2
  exit 2
fi
if [[ ! -r "$TLS_CERT" || ! -r "$TLS_KEY" ]]; then
  printf 'Missing TLS certificate or key. Run install_systemd_service.sh first.\n' >&2
  exit 2
fi

exec "$VENV_ROOT/bin/python" "$SOURCE_ROOT/web_console.py" \
  --config "$CONFIG" \
  --host "$HOST" \
  --port "$PORT" \
  --allow-lan \
  --tls-cert "$TLS_CERT" \
  --tls-key "$TLS_KEY"
