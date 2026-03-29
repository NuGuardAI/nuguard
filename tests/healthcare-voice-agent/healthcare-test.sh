#!/bin/bash

# Exit on error
set -e

echo "🚀 Preparing Healthcare Voice Agent for NuGuard Testing... \n"

 uv run nuguard sbom generate --from-repo https://github.com/NuGuardAI/Healthcare-voice-agent \
 --format markdown -o ./healthcare.sbom.json

echo "✅ SBOM generated successfully! \n"

echo " Compiling Cognitive Policy controls... \n"

 uv run nuguard policy compile --config ./nuguard.yaml

echo " Cognitive Policy Check... \n"

# policy check exits 2 when gaps are found — expected in testing; treat as non-fatal
 uv run nuguard policy check --config ./nuguard.yaml --format markdown -o ./healthcare-policy-check.md || true

echo " Validating results... \n"

# validate exits 2 when findings are present — expected in testing; treat as non-fatal
 uv run nuguard validate --config ./nuguard.yaml --format markdown -o ./healthcare-validation.md || true
