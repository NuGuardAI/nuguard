#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/python-backend"
UI_DIR="$SCRIPT_DIR/ui"

BACKEND_PORT="${BACKEND_PORT:-8250}"
FRONTEND_PORT="${FRONTEND_PORT:-3250}"
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
  set +o allexport
fi

NEXT_PUBLIC_API_BASE="${NEXT_PUBLIC_API_BASE:-http://localhost:${BACKEND_PORT}}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-http://localhost:${FRONTEND_PORT},http://127.0.0.1:${FRONTEND_PORT}}"
export BACKEND_PORT FRONTEND_PORT NEXT_PUBLIC_API_BASE ALLOWED_ORIGINS

die() {
  echo "ERROR: $*" >&2
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing '$1'. Install it and run this script again."
}

need_command node
need_command npm

[[ -d "$BACKEND_DIR" ]] || die "Backend directory not found: $BACKEND_DIR"
[[ -d "$UI_DIR" ]] || die "UI directory not found: $UI_DIR"

if command -v python3 >/dev/null 2>&1; then
  SYSTEM_PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  SYSTEM_PYTHON="$(command -v python)"
else
  die "Missing Python. Install Python 3 and run this script again."
fi

if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
  PYTHON="$BACKEND_DIR/.venv/bin/python"
elif [[ -x "$BACKEND_DIR/.venv/Scripts/python.exe" ]]; then
  PYTHON="$BACKEND_DIR/.venv/Scripts/python.exe"
else
  echo "Creating backend virtual environment..."
  "$SYSTEM_PYTHON" -m venv "$BACKEND_DIR/.venv"

  if [[ -x "$BACKEND_DIR/.venv/bin/python" ]]; then
    PYTHON="$BACKEND_DIR/.venv/bin/python"
  elif [[ -x "$BACKEND_DIR/.venv/Scripts/python.exe" ]]; then
    PYTHON="$BACKEND_DIR/.venv/Scripts/python.exe"
  else
    die "Virtual environment was created, but its Python executable could not be found."
  fi
fi

if ! "$PYTHON" -c "import uvicorn, fastapi, agents" >/dev/null 2>&1; then
  echo "Installing backend dependencies..."
  "$PYTHON" -m pip install -r "$BACKEND_DIR/requirements.txt"
fi

if [[ ! -x "$UI_DIR/node_modules/.bin/next" ]]; then
  echo "Installing UI dependencies..."
  (
    cd "$UI_DIR"
    npm install
  )
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "WARNING: OPENAI_API_KEY is not set. The app can start, but chat requests will fail until it is configured." >&2
fi

PIDS=()

cleanup() {
  if [[ ${#PIDS[@]} -gt 0 ]]; then
    echo
    echo "Stopping local demo..."
    kill "${PIDS[@]}" >/dev/null 2>&1 || true
    wait "${PIDS[@]}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

echo "Starting OpenAI CS Agents Demo"
echo "Backend:  http://localhost:${BACKEND_PORT}"
echo "Frontend: http://localhost:${FRONTEND_PORT}"
echo

(
  cd "$BACKEND_DIR"
  "$PYTHON" -m uvicorn api:app --reload --host "$BACKEND_HOST" --port "$BACKEND_PORT"
) &
PIDS+=("$!")

(
  cd "$UI_DIR"
  "$UI_DIR/node_modules/.bin/next" dev -p "$FRONTEND_PORT"
) &
PIDS+=("$!")

wait -n "${PIDS[@]}"
EXIT_CODE=$?

exit "$EXIT_CODE"
