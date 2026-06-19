#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

# App matrix: name|config|sbom|behavior_out_base|redteam_out_base|expected_endpoint
APP_RUNS=(
  "openai-cs|tests/apps/openai-cs-agents-demo/nuguard.prepublish.yaml|tests/apps/openai-cs-agents-demo/openai-cs.sbom.json|tests/apps/openai-cs-agents-demo/reports/openai-cs-prepublish-behavior|tests/apps/openai-cs-agents-demo/reports/openai-cs-prepublish-redteam|/api/chat"
  "gemini-auto|tests/apps/Gemini-Auto-app/nuguard.prepublish.yaml|tests/apps/Gemini-Auto-app/gemini-auto.sbom.json|tests/apps/Gemini-Auto-app/reports/gemini-auto-prepublish-behavior|tests/apps/Gemini-Auto-app/reports/gemini-auto-prepublish-redteam|/api/agent/chat"
  "pinnacle-bank|tests/apps/pinnacle-bank-app/nuguard-azure.prepublish.yaml|tests/apps/pinnacle-bank-app/pinnacle-bank.sbom.json|tests/apps/pinnacle-bank-app/reports/pinnacle-bank-prepublish-behavior|tests/apps/pinnacle-bank-app/reports/pinnacle-bank-prepublish-redteam|/api/chat"
)

BEHAVIOR_SUMMARIES=()
REDTEAM_SUMMARIES=()
DURATIONS=()

run_allow_findings() {
  local label="$1"
  shift

  set +e
  "$@"
  local rc=$?
  set -e

  if [[ "$rc" -ne 0 && "$rc" -ne 2 ]]; then
    echo "[FAIL] ${label} exited with code ${rc} (expected 0 or 2)." >&2
    exit 1
  fi

  if [[ "$rc" -eq 2 ]]; then
    echo "[WARN] ${label} exited 2 (findings/gating signal). Continuing to quality checks."
  fi
}

echo "[1/3] Repo smoke checks"
uv run nuguard --help > /dev/null
uv run pytest tests/test_config.py tests/test_secret_store.py -q

echo "[2/3] App prepublish sanity runs"
for entry in "${APP_RUNS[@]}"; do
  IFS='|' read -r name cfg sbom_out behavior_base redteam_base expected_endpoint <<< "$entry"
  mkdir -p "$(dirname "$behavior_base")"

  echo "---"
  echo "[APP] ${name}"
  app_start="$(date +%s)"

  run_allow_findings "${name}: sbom" \
    uv run nuguard sbom generate \
      --config "$cfg" \
      --format json \
      -o "$sbom_out"

  run_allow_findings "${name}: behavior" \
    uv run nuguard behavior \
      --config "$cfg" \
      --mode dynamic \
      --format json \
      --format markdown \
      --output "$behavior_base" \
      --verbose

  run_allow_findings "${name}: redteam" \
    uv run nuguard redteam \
      --config "$cfg" \
      --format json \
      --format markdown \
      --output "$redteam_base" \
      --verbose

  behavior_json="${behavior_base}.json"
  redteam_json="${redteam_base}.json"

  # Behavior gates: non-empty artifact, scenarios_executed > 0, explicit endpoint resolution.
  behavior_summary="$(uv run python - "$behavior_json" "$expected_endpoint" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
expected = sys.argv[2]
if not path.exists() or path.stat().st_size == 0:
    raise SystemExit(f"missing_or_empty_report:{path}")

data = json.loads(path.read_text(encoding="utf-8"))
run_profile = data.get("run_profile") or {}
executed = int(run_profile.get("scenarios_executed", 0) or 0)
if executed <= 0:
    raise SystemExit("behavior_scenarios_executed_zero")

source = str(data.get("target_endpoint_source") or "").lower()
if source and source != "config":
    raise SystemExit(f"behavior_endpoint_source_not_config:{source}")

effective = str(data.get("effective_endpoint") or "")
if expected and effective and effective != expected:
  raise SystemExit(f"behavior_effective_endpoint_mismatch:{effective}!={expected}")

outcome = str(data.get("scan_outcome") or "unknown")
print(f"outcome={outcome};scenarios={executed};endpoint_source={source or 'unknown'};effective_endpoint={effective or 'unknown'}")
PY
)"

  # Redteam gates: non-empty artifact, scenarios executed, no inconclusive target-errors,
  # explicit endpoint resolution, and not overwhelmingly aborted/failed.
  redteam_summary="$(uv run python - "$redteam_json" "$expected_endpoint" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
expected = sys.argv[2]
if not path.exists() or path.stat().st_size == 0:
    raise SystemExit(f"missing_or_empty_report:{path}")

data = json.loads(path.read_text(encoding="utf-8"))
outcome = str(data.get("scan_outcome") or "unknown")
if outcome == "inconclusive_target_errors":
    raise SystemExit("redteam_inconclusive_target_errors")

meta = data.get("_meta") or {}
source = str(meta.get("target_endpoint_source") or "").lower()
if source and source != "config":
    raise SystemExit(f"redteam_endpoint_source_not_config:{source}")

effective = str(meta.get("effective_endpoint") or "")
if expected and effective and effective != expected:
  raise SystemExit(f"redteam_effective_endpoint_mismatch:{effective}!={expected}")

diag = data.get("diagnostics") or {}
traces = list(diag.get("scenario_traces") or [])
if not traces:
    raise SystemExit("redteam_scenarios_executed_zero")

bad = 0
for item in traces:
    status = str(item.get("status") or "").lower()
    if status in {"aborted", "failed", "skipped", "similar_miss"}:
        bad += 1

ratio = bad / len(traces)
if ratio >= 0.8:
    raise SystemExit(f"redteam_majority_not_tested:{bad}/{len(traces)}")

print(
    "outcome={};scenarios={};not_tested={};endpoint_source={};effective_endpoint={}".format(
        outcome,
        len(traces),
        bad,
        source or "unknown",
        effective or "unknown",
    )
)
PY
)"

  app_end="$(date +%s)"
  duration="$((app_end - app_start))"

  BEHAVIOR_SUMMARIES+=("${name}:${behavior_summary}")
  REDTEAM_SUMMARIES+=("${name}:${redteam_summary}")
  DURATIONS+=("${name}:${duration}s")

  echo "[PASS] ${name} behavior -> ${behavior_summary}"
  echo "[PASS] ${name} redteam -> ${redteam_summary}"
  echo "[PASS] ${name} duration -> ${duration}s"
done

echo "[3/3] Final summary"
echo "Behavior summaries:"
printf '  %s\n' "${BEHAVIOR_SUMMARIES[@]}"
echo "Redteam summaries:"
printf '  %s\n' "${REDTEAM_SUMMARIES[@]}"
echo "Durations:"
printf '  %s\n' "${DURATIONS[@]}"
