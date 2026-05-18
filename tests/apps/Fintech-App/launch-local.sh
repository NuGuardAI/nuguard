#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing '$1'. Install it and run this script again."
}

need_command docker

if ! docker compose version >/dev/null 2>&1; then
  die "Docker Compose is not available. Install the Compose plugin and try again."
fi

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -o allexport
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
  set +o allexport
fi

if [[ -z "${APP_USERNAME:-}" || -z "${APP_PASSWORD:-}" ]]; then
  echo "WARNING: APP_USERNAME or APP_PASSWORD is not set. Some app flows may fail until they are configured." >&2
fi

echo "Starting Fintech-App local stack"
echo "Directory: $SCRIPT_DIR"
echo "Frontend:  http://localhost:8080"
echo "Orchestrator: http://localhost:8001"
echo

cd "$SCRIPT_DIR"
exec docker compose up --build