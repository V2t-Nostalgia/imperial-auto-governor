#!/usr/bin/env bash
set -euo pipefail

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_ROOT/../.." && pwd)"
RUNTIME_ROOT="${IAG_RUNTIME_ROOT:-$PROJECT_ROOT/runtime}"
VENV_ROOT="${IAG_VENV_ROOT:-$PROJECT_ROOT/.venv}"
CONFIG="${1:-$RUNTIME_ROOT/agent_config.json}"
HOST="${IAG_FRONTEND_HOST:-0.0.0.0}"
PORT="${IAG_FRONTEND_PORT:-8765}"
TLS_CERT="${IAG_TLS_CERT:-$RUNTIME_ROOT/secrets/frontend_tls.crt}"
TLS_KEY="${IAG_TLS_KEY:-$RUNTIME_ROOT/secrets/frontend_tls.key}"

if [[ ! -x "$VENV_ROOT/bin/python" ]]; then
  printf 'Missing isolated environment: %s\n' "$VENV_ROOT" >&2
  printf 'Run scripts/deploy/install_ubuntu.sh first.\n' >&2
  exit 2
fi
if [[ ! -f "$CONFIG" ]]; then
  printf 'Missing config: %s\n' "$CONFIG" >&2
  exit 2
fi
if ! "$VENV_ROOT/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  printf 'The IAG console requires Python 3.11 or newer.\n' >&2
  exit 2
fi
if [[ ! -r "$TLS_CERT" || ! -r "$TLS_KEY" ]]; then
  printf 'Missing TLS certificate or key. Run install_systemd_service.sh first.\n' >&2
  exit 2
fi

export IAG_PROJECT_ROOT="$PROJECT_ROOT"
exec "$VENV_ROOT/bin/python" -m apps.control_center.web_console \
  --config "$CONFIG" \
  --host "$HOST" \
  --port "$PORT" \
  --allow-lan \
  --tls-cert "$TLS_CERT" \
  --tls-key "$TLS_KEY"
