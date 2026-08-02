#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_ROOT="$(cd "$SOURCE_ROOT/.." && pwd)"
PYTHON_SOURCE="${IAG_PYTHON:-python3}"
VENV_ROOT="${IAG_VENV_ROOT:-$RUNTIME_ROOT/.venv}"

sudo apt-get update
sudo apt-get install -y \
  build-essential \
  ffmpeg \
  libnetfilter-queue-dev \
  nftables \
  openssl \
  pkg-config \
  python3 \
  python3-dev \
  python3-venv \
  xdotool

if ! command -v "$PYTHON_SOURCE" >/dev/null 2>&1 && [[ ! -x "$PYTHON_SOURCE" ]]; then
  printf 'Python executable not found: %s\n' "$PYTHON_SOURCE" >&2
  exit 2
fi

if ! "$PYTHON_SOURCE" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit(1)
PY
then
  printf 'IAG requires Python 3.11 or newer: %s\n' "$PYTHON_SOURCE" >&2
  exit 2
fi

if [[ ! -x "$VENV_ROOT/bin/python" ]]; then
  "$PYTHON_SOURCE" -m venv "$VENV_ROOT"
fi

VENV_VERSION="$("$VENV_ROOT/bin/python" -c 'import platform; print(platform.python_version())')"
if ! "$VENV_ROOT/bin/python" - <<'PY'
import sys

if sys.version_info < (3, 11):
    raise SystemExit(1)
PY
then
  printf 'Refusing venv with Python %s; version 3.11 or newer is required.\n' \
    "$VENV_VERSION" >&2
  exit 2
fi

"$VENV_ROOT/bin/python" -m pip install --upgrade pip
"$VENV_ROOT/bin/python" -m pip install -r "$SOURCE_ROOT/requirements-linux.txt"

if [[ ! -f "$RUNTIME_ROOT/agent_config.json" ]]; then
  install -m 600 "$SOURCE_ROOT/agent_config.example.json" "$RUNTIME_ROOT/agent_config.json"
fi

mkdir -p \
  "$RUNTIME_ROOT/calibration/captures" \
  "$RUNTIME_ROOT/operator/prompt_history" \
  "$RUNTIME_ROOT/runs" \
  "$RUNTIME_ROOT/secrets" \
  "$RUNTIME_ROOT/state"
chmod 700 "$RUNTIME_ROOT/secrets"

printf 'IAG runtime installed in %s\n' "$RUNTIME_ROOT"
printf 'Python environment: %s\n' "$VENV_ROOT"
printf 'Review %s before starting the console.\n' "$RUNTIME_ROOT/agent_config.json"
