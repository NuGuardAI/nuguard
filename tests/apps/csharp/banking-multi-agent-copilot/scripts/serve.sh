#!/usr/bin/env bash
# Start banking-multi-agent-copilot on 127.0.0.1:8091 (loopback only) with the in-memory data store.
# Run again to restart: all data is in memory, so a restart reseeds infra/data + fixture/ExtraSeedData.json.
#
# LLM wiring (Azure OpenAI API key; the managed-identity path stays in the code):
#   AZURE_OPENAI_KEY + AZURE_OPENAI_ENDPOINT   required
#   APP_MODEL_NAME          chat deployment name (default gpt-5.4-mini)
# Demo login (POST /auth/token {username, password}): only Mark (the NuGuard run identity) has a known
# password, BANKING_DEMO_PASSWORD (default nuguard-demo-only). Other users get a random password per start.
# Usage: scripts/serve.sh      Stop: scripts/stop.sh      Logs: reports/logs/
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="$APP_DIR/src/MultiAgentCopilot"
RUN_DIR="$APP_DIR/.run"; LOG_DIR="$APP_DIR/reports/logs"
APP_PORT=8091
mkdir -p "$RUN_DIR" "$LOG_DIR"
"$APP_DIR/scripts/stop.sh" >/dev/null 2>&1 || true

# The app targets net9.0; fall back to a user-local SDK when the dotnet on PATH has no 9.x SDK.
if ! dotnet --list-sdks 2>/dev/null | grep -q '^9\.' && [[ -x "$HOME/.dotnet/dotnet" ]]; then
  export PATH="$HOME/.dotnet:$PATH" DOTNET_ROOT="$HOME/.dotnet"
fi
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1

if [[ -z "${AZURE_OPENAI_KEY:-}" || -z "${AZURE_OPENAI_ENDPOINT:-}" ]]; then
  echo "[serve] AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT must be set" >&2
  exit 1
fi

echo "[serve] building"
dotnet build "$PROJECT_DIR/MultiAgentCopilot.csproj" -nologo -v q -c Release >"$LOG_DIR/build.log" 2>&1 \
  || { echo "[serve] build failed; last log lines:" >&2; tail -n 30 "$LOG_DIR/build.log" >&2; exit 1; }

echo "[serve] LLM: Azure OpenAI, deployment ${APP_MODEL_NAME:-gpt-5.4-mini}"
echo "[serve] starting app on 127.0.0.1:$APP_PORT"
# Prompts are read from the relative Prompts/ directory, so run from the project directory.
cd "$PROJECT_DIR"
ASPNETCORE_URLS="http://127.0.0.1:$APP_PORT" \
ASPNETCORE_ENVIRONMENT=Development \
DataStore__Type=InMemory \
DataStore__SeedDirectory="$APP_DIR/infra/data" \
DataStore__ExtraSeedFile="$APP_DIR/fixture/ExtraSeedData.json" \
SemanticKernelServiceSettings__AzureOpenAISettings__Endpoint="$AZURE_OPENAI_ENDPOINT" \
SemanticKernelServiceSettings__AzureOpenAISettings__Key="$AZURE_OPENAI_KEY" \
SemanticKernelServiceSettings__AzureOpenAISettings__CompletionsDeployment="${APP_MODEL_NAME:-gpt-5.4-mini}" \
DemoAuth__SigningKey="$(openssl rand -hex 32)" \
DemoAuth__Passwords__Mark="${BANKING_DEMO_PASSWORD:-nuguard-demo-only}" \
DemoAuth__Passwords__Sandeep="$(openssl rand -hex 16)" \
DemoAuth__Passwords__Theo="$(openssl rand -hex 16)" \
DemoAuth__Passwords__Sajee="$(openssl rand -hex 16)" \
DemoAuth__Passwords__Abhishek="$(openssl rand -hex 16)" \
DemoAuth__Passwords__David="$(openssl rand -hex 16)" \
  dotnet run --no-build --no-launch-profile -c Release --project "$PROJECT_DIR/MultiAgentCopilot.csproj" >"$LOG_DIR/app.log" 2>&1 &
echo $! >"$RUN_DIR/app.pid"

for _ in $(seq 1 90); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$APP_PORT/chat" -H 'Content-Type: application/json' -d '{}' || true)
  if [[ "$code" == "401" ]]; then echo "[serve] ready: target http://127.0.0.1:$APP_PORT/chat"; exit 0; fi
  if ! kill -0 "$(cat "$RUN_DIR/app.pid")" 2>/dev/null; then
    echo "[serve] app exited early; last log lines:" >&2; tail -n 30 "$LOG_DIR/app.log" >&2; exit 1
  fi
  sleep 1
done
echo "[serve] app did not become ready on port $APP_PORT" >&2; tail -n 30 "$LOG_DIR/app.log" >&2; exit 1
