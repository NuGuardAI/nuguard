#!/bin/bash

# Exit on error
set -e

echo "🚀 Preparing Healthcare Voice Agent for NuGuard Testing... \n"

 uv run nuguard sbom generate --from-repo https://github.com/NuGuardAI/Healthcare-voice-agent \
 --format markdown -o ./healthcare.sbom.json

echo "✅ SBOM generated successfully! \n"

echo " Cognitive Policy Check... \n"

 uv run nuguard policy --config ./nuguard.yaml --policy ./cognitive-policy.md

echo " Validateing results... \n"

 uv run nuguard validate --config ./nuguard.yaml
