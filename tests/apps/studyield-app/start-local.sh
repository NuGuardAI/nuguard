#!/usr/bin/env bash
# Start Studyield locally via its own docker-compose.yml for NuGuard testing.
#
# Clones ./repo on first run (see clone-studyield.sh), wires our .env values
# into Studyield's expected env files, then runs `docker compose up`.
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.example to .env and fill in OPENROUTER_API_KEY / OPENAI_API_KEY and APP_USERNAME/APP_PASSWORD first." >&2
  exit 1
fi

set -o allexport
# shellcheck disable=SC1091
source .env
set +o allexport

if [[ ! -d repo/.git ]]; then
  ./clone-studyield.sh
fi

# Studyield's docker-compose.yml reads DB/cache defaults from .env.docker
# (repo root) and app secrets from backend/.env — populate both from our
# single source-of-truth .env so values never drift.
cat > repo/.env.docker <<EOF
POSTGRES_USER=${POSTGRES_USER:-postgres}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-postgres}
POSTGRES_DB=${POSTGRES_DB:-studyield_dev}
POSTGRES_PORT=${POSTGRES_PORT:-5432}
REDIS_PORT=${REDIS_PORT:-6379}
QDRANT_PORT=${QDRANT_PORT:-6333}
QDRANT_GRPC_PORT=${QDRANT_GRPC_PORT:-6334}
CLICKHOUSE_PORT=${CLICKHOUSE_PORT:-8123}
CLICKHOUSE_DATABASE=${CLICKHOUSE_DATABASE:-studyield_analytics}
CLICKHOUSE_USER=${CLICKHOUSE_USER:-default}
CLICKHOUSE_PASSWORD=${CLICKHOUSE_PASSWORD:-}
BACKEND_PORT=${BACKEND_PORT:-3010}
FRONTEND_PORT=${FRONTEND_PORT:-5189}
JWT_ACCESS_SECRET=${JWT_ACCESS_SECRET:?Set JWT_ACCESS_SECRET in .env first}
JWT_REFRESH_SECRET=${JWT_REFRESH_SECRET:?Set JWT_REFRESH_SECRET in .env first}
OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
OPENAI_API_KEY=${OPENAI_API_KEY:-}
EOF

if [[ ! -f repo/backend/.env ]]; then
  cp repo/backend/.env.example repo/backend/.env
fi

# Backend reads its own .env directly (env_file: in docker-compose.yml) —
# patch in the values docker-compose's environment: block doesn't already cover.
python3 - "$ROOT_DIR/repo/backend/.env" <<'PYEOF'
import re, sys
path = sys.argv[1]
with open(path) as f:
    content = f.read()
import os
replacements = {
    "JWT_ACCESS_SECRET": os.environ.get("JWT_ACCESS_SECRET", ""),
    "JWT_REFRESH_SECRET": os.environ.get("JWT_REFRESH_SECRET", ""),
    "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY", ""),
    "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
}
for key, value in replacements.items():
    if not value:
        continue
    content = re.sub(rf"^{key}=.*$", f"{key}={value}", content, flags=re.MULTILINE)
with open(path, "w") as f:
    f.write(content)
PYEOF

cd repo
mkdir -p data logs
docker compose --env-file .env.docker up -d --build

echo "Waiting for Studyield backend to become healthy..."
for _ in $(seq 1 30); do
  if docker compose ps --format json backend 2>/dev/null | grep -q '"Health":"healthy"'; then
    break
  fi
  sleep 2
done

echo "Studyield is running:"
echo "  Frontend: http://localhost:${FRONTEND_PORT:-5189}"
echo "  Backend API: http://localhost:${BACKEND_PORT:-3010}/api/v1"
echo "Register a test account at the frontend URL above, then set"
echo "APP_USERNAME/APP_PASSWORD in .env before running behavior/redteam."
