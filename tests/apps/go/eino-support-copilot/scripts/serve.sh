#!/usr/bin/env bash
# Start eino-support-copilot, loopback only: Postgres (pgvector), the MCP tool server on
# 127.0.0.1:8093 and the API on 127.0.0.1:8092 (POST /auth/token, POST /chat, /api/v1/*).
# Run again to restart. Every start drops and recreates the database schema and reseeds it
# (seed/business.sql + fixture/nuguard_seed.sql), so refunds and conversations from earlier runs are gone.
#
# Database:
#   EINO_DATABASE_URL set   use it (e.g. a CI service container); its schema is reset only when
#                           EINO_ALLOW_DB_RESET=1, otherwise serve.sh refuses to start
#   else                    start Docker container nuguard-eino-pg (pgvector/pgvector:pg17) on 127.0.0.1:${PG_PORT:-55432}
#   DATABASE_URL from the shell is ignored; the chosen URL is passed only to the child processes.
# LLM wiring (OpenAI-compatible client pointed at the Azure OpenAI v1 endpoint):
#   AZURE_OPENAI_KEY + AZURE_OPENAI_ENDPOINT   (when unset: local stub, scripts/openai_chat_stub.py)
#   APP_MODEL_NAME     deployment name (default gpt-5.4-mini)
# Demo login (POST /auth/token {username, password}):
#   ada                DEMO_USER_PASSWORD (default nuguard-demo-only)
#   bob, lin           random per start, never printed (cannot be used by NuGuard)
# Usage: scripts/serve.sh      Stop: scripts/stop.sh      Logs: reports/logs/
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$APP_DIR/.run"; LOG_DIR="$APP_DIR/reports/logs"; BIN_DIR="$APP_DIR/bin"
API_PORT=8092; MCP_PORT=8093; STUB_PORT=8095; PG_PORT="${PG_PORT:-55432}"
PG_CONTAINER=nuguard-eino-pg
if [[ -n "${EINO_DATABASE_URL:-}" && "${EINO_ALLOW_DB_RESET:-}" != "1" ]]; then
  echo "[serve] EINO_DATABASE_URL is set but every start drops schema public; set EINO_ALLOW_DB_RESET=1 to allow" >&2
  exit 1
fi
mkdir -p "$RUN_DIR" "$LOG_DIR" "$BIN_DIR"
"$APP_DIR/scripts/stop.sh" >/dev/null 2>&1 || true

# go.mod needs Go 1.25+; fall back to a user-local toolchain when the go on PATH is missing or older.
go_ok() { command -v go >/dev/null 2>&1 && go version | grep -Eq 'go1\.(2[5-9]|[3-9][0-9])'; }
if ! go_ok && [[ -x "$HOME/.local/go/bin/go" ]]; then export PATH="$HOME/.local/go/bin:$PATH"; fi
go_ok || { echo "[serve] Go 1.25+ is required" >&2; exit 1; }

# Fork PRs get no secrets: use a deterministic loopback stub of the OpenAI chat API instead
# (fork-safe E2E, as the Java fixture does). It answers with a valid Draft and never calls tools.
llm_stub=""
if [[ -z "${AZURE_OPENAI_KEY:-}" || -z "${AZURE_OPENAI_ENDPOINT:-}" ]]; then
  llm_stub=1
  echo "[serve] LLM: deterministic local OpenAI-compatible stub"
  python3 "$APP_DIR/scripts/openai_chat_stub.py" --port "$STUB_PORT" >"$LOG_DIR/openai-stub.log" 2>&1 &
  echo $! >"$RUN_DIR/openai-stub.pid"
  for _ in $(seq 1 20); do
    curl -fsS "http://127.0.0.1:$STUB_PORT/health" >/dev/null 2>&1 && break
    sleep 0.5
  done
  curl -fsS "http://127.0.0.1:$STUB_PORT/health" >/dev/null 2>&1 \
    || { echo "[serve] local OpenAI stub did not become ready" >&2; tail -n 20 "$LOG_DIR/openai-stub.log" >&2; exit 1; }
  export AZURE_OPENAI_KEY="nuguard-e2e-stub" AZURE_OPENAI_ENDPOINT="http://127.0.0.1:$STUB_PORT/"
fi

echo "[serve] building"
(cd "$APP_DIR" && go build -o "$BIN_DIR/api" ./cmd/api && go build -o "$BIN_DIR/mcp-server" ./cmd/mcp-server) >"$LOG_DIR/build.log" 2>&1 \
  || { echo "[serve] build failed; last log lines:" >&2; tail -n 30 "$LOG_DIR/build.log" >&2; exit 1; }

