# Endpoint Resolution Patch Plan (Config Must Win)

Status: Planned

Scope: `validate`, `behavior`, and `redteam` endpoint resolution for target chat paths.

## Objective

Ensure an explicit endpoint set by config or CLI always wins over SBOM-derived candidates.
SBOM discovery and live probing must only run when endpoint is not explicitly configured.

## Runtime Verification (2026-06-18)

Latest verification confirms the override bug is active in redteam today.

1. Config explicitly sets `redteam.target_endpoint: /api/agent/chat` in `tests/apps/Gemini-Auto-app/nuguard.yaml`.
2. A fresh run of:
  `uv run nuguard redteam --config tests/apps/Gemini-Auto-app/nuguard.yaml --format markdown --output tests/apps/Gemini-Auto-app/reports/gemini-auto-redteam.md --verbose`
  still selected SBOM endpoint `/api/chat/message`.
3. Report output confirms source is SBOM, not config:
  `tests/apps/Gemini-Auto-app/reports/gemini-auto-redteam.md` shows
  `Effective Endpoint: /api/chat/message (source: sbom)`.
4. This runtime behavior matches the shared resolver implementation in
  `nuguard/common/endpoint_probe.py` where `discover_chat_config_from_sbom(...)`
  returns the highest-ranked SBOM candidate whenever candidates exist.

Current confidence: high (confirmed by code path + repeated runtime execution).

## Findings from Code Review

1. Shared resolver currently allows SBOM override even when explicit endpoint is passed:
  `nuguard/common/endpoint_probe.py` in `discover_chat_config_from_sbom(...)`.
2. `redteam` always passes through shared resolver in orchestrator init, so explicit endpoint can be replaced:
  `nuguard/redteam/executor/orchestrator.py`.
3. `behavior` analyzer respects explicit endpoint during initial discovery, but runner preflight can rotate to SBOM endpoints even when explicit endpoint was configured:
  `nuguard/behavior/runner.py`.
4. `validate` mostly respects explicit endpoint selection in execution flow, but endpoint source/reporting and trace URL wiring should be tightened:
  `nuguard/validate/runner.py`.

Priority update:

1. **Phase A (shared resolver) is the critical first fix** because it currently controls endpoint precedence for redteam and can negate explicit configuration.
2. Redteam fixes in Phase B should be validated immediately after Phase A to confirm endpoint source switches from `sbom` to `config` in report metadata.

## Patch Strategy

### Phase A - Shared Discovery Contract

File: `nuguard/common/endpoint_probe.py`

1. Update `discover_chat_config_from_sbom(...)` so explicit `chat_path` is authoritative.
2. When explicit `chat_path` is present, return it unchanged.
3. Only derive/override payload key/list/response key from SBOM when caller did not explicitly set them.
4. Keep current SBOM ranking and summary fallback behavior unchanged for unset endpoint.

Exit criteria:

1. Resolver never changes explicit endpoint path.
2. Existing behavior remains unchanged when endpoint is unset.

### Phase B - Redteam Precedence Enforcement

Files: `nuguard/redteam/executor/orchestrator.py`, `nuguard/cli/commands/redteam.py`

1. Preserve explicit endpoint through orchestrator init and endpoint source tracking (`config`).
2. Ensure `_maybe_probe_endpoints()` continues to skip when explicit endpoint is set.
3. Validate report metadata/source values reflect explicit config endpoint when provided.

Exit criteria:

1. Redteam report shows effective endpoint from config when configured.
2. No SBOM/probe override for explicit endpoint.

### Phase C - Behavior Preflight Guardrails

Files: `nuguard/behavior/runner.py`, `nuguard/behavior/analyzer.py`

1. Keep analyzer behavior: discovery only when endpoint is unset.
2. In runner preflight, disable endpoint rotation/probe when endpoint is explicit.
3. For explicit endpoint failures (404/405), fail fast with actionable error instead of auto-rotating.
4. Keep current rotation/probe fallback for unset endpoint.

Exit criteria:

1. Explicit endpoint runs do not rotate.
2. Implicit endpoint runs still auto-discover and probe.

### Phase D - Validate Consistency

File: `nuguard/validate/runner.py`

1. Keep current endpoint precedence behavior (explicit wins).
2. Fix trace URL/report consistency to use resolved endpoint rather than raw config field.
3. Add/propagate endpoint source metadata where practical so outputs clearly indicate `config` vs `probe` vs `default`.

Exit criteria:

1. Validate traces/report endpoint values match actual execution endpoint.
2. Endpoint source labeling is consistent.

## Test Plan

### Shared Resolver Tests

1. Explicit endpoint passed -> returned path remains explicit.
2. Unset endpoint -> highest-ranked SBOM candidate selected.
3. Unset endpoint and no SBOM candidates -> summary fallback/default behavior unchanged.

### Redteam Tests

1. Explicit endpoint in config remains effective endpoint in run metadata.
2. Explicit endpoint does not get replaced by SBOM candidates.
3. Unset endpoint still uses SBOM/probe fallback.

### Behavior Tests

1. Explicit endpoint plus preflight 404/405 does not rotate to SBOM candidate.
2. Unset endpoint still rotates/probes as before.
3. Endpoint source reflects `config` for explicit runs.

### Validate Tests

1. Explicit endpoint bypasses discovery path.
2. Resolved endpoint appears in traces/report output.
3. Endpoint source (if emitted) is correct.

## Rollout and Safety

1. Keep behavior additive and backward compatible for unset endpoint paths.
2. Call out explicit-endpoint fail-fast behavior in changelog/docs to avoid surprises.
3. Run focused test suites for behavior/validate/redteam and a full CI pass before merge.

## Definition of Done (Endpoint Precedence)

1. Explicit endpoint is never overridden by SBOM candidates in validate, behavior, or redteam.
2. SBOM/probe discovery works only as fallback when endpoint is unset.
3. Reports and traces show correct effective endpoint and source.
4. Regression tests cover explicit and fallback paths for all three command families.
