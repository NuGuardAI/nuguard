#!/bin/bash

# Exit on error
set -e

echo "🚀 Preparing Healthcare Voice Agent for NuGuard Testing... \n"

 uv run nuguard sbom generate --from-repo https://github.com/NuGuardAI/Healthcare-voice-agent \
 --format json -o ./healthcare.sbom.json -f markdown -o ./healthcare.sbom.md

echo "✅ SBOM generated successfully! \n"

echo " Validateing results... \n"

 uv run nuguard validate --config ./nuguard.yaml