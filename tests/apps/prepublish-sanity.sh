#!/usr/bin/env bash

set -euo pipefail

# Ensure uv is on PATH across Linux/macOS/WSL/Git-Bash without hardcoded usernames.
for bin_dir in \
  "$HOME/.local/bin" \
  "$HOME/bin" \
  /mnt/c/Users/*/.local/bin \
  /c/Users/*/.local/bin
do
  if [[ -d "$bin_dir" ]]; then
    PATH="$bin_dir:$PATH"
  fi
done
export PATH

# On Windows shells, uv may be available as uv.exe only.
if ! command -v uv >/dev/null 2>&1 && command -v uv.exe >/dev/null 2>&1; then
  uv() { uv.exe "$@"; }
  export -f uv
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "[FAIL] uv not found. Install uv and ensure it is on PATH before running prepublish." >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

# App matrix: name|config|sbom|behavior_out_base|redteam_out_base|expected_endpoint
APP_RUNS=(
  "openai-cs|tests/apps/openai-cs-agents-demo/nuguard.prepublish.yaml|tests/apps/openai-cs-agents-demo/openai-cs.sbom.json|tests/apps/openai-cs-agents-demo/reports/openai-cs-prepublish-behavior|tests/apps/openai-cs-agents-demo/reports/openai-cs-prepublish-redteam|/chat"
  "gemini-auto|tests/apps/Gemini-Auto-app/nuguard.prepublish.yaml|tests/apps/Gemini-Auto-app/gemini-auto.sbom.json|tests/apps/Gemini-Auto-app/reports/gemini-auto-prepublish-behavior|tests/apps/Gemini-Auto-app/reports/gemini-auto-prepublish-redteam|/api/agent/chat"
  "pinnacle-bank|tests/apps/pinnacle-bank-app/nuguard-azure.prepublish.yaml|tests/apps/pinnacle-bank-app/pinnacle-bank.sbom.json|tests/apps/pinnacle-bank-app/reports/pinnacle-bank-prepublish-behavior|tests/apps/pinnacle-bank-app/reports/pinnacle-bank-prepublish-redteam|/api/chat"
)

# Optional first argument restricts the run to a single app (matching the
# "name" field above), e.g. `prepublish-sanity.sh openai-cs`. Used by CI to
# run a fast one-app check on develop-branch PRs while still reusing the full
# matrix (no argument) for main-branch PRs and manual prepublish runs.
FILTER_APP="${1:-}"

BEHAVIOR_SUMMARIES=()
REDTEAM_SUMMARIES=()
DURATIONS=()

# Appends the behavior/redteam markdown reports for one app to the GitHub
# Actions Job Summary (visible directly on the run page, no artifact download
# needed). No-op outside of Actions, where GITHUB_STEP_SUMMARY is unset.
# Called on both the success path and every failure path below so a failed
# run's partial report (whatever exists) is still visible, not just its
# ::error:: annotation.
write_job_summary() {
  local app_name="$1"
  local behavior_md="$2"
  local redteam_md="$3"
  local failure_note="$4"

  if [[ -z "${GITHUB_STEP_SUMMARY:-}" ]]; then
    return 0
  fi

  {
    echo "## NuGuard prepublish sanity — ${app_name}"
    echo
    if [[ -n "$failure_note" ]]; then
      echo "> **FAILED:** ${failure_note}"
      echo
    fi
    if [[ -f "$behavior_md" ]]; then
      echo "<details><summary>Behavior report</summary>"
      echo
      cat "$behavior_md"
      echo
      echo "</details>"
      echo
    fi
    if [[ -f "$redteam_md" ]]; then
      echo "<details><summary>Redteam report</summary>"
      echo
      cat "$redteam_md"
      echo
      echo "</details>"
      echo
    fi
  } >> "$GITHUB_STEP_SUMMARY"
}

run_with_allowed_rc() {
  local label="$1"
  local allowed_csv="$2"
  shift
  shift

  set +e
  "$@"
  local rc=$?
  set -e

  if [[ ",${allowed_csv}," != *",${rc},"* ]]; then
    echo "::error title=NuGuard prepublish sanity failed::${label} exited with code ${rc} (allowed: ${allowed_csv})."
    echo "[FAIL] ${label} exited with code ${rc} (allowed: ${allowed_csv})." >&2
    write_job_summary "${name:-unknown}" "${behavior_base:-}.md" "${redteam_base:-}.md" \
      "${label} exited with code ${rc} (allowed: ${allowed_csv})."
    exit 1
  fi

  if [[ "$rc" -ne 0 ]]; then
    echo "[WARN] ${label} exited ${rc} (non-blocking findings/gating signal). Continuing to quality checks."
  fi
}

