#!/bin/bash

# Exit on error
set -e

source .env

echo "🚀 Preparing Agentic Healthcare Voice Agent for NuGuard Testing... \n"

 uv run nuguard sbom generate --from-repo https://github.com/rangoel-nu/agentic-healthcare-ai \
 --format markdown -o ./agentic-healthcare.sbom.json

echo "✅ SBOM generated successfully! \n"

echo " Compiling Cognitive Policy controls... \n"

 uv run nuguard policy compile --config ./nuguard.yaml

echo " Cognitive Policy Check... \n"

# policy check exits 2 when gaps are found — expected in testing; treat as non-fatal
 uv run nuguard policy check --config ./nuguard.yaml --format markdown -o ./healthcare-policy-check.md || {
    _exit=$?
    [[ $_exit -eq 2 ]] || { echo "ERROR: policy check failed (exit $_exit)" >&2; exit $_exit; }
  }

echo " Validating results... \n"

# behavior exits 2 when findings are present — expected in testing; treat as non-fatal
 uv run nuguard behavior --config ./nuguard.yaml --format markdown -o ./healthcare-validation.md || {
    _exit=$?
    [[ $_exit -eq 2 ]] || { echo "ERROR: behavior scan failed (exit $_exit)" >&2; exit $_exit; }
  }
