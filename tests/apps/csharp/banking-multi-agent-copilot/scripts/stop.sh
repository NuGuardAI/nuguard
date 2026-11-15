#!/usr/bin/env bash
# Stop the app started by scripts/serve.sh.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pidfile="$APP_DIR/.run/app.pid"
if [[ -f "$pidfile" ]]; then
  pid="$(cat "$pidfile")"
  # dotnet run starts the app as a child process; stop both.
  pkill -P "$pid" 2>/dev/null || true
  kill "$pid" 2>/dev/null || true
  rm -f "$pidfile"
fi
stubpid="$APP_DIR/.run/openai-stub.pid"
if [[ -f "$stubpid" ]]; then
  kill "$(cat "$stubpid")" 2>/dev/null || true
  rm -f "$stubpid"
fi
exit 0