if [[ -z "${EINO_DATABASE_URL:-}" ]]; then
  pg_password="$(openssl rand -hex 16)"
  echo "[serve] starting Postgres container $PG_CONTAINER on 127.0.0.1:$PG_PORT"
  docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$PG_CONTAINER" -p "127.0.0.1:$PG_PORT:5432" \
    -e POSTGRES_DB=copilot -e POSTGRES_USER=copilot -e POSTGRES_PASSWORD="$pg_password" \
    pgvector/pgvector:pg17 >"$LOG_DIR/postgres.log" 2>&1 \
    || { echo "[serve] docker run failed" >&2; tail -n 20 "$LOG_DIR/postgres.log" >&2; exit 1; }
  echo "$PG_CONTAINER" >"$RUN_DIR/pg.container"
  ready=""
  for _ in $(seq 1 60); do
    # The image restarts Postgres once after init; wait for a real query over TCP.
    if docker exec "$PG_CONTAINER" psql -h 127.0.0.1 -U copilot -d copilot -tAc 'select 1' >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
  done
  [[ -n "$ready" ]] || { echo "[serve] Postgres did not become ready" >&2; docker logs "$PG_CONTAINER" 2>&1 | tail -n 20 >&2; exit 1; }
  db_url="postgres://copilot:$pg_password@127.0.0.1:$PG_PORT/copilot?sslmode=disable"
else
  echo "[serve] using EINO_DATABASE_URL from the environment (schema reset allowed)"
  db_url="$EINO_DATABASE_URL"
fi

wait_http() { # name pidfile url log
  for _ in $(seq 1 60); do
    curl -fsS "$3" >/dev/null 2>&1 && return 0
    if ! kill -0 "$(cat "$2")" 2>/dev/null; then
      echo "[serve] $1 exited early; last log lines:" >&2; tail -n 30 "$4" >&2; return 1
    fi
    sleep 1
  done
  echo "[serve] $1 did not become ready" >&2; tail -n 30 "$4" >&2; return 1
}

# Both binaries read migrations/, seed/ and fixture/ relative to the app directory.
cd "$APP_DIR"
echo "[serve] starting MCP server on 127.0.0.1:$MCP_PORT"
DATABASE_URL="$db_url" APP_MODE=mock MCP_ADDR="127.0.0.1:$MCP_PORT" "$BIN_DIR/mcp-server" >"$LOG_DIR/mcp.log" 2>&1 &
echo $! >"$RUN_DIR/mcp.pid"
wait_http "MCP server" "$RUN_DIR/mcp.pid" "http://127.0.0.1:$MCP_PORT/healthz" "$LOG_DIR/mcp.log"

if [[ -n "$llm_stub" ]]; then
  echo "[serve] LLM: local OpenAI-compatible stub on 127.0.0.1:$STUB_PORT"
else
  echo "[serve] LLM: Azure OpenAI v1 endpoint, deployment ${APP_MODEL_NAME:-gpt-5.4-mini}"
fi
echo "[serve] starting API on 127.0.0.1:$API_PORT (schema reset + reseed)"
DATABASE_URL="$db_url" \
APP_MODE=openai-compatible \
HTTP_ADDR="127.0.0.1:$API_PORT" \
MCP_URL="http://127.0.0.1:$MCP_PORT/mcp" \
MODEL_BASE_URL="${AZURE_OPENAI_ENDPOINT%/}/openai/v1/" \
MODEL_API_KEY="$AZURE_OPENAI_KEY" \
MODEL_NAME="${APP_MODEL_NAME:-gpt-5.4-mini}" \
NUGUARD_RESET_DB=true \
NUGUARD_FIXTURE_SEED="$APP_DIR/fixture/nuguard_seed.sql" \
JWT_SIGNING_KEY="$(openssl rand -hex 32)" \
DEMO_USER_PASSWORD="${DEMO_USER_PASSWORD:-nuguard-demo-only}" \
DEMO_PASSWORD_BOB="$(openssl rand -hex 16)" \
DEMO_PASSWORD_LIN="$(openssl rand -hex 16)" \
  "$BIN_DIR/api" >"$LOG_DIR/api.log" 2>&1 &
echo $! >"$RUN_DIR/api.pid"
wait_http "API" "$RUN_DIR/api.pid" "http://127.0.0.1:$API_PORT/healthz" "$LOG_DIR/api.log"
echo "[serve] ready: login http://127.0.0.1:$API_PORT/auth/token, target http://127.0.0.1:$API_PORT/chat"
