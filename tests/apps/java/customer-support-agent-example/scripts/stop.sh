#!/usr/bin/env bash
# Stop the app and shim started by scripts/serve.sh.
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for name in shim app; do
  pidfile="$APP_DIR/.run/$name.pid"
  if [[ -f "$pidfile" ]]; then
    kill "$(cat "$pidfile")" 2>/dev/null || true
    rm -f "$pidfile"
  fi
done
exit 0
