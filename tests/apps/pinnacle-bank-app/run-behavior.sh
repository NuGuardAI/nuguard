#!/usr/bin/env bash
REPO=/Users/ganesh/work/nuguard-base/nuguard-private
cd "$REPO"

set -o allexport
source "$REPO/.env"
set +o allexport

exec "$REPO/penv/bin/python3.14" "$REPO/penv/bin/nuguard" behavior \
  --config "$REPO/tests/apps/pinnacle-bank-app/nuguard-sbom-anthropic.yaml" \
  --format markdown \
  -o "$REPO/tests/apps/pinnacle-bank-app/reports/behavior-10-anthropic.md"
