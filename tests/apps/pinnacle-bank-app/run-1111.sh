#!/usr/bin/env bash
set -o allexport
source /Users/ganesh/work/nuguard-base/nuguard-private/.env
set +o allexport

REPO=/Users/ganesh/work/nuguard-base/nuguard-private
CONF="$REPO/tests/apps/pinnacle-bank-app/nuguard-sbom-azure.yaml"
RPT="$REPO/tests/apps/pinnacle-bank-app/reports"
PY="$REPO/penv/bin/python3.14"
NG="$REPO/penv/bin/nuguard"

nohup "$PY" "$NG" behavior --config "$CONF" --format markdown --verbose -o "$RPT/behavior-2222.md" > "$RPT/behavior-2222.log" 2>&1 &
echo "behavior PID: $!"

nohup "$PY" "$NG" redteam --config "$CONF" --format markdown --verbose --profile full -o "$RPT/redteam-2222.md" > "$RPT/redteam-2222.log" 2>&1 &
echo "redteam PID: $!"
