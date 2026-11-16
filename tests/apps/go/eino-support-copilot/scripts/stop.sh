#!/usr/bin/env bash
# Stop the API, the MCP server and (if serve.sh started it) the Postgres container.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for name in api mcp openai-stub; do
  pidfile="$APP_DIR/.run/$name.pid"
  if [[ -f "$pidfile" ]]; then
    kill "$(cat "$pidfile")" 2>/dev/null || true
    rm -f "$pidfile"
  fi
done
if [[ -f "$APP_DIR/.run/pg.container" ]]; then
  docker rm -f "$(cat "$APP_DIR/.run/pg.container")" >/dev/null 2>&1 || true
  rm -f "$APP_DIR/.run/pg.container"
fi
exit 0
