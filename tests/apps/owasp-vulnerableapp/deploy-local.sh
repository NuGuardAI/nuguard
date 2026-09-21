#!/usr/bin/env bash
# Local deployment for OWASP Juice Shop, matching nuguard.yaml's target
# (http://127.0.0.1:3000). See https://github.com/juice-shop/juice-shop#setup.
#
# Default: run the official Docker image (fastest, no Node toolchain needed).
# Fallback: clone the repo "from sources" per the upstream README and run it
#           with npm, when Docker isn't available or --source is passed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/juice-shop-src"
PORT="${PORT:-3000}"
REF="${REF:-master}"
MODE="auto"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
  set +o allexport
  echo "Loaded environment variables from $SCRIPT_DIR/.env file."
else
  echo "WARNING: .env not found — ALCHEMY_API_KEY-dependent challenges (Mint the Honey Pot, Wallet Depletion) will not work as intended." >&2
fi

usage() {
  cat <<EOF
Usage: $(basename "$0") [--docker|--source] [--port PORT]

  --docker      Force the Docker container method (docker pull + run).
  --source      Force the "from sources" method (git clone + npm install/start).
  --port PORT   Host port to bind (default: 3000, matching nuguard.yaml).

With no flag, uses Docker if available, otherwise falls back to source.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --docker) MODE="docker"; shift ;;
    --source) MODE="source"; shift ;;
    --port) PORT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

die() {
  echo "ERROR: $*" >&2
  exit 1
}

run_docker() {
  command -v docker >/dev/null 2>&1 || die "Docker requested but not installed."
  echo "Pulling bkimminich/juice-shop..."
  docker pull bkimminich/juice-shop
  echo "Starting OWASP Juice Shop on http://127.0.0.1:${PORT}"
  local -a env_args=()
  [[ -n "${ALCHEMY_API_KEY:-}" ]] && env_args+=(-e "ALCHEMY_API_KEY=${ALCHEMY_API_KEY}")
  # Points the AI chatbot (Chatbot Prompt Injection, System Prompt Extraction, etc.
  # challenges) at OpenAI instead of the default local-Ollama expectation.
  [[ -n "${OPENAI_API_KEY:-}" ]] && env_args+=(-e "LLM_API_KEY=${OPENAI_API_KEY}")
  local -a mount_args=()
  [[ -f "$SCRIPT_DIR/config.local.yml" ]] && mount_args+=(-v "$SCRIPT_DIR/config.local.yml:/juice-shop/config/local.yml:ro")
  exec docker run --rm --name juice-shop -p "127.0.0.1:${PORT}:3000" "${env_args[@]}" "${mount_args[@]}" bkimminich/juice-shop
}

run_source() {
  command -v node >/dev/null 2>&1 || die "Missing 'node'. Install Node.js and run this script again."
  command -v npm >/dev/null 2>&1 || die "Missing 'npm'. Install Node.js and run this script again."

  if [[ ! -d "$SRC_DIR/.git" ]]; then
    echo "Cloning juice-shop ($REF) into $SRC_DIR..."
    git clone --depth 1 --branch "$REF" https://github.com/juice-shop/juice-shop.git "$SRC_DIR"
  else
    echo "Reusing existing checkout at $SRC_DIR."
  fi

  if [[ ! -d "$SRC_DIR/node_modules" ]]; then
    echo "Installing dependencies (npm install)..."
    (cd "$SRC_DIR" && npm install)
  fi

  if [[ -f "$SCRIPT_DIR/config.local.yml" ]]; then
    cp "$SCRIPT_DIR/config.local.yml" "$SRC_DIR/config/local.yml"
  fi

  export LLM_API_KEY="${OPENAI_API_KEY:-}"

  echo "Starting OWASP Juice Shop on http://127.0.0.1:${PORT}"
  (cd "$SRC_DIR" && PORT="$PORT" exec npm start)
}

if [[ "$MODE" == "auto" ]]; then
  if command -v docker >/dev/null 2>&1; then
    MODE="docker"
  else
    MODE="source"
  fi
fi

case "$MODE" in
  docker) run_docker ;;
  source) run_source ;;
esac