echo "[1/3] Repo smoke checks"
uv run nuguard --help > /dev/null 2>&1
uv run pytest tests/test_config.py tests/test_secret_store.py -q

echo "[2/3] App prepublish sanity runs"
matched_filter=0
for entry in "${APP_RUNS[@]}"; do
  IFS='|' read -r name cfg sbom_out behavior_base redteam_base expected_endpoint <<< "$entry"

  if [[ -n "$FILTER_APP" && "$name" != "$FILTER_APP" ]]; then
    continue
  fi
  matched_filter=1

  mkdir -p "$(dirname "$behavior_base")"

  echo "---"
  echo "[APP] ${name}"
  app_start="$(date +%s)"

  # SBOM generation is expected to be strictly successful.
  run_with_allowed_rc "${name}: sbom" "0" \
    uv run nuguard sbom generate \
      --config "$cfg" \
      --format json \
      -o "$sbom_out"

  # Findings from app-under-test must not block publishing NuGuard itself.
  # Allow severity/gating exit codes and enforce quality via JSON checks below.
  run_with_allowed_rc "${name}: behavior" "0,1,2" \
    uv run nuguard behavior \
      --config "$cfg" \
      --mode dynamic \
      --format json \
      --format markdown \
      --output "$behavior_base" \
      --verbose

  run_with_allowed_rc "${name}: redteam" "0,1,2" \
    uv run nuguard redteam \
      --config "$cfg" \
      --profile minimal \
      --format json \
      --format markdown \
      --output "$redteam_base" \
      --verbose

  behavior_json="${behavior_base}.json"
  redteam_json="${redteam_base}.json"

  # Behavior gates: non-empty artifact, scenarios_executed > 0, explicit endpoint resolution.
  if ! behavior_summary="$(uv run python - "$behavior_json" "$expected_endpoint" 2>&1 <<'PY'
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
)"; then
    echo "::error title=NuGuard prepublish sanity failed::${name}: behavior gate check failed: ${behavior_summary}"
    echo "[FAIL] ${name}: behavior gate check failed: ${behavior_summary}" >&2
    write_job_summary "$name" "${behavior_base}.md" "${redteam_base}.md" \
      "behavior gate check failed: ${behavior_summary}"
    exit 1
  fi

  # Redteam gates: non-empty artifact, scenarios executed, no inconclusive target-errors,
  # explicit endpoint resolution, and not overwhelmingly aborted/failed.
  if ! redteam_summary="$(uv run python - "$redteam_json" "$expected_endpoint" 2>&1 <<'PY'
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
)"; then
    echo "::error title=NuGuard prepublish sanity failed::${name}: redteam gate check failed: ${redteam_summary}"
    echo "[FAIL] ${name}: redteam gate check failed: ${redteam_summary}" >&2
    write_job_summary "$name" "${behavior_base}.md" "${redteam_base}.md" \
      "redteam gate check failed: ${redteam_summary}"
    exit 1
  fi

  app_end="$(date +%s)"
  duration="$((app_end - app_start))"

  BEHAVIOR_SUMMARIES+=("${name}:${behavior_summary}")
  REDTEAM_SUMMARIES+=("${name}:${redteam_summary}")
  DURATIONS+=("${name}:${duration}s")

  echo "[PASS] ${name} behavior -> ${behavior_summary}"
  echo "[PASS] ${name} redteam -> ${redteam_summary}"
  echo "[PASS] ${name} duration -> ${duration}s"
  write_job_summary "$name" "${behavior_base}.md" "${redteam_base}.md" ""
done

if [[ -n "$FILTER_APP" && "$matched_filter" -eq 0 ]]; then
  echo "::error title=NuGuard prepublish sanity failed::no app named '${FILTER_APP}' found in APP_RUNS."
  echo "[FAIL] no app named '${FILTER_APP}' found in APP_RUNS." >&2
  exit 1
fi

echo "[3/3] Final summary"
echo "Behavior summaries:"
printf '  %s\n' "${BEHAVIOR_SUMMARIES[@]}"
echo "Redteam summaries:"
printf '  %s\n' "${REDTEAM_SUMMARIES[@]}"
echo "Durations:"
printf '  %s\n' "${DURATIONS[@]}"
