#!/usr/bin/env bash
# Start Phlox locally via docker compose for NuGuard testing.
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.example to .env and fill in OPENAI_API_KEY first." >&2
  exit 1
fi

mkdir -p data logs

docker compose up -d --build
echo "Waiting for Phlox to become healthy..."
for _ in $(seq 1 30); do
  if docker compose ps --format json phlox 2>/dev/null | grep -q '"Health":"healthy"'; then
    break
  fi
  sleep 2
done

echo "Phlox is running at http://localhost:${PORT:-5000}"
echo "First-run setup required: open the UI and configure Settings -> Model Settings"
echo "with your OpenAI-compatible LLM endpoint/key before running behavior/redteam tests."
