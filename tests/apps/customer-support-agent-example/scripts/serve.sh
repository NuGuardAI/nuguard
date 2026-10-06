#!/usr/bin/env bash
# Start customer-support-agent-example (:8087) and the POST-to-GET shim (:8088), loopback only.
# Run again to restart: the booking store is in memory, so a restart reseeds MS-777.
#
# LLM wiring (the app is not modified; only its Spring properties are overridden):
#   OPENAI_API_KEY set                          -> OpenAI as the app expects
#   else AZURE_OPENAI_KEY + AZURE_OPENAI_ENDPOINT -> Azure OpenAI v1 endpoint through the OpenAI starter
#       APP_MODEL_NAME   deployment name (default gpt-5.4-mini)
#       APP_TEMPERATURE  default 1.0 (newer GPT-5 models reject temperature 0)
# Usage: scripts/serve.sh      Stop: scripts/stop.sh      Logs: reports/logs/
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$APP_DIR/.run"; LOG_DIR="$APP_DIR/reports/logs"
APP_PORT=8087; SHIM_PORT=8088
mkdir -p "$RUN_DIR" "$LOG_DIR"
cd "$APP_DIR"
"$APP_DIR/scripts/stop.sh" >/dev/null 2>&1 || true

# The example reads its terms-of-use file with getFile(), which fails inside a packaged jar.
# So run from the exploded classes directory with an explicit classpath (no source change).
CP_FILE="$APP_DIR/target/classpath.txt"
if [[ ! -f "$CP_FILE" || ! -d "$APP_DIR/target/classes" ]]; then
  echo "[serve] compiling and resolving the classpath"
  mvn -B -q -DskipTests compile dependency:build-classpath -Dmdep.outputFile="$CP_FILE"
fi

LLM_ARGS=()
if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  echo "[serve] LLM: OpenAI (OPENAI_API_KEY)"
elif [[ -n "${AZURE_OPENAI_KEY:-}" && -n "${AZURE_OPENAI_ENDPOINT:-}" ]]; then
  echo "[serve] LLM: Azure OpenAI v1 endpoint, deployment ${APP_MODEL_NAME:-gpt-5.4-mini}"
  export OPENAI_API_KEY="$AZURE_OPENAI_KEY"
  LLM_ARGS=(
    "--langchain4j.open-ai.chat-model.base-url=${AZURE_OPENAI_ENDPOINT%/}/openai/v1"
    "--langchain4j.open-ai.chat-model.model-name=${APP_MODEL_NAME:-gpt-5.4-mini}"
    "--langchain4j.open-ai.chat-model.temperature=${APP_TEMPERATURE:-1.0}"
  )
else
  echo "[serve] no LLM key in the environment (OPENAI_API_KEY, or AZURE_OPENAI_KEY + AZURE_OPENAI_ENDPOINT)" >&2
  echo "[serve] starting anyway with a placeholder key; model calls will fail" >&2
  export OPENAI_API_KEY="not-a-real-key"
fi

echo "[serve] starting app on 127.0.0.1:$APP_PORT"
SERVER_ADDRESS=127.0.0.1 SERVER_PORT=$APP_PORT \
  java -cp "$APP_DIR/target/classes:$(cat "$CP_FILE")" dev.langchain4j.example.CustomerSupportAgentApplication \
  "${LLM_ARGS[@]}" >"$LOG_DIR/app.log" 2>&1 &
echo $! >"$RUN_DIR/app.pid"

for _ in $(seq 1 120); do
  if (exec 3<>/dev/tcp/127.0.0.1/$APP_PORT) 2>/dev/null; then break; fi
  if ! kill -0 "$(cat "$RUN_DIR/app.pid")" 2>/dev/null; then
    echo "[serve] app exited early; last log lines:" >&2; tail -n 30 "$LOG_DIR/app.log" >&2; exit 1
  fi
  sleep 1
done
(exec 3<>/dev/tcp/127.0.0.1/$APP_PORT) 2>/dev/null || { echo "[serve] app did not open port $APP_PORT" >&2; tail -n 30 "$LOG_DIR/app.log" >&2; exit 1; }

echo "[serve] starting shim on 127.0.0.1:$SHIM_PORT -> :$APP_PORT"
python3 scripts/post_to_get_shim.py --port $SHIM_PORT --upstream "http://127.0.0.1:$APP_PORT" >"$LOG_DIR/shim.log" 2>&1 &
echo $! >"$RUN_DIR/shim.pid"
for _ in $(seq 1 20); do
  curl -fsS "http://127.0.0.1:$SHIM_PORT/health" >/dev/null 2>&1 && { echo "[serve] ready: target http://127.0.0.1:$SHIM_PORT/chat"; exit 0; }
  sleep 0.5
done
echo "[serve] shim did not start" >&2; tail -n 20 "$LOG_DIR/shim.log" >&2; exit 1
