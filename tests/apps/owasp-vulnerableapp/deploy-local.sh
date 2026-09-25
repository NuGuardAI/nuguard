#!/usr/bin/env bash
# Run the standalone OWASP VulnerableApp image locally.
#
# The app has no site-wide login. Individual vulnerability labs may present
# their own credential forms, which are part of the target attack surface.

set -euo pipefail

PORT="${PORT:-9090}"
CONTAINER_NAME="${CONTAINER_NAME:-owasp-vulnerableapp}"
IMAGE="${VULNERABLEAPP_IMAGE:-sasanlabs/owasp-vulnerableapp:latest}"

command -v docker >/dev/null 2>&1 || {
  echo "ERROR: Docker is required to run VulnerableApp locally." >&2
  exit 1
}

echo "Pulling $IMAGE ..."
docker pull "$IMAGE"

echo "Starting OWASP VulnerableApp at http://127.0.0.1:${PORT}/VulnerableApp"
exec docker run --rm \
  --name "$CONTAINER_NAME" \
  --publish "127.0.0.1:${PORT}:9090" \
  "$IMAGE"
